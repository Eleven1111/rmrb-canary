"""
证据不足期（强度 = None）不得让下游崩溃，也不得被当成 0 级。

背景：F04 修复后，measure_intensity 在证据不足时返回 max_level=None
（"没证据"不许落成"1 级研究探索"）。这个 None 会一路流到两个地方，
而两处都没跟上，274 个测试全绿却挡不住 —— 因为测试数据里从来没有过 None 强度：

  rolling_trend   sum(intensities) 直接 TypeError，整条管道在第 11 步中断
  locate_stage    intensity >= 4 直接 TypeError，传导链定位被 try 吞掉、整体降级

修法的关键不是"补个默认值"。补 0 或补 1 会把"不知道强度"读成"强度极低"，
等于把 F04 刚拆掉的结论从后门放回来。正确语义是：
  - 强度统计**排除**这些期，并报出排除了几期
  - 以强度为条件的判定一律不成立（两个方向都不成立）
"""

import pytest

from agent.store import db
from agent.tools.silence_detector import rolling_trend
from agent.tools.transmission_chain import locate_stage
from test_store_db import make_pipeline_result


def seed(date, level, articles=3):
    """level=None 模拟证据不足期：强度两列都入库为 NULL。"""
    result = make_pipeline_result(date=date, weighted_level=level)
    result['intensity']['max_level'] = level
    result['rmrb']['total_articles'] = articles
    return db.save_analysis_detailed(result)


class TestRollingTrendUnrated:
    def test_unrated_period_does_not_crash(self):
        # 合并前：TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'
        seed('20260601', 4); seed('20260602', None); seed('20260603', 5)
        trend = rolling_trend(['光伏'], as_of='20260604')
        assert trend['data_points'] == 3

    def test_unrated_period_excluded_not_zeroed(self):
        seed('20260601', 4); seed('20260602', None); seed('20260603', 4)
        w = rolling_trend(['光伏'], as_of='20260604', windows=[30])['windows']['30d']
        assert w['count'] == 3          # 窗口内 3 期
        assert w['graded_count'] == 2   # 但只有 2 期有强度结论
        assert w['unrated_count'] == 1
        # 补 0 的话均值是 (4+0+4)/3 = 2.7；排除的话是 4.0。
        assert w['avg_intensity'] == 4.0

    def test_zeroing_would_have_faked_a_downtrend(self):
        # 这是"补 0"最危险的后果：一期缺证据就足以把持平读成下降。
        for d, lv in (('20260601', 5), ('20260602', 5), ('20260603', 5), ('20260604', None)):
            seed(d, lv)
        w = rolling_trend(['光伏'], as_of='20260605', windows=[30])['windows']['30d']
        assert w['trend_direction'] == '持平'

    def test_all_unrated_reports_insufficient_not_zero(self):
        seed('20260601', None); seed('20260602', None)
        w = rolling_trend(['光伏'], as_of='20260603', windows=[30])['windows']['30d']
        assert w['avg_intensity'] is None
        assert w['trend_direction'] == '强度证据不足'
        assert w['unrated_count'] == 2

    def test_comparison_window_dates_come_from_graded_periods(self):
        # mid 是按有强度结论的期数分的半；用 ordered 索引会标出
        # 一段并没有参与计算的日期区间。
        seed('20260601', None); seed('20260602', 2)
        seed('20260603', None); seed('20260604', 6)
        cmp_ = rolling_trend(['光伏'], as_of='20260605',
                             windows=[30])['windows']['30d']['comparison']
        assert cmp_['earlier_window'] == '20260602–20260602'
        assert cmp_['later_window'] == '20260604–20260604'
        assert cmp_['unrated_excluded'] == 2

    def test_fully_graded_window_is_unaffected(self):
        # 负向对照：没有缺证据的期时，行为与修复前完全一致。
        for d, lv in (('20260601', 2), ('20260602', 2), ('20260603', 6), ('20260604', 6)):
            seed(d, lv)
        w = rolling_trend(['光伏'], as_of='20260605', windows=[30])['windows']['30d']
        assert w['unrated_count'] == 0
        assert w['avg_intensity'] == 4.0
        assert w['trend_direction'] == '上升'


class TestLocateStageUnknownIntensity:
    BASE = {'article_count': 2, 'coordination_level': 'L2',
            'has_judicial': False, 'has_discipline': False,
            'has_politburo': False, 'joint_found': False}

    def test_unknown_intensity_does_not_crash(self):
        # 合并前：TypeError: '>=' not supported between NoneType and int
        out = locate_stage({**self.BASE, 'intensity_level': None}, {})
        assert out['intensity_known'] is False
        assert out['stage']

    def test_missing_article_count_does_not_crash(self):
        out = locate_stage({'intensity_level': 3}, {})
        assert out['layers']['amplifier']['count'] == 0

    def test_none_article_count_does_not_crash(self):
        out = locate_stage({**self.BASE, 'article_count': None,
                            'intensity_level': 3}, {})
        assert out['layers']['amplifier']['count'] == 0
        assert out['layers']['amplifier']['active'] is False

    def test_unknown_intensity_does_not_fake_closing_stage(self):
        # 收尾期要求"强度回落"。不知道强度 ≠ 强度低 ——
        # 若把 None 当成 1 级，这里会假成立。
        out = locate_stage({**self.BASE, 'intensity_level': None}, {},
                           texts=['专项整治工作已取得阶段性成效，转入总结阶段。'])
        assert out['stage'] != '收尾期'

    def test_known_low_intensity_still_reaches_closing_stage(self):
        # 负向对照：强度确实低时，收尾期必须仍然判得出来 ——
        # 否则上一条只是把这个分支整个关掉了。
        out = locate_stage({**self.BASE, 'intensity_level': 2}, {},
                           texts=['专项整治工作已取得阶段性成效，转入总结阶段。'])
        assert out['stage'] == '收尾期'

    def test_unknown_intensity_does_not_fake_enforcement(self):
        out = locate_stage({**self.BASE, 'intensity_level': None}, {})
        assert out['stage'] != '运动期'

    def test_judicial_still_reaches_enforcement_without_intensity(self):
        # 强度未知不该拖累其他层的证据：司法入轨本身就足以判运动期。
        out = locate_stage({**self.BASE, 'intensity_level': None,
                            'has_judicial': True}, {})
        assert out['stage'] == '运动期'

    def test_unknown_intensity_is_stated_in_evidence(self):
        out = locate_stage({**self.BASE, 'intensity_level': None}, {})
        assert '无强度结论' in out['evidence']

    def test_bool_is_not_treated_as_intensity(self):
        # True 是 int 的子类。若不排除，False 会被当成 0 级、True 当成 1 级。
        out = locate_stage({**self.BASE, 'intensity_level': True}, {})
        assert out['intensity_known'] is False


class TestEndToEndNoCrash:
    def test_pipeline_survives_a_period_with_no_intensity(self):
        """整条链：缺证据期入库 → 下一期读到它 → 不得中断。"""
        seed('20260601', 5)
        seed('20260602', None)
        trend = rolling_trend(['光伏'], as_of='20260603')
        assert trend['data_points'] == 2
        assert any(r['max_level'] is None for r in trend['intensity_timeline'])
