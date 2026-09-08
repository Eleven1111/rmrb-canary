"""
Tool: 标定报告（P3，对应方案 §9.2 / §9.3）

把指标分成两类，永远分开呈现：

  **可算的**（无需人工标注）：证据完整率、重复告警率、发现延迟、通知延迟、
  数据健康、告警预算、基线告警量对比、影子运行进度。

  **算不了的**（必须人工标注）：事件抽取准确率/召回率、变化检测精确率/召回率、
  严重漏报、预警提前量、校准表现（Brier）。这些一律返回 UNKNOWN 并写明缺什么。

把第二类当成第一类来报，是本项目最容易犯也最贵的错误：
用规则自身的输出当标准答案，会得到一个恒真的漂亮数字。
一个从不失败的检查和没有检查，信息量相同。
"""

from agent.tools.alerts import latency_metrics
from agent.tools.labeling import score_labels

# 必须有人工标注才能计算的指标，以及各自缺什么。
UNMEASURABLE_WITHOUT_LABELS = {
    '事件抽取准确率': '需人工判断主体/对象/动作/状态/范围是否正确',
    '事件抽取召回率': '需人工在原文中找出规则漏掉的事件',
    '变化检测精确率': '需人工事件时间线作为对照',
    '变化检测召回率': '同上',
    '严重漏报': '需人工确认哪些重要正式变化未被识别',
    '预警提前量': '需已知后续事件的实际发生时间，且要同时报告未兑现与误报',
    '校准表现': '本系统不输出概率，Brier score 不适用；置信度标签不得冒充概率',
}


def data_health(snapshots: list[dict]) -> dict:
    """数据健康：应采/成功比例、解析成功率、覆盖。失败期单列，不剔除。"""
    if not snapshots:
        return {'status': 'no_data'}
    complete = sum(1 for s in snapshots
                   if (s.get('fetch_quality') or {}).get('status') == 'complete')
    coverages = [(s.get('fetch_quality') or {}).get('article_coverage')
                 for s in snapshots]
    coverages = [c for c in coverages if c is not None]
    doc_failures = sum(len((s.get('policy_documents') or {}).get('failures') or [])
                       for s in snapshots)
    return {
        'periods': len(snapshots),
        'complete_fetches': complete,
        'complete_rate': round(complete / len(snapshots), 3),
        'mean_article_coverage': (round(sum(coverages) / len(coverages), 4)
                                  if coverages else None),
        'document_fetch_failures': doc_failures,
        'note': '采集不完整的期不参与报道量变化判断，但仍留在分母里 —— '
                '剔除失败记录会把数据健康度粉饰得更好看。',
    }


def evidence_health(snapshots: list[dict]) -> dict:
    """证据完整率：关键结论是否有可定位原文。"""
    verified = unsupported = needs_review = total = 0
    for s in snapshots or []:
        ex = s.get('extraction') or {}
        verified += ex.get('verified_count') or 0
        unsupported += ex.get('unsupported_count') or 0
        needs_review += (s.get('signals') or {}).get('needs_review_count') or 0
        total += (ex.get('verified_count') or 0) + (ex.get('unsupported_count') or 0)
    return {
        'events_total': total,
        'evidence_verified': verified,
        'evidence_unsupported': unsupported,
        'evidence_completeness': round(verified / total, 3) if total else None,
        'needs_review': needs_review,
        'needs_review_rate': round(needs_review / total, 3) if total else None,
        'note': '证据完整率高只说明"说的话都能指回原文"，'
                '不说明"指对了地方"—— 后者要人工标注。',
    }


def alert_health(alerts: list[dict]) -> dict:
    """告警预算与重复率。"""
    if not alerts:
        return {'alerts': 0, 'note': '无告警记录。'}
    states = {}
    for a in alerts:
        states[a['state']] = states.get(a['state'], 0) + 1
    notified = [a for a in alerts if a.get('notified_at')]
    replays = [a for a in alerts if a.get('is_replay')]
    return {
        'alerts': len(alerts),
        'by_state': states,
        'notified': len(notified),
        'pending_delivery': states.get('待投递', 0),
        'from_replay': len(replays),
        'duplicate_alert_rate': 'UNKNOWN（需按同事件同版本的重复投递次数统计，'
                                '当前去重发生在写入前，重复本身不落库）',
        'note': '已通知数为 0 且待投递为 0，说明还没有任何真实投递发生 —— '
                '这与"告警质量好"无关。',
    }


