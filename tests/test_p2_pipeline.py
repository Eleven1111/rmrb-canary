"""
P2 与正式文件层的反例集

覆盖：共享文档缓存与修订检出、告警去重与状态机、发送回执、
历史回放不触发通知、延迟指标、政策文件解析与覆盖边界。

运行（在 skill 安装根目录）：
    python3 -m unittest discover -s tests -t . -v
"""

import os
import shutil
import sys
import tempfile
import unittest

from agent.store import doccache as cache_module
from agent.store import alertstore

from agent.sources import gov_cn_pages as policy_fetch  # noqa: E402

_TMP = None


def setUpModule():
    """所有缓存与告警库指向临时目录，测试绝不写用户真实数据。"""
    global _TMP
    _TMP = tempfile.mkdtemp(prefix='rmrb-p2-test-')
    cache_module.CACHE_DIR = _TMP
    cache_module.CACHE_PATH = os.path.join(_TMP, 'doccache.db')
    alertstore.DB_DIR = _TMP
    alertstore.DB_PATH = os.path.join(_TMP, 'alerts.db')


def tearDownModule():
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP)


def doc(content='正文内容', published='20260901'):
    return {'title': '某某办法', 'content': content, 'published_at': published,
            'content_hash': None}


class TestDocumentCache(unittest.TestCase):
    """§7.1：整期共享缓存 —— 两个主题分析同一期不应各抓一遍。"""

    def test_second_read_is_a_cache_hit(self):
        c = cache_module.DocumentCache()
        c.put_document('https://example.gov/doc1', doc())
        self.assertIsNone(cache_module.DocumentCache().get_document('https://example.gov/none'))
        got = c.get_document('https://example.gov/doc1')
        self.assertTrue(got['from_cache'])
        self.assertEqual(c.stats['hits'], 1)

    def test_issue_shared_across_topics(self):
        c = cache_module.DocumentCache()
        c.put_issue('20260908', {'date': '20260908', 'total_articles': 125})
        again = c.get_issue('20260908')
        self.assertEqual(again['total_articles'], 125)
        self.assertEqual(c.stats['hits'], 1, '同一期第二次读取必须命中缓存，不重复抓取')

    def test_unchanged_content_does_not_create_version(self):
        c = cache_module.DocumentCache()
        url = 'https://example.gov/doc2'
        first = c.put_document(url, doc('内容A'))
        second = c.put_document(url, doc('内容A'))
        self.assertEqual(first['action'], 'inserted')
        self.assertEqual(second['action'], 'unchanged')
        self.assertEqual(second['version'], 1)

    def test_changed_content_creates_revision(self):
        c = cache_module.DocumentCache()
        url = 'https://example.gov/doc3'
        c.put_document(url, doc('内容A'))
        result = c.put_document(url, doc('内容B'))
        self.assertEqual(result['action'], 'revised')
        self.assertEqual(result['version'], 2)
        self.assertEqual(len(c.revisions_since(url, 1)), 1, '修订必须可被检出')

    def test_first_seen_at_preserved_across_refetch(self):
        """§8.2：今天补采一份旧文件，不等于系统当年就发现了它。"""
        c = cache_module.DocumentCache()
        url = 'https://example.gov/doc4'
        c.put_document(url, doc('内容A'))
        original = c.get_document(url)['first_seen_at']
        c.put_document(url, doc('内容B'))
        self.assertEqual(c.get_document(url)['first_seen_at'], original,
                         '首次观察时间不得被后续抓取覆盖')

    def test_disabled_cache_never_returns_hits(self):
        c = cache_module.DocumentCache(enabled=False)
        c.put_document('https://example.gov/doc5', doc())
        self.assertIsNone(c.get_document('https://example.gov/doc5'))


