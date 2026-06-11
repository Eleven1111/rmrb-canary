"""
Tool: 部委协同度检测（纯代码）

L0-L5 五档协同度，是"确定性行动"最强的单一预测变量。

v2: 加权版 — 部委出现在标题（文章主体）vs 正文背景提及，权重差 3 倍。
    版面位置进一步放大信号（头版部委信号 >> 后版提及）。
v3: 机构词表补齐 2023 年机构改革后新设机构（金融监管总局、国家数据局、
    中央金融委/科技委等党中央议事协调机构）+ 纪委监委（行业反腐最强信号）；
    新增"等N部门联合印发"显式检测——联合发文是协同度最直接的证据。
"""

import re

from agent.tools.weighting import weighted_pattern_match, get_article_weight

MINISTRY_PATTERNS = {
    '国家发展改革委': {
        'patterns': ['发改委', '国家发展改革委', '发展改革'],
        'tier': 'ministry',
    },
    '工业和信息化部': {
        'patterns': ['工信部', '工业和信息化部'],
        'tier': 'ministry',
    },
    '财政部': {
        'patterns': ['财政部', '财政政策'],
        'tier': 'ministry',
    },
    '国家市场监督管理总局': {
        'patterns': ['市场监管总局', '市场监管', '反垄断'],
        'tier': 'ministry',
    },
    '国家互联网信息办公室': {
        'patterns': ['网信办', '网络安全和信息化'],
        'tier': 'ministry',
    },
    '中国人民银行': {
        'patterns': ['人民银行', '央行', '货币政策'],
        'tier': 'ministry',
    },
    '国家税务总局': {
        'patterns': ['税务总局', '税务机关'],
        'tier': 'ministry',
    },
    '生态环境部': {
        'patterns': ['生态环境部', '环保部门'],
        'tier': 'ministry',
    },
    '教育部': {
        'patterns': ['教育部', '教育主管'],
        'tier': 'ministry',
    },
    '国家能源局': {
        'patterns': ['国家能源局', '能源监管'],
        'tier': 'ministry',
    },
    '商务部': {
        'patterns': ['商务部', '外贸', '进出口管制'],
        'tier': 'ministry',
    },
    # 2023 机构改革后新设/重组机构
    '国家金融监督管理总局': {
        'patterns': ['金融监管总局', '国家金融监督管理总局'],
        'tier': 'ministry',
    },
    '中国证监会': {
        'patterns': ['证监会', '证券监督管理'],
        'tier': 'ministry',
    },
    '国家数据局': {
        'patterns': ['国家数据局'],
        'tier': 'ministry',
    },
    '国家卫生健康委': {
        'patterns': ['卫健委', '国家卫生健康委'],
        'tier': 'ministry',
    },
    '住房城乡建设部': {
        'patterns': ['住建部', '住房城乡建设部', '住房和城乡建设部'],
        'tier': 'ministry',
    },
    '农业农村部': {
        'patterns': ['农业农村部'],
        'tier': 'ministry',
    },
    '人力资源社会保障部': {
        'patterns': ['人社部', '人力资源社会保障部', '人力资源和社会保障部'],
        'tier': 'ministry',
    },
    '应急管理部': {
        'patterns': ['应急管理部'],
        'tier': 'ministry',
    },
    '海关总署': {
        'patterns': ['海关总署'],
        'tier': 'ministry',
    },
    '国家医疗保障局': {
        'patterns': ['国家医保局', '医保局'],
        'tier': 'ministry',
    },
    # 高信号层级
    '国务院': {
        'patterns': ['国务院常务会议', '国务院专题', '国务院部署', '国务院办公厅'],
        'tier': 'state_council',
    },
    '中央政治局': {
        'patterns': ['政治局会议', '政治局常委', '中央政治局', '政治局集体学习'],
        'tier': 'politburo',
    },
    # 党中央议事协调机构（顶层领导挂帅，信号强度等同政治局级）
    '中央金融委员会': {
        'patterns': ['中央金融委员会', '中央金融委', '中央金融工作会议'],
        'tier': 'central_commission',
    },
    '中央科技委员会': {
        'patterns': ['中央科技委员会', '中央科技委'],
        'tier': 'central_commission',
    },
    '中央深改委': {
        'patterns': ['中央全面深化改革委员会', '中央深改委', '深改委会议'],
        'tier': 'central_commission',
    },
    '中央财经委': {
        'patterns': ['中央财经委员会', '中央财经委'],
        'tier': 'central_commission',
    },
    '中央网信委': {
        'patterns': ['中央网络安全和信息化委员会', '中央网信委'],
        'tier': 'central_commission',
    },
    # 纪检监察（行业性反腐的最强信号，如 2023 医药反腐由纪委动员会启动）
    '中央纪委国家监委': {
        'patterns': ['中央纪委', '国家监委', '纪检监察机关'],
        'tier': 'discipline',
    },
    # 司法/执法
    '公安部': {
        'patterns': ['公安部', '公安机关', '警方'],
        'tier': 'judicial',
    },
    '最高人民检察院': {
        'patterns': ['最高检', '检察院', '检察机关'],
        'tier': 'judicial',
    },
    '最高人民法院': {
        'patterns': ['最高法', '人民法院'],
        'tier': 'judicial',
    },
}

