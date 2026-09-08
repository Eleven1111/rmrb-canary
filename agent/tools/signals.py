"""
Tool: 多维信号汇总（P1，对应方案 §6.1）

七级单轴不足以描述政策：支持与监管可以同时增强，不能共用一根从宽松到打击的标尺。
本模块把事件汇总成七个并列维度，每个维度独立呈现、各带证据。

进入维度汇总的事件必须同时满足：
  - `applies_to_topic`：对象落在本主题上
  - `authority == 'official'`：不是被引述观点或市场主体的行为
  - `evidence_verified`：通过原文核验

历史回顾事件单独统计，不混进"本期状态"。needs_review 事件也单独列出，
数量本身就是一个该被看见的信号 —— 待核验越多，结论越不该说死。
"""

from collections import Counter

DIMENSIONS = ('政策方向', '政策工具', '程序状态', '约束与动作',
              '适用范围', '执行证据', '传播变化')

# 执行证据的四个可观察构件（§6.1）。缺哪个就是缺哪个，不推断。
EXECUTION_COMPONENTS = {
    '责任主体': lambda e: '责任' in e.get('obligation_levels', []),
    '细则或标准': lambda e: '标准' in e.get('policy_tools', []),
    '资源安排': lambda e: any(t in e.get('policy_tools', []) for t in ('财政', '税收', '信贷', '采购')),
    '执行记录': lambda e: ('执法' in e.get('policy_tools', []) and not e.get('negated')),
}


# 董事长双关注方向（chair_focus）。两个镜头**并列**，同一事件可能只落进一个，
# 也可能两个都不落 —— 不许为了填满版面把事件硬塞进某一边。
OPPORTUNITY_TOOLS = {'财政', '税收', '信贷', '采购', '试点'}
RISK_TOOLS = {'执法', '准入', '标准', '监测'}


def classify_lens(event: dict) -> list[str]:
    """把一个事件归入关注镜头。可能属于两个（既给支持又提要求），也可能都不属于。"""
    lenses = []
    direction = event.get('direction')
    tools = set(event.get('policy_tools') or [])
    obligations = set(event.get('obligation_levels') or [])

    if direction == '支持' or (tools & OPPORTUNITY_TOOLS):
        lenses.append('政策窗口与机会')
    if (direction in ('限制', '规范') or (tools & RISK_TOOLS)
            or (obligations & {'义务', '责任', '期限'})):
        lenses.append('监管合规')
    return lenses


def _qualified(events):
    return [e for e in events
            if e.get('applies_to_topic')
            and e.get('authority') == 'official'
            and e.get('evidence_verified')]


def _evidence_sample(events, limit=3):
    return [{'clause': e.get('clause'), 'quote': (e.get('evidence') or {}).get('quote'),
             'article_title': (e.get('evidence') or {}).get('article_title'),
             'page_no': (e.get('evidence') or {}).get('page_no')}
            for e in events[:limit]]


def _unknown(reason: str) -> dict:
    return {
        'status': 'unknown',
        'reason': reason,
        'dimensions': {d: {'status': 'unknown', 'value': None} for d in DIMENSIONS},
        'note': '无合格事件：不输出任何维度取值。',
    }


