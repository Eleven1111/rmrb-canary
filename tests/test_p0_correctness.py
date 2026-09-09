"""
P0 正确性反例：无证据不得被包装成政策状态；集体学习不是执行信号。

两条都来自升级方案的最小复核样例，且在本仓库原有实现中同样存在：
  - 空文章列表 → 强度 1 级"研究探索" + 窗口"12个月+"
  - "中央政治局集体学习" → L4 执行信号
"""

from agent.store import db, ledger
from agent.tools.discourse_level import measure_intensity
from agent.tools.ministry_signals import detect_ministries
from agent.tools.policy_clock import calculate_risk_window
from conftest import make_article


class TestNoEvidenceIsNotAPolicyState:
    def test_empty_input_is_unknown_not_level_one(self):
        r = measure_intensity([])
        assert r['status'] == 'unknown'
        assert r['max_level'] is None
        assert r['weighted_max_level'] is None

    def test_empty_input_yields_no_action_window(self):
        assert measure_intensity([])['action_window'] is None

    def test_no_phrase_hit_is_also_unknown(self):
        r = measure_intensity([make_article(content='今年粮食产量稳步增长。')])
        assert r['status'] == 'unknown', '有文章但零命中，同样是"不知道"'

    def test_distribution_still_readable_when_unknown(self):
        """契约不变：下游仍可读 level_N，"无证据"由 status/max_level 表达。"""
        d = measure_intensity([])['distribution']
        assert d['level_4']['count'] == 0 and d['level_5']['count'] == 0

    def test_real_hit_still_reports_ok(self):
        """反向对照：有真实命中时必须照常定级，不能被 unknown 分支吃掉。"""
        r = measure_intensity([make_article(content='坚决遏制无序扩张。', page_no=1)])
        assert r['status'] == 'ok' and r['max_level'] == 5

    def test_no_evidence_yields_no_risk_window(self):
        w = calculate_risk_window(intensity_level=None, ministry_compression=1.0,
                                  clock_coefficient=1.0, narrative_speed_modifier=1.0)
        assert w['status'] == 'unknown'
        assert w['adjusted_window_months'] is None
        assert w['risk_emoji'] == '⚪'

    def test_normal_risk_window_unaffected(self):
        w = calculate_risk_window(intensity_level=5, ministry_compression=0.8,
                                  clock_coefficient=1.0, narrative_speed_modifier=1.0)
        assert w.get('status') != 'unknown'
        assert w['adjusted_window_months'] is not None

    def test_no_evidence_records_no_prediction(self):
        """无证据的预测落进台账，将来无论裁定 hit 还是 miss 都是噪声。"""
        result = {
            'intensity': measure_intensity([]),
            'risk_window': calculate_risk_window(None, 1.0, 1.0, 1.0),
            'rmrb': {'date': '20260908'}, 'keywords': ['x'],
        }
        assert ledger.record_prediction(db.save_analysis(result), result) is None


class TestCollectiveStudyIsNotExecution:
    STUDY = '中央政治局就人工智能产业发展举行第十次集体学习。'
    DECISION = '中央政治局会议审议人工智能产业发展有关事项。'

    def test_collective_study_not_l4(self):
        m = detect_ministries([make_article(title=self.STUDY, content=self.STUDY, page_no=1)])
        assert m['coordination_level'] == 'L3-前瞻'
        assert m['has_forward_signal'] is True
        assert m['has_politburo'] is False, '学习活动不得计入政治局决策层级'

    def test_decision_meeting_still_l4(self):
        """反向对照：真正的决策会议不能被一并压掉。"""
        m = detect_ministries([make_article(title=self.DECISION, content=self.DECISION,
                                            page_no=1)])
        assert m['coordination_level'] == 'L4'
        assert m['has_politburo'] is True

    def test_decision_meeting_that_also_held_study_stays_l4(self):
        text = '中央政治局召开会议，会议部署人工智能工作，并举行集体学习。'
        m = detect_ministries([make_article(title=text, content=text, page_no=1)])
        assert m['coordination_level'] == 'L4', '既决策又学习，仍是决策会议'
        assert m['has_forward_signal'] is True, '但学习属性也要记下来'

    def test_forward_signal_reported_even_when_not_escalated(self):
        m = detect_ministries([make_article(content=self.STUDY, page_no=8)])
        assert m['has_forward_signal'] is True, '低权重版面的前瞻信号不得整个消失'