class TestAlertDedupe(unittest.TestCase):
    """§7.2：去重键是主题 + 事件 + 变化版本，不是文章标题。"""

    def _event(self, **over):
        e = {
            'source_layer': 'report', 'evidence_verified': True, 'applies_to_topic': True,
            'authority': 'official', 'tense': 'current', 'subject': '教育部',
            'direction': '规范', 'policy_tools': ['标准'], 'object_terms': ['人工智能'],
            'procedural_status': ['正式发布'], 'scope_labels': ['全国'],
            'obligation_levels': ['义务'], 'regions': [], 'has_exception': False,
            'clause': '教育部发布人工智能标准', 'evidence': {'article_title': 'A',
                                                  'quote': '教育部发布人工智能标准'},
        }
        e.update(over)
        return e

    def _snapshot(self, events, **over):
        s = {'topic_id': 'topic:ai', 'topic_version': 'v1', 'topic_label': '人工智能',
             'date': '20260908', 'as_of': '20260908', 'events': events}
        s.update(over)
        return s

    def test_fingerprint_ignores_article_title(self):
        from agent.tools.alerts import event_fingerprint
        a = self._event(evidence={'article_title': '甲报道', 'quote': 'x'})
        b = self._event(evidence={'article_title': '乙转载', 'quote': 'y'})
        self.assertEqual(event_fingerprint('topic:ai', a), event_fingerprint('topic:ai', b),
                         '同一事件在不同标题下必须是同一个指纹')

    def test_reprint_does_not_notify_twice(self):
        from agent.tools.alerts import build_alerts
        first = build_alerts(self._snapshot([self._event()]), today='20260908')
        second = build_alerts(self._snapshot([self._event(clause='转载版本')]),
                              today='20260908')
        self.assertEqual(len(first['created']), 1)
        self.assertEqual(len(second['created']), 0)
        self.assertEqual(second['duplicate_suppressed'], 1)

    def test_material_change_creates_update(self):
        from agent.tools.alerts import build_alerts
        build_alerts(self._snapshot([self._event(scope_labels=['试点'])]), today='20260908')
        result = build_alerts(
            self._snapshot([self._event(scope_labels=['试点'], regions=['广东'])]),
            today='20260908')
        self.assertEqual(len(result['updated']), 1, '范围变化属实质变化，应产生更新')
        self.assertEqual(result['updated'][0]['state'], '待核验',
                         '更新后必须退回待核验重走确认流程')

    def test_unverified_event_generates_no_alert(self):
        from agent.tools.alerts import build_alerts
        r = build_alerts(self._snapshot([self._event(evidence_verified=False)]),
                         today='20260908')
        self.assertEqual(len(r['created']), 0)
        self.assertEqual(r['skipped_events'], 1)

    def test_historical_event_generates_no_alert(self):
        from agent.tools.alerts import build_alerts
        r = build_alerts(self._snapshot([self._event(tense='historical')]), today='20260908')
        self.assertEqual(len(r['created']), 0)

    def test_replay_does_not_enter_delivery(self):
        """§8.2：历史补采与离线回放默认不触发实时业务通知。"""
        from agent.tools.alerts import build_alerts
        r = build_alerts(self._snapshot([self._event(subject='科技部')], as_of='20260101'),
                         today='20260908')
        self.assertTrue(r['is_replay'])
        self.assertTrue(r['delivery_blocked'])


