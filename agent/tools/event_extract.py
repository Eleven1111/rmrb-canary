"""
Tool: 政策事件抽取（P1，对应方案 §5.2 / §6.1 / §6.2）

判断单位从"含关键词的句子"再降一级到**分句**，每个分句抽成一个候选事件：
主体 / 动作 / 对象 / 政策工具 / 方向 / 程序状态 / 约束强度 / 适用范围 /
否定 / 时态 / 权威性。

这是**规则召回 + 预结构化**，不是完整语义解析。分工按方案 §6.2：
  规则（本模块）  → 给出可核验的候选特征与原文定位
  模型（上层）    → 在候选之上做结构化语义抽取
  程序（evidence_check）→ 核验抽取结果是否真有原文支持

因此本模块的 confidence 只有 'low' / 'medium'，永远不给 'high'；
凡是主体不明、方向冲突、否定范围不清、对象不落在主题上的，一律
`needs_review=True`，由人工或模型消解，**不得直接进入已确认结论**。

四类必须处理的陷阱（方案 §6.2）在此实现：
  1. "不得随意查封企业" → 否定 + 执法工具 = 约束执法行为，不是行业整治升级
  2. "去年依法查处……"   → 历史回顾，不计为本期新增行动
  3. "支持行业发展，同时整治违法收费" → 拆成两个分句事件，对象不同
  4. "拟/研究/征求意见" 与 "决定/自某日起施行" → 程序状态不同
  另加：地方机关 vs 中央机关、作者建议与被引述观点不计为官方行动。

主体继承：同一句内主体只出现一次是中文常态，分句无主体时向前继承同句主体，
并标 `subject_inherited=True` + 待核验。跨句不继承。
既无主体又无主题词的分句是续写残片，直接丢弃，不计为事件。

否定与回顾的判定**委托给 `context_filter.classify_occurrence`**：它按命中位置
在同句内回看，否定标记要求紧邻（10 字内），比分句级布尔更准
（"不搞运动式减碳"里的否定管得住"运动式"，但管不到十几字外的另一个动作）。
本模块自己的标记表作为补集并入 —— 两边词表互有缺漏（它有"不搞/防止"，
本模块有"不得/严禁"），取并集而不是二选一。
"""

import re

from agent.tools.scoping import evidence_of
from agent.tools.context_filter import classify_occurrence

# ── 分句：比句子更细，用于拆开"支持A，同时整治B"这类并列结构 ──────
_CLAUSE_SPLIT = re.compile(r'[^，,；;、]+[，,；;、]?')

# ── 主体 ────────────────────────────────────────────────
CENTRAL_SUBJECTS = [
    '党中央', '国务院', '中央政治局', '中央财经委', '中央深改委', '国家安全委员会',
    '国家发展改革委', '发改委', '工业和信息化部', '工信部', '财政部', '商务部',
    '市场监管总局', '网信办', '国家互联网信息办公室', '中国人民银行', '国家税务总局',
    '生态环境部', '教育部', '科学技术部', '科技部', '国家数据局', '国家能源局',
    '国家卫生健康委', '国家疾病预防控制局', '国家疾控局', '国家药品监督管理局',
    '国家药监局', '国家医疗保障局', '国家医保局', '公安部', '最高人民检察院',
    '最高人民法院', '海关总署', '国家标准化管理委员会',
]
LOCAL_SUBJECT_PATTERNS = [
    re.compile(r'[一-龥]{2,8}(?:省|市|自治区|自治州|县|区)(?:委|政府|人民政府|有关部门|主管部门|相关部门)'),
    re.compile(r'(?:各地|地方|当地|属地)(?:政府|党委|有关部门|主管部门|相关部门)?'),
]
UNSPECIFIED_OFFICIAL = [
    '有关部门', '主管部门', '相关部门', '监管部门', '有关方面', '有关单位',
]
MARKET_SUBJECTS = [
    '企业', '公司', '平台', '经营者', '生产企业', '医疗机构', '接种单位',
    '行业协会', '用人单位', '从业者', '机构', '研发单位',
]
QUOTED_SUBJECTS = [
    '专家', '学者', '代表', '委员', '业内人士', '受访者', '研究员', '教授',
    '分析人士', '负责人表示', '有关人士',
]
QUOTED_VERBS = ['建议', '认为', '呼吁', '表示', '指出', '坦言', '介绍说']

