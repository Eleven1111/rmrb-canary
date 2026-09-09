"""
Tool: 基线对比（P3，对应方案 §9.3）

方案要求："比较基线至少包括：当前七级规则、简单关键词订阅、仅正式文件监测、新方案。
新方案必须在相近告警预算下改善精确率、召回率或发现速度。
增加语义模型、复杂统计检测器前，先证明相对简单基线有增益。"

本模块在**同一份快照**上跑四条基线，输出可比较的告警集合。

必须先说清楚的事：
  没有人工标注，就**算不出**精确率与召回率。本模块只输出告警量、
  证据可得性、重复率、来源构成这些无需标注即可计算的量。
  精确率/召回率一律标 UNKNOWN，直到 labeling 模块收到标注为止。
  "新方案告警更少"本身不是优势 —— 少而准才是，而"准"需要标注来证明。
"""

from agent.tools.alerts import classify_event, event_fingerprint

# 旧版七级规则的告警阈值：强度达到 5 级即报警。
LEGACY_INTENSITY_THRESHOLD = 5


def baseline_keyword_subscription(snapshot: dict) -> dict:
    """
    基线 1：简单关键词订阅。命中关键词的每篇报道各推一条。
    这是最朴素的做法，也是新方案必须超过的下限。
    """
    items = [{
        'id': f"kw:{a.get('title', '')[:40]}",
        'title': a.get('title'),
        'source_layer': 'report',
        'has_evidence_quote': False,
        'has_doc_number': False,
    } for a in snapshot.get('articles', [])]
    return {
        'name': '简单关键词订阅',
        'items': items,
        'count': len(items),
        'note': '每篇命中文章一条。不区分对象、否定、历史回顾，也不去重转载。',
    }


def baseline_seven_level(snapshot: dict) -> dict:
    """
    基线 2：当前（旧版）七级规则。词语强度达到阈值就整体报一条。
    这是升级前的行为，用来回答"新方案比原来强在哪"。
    """
    intensity = snapshot.get('intensity') or {}
    level = intensity.get('max_level')
    fired = intensity.get('status') == 'ok' and level is not None \
        and level >= LEGACY_INTENSITY_THRESHOLD
    items = []
    if fired:
        items.append({
            'id': f"lvl:{snapshot.get('topic_id')}:{snapshot.get('date')}",
            'title': f'词语强度达到 {level} 级',
            'source_layer': 'report',
            'has_evidence_quote': bool(intensity.get('max_level_evidence')),
            'has_doc_number': False,
        })
    return {
        'name': '七级规则（旧版）',
        'items': items,
        'count': len(items),
        'threshold': LEGACY_INTENSITY_THRESHOLD,
        'observed_level': level,
        'note': '整期一条，不定位到具体事件与对象；强度未达阈值则完全沉默。',
    }


def baseline_official_documents_only(snapshot: dict) -> dict:
    """
    基线 3：只监测正式文件。命中主题的每份文件各推一条。
    这是"只要事实锚点、不要报道"的极简方案。
    """
    docs = (snapshot.get('policy_documents') or {}).get('documents', [])
    items = [{
        'id': f"doc:{d.get('doc_id')}",
        'title': d.get('title'),
        'source_layer': 'official_document',
        'has_evidence_quote': bool(d.get('content')),
        'has_doc_number': bool(d.get('doc_number')),
        'published_at': d.get('published_at'),
    } for d in docs]
    return {
        'name': '仅正式文件监测',
        'items': items,
        'count': len(items),
        'note': '每份命中文件一条。证据力最强，但完全看不到尚未成文的议程变化，'
                '且受已接入来源覆盖限制。',
    }


def baseline_new_scheme(snapshot: dict) -> dict:
    """基线 4：新方案。通过证据核验、够格生成告警的事件。"""
    items = []
    for e in snapshot.get('events') or []:
        if not (e.get('evidence_verified') and e.get('applies_to_topic')
                and e.get('authority') == 'official' and e.get('tense') != 'historical'):
            continue
        if not classify_event(e):
            continue
        items.append({
            'id': f"ev:{event_fingerprint(snapshot.get('topic_id'), e)}",
            'title': (e.get('clause') or '')[:60],
            'source_layer': e.get('source_layer'),
            'has_evidence_quote': bool((e.get('evidence') or {}).get('quote')),
            'has_doc_number': bool(e.get('doc_number')),
            'needs_review': e.get('needs_review'),
            'published_at': e.get('published_at'),
        })
    # 事件指纹去重：同一事件在多处出现只算一条。
    unique = {i['id']: i for i in items}
    return {
        'name': '新方案（事件 + 证据核验）',
        'items': list(unique.values()),
        'count': len(unique),
        'pre_dedupe_count': len(items),
        'note': '只保留通过原文核验、对象落在主题上、官方主体、非历史回顾的事件，'
                '并按事件指纹去重。',
    }


ALL_BASELINES = (baseline_keyword_subscription, baseline_seven_level,
                 baseline_official_documents_only, baseline_new_scheme)


def compare_baselines(snapshot: dict) -> dict:
    """
    在同一份快照上跑全部基线并对比。

    输出的是**告警预算与证据可得性**的对比，不是效果对比。
    精确率与召回率需要人工标注，见 agent.tools.labeling。
    """
    results = [fn(snapshot) for fn in ALL_BASELINES]
    table = []
    for r in results:
        items = r['items']
        with_quote = sum(1 for i in items if i.get('has_evidence_quote'))
        with_doc = sum(1 for i in items if i.get('has_doc_number'))
        table.append({
            'baseline': r['name'],
            'alert_count': r['count'],
            'evidence_quote_rate': round(with_quote / len(items), 3) if items else None,
            'doc_number_rate': round(with_doc / len(items), 3) if items else None,
            'source_layers': sorted({i.get('source_layer') for i in items if i.get('source_layer')}),
            'precision': 'UNKNOWN（需人工标注）',
            'recall': 'UNKNOWN（需人工标注）',
            'note': r['note'],
        })

    return {
        'topic_id': snapshot.get('topic_id'),
        'topic_label': snapshot.get('topic_label'),
        'date': snapshot.get('date'),
        'as_of': snapshot.get('as_of'),
        'comparison': table,
        'detail': {r['name']: r for r in results},
        'verdict': 'UNDETERMINED',
        'verdict_reason': (
            '没有人工标注，无法判定哪条基线更准。告警量差异只说明预算不同，'
            '不说明质量高低 —— 告警更少既可能是更准，也可能是漏报更多。'
            '在拿到标注之前，不得宣称新方案优于任何基线。'),
    }