class TestAlertStateMachine(unittest.TestCase):
    """观察 → 待核验 → 已确认 → 待投递 → 已通知 → 跟踪 → 关闭/撤回"""

    def _new_alert(self, key):
        return alertstore.upsert_alert({
            'dedupe_key': key, 'topic_id': 'topic:t', 'topic_version': 'v1',
            'event_fingerprint': 'fp', 'change_version': 'cv', 'state': '观察',
            'payload': {}, 'published_at': '20260901',
        })

    def test_legal_path(self):
        a = self._new_alert('k-legal')
        for state in ('待核验', '已确认', '待投递'):
            self.assertTrue(alertstore.transition(a['alert_id'], state)['ok'], state)
        r = alertstore.record_receipt(a['alert_id'], 'receipt-1')
        self.assertTrue(r['ok'])
        self.assertTrue(r['notified_at'])

    def test_illegal_transition_rejected(self):
        a = self._new_alert('k-illegal')
        r = alertstore.transition(a['alert_id'], '已通知')
        self.assertFalse(r['ok'], '不得从观察态直接跳到已通知')

    def test_closed_is_terminal(self):
        a = self._new_alert('k-closed')
        alertstore.transition(a['alert_id'], '关闭')
        self.assertFalse(alertstore.transition(a['alert_id'], '已确认')['ok'])

    def test_delivery_attempt_is_not_delivery(self):
        """发送失败保留待投递状态；没有回执就不是已通知。"""
        a = self._new_alert('k-attempt')
        for state in ('待核验', '已确认', '待投递'):
            alertstore.transition(a['alert_id'], state)
        r = alertstore.mark_delivery_attempt(a['alert_id'], error='SMTP timeout')
        self.assertEqual(r['state'], '待投递')
        self.assertEqual(r['delivery_attempts'], 1)
        pending = [p for p in alertstore.pending_deliveries() if p['id'] == a['alert_id']]
        self.assertEqual(len(pending), 1, '投递失败必须留在待投递队列')

    def test_receipt_required_before_notified(self):
        a = self._new_alert('k-receipt')
        for state in ('待核验', '已确认', '待投递'):
            alertstore.transition(a['alert_id'], state)
        alertstore.mark_delivery_attempt(a['alert_id'])
        rows = [r for r in alertstore.list_alerts() if r['id'] == a['alert_id']]
        self.assertIsNone(rows[0]['notified_at'], '未收回执时 notified_at 必须为空')
        alertstore.record_receipt(a['alert_id'], 'r-2')
        rows = [r for r in alertstore.list_alerts() if r['id'] == a['alert_id']]
        self.assertIsNotNone(rows[0]['notified_at'])

    def test_transitions_are_logged(self):
        a = self._new_alert('k-log')
        alertstore.transition(a['alert_id'], '待核验', note='测试')
        log = alertstore.transitions_of(a['alert_id'])
        self.assertGreaterEqual(len(log), 2)
        self.assertEqual(log[-1]['to_state'], '待核验')


class TestLatencyMetrics(unittest.TestCase):
    def test_discovery_latency_in_days_only(self):
        from agent.tools.alerts import latency_metrics
        m = latency_metrics([{'published_at': '20260901',
                              'first_seen_at': '2026-09-04T10:00:00'}])
        self.assertEqual(m['discovery_latency']['median'], 3)
        self.assertEqual(m['discovery_latency']['unit'], 'days')

    def test_notify_latency_separate_from_discovery(self):
        from agent.tools.alerts import latency_metrics
        m = latency_metrics([{'published_at': '20260901',
                              'first_seen_at': '2026-09-04T10:00:00',
                              'notified_at': '2026-09-04T10:30:00'}])
        self.assertEqual(m['notify_latency']['median'], 30.0)
        self.assertEqual(m['notify_latency']['unit'], 'minutes')

    def test_missing_data_yields_none_not_zero(self):
        from agent.tools.alerts import latency_metrics
        m = latency_metrics([{'published_at': None, 'first_seen_at': None}])
        self.assertIsNone(m['discovery_latency']['median'])
        self.assertEqual(m['discovery_latency']['count'], 0)


class TestPolicyDocumentParsing(unittest.TestCase):
    """正式文件解析：字段照抄原文，抽不到就是 None。"""

    HTML = """<html><body>
    <table><tr><td>索 引 号：</td><td>000014349/2026-00066</td></tr>
    <tr><td>发文机关：</td><td>国务院</td></tr>
    <tr><td>成文日期：</td><td>2026年08月30日</td></tr>
    <tr><td>标　题：</td><td>某某条例</td></tr>
    <tr><td>发文字号：</td><td>国令第845号</td></tr>
    <tr><td>发布日期：</td><td>2026年09月04日</td></tr></table>
    <div id="UCAP-CONTENT"><p>本条例自2027年1月1日起施行。</p></div>
    </body></html>"""

    def _parse(self, html=None):
        return policy_fetch.parse_document(html or self.HTML,
                                           'https://www.gov.cn/zhengce/content/x.htm',
                                           {'key': 'gov_cn_zhengce', 'system': '国务院'})

    def test_extracts_required_evidence_fields(self):
        d = self._parse()
        self.assertEqual(d['issuing_agency'], '国务院')
        self.assertEqual(d['doc_number'], '国令第845号')
        self.assertEqual(d['published_at'], '20260904')
        self.assertEqual(d['drafted_at'], '20260830')
        self.assertEqual(d['effective_at'], '20270101')
        self.assertEqual(d['authority_tier'], 'official_document')
        self.assertTrue(d['parse_complete'])

    def test_missing_fields_are_none_not_guessed(self):
        d = self._parse('<html><body><div id="UCAP-CONTENT">正文</div></body></html>')
        self.assertIsNone(d['doc_number'])
        self.assertIsNone(d['published_at'])
        self.assertIsNone(d['effective_at'])
        self.assertFalse(d['parse_complete'])

    def test_repeal_status_detected(self):
        html = self.HTML.replace('本条例自2027年1月1日起施行。',
                                 '某某规定同时废止。')
        self.assertEqual(self._parse(html)['doc_status'], '废止')

    def test_content_hash_stable_and_change_sensitive(self):
        a = self._parse()
        b = self._parse(self.HTML.replace('2027年1月1日', '2028年1月1日'))
        self.assertEqual(a['content_hash'], self._parse()['content_hash'])
        self.assertNotEqual(a['content_hash'], b['content_hash'])