# 加权协同度阈值：低权重的部委提及不足以触发高协同等级
# 例如第8版文章背景提到"人民法院"不应直接触发 L5
TIER_WEIGHT_THRESHOLDS = {
    'judicial': 3.0,     # 司法信号需要足够权重才算有效
    'discipline': 3.0,   # 纪检信号同司法标准
    'politburo': 2.0,    # 政治局信号阈值
    'central_commission': 2.0,
    'state_council': 2.0,
    'ministry': 1.0,     # 普通部委门槛最低
}

# ── 联合发文显式检测 ─────────────────────────────────────
# "等十部门联合印发"是协同度最直接的证据，强于任何共现统计
CN_NUM = {
    '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
}

JOINT_PATTERNS = [
    re.compile(
        r'等?\s*([0-9]+|十[一二三四五六七八九]?|[一二两三四五六七八九]十?[一二三四五六七八九]?)'
        r'\s*(?:个)?(?:部门|部委|单位)(?:联合|共同)?'
        r'(?:印发|发布|出台|发出|部署|开展|启动|发文|制定)'
    ),
    re.compile(r'(?:联合|会同)[^。；\n]{0,30}?(?:印发|发布|出台|开展|部署|约谈|执法|检查)'),
    re.compile(r'部际联席会议|联合工作机制|联合惩戒'),
]


def _parse_cn_number(token: str) -> int:
    if token.isdigit():
        return int(token)
    if token.startswith('十'):
        return 10 + CN_NUM.get(token[1:], 0) if len(token) > 1 else 10
    if len(token) >= 2 and token[1] == '十':
        tens = CN_NUM.get(token[0], 0) * 10
        return tens + (CN_NUM.get(token[2], 0) if len(token) > 2 else 0)
    return CN_NUM.get(token, 0)


def detect_joint_issuance(articles: list[dict]) -> dict:
    """
    检测"等N部门联合印发"类显式联合发文证据。

    返回：
      {found, max_departments, evidence: [{snippet, departments, title, page_no}]}
    """
    evidence = []
    max_departments = 0

    for a in articles:
        text = a.get('title', '') + '\n' + a.get('content', '')
        for pattern in JOINT_PATTERNS:
            for m in pattern.finditer(text):
                groups = m.groups()
                departments = _parse_cn_number(groups[0]) if groups and groups[0] else 2
                max_departments = max(max_departments, departments)
                start = max(m.start() - 20, 0)
                evidence.append({
                    'snippet': text[start:m.end() + 10].replace('\n', ' ').strip(),
                    'departments': departments,
                    'title': a.get('title', ''),
                    'page_no': a.get('page_no', 0),
                })

    evidence.sort(key=lambda x: -x['departments'])
    return {
        'found': bool(evidence),
        'max_departments': max_departments,
        'evidence': evidence[:8],
    }


