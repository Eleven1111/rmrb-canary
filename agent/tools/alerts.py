"""
Tool: 事件告警生成（P2，对应方案 §7.2）

告警从"分数越线"转向"事件变化"。旧思路是强度到几级就报警；
新思路是只有**可核验的事件变化**才值得打扰人。

优先提醒（§7.2）：
  正式文件新发或修订 / 适用范围扩大 / 明确期限临近 / 执行责任形成 /
  地方落实 / 可信的方向反转。

软信号（词频、版面升温）需要连续观察或独立证据支撑，本模块默认不生成告警；
但**正式文件中的明确新义务不因为"要等连续两期"而延迟**。

去重键 = 主题 + 事件指纹（**不含**变化版本），与文章标题无关。
变化版本是判断"重复"还是"更新"的依据，因此不能进身份键 ——
若把它算进键里，每次实质变化都会变成一条全新告警，
状态机就跟不住同一个事件的生命周期了。

于是：同键同版本 = 转载或重跑，不重复通知；
同键新版本 = 责任/范围/期限有实质变化，退回待核验重走确认。

历史补采与离线回放（as_of 早于今天）默认**不触发实时通知**，
只落库为观察态，避免把回放当成实时事件推送出去。
"""

import datetime
import hashlib
import json

from agent.store import alertstore

# 优先级：这些是"值得打断人"的变化类型。其余一律留在观察态。
PRIORITY_RULES = {
    'official_document_new': ('high', '已接入来源出现与本主题相关的正式文件'),
    'official_document_revised': ('high', '正式文件出现修订或废止表述'),
    'obligation_formed': ('high', '出现明确义务或责任条款'),
    'deadline_present': ('high', '出现原文明确期限'),
    'scope_expanded': ('medium', '适用范围扩大或新增地域'),
    'execution_evidence': ('medium', '出现执行责任、资源安排或实施记录'),
    'direction_reversal': ('medium', '方向发生可信反转'),
}


def event_fingerprint(topic_id: str, event: dict) -> str:
    """
    事件指纹：同一件事在不同报道、不同抓取批次里应得到同一个指纹。

    只用语义构件（主体 / 方向 / 工具 / 对象 / 程序状态 / 范围），
    **不含**文章标题、URL、抓取时间 —— 那些一变就会造成重复告警。
    """
    parts = [
        topic_id,
        str(event.get('subject') or ''),
        str(event.get('direction') or ''),
        ','.join(sorted(event.get('policy_tools') or [])),
        ','.join(sorted(event.get('object_terms') or [])),
        ','.join(sorted(event.get('procedural_status') or [])),
        ','.join(sorted(event.get('scope_labels') or [])),
    ]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:16]


def change_version(event: dict) -> str:
    """
    关键变化版本：只有责任、范围、期限这类**实质**内容变化才换版本。

    措辞润色、版面变动、转载来源增加都不换版本，因此不会重复通知。
    """
    parts = [
        ','.join(sorted(event.get('obligation_levels') or [])),
        ','.join(sorted(event.get('scope_labels') or [])),
        ','.join(sorted(event.get('regions') or [])),
        str(event.get('has_exception')),
        str(event.get('doc_number') or ''),
        str(event.get('effective_at') or ''),
        str(event.get('doc_status') or ''),
    ]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:12]


def classify_event(event: dict, deadlines: list[dict] = None) -> tuple[str, str, str] | None:
    """判断一个事件是否够格生成告警。不够格返回 None（留在观察态即可）。"""
    is_doc = event.get('source_layer') == 'official_document'
    obligations = set(event.get('obligation_levels') or [])
    scopes = set(event.get('scope_labels') or [])

    if is_doc and event.get('doc_status') in ('废止', '修订'):
        kind = 'official_document_revised'
    elif is_doc:
        kind = 'official_document_new'
    elif obligations & {'义务', '责任'}:
        kind = 'obligation_formed'
    elif event.get('effective_at') or (deadlines and event.get('applies_to_topic')):
        kind = 'deadline_present'
    elif scopes & {'试点', '全国'} or event.get('regions'):
        kind = 'scope_expanded'
    elif '执法' in (event.get('policy_tools') or []) and not event.get('negated'):
        kind = 'execution_evidence'
    else:
        return None

    priority, reason = PRIORITY_RULES[kind]
    return kind, priority, reason


