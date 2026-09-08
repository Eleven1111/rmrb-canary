"""
P3 标定层反例集

覆盖：双关注镜头、回放泄漏自检、影子运行不可改写、结果回填约束、
基线对比不越权下结论、无标注时指标必须是 UNKNOWN。

最重要的一条：**没有人工标注就不许出精确率数字**。
用规则自身输出当标准答案会得到恒真的漂亮结果，那与没测一样。
"""

import os
import shutil
import tempfile
import unittest


_TMP = None


def setUpModule():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix='rmrb-p3-test-')


def tearDownModule():
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP)


def event(**over):
    e = {'applies_to_topic': True, 'authority': 'official', 'evidence_verified': True,
         'tense': 'current', 'direction': '支持', 'policy_tools': [],
         'procedural_status': [], 'obligation_levels': [], 'scope_labels': [],
         'regions': [], 'has_exception': False, 'source_layer': 'report',
         'clause': '支持人工智能产业发展', 'object_terms': ['人工智能'],
         'evidence': {'quote': '支持人工智能产业发展', 'article_title': 'A'}}
    e.update(over)
    return e


class TestChairLens(unittest.TestCase):
    """双关注方向并列，不合成净值，也不互相填充。"""

    def test_support_event_is_opportunity(self):
        from agent.tools.signals import classify_lens
        self.assertIn('政策窗口与机会', classify_lens(event(direction='支持')))

    def test_obligation_event_is_compliance(self):
        from agent.tools.signals import classify_lens
        lenses = classify_lens(event(direction='规范', obligation_levels=['义务']))
        self.assertIn('监管合规', lenses)

    def test_event_can_belong_to_both(self):
        from agent.tools.signals import classify_lens
        lenses = classify_lens(event(direction='支持', policy_tools=['标准'],
                                     obligation_levels=['义务']))
        self.assertEqual(set(lenses), {'政策窗口与机会', '监管合规'},
                         '既给支持又提要求的事件，两个镜头都该看见')

    def test_neutral_event_belongs_to_neither(self):
        from agent.tools.signals import classify_lens
        self.assertEqual(classify_lens(event(direction='未明确')), [],
                         '不该为了填满版面把中性事件硬塞进某一边')

    def test_signals_expose_both_lenses_even_when_empty(self):
        from agent.tools.signals import build_signals
        signals = build_signals({'events': [event(direction='支持')]})
        lenses = signals['by_chair_lens']
        self.assertEqual(lenses['政策窗口与机会']['count'], 1)
        self.assertEqual(lenses['监管合规']['count'], 0,
                         '空的那一侧必须显式为 0，而不是消失')


class TestReplayLeakage(unittest.TestCase):
    """回放读到未来记录 = 整次回放作废。"""

    def _snap(self, **over):
        s = {'date': '20260903', 'as_of': '20260903',
             'rolling_trend': {'intensity_timeline': [{'date': '20260901'}]},
             'trend': {'previous_date': '20260902'},
             'coverage_change': {'recent_counts': [{'date': '20260901'}]}}
        s.update(over)
        return s

    def test_clean_replay_passes(self):
        from agent.replay import check_leakage
        self.assertTrue(check_leakage(self._snap())['clean'])

    def test_same_day_history_is_leakage(self):
        from agent.replay import check_leakage
        r = check_leakage(self._snap(trend={'previous_date': '20260903'}))
        self.assertFalse(r['clean'], '上一期不得等于 as_of 当天')

    def test_future_timeline_is_leakage(self):
        from agent.replay import check_leakage
        r = check_leakage(self._snap(
            rolling_trend={'intensity_timeline': [{'date': '20260910'}]}))
        self.assertFalse(r['clean'])

    def test_snapshot_date_after_as_of_is_leakage(self):
        from agent.replay import check_leakage
        self.assertFalse(check_leakage(self._snap(date='20260905'))['clean'])

    def test_date_range_expands_correctly(self):
        from agent.replay import date_range
        self.assertEqual(date_range('20260901', '20260903'),
                         ['20260901', '20260902', '20260903'])

    def test_reversed_range_rejected(self):
        from agent.replay import date_range
        with self.assertRaises(ValueError):
            date_range('20260903', '20260901')


# 影子运行台账未另起炉灶：改为强化远端 agent/store/ledger.py 的不可改写保证，
# 用例见 tests/test_ledger_immutability.py。此处不再重复实现一套。


class TestBaselines(unittest.TestCase):
    """四条基线在同一份快照上比较，但不越权判优劣。"""

    SNAP = {
        'topic_id': 'topic:ai', 'topic_label': '人工智能', 'date': '20260908',
        'as_of': '20260908',
        'articles': [{'title': 'A'}, {'title': 'B'}],
        'intensity': {'status': 'ok', 'max_level': 6, 'max_level_evidence': [{'quote': 'q'}]},
        'policy_documents': {'documents': [
            {'doc_id': 'd1', 'title': 'D', 'doc_number': '国发〔2026〕1号',
             'content': '正文', 'published_at': '20260801'}]},
        'events': [event(policy_tools=['标准'], obligation_levels=['义务'])],
    }

    def test_all_four_baselines_run(self):
        from agent.tools.baselines import compare_baselines
        r = compare_baselines(self.SNAP)
        names = {row['baseline'] for row in r['comparison']}
        self.assertEqual(len(names), 4)
        self.assertIn('七级规则（旧版）', names)
        self.assertIn('仅正式文件监测', names)

    def test_precision_and_recall_are_unknown(self):
        from agent.tools.baselines import compare_baselines
        for row in compare_baselines(self.SNAP)['comparison']:
            self.assertIn('UNKNOWN', row['precision'])
            self.assertIn('UNKNOWN', row['recall'])

    def test_verdict_is_undetermined(self):
        from agent.tools.baselines import compare_baselines
        r = compare_baselines(self.SNAP)
        self.assertEqual(r['verdict'], 'UNDETERMINED')
        self.assertIn('不得宣称新方案优于任何基线', r['verdict_reason'])

    def test_legacy_rule_silent_below_threshold(self):
        from agent.tools.baselines import baseline_seven_level
        snap = {**self.SNAP, 'intensity': {'status': 'ok', 'max_level': 2}}
        self.assertEqual(baseline_seven_level(snap)['count'], 0)

    def test_new_scheme_dedupes_by_fingerprint(self):
        from agent.tools.baselines import baseline_new_scheme
        e = event(policy_tools=['标准'], obligation_levels=['义务'])
        snap = {**self.SNAP, 'events': [e, dict(e), dict(e)]}
        r = baseline_new_scheme(snap)
        self.assertEqual(r['pre_dedupe_count'], 3)
        self.assertEqual(r['count'], 1, '同一事件多处出现只算一条')