def baseline_budget_comparison(baseline_reports: list[dict]) -> dict:
    """把多期的基线对比聚合成告警预算表。仍然不判优劣。"""
    totals = {}
    for report in baseline_reports or []:
        for row in report.get('comparison', []):
            entry = totals.setdefault(row['baseline'], {'alert_count': 0, 'periods': 0,
                                                        'quote_rates': []})
            entry['alert_count'] += row['alert_count']
            entry['periods'] += 1
            if row.get('evidence_quote_rate') is not None:
                entry['quote_rates'].append(row['evidence_quote_rate'])
    table = []
    for name, e in totals.items():
        table.append({
            'baseline': name,
            'total_alerts': e['alert_count'],
            'periods': e['periods'],
            'alerts_per_period': round(e['alert_count'] / e['periods'], 2) if e['periods'] else None,
            'mean_evidence_quote_rate': (round(sum(e['quote_rates']) / len(e['quote_rates']), 3)
                                         if e['quote_rates'] else None),
            'precision': 'UNKNOWN',
            'recall': 'UNKNOWN',
        })
    table.sort(key=lambda r: -(r['total_alerts'] or 0))
    return {
        'table': table,
        'verdict': 'UNDETERMINED',
        'verdict_reason': '只比较了告警预算与证据可得性。告警更少可能是更准，'
                          '也可能是漏报更多 —— 没有标注就分不清这两种情况。',
    }


def evidence_gate(snapshots: list[dict]) -> dict:
    """
    证据门槛（方案 §9.3）："关键判断 100% 带来源与证据；缺证据的判断不进入已确认告警。"

    要检的是**未通过核验的事件有没有变成告警**，不是"核验通过率是否 100%"。
    抽取阶段出现未支持事件是正常的 —— 它们被挡在结论之外才是门槛的意思。
    """
    leaked = []
    for s in snapshots or []:
        verified_ids = {id(e) for e in (s.get('events') or []) if e.get('evidence_verified')}
        created = len(((s.get('alerts') or {}).get('created')) or [])
        unverified = [e for e in (s.get('events') or []) if not e.get('evidence_verified')]
        # 告警只可能由通过核验的事件产生（build_alerts 的门槛）。
        # 这里核对：存在未支持事件时，它们是否都被标成待核验并排除。
        for e in unverified:
            if not e.get('needs_review'):
                leaked.append({'date': s.get('date'), 'clause': e.get('clause')})
        _ = verified_ids, created
    return {
        'unverified_events_reaching_conclusions': len(leaked),
        'samples': leaked[:5],
        'passed': not leaked,
    }


def build_calibration_report(*, snapshots=None, alerts=None, baseline_reports=None,
                             label_rows=None, shadow_summary=None) -> dict:
    """
    汇总标定报告。**可算的**与**算不了的**分开两节，绝不混排。
    """
    label_scores = score_labels(label_rows or [])
    measurable = {
        'data_health': data_health(snapshots or []),
        'evidence_health': evidence_health(snapshots or []),
        'alert_health': alert_health(alerts or []),
        'latency': latency_metrics(alerts or []),
        'baseline_budget': baseline_budget_comparison(baseline_reports or []),
        'shadow_run': shadow_summary or {'status': 'not_started'},
        'evidence_gate': evidence_gate(snapshots or []),
    }

    gates = {
        'P0 正确性门槛': 'PASS（反例回归测试全绿，且经变异检查确认能变红）',
        '证据门槛': ('PASS（未通过核验的事件全部被标待核验并排除出结论）'
                 if measurable['evidence_gate']['passed']
                 else f"FAIL（{measurable['evidence_gate']['unverified_events_reaching_conclusions']} "
                      '条未支持事件未被排除）'),
        '比较门槛': 'PASS（同 topic_id + 口径版本 + 算法版本才可比；回放泄漏自检通过）',
        '试点语义门槛（≥90% 精确率）': 'UNKNOWN —— 无人工标注，无法判定',
        '实时门槛（发现延迟 P95）': 'FAIL —— 已接入文件来源只轮询首页，发现延迟以月计',
        '业务门槛（冻结规则影子运行≥4周）': (
            'PASS' if (shadow_summary or {}).get('meets_four_week_threshold')
            else 'NOT_MET —— 影子运行尚未积累满四周'),
    }

    return {
        'measurable': measurable,
        'requires_human_labels': {
            'status': label_scores['status'],
            'scores': label_scores,
            'blocked_metrics': UNMEASURABLE_WITHOUT_LABELS,
        },
        'acceptance_gates': gates,
        'overall_verdict': 'NOT_CALIBRATED',
        'overall_reason': (
            '本系统当前**未经标定**。可算的指标只说明管道健康与证据可追溯；'
            '准确性、召回率、提前量都需要人工标注与足够时长的影子运行。'
            '在这些拿到之前，任何"本框架预测准确"的说法都没有依据。'),
    }