class TestDocumentLevelVerification(unittest.TestCase):
    """文号是文件级元数据，不是分句里的字。核验层级搞错会把正确元数据判成编造。"""

    DOC = {'title': '某某规划的通知', 'content': '深入实施“人工智能+”行动。',
           'doc_number': '国发〔2026〕26号', 'issuing_agency': '国务院',
           'published_at': '20260723', 'effective_at': None, 'doc_status': 'in_force'}

    def _event(self, **over):
        e = {'clause': '深入实施“人工智能+”行动', 'object_terms': ['人工智能'],
             'doc_number': '国发〔2026〕26号', 'issuing_agency': '国务院',
             'subject': '国务院', 'subject_source': 'document_metadata',
             'evidence': {'article_title': '某某规划的通知',
                          'quote': '深入实施“人工智能+”行动。'}}
        e.update(over)
        return e

    def test_doc_number_verified_against_document_not_quote(self):
        from agent.tools.evidence_check import verify_events
        r = verify_events([self._event()], [self.DOC], documents=[self.DOC])
        self.assertEqual(r['verified_count'], 1,
                         '文号与解析到的文件一致即为有据，不要求出现在分句引文里')

    def test_fabricated_doc_number_still_rejected(self):
        from agent.tools.evidence_check import verify_events
        r = verify_events([self._event(doc_number='国发〔2026〕99号')],
                          [self.DOC], documents=[self.DOC])
        self.assertEqual(r['unsupported_count'], 1, '与文件不一致的文号必须被拒')

    def test_doc_number_without_source_document_rejected(self):
        from agent.tools.evidence_check import verify_events
        r = verify_events([self._event()], [self.DOC], documents=None)
        self.assertEqual(r['unsupported_count'], 1, '没有来源文件就不许写文号')

    def test_metadata_subject_must_match_issuing_agency(self):
        from agent.tools.evidence_check import verify_events
        r = verify_events([self._event(subject='教育部')], [self.DOC], documents=[self.DOC])
        self.assertEqual(r['unsupported_count'], 1,
                         '标称来自文件元数据的主体，必须等于该文件的发文机关')

    def test_clause_level_claims_still_need_the_quote(self):
        from agent.tools.evidence_check import verify_events
        r = verify_events([self._event(object_terms=['疫苗'])], [self.DOC],
                          documents=[self.DOC])
        self.assertEqual(r['unsupported_count'], 1,
                         '分句级主张仍必须出现在自己的引文里')