class TestLabelingAndCalibration(unittest.TestCase):
    """没有标注就不许出准确性数字。"""

    def test_no_labels_yields_unknown(self):
        from agent.tools.labeling import score_labels
        r = score_labels([])
        self.assertEqual(r['status'], 'no_labels')
        self.assertEqual(r['event_precision'], 'UNKNOWN')
        self.assertIn('不得', r['note'])

    def test_unfilled_sheet_is_not_labels(self):
        from agent.tools.labeling import build_label_sheet, score_labels
        rows = build_label_sheet([{'topic_id': 't', 'date': '20260901',
                                   'events': [event()]}])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['labels']['is_relevant'])
        self.assertEqual(score_labels(rows)['status'], 'no_labels',
                         '生成了清单不等于有了标注')

    def test_label_sheet_carries_evidence_not_just_verdict(self):
        from agent.tools.labeling import build_label_sheet
        rows = build_label_sheet([{'topic_id': 't', 'date': '20260901',
                                   'events': [event()]}])
        self.assertTrue(rows[0]['quote'], '标注者必须能只看证据作答')
        self.assertIn('rule_direction', rows[0])

    def test_filled_labels_produce_scores(self):
        from agent.tools.labeling import build_label_sheet, score_labels
        rows = build_label_sheet([{'topic_id': 't', 'date': '20260901',
                                   'events': [event()]}])
        rows[0]['labels'].update({'is_relevant': True, 'subject_correct': True,
                                  'direction_correct': True, 'should_alert': True})
        rows[0]['rule_would_alert'] = True
        r = score_labels(rows)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['alert_precision'], 1.0)
        self.assertIn('置信区间', r['confidence_caveat'])

    def test_missed_alerts_are_listed(self):
        from agent.tools.labeling import build_label_sheet, score_labels
        rows = build_label_sheet([{'topic_id': 't', 'date': '20260901',
                                   'events': [event()]}])
        rows[0]['labels'].update({'is_relevant': True, 'should_alert': True})
        rows[0]['rule_would_alert'] = False
        r = score_labels(rows)
        self.assertEqual(r['alert_recall'], 0.0)
        self.assertEqual(len(r['severe_misses']), 1)

    def test_calibration_keeps_two_sections_separate(self):
        from agent.tools.calibration_report import build_calibration_report
        r = build_calibration_report(snapshots=[], alerts=[], baseline_reports=[])
        self.assertIn('measurable', r)
        self.assertIn('requires_human_labels', r)
        self.assertEqual(r['overall_verdict'], 'NOT_CALIBRATED')

    def test_evidence_gate_checks_exclusion_not_perfection(self):
        """证据门槛问的是"未支持事件有没有溜进结论"，不是"核验通过率是否100%"。"""
        from agent.tools.calibration_report import evidence_gate
        ok = evidence_gate([{'date': '20260901', 'events': [
            {'evidence_verified': False, 'needs_review': True, 'clause': 'x'},
            {'evidence_verified': True, 'needs_review': False, 'clause': 'y'}]}])
        self.assertTrue(ok['passed'], '未支持事件被标待核验排除 = 门槛通过')

    def test_evidence_gate_fails_when_unverified_slips_through(self):
        from agent.tools.calibration_report import evidence_gate
        bad = evidence_gate([{'date': '20260901', 'events': [
            {'evidence_verified': False, 'needs_review': False, 'clause': 'x'}]}])
        self.assertFalse(bad['passed'])
        self.assertEqual(bad['unverified_events_reaching_conclusions'], 1)

    def test_semantic_gate_is_unknown_without_labels(self):
        from agent.tools.calibration_report import build_calibration_report
        gates = build_calibration_report()['acceptance_gates']
        self.assertIn('UNKNOWN', gates['试点语义门槛（≥90% 精确率）'])

    def test_four_week_gate_not_met_by_default(self):
        from agent.tools.calibration_report import build_calibration_report
        gates = build_calibration_report()['acceptance_gates']
        self.assertIn('NOT_MET', gates['业务门槛（冻结规则影子运行≥4周）'])

    def test_baseline_budget_does_not_declare_a_winner(self):
        from agent.tools.calibration_report import baseline_budget_comparison
        r = baseline_budget_comparison([{'comparison': [
            {'baseline': 'A', 'alert_count': 1, 'evidence_quote_rate': 1.0},
            {'baseline': 'B', 'alert_count': 9, 'evidence_quote_rate': 0.1}]}])
        self.assertEqual(r['verdict'], 'UNDETERMINED')
        self.assertTrue(all(row['precision'] == 'UNKNOWN' for row in r['table']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