def detect_ministries(articles: list[dict]) -> dict:
    """
    从文章集合中检测部委信号和协同等级（加权版）。

    参数：
      articles: 文章列表，每篇需含 title, content, page_no, column 字段

    返回：
      {
        coordination_level: str (L0-L5),
        coordination_label: str,
        ministry_count: int,
        ministries_found: [str],
        ministry_details: {部委名: {tier, total_score, hit_count, in_title}},
        tier_breakdown: {ministry: int, state_council: int, ...},
        has_state_council: bool,
        has_politburo: bool,
        has_judicial: bool,
        time_compression: float,
        high_signal_hits: [{ministry, title, page_no, score}],  # 高权重部委信号
      }
    """
    ministry_data = {}
    high_signal_hits = []

    for ministry, config in MINISTRY_PATTERNS.items():
        for a in articles:
            score, hit = weighted_pattern_match(a, config['patterns'])
            if hit:
                if ministry not in ministry_data:
                    ministry_data[ministry] = {
                        'tier': config['tier'],
                        'total_score': 0.0,
                        'hit_count': 0,
                        'in_title': False,
                    }
                ministry_data[ministry]['total_score'] += score
                ministry_data[ministry]['hit_count'] += 1

                # 标题命中标记
                title = a.get('title', '')
                if any(pat in title for pat in config['patterns']):
                    ministry_data[ministry]['in_title'] = True

                # 收集高权重命中
                if score >= 4.0:
                    high_signal_hits.append({
                        'ministry': ministry,
                        'title': a.get('title', ''),
                        'page_no': a.get('page_no', 0),
                        'score': round(score, 1),
                    })

    # 按阈值过滤：低权重的高层级信号降级处理
    effective_tiers = {}
    for ministry, data in ministry_data.items():
        tier = data['tier']
        threshold = TIER_WEIGHT_THRESHOLDS.get(tier, 1.0)
        if data['total_score'] >= threshold:
            effective_tiers[ministry] = tier
        else:
            # 权重不足，降级为普通 ministry 信号
            effective_tiers[ministry] = 'ministry'

    tier_breakdown = {}
    for ministry, tier in effective_tiers.items():
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1

    has_judicial = tier_breakdown.get('judicial', 0) > 0
    has_discipline = tier_breakdown.get('discipline', 0) > 0
    has_politburo = (
        tier_breakdown.get('politburo', 0) > 0
        or tier_breakdown.get('central_commission', 0) > 0
    )
    has_state_council = tier_breakdown.get('state_council', 0) > 0
    ministry_count = len(ministry_data)

    joint = detect_joint_issuance(articles)

    if has_judicial or has_discipline:
        level, label, compression = 'L5', '司法/纪检入轨，窗口<=30天', 0.1
    elif has_politburo:
        level, label, compression = 'L4', '政治局/中央委员会级，最高优先级', 0.4
    elif has_state_council:
        level, label, compression = 'L3', '国务院级，执行意志确认', 0.6
    elif ministry_count >= 3 or (joint['found'] and joint['max_departments'] >= 3):
        level, label, compression = 'L2', '多部委协同，行动概率显著提升', 0.8
    elif ministry_count >= 1 or joint['found']:
        level, label, compression = 'L1', '单部委关注，预警信号', 1.0
    else:
        level, label, compression = 'L0', '未检测到部委信号', 1.0

    # 联合发文是确定性证据：N>=5 的联合行动直接压缩时间窗
    if joint['found'] and joint['max_departments'] >= 5 and level in ('L1', 'L2'):
        level, label, compression = (
            'L2',
            f"{joint['max_departments']}部门联合发文，跨部委协调已完成",
            0.7,
        )

    # 详细数据（含每个部委的加权得分）
    ministry_details = {
        m: {
            'tier': effective_tiers.get(m, data['tier']),
            'original_tier': data['tier'],
            'total_score': round(data['total_score'], 1),
            'hit_count': data['hit_count'],
            'in_title': data['in_title'],
        }
        for m, data in ministry_data.items()
    }

    high_signal_hits.sort(key=lambda x: -x['score'])

    return {
        'coordination_level': level,
        'coordination_label': label,
        'ministry_count': ministry_count,
        'ministries_found': list(ministry_data.keys()),
        'ministry_details': ministry_details,
        'tier_breakdown': tier_breakdown,
        'has_state_council': has_state_council,
        'has_politburo': has_politburo,
        'has_judicial': has_judicial,
        'has_discipline': has_discipline,
        'joint_issuance': joint,
        'time_compression': compression,
        'high_signal_hits': high_signal_hits[:10],
    }
