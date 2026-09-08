"""
Tool: 人工标注载体（P3，对应方案 §9.1 / §9.2）

精确率与召回率**只能**由人工标注得出。本模块做两件事：
  1. 从回放语料生成待标注清单（JSONL，每条带原文证据，标注者不必回原始网页）
  2. 读回标注后计算指标

在标注文件存在之前，所有准确性指标一律返回 UNKNOWN。
**绝不用规则自己的输出当作标准答案** —— 那是拿考卷当答案，
算出来的 100% 与没算一样（这正是制度里"探针恒真"那条戒律）。

标注规范（方案 §9.1）：
  - 争议样本双人标注并记录裁决。
  - 关联政策与转载归为同一事件，防止训练/测试泄漏。
  - 若由模型辅助标注，要警惕它凭训练知识知道历史结局；
    结论只能由当时可见的证据支持。
"""

import json
import os

# 标注者需要判断的字段。每一项都必须能只看证据就作答。
LABEL_SCHEMA = {
    'is_relevant': '这条事件是否真的与本主题相关？（true/false）',
    'subject_correct': '抽到的主体是否正确？（true/false/unclear）',
    'direction_correct': '抽到的方向是否正确？（true/false/unclear）',
    'is_new_action': '这是否为当期新增的官方动作（而非历史回顾/引述/否定）？',
    'should_alert': '这条是否值得生成告警？（true/false）',
    'notes': '自由说明，尤其是判 false 的理由',
}


def build_label_sheet(snapshots: list[dict], limit_per_snapshot: int = 50) -> list[dict]:
    """
    从回放语料生成待标注清单。

    每条带完整原文证据与规则的判断结果。**规则的判断只是候选，不是答案**，
    标注者需要独立判断，字段留空待填。
    """
    rows = []
    for snap in snapshots or []:
        for e in (snap.get('events') or [])[:limit_per_snapshot]:
            evidence = e.get('evidence') or {}
            rows.append({
                'label_id': f"{snap.get('topic_id')}|{snap.get('date')}|{len(rows)}",
                'topic_key': snap.get('topic_key'),
                'topic_version': snap.get('topic_version'),
                'date': snap.get('date'),
                'as_of': snap.get('as_of'),
                # ── 标注者看的东西 ──
                'quote': evidence.get('quote'),
                'source_title': evidence.get('article_title'),
                'source_layer': e.get('source_layer'),
                'doc_number': e.get('doc_number'),
                # ── 规则的候选判断（供参考，不是答案）──
                'rule_subject': e.get('subject'),
                'rule_direction': e.get('direction'),
                'rule_is_new_action': e.get('is_new_action'),
                'rule_needs_review': e.get('needs_review'),
                'rule_would_alert': None,
                # ── 待填 ──
                'labels': {k: None for k in LABEL_SCHEMA},
                'annotator': None,
                'adjudication': None,
            })
    return rows


def write_label_sheet(rows: list[dict], path: str) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    return {'path': path, 'rows': len(rows), 'schema': LABEL_SCHEMA,
            'note': '逐行填写 labels 字段后交给 score_labels() 计算指标。'}


def load_labels(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def score_labels(rows: list[dict]) -> dict:
    """
    用人工标注计算准确性指标。

    没有已标注行 → 全部返回 UNKNOWN。这不是失败，这是当前的真实状态：
    没标注就是不知道，写个数字上去才是失败。
    """
    labeled = [r for r in rows or []
               if (r.get('labels') or {}).get('is_relevant') is not None]
    if not labeled:
        return {
            'status': 'no_labels',
            'labeled_count': 0,
            'total_rows': len(rows or []),
            'event_precision': 'UNKNOWN',
            'event_recall': 'UNKNOWN',
            'alert_precision': 'UNKNOWN',
            'severe_misses': 'UNKNOWN',
            'note': '尚无人工标注。准确性指标不可计算，'
                    '**不得**用规则自身输出充当标准答案。',
        }

    relevant = [r for r in labeled if r['labels'].get('is_relevant') is True]
    subject_ok = [r for r in relevant if r['labels'].get('subject_correct') is True]
    direction_ok = [r for r in relevant if r['labels'].get('direction_correct') is True]

    should_alert = [r for r in labeled if r['labels'].get('should_alert') is True]
    rule_alerted = [r for r in labeled if r.get('rule_would_alert') is True]
    true_positive = [r for r in rule_alerted if r['labels'].get('should_alert') is True]
    missed = [r for r in should_alert if r.get('rule_would_alert') is not True]

    def rate(num, den):
        return round(len(num) / len(den), 3) if den else None

    return {
        'status': 'ok',
        'labeled_count': len(labeled),
        'total_rows': len(rows),
        'label_coverage': round(len(labeled) / len(rows), 3) if rows else None,
        'relevance_precision': rate(relevant, labeled),
        'subject_accuracy': rate(subject_ok, relevant),
        'direction_accuracy': rate(direction_ok, relevant),
        'alert_precision': rate(true_positive, rule_alerted),
        'alert_recall': rate(true_positive, should_alert),
        'severe_misses': [
            {'label_id': r['label_id'], 'quote': r.get('quote'),
             'notes': r['labels'].get('notes')} for r in missed
        ][:20],
        'confidence_caveat': (
            f'样本量 {len(labeled)} 条。样本少时这些比率的置信区间很宽，'
            '不要当作稳定的能力指标；方案 §9.3 的 90% 门槛需要足够样本与置信区间一起报告。'),
    }