def build_signals(extraction: dict, agenda: dict = None,
                  dedupe: dict = None, narrative: dict = None) -> dict:
    """
    汇总多维信号。

    参数：
      extraction: evidence_check.verify_events() 的返回值（已含核验结果）
      agenda / dedupe / narrative: 供"传播变化"维度使用的版面、归并与框架信息
    """
    events = (extraction or {}).get('events') or []
    if not events:
        return _unknown('本期没有抽出任何事件')

    qualified = _qualified(events)
    historical = [e for e in qualified if e.get('tense') == 'historical']
    current = [e for e in qualified if e.get('tense') != 'historical']
    review = [e for e in events if e.get('needs_review')]

    if not current:
        base = _unknown('有事件，但没有一个同时满足：对象落在主题上、官方主体、'
                        '通过原文核验、且非历史回顾')
        base['historical_only'] = len(historical)
        base['needs_review_count'] = len(review)
        return base

    # ① 政策方向：必须绑定对象，分方向各自计数，不合并成一个净值。
    dir_counter = Counter(e['direction'] for e in current)
    by_direction = {
        d: {'count': c,
            'evidence': _evidence_sample([e for e in current if e['direction'] == d])}
        for d, c in dir_counter.items()
    }
    supportive = dir_counter.get('支持', 0)
    restrictive = dir_counter.get('限制', 0) + dir_counter.get('规范', 0)

    # ② 政策工具
    tool_counter = Counter(t for e in current for t in e.get('policy_tools', []))

    # ③ 程序状态：允许并行，不排序成阶段。
    status_counter = Counter(s for e in current for s in e.get('procedural_status', []))

    # ④ 约束与动作
    obligation_counter = Counter(o for e in current for o in e.get('obligation_levels', []))

    # ⑤ 适用范围
    scope_counter = Counter(s for e in current for s in e.get('scope_labels', []))
    regions = sorted({r for e in current for r in e.get('regions', [])})
    exceptions = [e for e in current if e.get('has_exception')]

    # ⑥ 执行证据：四个构件分别判定，缺失就写缺失。
    execution = {}
    for name, test in EXECUTION_COMPONENTS.items():
        hits = [e for e in current if test(e)]
        execution[name] = {'present': bool(hits), 'count': len(hits),
                           'evidence': _evidence_sample(hits, 2)}
    execution_present = sum(1 for v in execution.values() if v['present'])

    # ⑦ 传播变化
    agenda = agenda or {}
    dedupe = dedupe or {}
    propagation = {
        'front_page_count': agenda.get('front_page_count'),
        'authority_column_hits': len(agenda.get('authority_column_hits') or []),
        'original_sources': dedupe.get('original_count'),
        'reprint_count': dedupe.get('reprint_count'),
        'primary_frame': (narrative or {}).get('primary_frame'),
        'note': '转载数量属传播强度，不是多个独立决策证据。',
    }

    doc_events = [e for e in current if e.get('source_layer') == 'official_document']
    report_events = [e for e in current if e.get('source_layer') != 'official_document']

    return {
        'status': 'ok',
        'reason': None,
        'qualified_events': len(current),
        # 正式文件与报道的证据力不同，分开计数，绝不合并成一个"事件数"。
        'by_source_layer': {
            'official_document': len(doc_events),
            'report': len(report_events),
        },
        'has_official_document_evidence': bool(doc_events),
        # 双镜头：风险与机会各自独立成组，各带证据，不合成净值。
        'by_chair_lens': {
            lens: {
                'count': sum(1 for e in current if lens in classify_lens(e)),
                'evidence': _evidence_sample(
                    [e for e in current if lens in classify_lens(e)]),
            }
            for lens in ('监管合规', '政策窗口与机会')
        },
        'lens_note': ('两个关注方向并列呈现。某方向计数为 0 时写"本期该方向无新增证据"，'
                      '不得用另一方向的内容填充，也不得把两者揉成一段"机遇与挑战"。'),
        'source_layer_note': (
            '有正式文件证据：可以谈"正式规定了什么"。' if doc_events
            else '本期只有报道证据，没有正式文件。只能谈"议程如何呈现"，'
                 '不能断言正式规定的内容 —— 且已接入的文件来源覆盖有限，'
                 '"没有文件"不等于"没有出台文件"。'),
        'historical_events': len(historical),
        'needs_review_count': len(review),
        'evidence_completeness': (extraction or {}).get('evidence_completeness'),
        'dimensions': {
            '政策方向': {
                'status': 'ok',
                'by_direction': by_direction,
                'coexists': supportive > 0 and restrictive > 0,
                'note': ('本期同时存在支持与限制/规范方向的事件，必须分对象呈现，'
                         '不得合成单一方向。' if supportive > 0 and restrictive > 0
                         else '各方向事件见 by_direction，每条都绑定具体对象。'),
            },
            '政策工具': {'status': 'ok', 'counts': dict(tool_counter),
                     'evidence': _evidence_sample(current)},
            '程序状态': {'status': 'ok', 'counts': dict(status_counter),
                     'note': '程序状态允许跳转与并行，不代表线性阶段。'},
            '约束与动作': {'status': 'ok', 'counts': dict(obligation_counter),
                      'note': '看的是义务、责任、期限是否出现，不只看措辞强弱。'},
            '适用范围': {'status': 'ok', 'counts': dict(scope_counter),
                     'regions': regions,
                     'exceptions': _evidence_sample(exceptions, 3)},
            '执行证据': {'status': 'ok', 'components': execution,
                     'components_present': f'{execution_present}/4',
                     'note': '四个构件缺一即为执行链不完整，不得据此推断已落地。'},
            '传播变化': {'status': 'ok', **propagation},
        },
        'note': '七个维度并列，不合成总分。词语强度（七级）作为兼容特征另行呈现，'
                '不参与本汇总。',
    }