# ── 政策工具 ─────────────────────────────────────────────
POLICY_TOOLS = {
    '财政': ['补贴', '专项资金', '财政资金', '财政投入', '资金支持', '奖补', '贴息'],
    '税收': ['减税', '税收优惠', '免征', '退税', '税前扣除'],
    '信贷': ['贷款', '信贷', '融资支持', '再贷款', '授信'],
    '准入': ['许可', '审批', '备案', '准入', '资质', '牌照', '注册审评', '批签发'],
    '标准': ['标准', '技术要求', '规范性文件', '指南', '规程', '目录管理'],
    '执法': ['查处', '检查', '处罚', '整治', '取缔', '约谈', '督查', '执法',
             '查封', '吊销', '通报批评', '立案', '曝光'],
    '采购': ['集中采购', '政府采购', '招标', '带量采购', '挂网'],
    '试点': ['试点', '示范', '先行先试', '试行'],
    '规划': ['规划', '实施方案', '指导意见', '行动计划', '纲要', '部署', '专项行动'],
    '监测': ['监测', '监管', '追溯', '信息化管理', '全程追溯', '抽检'],
}

# ── 方向 ─────────────────────────────────────────────────
DIRECTION_TERMS = {
    '支持': ['支持', '鼓励', '推动', '促进', '加快', '扶持', '优先', '加大力度',
             '培育', '壮大', '激励', '便利化'],
    '限制': ['禁止', '不得', '严禁', '限制', '取缔', '叫停', '关停', '压减',
             '淘汰', '暂停', '收紧'],
    '规范': ['规范', '加强管理', '加强监管', '完善监管', '健全', '有序', '依法管理',
             '整治', '治理', '强化监管', '严格管理', '加强规范', '严格监管'],
}

# ── 程序状态 ─────────────────────────────────────────────
PROCEDURAL_STATUS = {
    '研究': ['研究制定', '正在研究', '拟制定', '拟出台', '谋划', '论证'],
    '征求意见': ['公开征求意见', '征求意见稿', '征求社会公众意见'],
    '正式发布': ['印发', '发布', '出台', '公布', '审议通过', '正式实施方案'],
    '实施': ['施行', '实施', '落地', '开展', '启动', '推行'],
    '修订': ['修订', '修改', '修正', '完善为'],
    '废止': ['废止', '取消', '撤销', '终止', '不再执行'],
}

# ── 约束强度 ─────────────────────────────────────────────
OBLIGATION_LEVELS = {
    '义务': ['应当', '必须', '不得', '严禁', '须', '一律'],
    '责任': ['牵头', '负责', '问责', '追究责任', '责任主体', '配合单位', '压实责任'],
    '期限': ['前完成', '起施行', '截止', '限期', '之日起'],
    '具体任务': ['推进', '开展', '建设', '组织实施', '部署', '落实'],
    '倡议': ['鼓励', '倡导', '引导', '支持', '提倡'],
}

# ── 适用范围 ─────────────────────────────────────────────
SCOPE_TERMS = {
    '全国': ['全国', '各地区', '在全国范围'],
    '试点': ['试点', '示范区', '先行先试', '试行'],
    '地方': ['本省', '本市', '当地', '属地'],
    '例外': ['除外', '不适用', '另有规定', '暂不包括'],
}
REGION_PATTERN = re.compile(
    r'(北京|上海|广东|深圳|浙江|江苏|四川|湖北|湖南|河南|河北|山东|陕西|福建|安徽|'
    r'辽宁|吉林|黑龙江|云南|贵州|广西|内蒙古|新疆|西藏|青海|甘肃|宁夏|海南|重庆|天津|江西|山西)')
AMOUNT_PATTERN = re.compile(r'(?:人民币)?\s*\d+(?:\.\d+)?\s*(?:亿|万)?元')

# ── 否定与时态 ───────────────────────────────────────────
NEGATION_MARKERS = ['不得', '严禁', '禁止', '不再', '不予', '避免', '防止', '杜绝',
                    '不搞', '不能', '严禁以', '坚决防止']
HISTORICAL_MARKERS = ['去年', '上年', '以来', '累计', '已经', '已', '曾', '过去',
                      '近年来', '同比', '截至目前', '此前', '历年']
FORWARD_MARKERS = ['将', '拟', '下一步', '明年', '未来', '即将', '计划', '决定']


