"""验收报告中的阻断反例：这些测试守住生产入口与推论边界。"""

import sys

from agent.tools.change_detect import detect_changes
from agent.tools.evidence_check import verify_events
from agent.tools.event_extract import extract_events
from agent.tools.scoping import build_target_segments
from agent.tools.signals import build_signals


def _events(text):
    article = {'title': '原始文章', 'content': text, 'page_no': 1}
    scope = build_target_segments([article], ['光伏'])
    return verify_events(extract_events(scope, ['光伏'])['events'], [article])


def test_expert_recommendation_is_not_an_official_action():
    events = _events('专家建议国务院支持光伏并给予补贴。')
    assert events['events'][0]['authority'] == 'quoted_opinion'
    assert build_signals(events)['status'] == 'unknown'


def test_forward_enforcement_is_not_execution_evidence():
    signals = build_signals(_events('市场监管总局将开展光伏整治专项行动。'))
    assert signals['dimensions']['执行证据']['components']['执行记录']['present'] is False


def test_forged_document_title_or_offset_fails_evidence_check():
    article = {'title': '真正标题', 'content': '财政部支持光伏补贴。'}
    event = {
        'clause': '财政部支持光伏补贴', 'subject': '财政部',
        'tool_terms': ['补贴'], 'object_terms': ['光伏'],
        'evidence': {'article_title': '虚构标题', 'quote': '财政部支持光伏补贴。',
                     'char_start': 9999, 'char_end': 10000},
    }
    result = verify_events([event], [article])
    assert result['unsupported_count'] == 1


def _snapshot(amount, subject, regions):
    event = {
        'evidence_verified': True, 'needs_review': False, 'applies_to_topic': True,
        'authority': 'official', 'tense': 'current', 'amounts': [amount],
        'subject': subject, 'evidence': {'article_title': '文件', 'quote': amount},
    }
    return {
        'events': [event],
        'signals': {'status': 'ok', 'dimensions': {
            '政策方向': {'by_direction': {'支持': {'count': 1, 'evidence': []}}, 'coexists': False},
            '约束与动作': {'counts': {'责任': 1}},
            '适用范围': {'counts': {}, 'regions': regions, 'exceptions': []},
            '政策工具': {'counts': {'财政': 1}},
            '程序状态': {'counts': {'正式发布': 1}},
            '执行证据': {'components': {}, 'components_present': '0/4'},
        }},
    }


def test_change_detection_preserves_old_and_new_evidence_for_material_changes():
    result = detect_changes(_snapshot('100亿元', '商务部', ['北京']),
                            _snapshot('10亿元', '财政部', ['北京', '上海']))
    changed = {row['question']: row for row in result['changes']}
    assert changed['资源变了吗']['changed'] is True
    assert changed['责任变了吗']['changed'] is True
    assert changed['范围变了吗']['changed'] is True
    assert changed['资源变了吗']['old_evidence']
    assert changed['资源变了吗']['new_evidence']


def test_cli_accepts_documented_topic_and_dry_run(monkeypatch, capsys):
    import agent.agent as module
    seen = {}
    monkeypatch.setattr(module, 'run_pipeline', lambda **kwargs: seen.update(kwargs) or {})
    monkeypatch.setattr(sys, 'argv', ['agent', '--topic', 'ai', '--dry-run'])
    module.main()
    assert seen['topic_key'] == 'ai' and seen['dry_run'] is True


def test_replay_alert_does_not_consume_live_dedupe_key(monkeypatch):
    from agent.tools import alerts

    records = []
    monkeypatch.setattr(alerts.alertstore, 'upsert_alert',
                        lambda record: records.append(record) or {'action': 'created', 'alert_id': 1, 'state': '观察'})
    event = {
        'evidence_verified': True, 'needs_review': False, 'applies_to_topic': True,
        'authority': 'official', 'tense': 'current', 'source_layer': 'official_document',
        'subject': '国务院', 'direction': '支持', 'policy_tools': [], 'object_terms': ['人工智能'],
        'procedural_status': [], 'scope_labels': [], 'obligation_levels': [], 'regions': [],
        'evidence': {},
    }
    alerts.build_alerts({'topic_id': 'ai', 'as_of': '20200101', 'events': [event]}, today='20260101')
    alerts.build_alerts({'topic_id': 'ai', 'as_of': '20260101', 'events': [event]}, today='20260101')
    assert records[0]['dedupe_key'].startswith('replay|')
    assert records[1]['dedupe_key'] == records[0]['dedupe_key'].removeprefix('replay|')


def test_historical_issue_cache_miss_does_not_fetch_today(monkeypatch):
    from agent.tools import fetch_rmrb

    class EmptyCache:
        def get_issue(self, date, as_of=None):
            assert as_of == '20200101'
            return None

    monkeypatch.setattr(fetch_rmrb.rmrb_fetch, 'fetch',
                        lambda **_kwargs: (_ for _ in ()).throw(AssertionError('must not fetch')))
    result = fetch_rmrb.fetch_rmrb(['人工智能'], date='20200101', cache=EmptyCache(), as_of='20200101')
    assert result['fetch_quality']['status'] == 'failed'
