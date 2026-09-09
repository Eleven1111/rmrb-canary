"""
滚动趋势的方向必须与时间同向。

历史查询是 ORDER BY date DESC（新→旧），若直接在倒序列表上取"前半 vs 后半"，
前半其实是较新的一段，方向会整个反过来。
方案最小复核样例：按日期由旧到新为 2、2、6、6 时，旧实现输出"下降"。
"""

import datetime

from agent.store import db
from agent.tools.silence_detector import rolling_trend
from test_store_db import make_pipeline_result


def _seed(levels):
    """按由旧到新的顺序写入若干期，日期取最近几天以便落入 7d 窗口。"""
    today = datetime.datetime.now()
    n = len(levels)
    for i, level in enumerate(levels):
        date = (today - datetime.timedelta(days=n - i)).strftime('%Y%m%d')
        db.save_analysis(make_pipeline_result(date=date, weighted_level=level))


class TestTrendDirection:
    def test_rising_series_reports_rising(self):
        _seed([2, 2, 6, 6])          # 由旧到新递增
        w = rolling_trend(['光伏'])['windows']['7d']
        assert w['trend_direction'] == '上升', '由旧到新递增必须报上升'

    def test_falling_series_reports_falling(self):
        _seed([6, 6, 2, 2])
        assert rolling_trend(['光伏'])['windows']['7d']['trend_direction'] == '下降'

    def test_flat_series_reports_flat(self):
        _seed([3, 3, 3, 3])
        assert rolling_trend(['光伏'])['windows']['7d']['trend_direction'] == '持平'

    def test_comparison_windows_are_chronological(self):
        """必须能看出比的是哪两段，且较早段在前。"""
        _seed([2, 2, 6, 6])
        cmp = rolling_trend(['光伏'])['windows']['7d']['comparison']
        assert cmp is not None
        earlier_start = cmp['earlier_window'].split('–')[0]
        later_start = cmp['later_window'].split('–')[0]
        assert earlier_start < later_start, '较早窗口的起点必须早于较近窗口'
        assert cmp['earlier_avg_intensity'] < cmp['later_avg_intensity']

    def test_single_point_is_insufficient(self):
        _seed([5])
        assert rolling_trend(['光伏'])['windows']['7d']['trend_direction'] == '数据不足'