def build_alerts(snapshot: dict, deadlines: list[dict] = None,
                 today: str = None) -> dict:
    """
    从快照生成告警候选并落库。

    门槛（缺一不可）：
      - 事件通过证据核验（`evidence_verified`）
      - 对象落在本主题上（`applies_to_topic`）
      - 官方主体（`authority == 'official'`）
      - 非历史回顾
    未通过的事件不生成告警 —— 待核验的东西不该去打扰人。

    回放判定：`as_of` 早于今天即视为历史补采，落库为观察态且不进入投递队列。
    """
    events = snapshot.get('events') or []
    topic_id = snapshot.get('topic_id')
    today = today or datetime.datetime.now().strftime('%Y%m%d')
    as_of = snapshot.get('as_of') or today
    is_replay = as_of < today

    created, updated, duplicates, skipped = [], [], [], 0

    for e in events:
        if not (e.get('evidence_verified') and e.get('applies_to_topic')
                and e.get('authority') == 'official' and e.get('tense') != 'historical'):
            skipped += 1
            continue

        classified = classify_event(e, deadlines)
        if not classified:
            skipped += 1
            continue
        kind, priority, reason = classified

        fp = event_fingerprint(topic_id, e)
        cv = change_version(e)
        record = {
            'dedupe_key': f'{topic_id}|{fp}',
            'topic_id': topic_id,
            'topic_version': snapshot.get('topic_version'),
            'event_fingerprint': fp,
            'change_version': cv,
            'state': '观察',
            'priority': priority,
            'reason': f'{reason}（{kind}）',
            'published_at': (e.get('published_at') or snapshot.get('date')),
            'first_seen_at': datetime.datetime.now().isoformat(),
            'is_replay': is_replay,
            'payload': {
                'kind': kind,
                'topic_label': snapshot.get('topic_label'),
                'clause': e.get('clause'),
                'subject': e.get('subject'),
                'direction': e.get('direction'),
                'policy_tools': e.get('policy_tools'),
                'source_layer': e.get('source_layer'),
                'evidence': e.get('evidence'),
                'needs_review': e.get('needs_review'),
                'review_reasons': e.get('review_reasons'),
            },
        }
        result = alertstore.upsert_alert(record)
        if result['action'] == 'created':
            created.append({**result, 'kind': kind, 'priority': priority})
        elif result['action'] == 'updated':
            updated.append({**result, 'kind': kind, 'priority': priority})
        else:
            duplicates.append(result)

    return {
        'status': 'ok',
        'created': created,
        'updated': updated,
        'duplicate_suppressed': len(duplicates),
        'skipped_events': skipped,
        'is_replay': is_replay,
        'delivery_blocked': is_replay,
        'note': ('历史补采/回放：告警只落库为观察态，不进入投递队列，'
                 '不触发实时通知。' if is_replay else
                 '新建告警处于观察态；须经待核验 → 已确认 → 待投递，'
                 '且收到回执后才算已通知。'),
    }


def render_alert(alert_row: dict) -> dict:
    """
    渲染一条提醒。§7.2 要求每次提醒至少包含：
    变化一句话、原始来源、原文证据、相较上次差异、对象与地域、
    确定程度、建议动作、下一个观察节点。
    """
    payload = alert_row.get('payload_json')
    payload = json.loads(payload) if isinstance(payload, str) else (payload or {})
    evidence = payload.get('evidence') or {}
    return {
        'one_line': f"{payload.get('topic_label')}：{payload.get('clause', '')[:60]}",
        'source': evidence.get('article_title'),
        'quote': evidence.get('quote'),
        'source_layer': payload.get('source_layer'),
        'change_since_last': ('本告警为新建，尚无上一版本'
                              if alert_row.get('state') == '观察'
                              else '关键变化版本已更新，详见状态流转记录'),
        'object_and_region': {
            'subject': payload.get('subject'),
            'direction': payload.get('direction'),
        },
        'certainty': ('待核验' if payload.get('needs_review') else '证据已核验'),
        'certainty_reasons': payload.get('review_reasons') or [],
        'suggested_action': '回原文核对适用对象与生效条件，再决定是否升级处理',
        'next_checkpoint': '该主题下一期采集，或相关正式文件发布',
        'state': alert_row.get('state'),
    }


def latency_metrics(alerts: list[dict]) -> dict:
    """
    §9.2 的延迟指标。

    发现延迟 = first_seen_at − published_at（发布时间只到日，故按日计，不冒充分钟精度）
    通知延迟 = notified_at − first_seen_at
    两者分开：来源延迟不该记到系统头上。
    """
    discovery, notify = [], []
    for a in alerts:
        pub, seen, notified = a.get('published_at'), a.get('first_seen_at'), a.get('notified_at')
        if pub and seen:
            try:
                seen_day = datetime.datetime.fromisoformat(seen).strftime('%Y%m%d')
                d = (datetime.datetime.strptime(seen_day, '%Y%m%d')
                     - datetime.datetime.strptime(pub, '%Y%m%d')).days
                discovery.append(d)
            except (ValueError, TypeError):
                pass
        if seen and notified:
            try:
                delta = (datetime.datetime.fromisoformat(notified)
                         - datetime.datetime.fromisoformat(seen)).total_seconds() / 60
                notify.append(round(delta, 1))
            except (ValueError, TypeError):
                pass

    def summarize(values, unit):
        if not values:
            return {'count': 0, 'median': None, 'p95': None, 'unit': unit}
        s = sorted(values)
        return {'count': len(s), 'median': s[len(s) // 2],
                'p95': s[min(len(s) - 1, int(len(s) * 0.95))], 'unit': unit}

    return {
        'discovery_latency': summarize(discovery, 'days'),
        'notify_latency': summarize(notify, 'minutes'),
        'note': '发布时间仅精确到日，发现延迟按日计算，不报告分钟精度。'
                '来源不可用时段应单列，不得通过剔除失败记录美化数据。',
    }