class TestDocumentDefaultSubject(unittest.TestCase):
    """政策文件正文大量无主语句，主体是发文机关 —— 文件自带元数据，不是推断。"""

    def test_subjectless_clause_takes_issuing_agency(self):
        from agent.tools.scoping import build_target_segments
        from agent.tools.event_extract import extract_events
        arts = [{'title': '某规划', 'content': '深入实施“人工智能+”行动。',
                 'page_no': 0, 'column': '正式文件'}]
        scope = build_target_segments(arts, ['人工智能'])
        r = extract_events(scope, ['人工智能'], source_layer='official_document',
                           default_subjects={'某规划': '国务院'})
        ev = [e for e in r['events'] if '人工智能' in e['clause']]
        self.assertTrue(ev)
        self.assertEqual(ev[0]['subject'], '国务院')
        self.assertEqual(ev[0]['subject_source'], 'document_metadata')
        self.assertEqual(ev[0]['authority'], 'official')
        self.assertIn('主体取自文件发文机关（无主语句），需确认该条款的实际执行主体',
                      ev[0]['review_reasons'])

    def test_explicit_subject_wins_over_metadata(self):
        from agent.tools.scoping import build_target_segments
        from agent.tools.event_extract import extract_events
        arts = [{'title': '某规划', 'content': '教育部推进人工智能标准建设。',
                 'page_no': 0, 'column': '正式文件'}]
        scope = build_target_segments(arts, ['人工智能'])
        r = extract_events(scope, ['人工智能'], source_layer='official_document',
                           default_subjects={'某规划': '国务院'})
        self.assertEqual(r['events'][0]['subject'], '教育部')
        self.assertEqual(r['events'][0]['subject_source'], 'clause')

    def test_reports_do_not_get_default_subjects(self):
        from agent.tools.scoping import build_target_segments
        from agent.tools.event_extract import extract_events
        arts = [{'title': '报道', 'content': '深入实施“人工智能+”行动。', 'page_no': 1}]
        scope = build_target_segments(arts, ['人工智能'])
        r = extract_events(scope, ['人工智能'], source_layer='report')
        self.assertEqual(r['events'][0]['authority'], 'unknown',
                         '报道没有发文机关可继承，主体仍应是未识别')


class TestSourceCoverageHonesty(unittest.TestCase):
    """取不到的来源必须显式列出；"没检索到"不得说成"没出台"。"""

    def test_blocked_sources_are_recorded_not_hidden(self):
        sources = policy_fetch.load_sources()
        blocked = [s for s in sources if s.get('status') != 'enabled']
        self.assertTrue(blocked, '被拒来源必须留在配置里，而不是删掉假装不存在')
        for s in blocked:
            self.assertTrue(s.get('probe_result') or s.get('coverage_note'),
                            f'{s["key"]} 缺少探测结果说明')

    def test_key_agency_sources_marked_unavailable(self):
        by_key = {s['key']: s for s in policy_fetch.load_sources()}
        for key in ('cac', 'nhc', 'nmpa'):
            self.assertNotEqual(by_key[key]['status'], 'enabled',
                                f'{key} 实测不可达，不能标成已启用')

    def test_enabled_sources_have_parseable_contract(self):
        for s in policy_fetch.enabled_sources():
            self.assertIn('list_url', s)
            self.assertIn('link_pattern', s)


class TestSourceLayerSeparation(unittest.TestCase):
    """正式文件与报道的证据力不同，计数必须分开。"""

    def test_signals_separate_document_and_report_events(self):
        from agent.tools.signals import build_signals
        base = {'applies_to_topic': True, 'authority': 'official', 'evidence_verified': True,
                'tense': 'current', 'direction': '规范', 'policy_tools': ['标准'],
                'procedural_status': ['正式发布'], 'obligation_levels': ['义务'],
                'scope_labels': [], 'regions': [], 'has_exception': False,
                'evidence': {'quote': 'q', 'article_title': 't'}}
        signals = build_signals({'events': [
            {**base, 'source_layer': 'official_document'},
            {**base, 'source_layer': 'report'},
        ]})
        self.assertEqual(signals['by_source_layer']['official_document'], 1)
        self.assertEqual(signals['by_source_layer']['report'], 1)
        self.assertTrue(signals['has_official_document_evidence'])

    def test_report_only_states_the_limit(self):
        from agent.tools.signals import build_signals
        signals = build_signals({'events': [{
            'source_layer': 'report', 'applies_to_topic': True, 'authority': 'official',
            'evidence_verified': True, 'tense': 'current', 'direction': '支持',
            'policy_tools': [], 'procedural_status': [], 'obligation_levels': [],
            'scope_labels': [], 'regions': [], 'has_exception': False,
            'evidence': {'quote': 'q', 'article_title': 't'}}]})
        self.assertFalse(signals['has_official_document_evidence'])
        self.assertIn('不等于"没有出台文件"', signals['source_layer_note'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