def _split_clauses(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _CLAUSE_SPLIT.finditer(text):
        raw = m.group().strip('，,；;、 ')
        if raw:
            out.append((m.start(), m.end(), raw))
    return out or [(0, len(text), text)]


def _match_any(text: str, terms) -> list[str]:
    return [t for t in terms if t in text]


def _detect_subject(clause: str) -> dict:
    """主体识别。中央机关、地方机关、市场主体、被引述观点必须分开。"""
    # “专家建议国务院……”里的国务院是建议对象，不是实施主体。先处理
    # 引述观点，才能避免随后中央机关词表把它误判成官方已经行动。
    quoted = _match_any(clause, QUOTED_SUBJECTS)
    if quoted and _match_any(clause, QUOTED_VERBS):
        return {'subject': quoted[0], 'level': '非官方', 'authority': 'quoted_opinion',
                'all_matched': quoted}

    central = _match_any(clause, CENTRAL_SUBJECTS)
    if central:
        return {'subject': central[0], 'level': '中央', 'authority': 'official',
                'all_matched': central}

    for pattern in LOCAL_SUBJECT_PATTERNS:
        m = pattern.search(clause)
        if m and m.group().strip():
            return {'subject': m.group(), 'level': '地方', 'authority': 'official',
                    'all_matched': [m.group()]}

    unspecified = _match_any(clause, UNSPECIFIED_OFFICIAL)
    if unspecified:
        # 是官方主体，但没说是哪个机关 —— 保留官方属性，同时强制标待核验，
        # 不能因为"没点名"就当成非官方行为丢掉。
        return {'subject': unspecified[0], 'level': '未指明机关',
                'authority': 'official', 'all_matched': unspecified}

    market = _match_any(clause, MARKET_SUBJECTS)
    if market:
        return {'subject': market[0], 'level': '市场主体', 'authority': 'non_official',
                'all_matched': market}

    return {'subject': None, 'level': '未识别', 'authority': 'unknown', 'all_matched': []}


def _context_class(seg_text: str, clause: str, phrases: list[str]) -> str:
    """
    用远端语境过滤器判定本分句命中的语境。

    对分句里第一个命中的短语，在**整句**文本中定位后调用 classify_occurrence——
    否定标记常常落在分句边界之外（"不搞A，也不搞B"），只看分句会漏。
    """
    for phrase in phrases:
        pos = seg_text.find(phrase)
        if pos >= 0:
            return classify_occurrence(seg_text, pos, phrase)
    return 'valid'


def _detect_tense(clause: str) -> str:
    hist = _match_any(clause, HISTORICAL_MARKERS)
    fwd = _match_any(clause, FORWARD_MARKERS)
    if hist and not fwd:
        return 'historical'
    if fwd and not hist:
        return 'forward'
    if hist and fwd:
        return 'ambiguous'
    return 'current'


def _detect_direction(clause: str, tools: dict, negated: bool) -> tuple[str, list[str]]:
    """
    方向判定。关键规则：**被否定的执法动作是对执法行为的约束，不是行业整治升级**。
    "不得随意查封企业"约束的是执法者，把它读成"整治升级"是方向反了。
    """
    hits = {d: _match_any(clause, terms) for d, terms in DIRECTION_TERMS.items()}
    present = [d for d, v in hits.items() if v]

    if negated and '执法' in tools:
        return '约束执法行为', sorted({w for v in hits.values() for w in v})

    if not present:
        return '未明确', []
    if len(present) == 1:
        return present[0], hits[present[0]]
    if set(present) == {'限制', '规范'}:
        return '规范', hits['规范'] + hits['限制']
    return '混合', sorted({w for v in hits.values() for w in v})


def _detect_scope(clause: str) -> dict:
    scope = {k: _match_any(clause, v) for k, v in SCOPE_TERMS.items()}
    regions = sorted(set(REGION_PATTERN.findall(clause)))
    labels = [k for k, v in scope.items() if v]
    return {
        'scope_labels': labels,
        'regions': regions,
        'has_exception': bool(scope['例外']),
    }


def extract_events(scope: dict, topic_terms: list[str],
                   source_layer: str = 'report',
                   default_subjects: dict = None) -> dict:
    """
    从目标片段中抽取候选政策事件。

    参数：
      scope: agent.tools.scoping.build_target_segments() 的返回值
      topic_terms: 主题检索词（关键词 + 同义词）
      source_layer: 'report'（报道）或 'official_document'（正式文件）。
        两者证据力不同：报道说明议程如何呈现，文件说明正式规定了什么。
        绝不能混成一类计数。

    返回：
      {status, events: [...], counts: {...}, needs_review_count, note}

    对象归属规则：分句里没有主题词 → `applies_to_topic=False`。
    这类事件仍然保留（它是同篇文章里的共现事件），但**不进入主题方向汇总**，
    并标 needs_review。"支持行业发展，同时整治违法收费"就是靠这条拆开的。
    """
    if not isinstance(scope, dict) or not scope.get('segments'):
        return {'status': 'unknown', 'events': [], 'counts': {},
                'needs_review_count': 0,
                'reason': '无目标相关片段',
                'note': '无证据，不抽取事件。'}

    terms = [t for t in (topic_terms or []) if t]
    events = []

    for seg in scope['segments']:
        seg_text = seg.get('text', '')
        seg_start = seg.get('char_start', 0)

        # 主体在句内向前继承：中文行文里主体通常只在句首出现一次，
        # "教育部将实施A，并推动B"的第二个分句主体是省略的，不是没有。
        # 按分句独立判主体会把 80%+ 的事件判成"主体未识别"（真实样本实测）。
        # 继承范围仅限同一句，跨句不继承 —— 那样会把上一句的主体安到下一句头上。
        inherited_subject = None

        for c_start, c_end, clause in _split_clauses(seg_text):
            tools = {name: hits for name, hits in
                     ((n, _match_any(clause, v)) for n, v in POLICY_TOOLS.items()) if hits}
            status_hits = {name: hits for name, hits in
                           ((n, _match_any(clause, v)) for n, v in PROCEDURAL_STATUS.items())
                           if hits}
            obligations = {name: hits for name, hits in
                           ((n, _match_any(clause, v)) for n, v in OBLIGATION_LEVELS.items())
                           if hits}
            negation_hits = _match_any(clause, NEGATION_MARKERS)
            # 两套判定取并集：远端按命中位置回看且要求否定紧邻，本模块按分句词表。
            all_phrases = [w for hits in tools.values() for w in hits] + \
                          [w for hits in status_hits.values() for w in hits]
            ctx = _context_class(seg_text, clause, all_phrases)
            negated = bool(negation_hits) or ctx == 'negated'
            if ctx == 'negated' and not negation_hits:
                negation_hits = ['(context_filter 判定为否定语境)']

            subject = _detect_subject(clause)
            if subject['authority'] == 'unknown' and default_subjects:
                fallback = default_subjects.get(seg.get('article_title', ''))
                if fallback:
                    subject = {'subject': fallback, 'level': '中央',
                               'authority': 'official', 'all_matched': [fallback],
                               'from_metadata': True}
            if subject['authority'] != 'unknown':
                inherited_subject = subject
                subject = {**subject, 'inherited': False}
            elif inherited_subject:
                subject = {**inherited_subject, 'inherited': True}
            else:
                subject = {**subject, 'inherited': False}

            direction, direction_words = _detect_direction(clause, tools, negated)

            # 工具、程序状态、方向三者全无的分句才是纯描述，跳过。
            # 只要有明确方向词（支持/限制/规范），即便没有具体工具，也是一条方向证据 ——
            # "支持某产业发展"必须能被抽出来，否则支持类政策会系统性漏报。
            if not tools and not status_hits and direction == '未明确':
                continue

            object_terms_here = [t for t in terms if t in clause]
            # 既没有行为主体、也没有主题对象的分句是上一分句的续写残片
            # （"以促进地区和平发展繁荣。"），不是独立事件。收进来只会虚增事件数。
            if subject['authority'] == 'unknown' and not object_terms_here:
                continue
            tense = _detect_tense(clause)
            if ctx == 'retrospective' and tense == 'current':
                # 远端回顾标记（当年/曾经/历史上…）覆盖面比本模块宽，采信它。
                tense = 'historical'
            scope_info = _detect_scope(clause)
            object_terms = object_terms_here
            applies = bool(object_terms)

            review_reasons = []
            if subject['authority'] == 'unknown':
                review_reasons.append('主体未识别，无法判断是谁在做')
            if subject['level'] == '未指明机关':
                review_reasons.append('主体为"有关部门"等泛指，未点明具体机关')
            if subject.get('inherited'):
                review_reasons.append('主体由同句前文继承而来，需确认该分句确实沿用同一主体')
            if subject.get('from_metadata'):
                review_reasons.append('主体取自文件发文机关（无主语句），需确认该条款的实际执行主体')
            if subject['authority'] in ('quoted_opinion', 'non_official'):
                review_reasons.append('主体为被引述观点或市场主体，不是官方行动')
            if direction == '混合':
                review_reasons.append(f'方向{direction}，需回原文消解')
            elif direction == '未明确' and not (tools or status_hits):
                review_reasons.append('方向未明确且没有可核验的工具或程序状态')
            if tense in ('historical', 'ambiguous'):
                review_reasons.append(f'时态为{tense}，可能是历史回顾而非本期新增')
            if not applies:
                review_reasons.append('分句中没有主题词，对象可能不是本主题')
            if negated:
                review_reasons.append('含否定表述，需确认否定范围覆盖了哪个动作')
            if len(status_hits) > 1:
                review_reasons.append('同一分句出现多个程序状态')

            events.append({
                'source_layer': source_layer,
                'clause': clause,
                'subject': subject['subject'],
                'subject_level': subject['level'],
                'subject_inherited': bool(subject.get('inherited')),
                'subject_source': ('document_metadata' if subject.get('from_metadata')
                                   else 'clause'),
                'authority': subject['authority'],
                'policy_tools': sorted(tools.keys()),
                'tool_terms': sorted({w for v in tools.values() for w in v}),
                'direction': direction,
                'direction_terms': sorted(set(direction_words)),
                'procedural_status': sorted(status_hits.keys()),
                'obligation_levels': sorted(obligations.keys()),
                'negated': negated,
                'negation_terms': negation_hits,
                'context_class': ctx,
                'tense': tense,
                'object_terms': object_terms,
                'applies_to_topic': applies,
                'scope_labels': scope_info['scope_labels'],
                'regions': scope_info['regions'],
                'amounts': sorted(set(AMOUNT_PATTERN.findall(clause))),
                'has_exception': scope_info['has_exception'],
                'is_new_action': (tense not in ('historical', 'ambiguous')
                                  and subject['authority'] == 'official'
                                  and applies and not negated),
                'needs_review': bool(review_reasons),
                'review_reasons': review_reasons,
                # 规则抽取的置信度上限就是 medium：结构是猜的，证据是真的。
                'confidence': 'low' if review_reasons else 'medium',
                # 证据范围必须覆盖主张本身：主体是从前文继承的，引文就得包含前文，
                # 否则核验器会（正确地）判定"主体未出现在自己的引文里"。
                # 放宽核验是错的，扩大证据范围才对。
                'evidence': evidence_of(
                    {**seg,
                     'text': seg_text if subject.get('inherited') else clause,
                     'char_start': seg_start if subject.get('inherited')
                     else seg_start + c_start,
                     'char_end': (seg_start + len(seg_text)) if subject.get('inherited')
                     # _split_clauses 的正则 span 包含分隔符，clause 已剥离。
                     # 用 c_end 会把“，”计入位置，使实际切片多一个字符。
                     else seg_start + c_start + len(clause)},
                    '/'.join(sorted(tools.keys())) or '/'.join(sorted(status_hits.keys())),
                ),
            })

    on_topic = [e for e in events if e['applies_to_topic']]
    return {
        'source_layer': source_layer,
        'status': 'ok' if events else 'no_events',
        'events': events,
        'counts': {
            'total': len(events),
            'on_topic': len(on_topic),
            'off_topic': len(events) - len(on_topic),
            'official_new_actions': sum(1 for e in events if e['is_new_action']),
            'historical': sum(1 for e in events if e['tense'] == 'historical'),
            'negated': sum(1 for e in events if e['negated']),
            'quoted_opinion': sum(1 for e in events if e['authority'] == 'quoted_opinion'),
        },
        'needs_review_count': sum(1 for e in events if e['needs_review']),
        'note': '规则召回 + 预结构化，不是语义解析。needs_review 的事件不得直接进入结论；'
                '模型可在此基础上做结构化抽取，但必须通过 evidence_check 的原文核验。',
    }
