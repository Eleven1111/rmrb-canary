"""
预测台账的不可改写保证。

原方案第三层要求"冻结规则后持续生成判断，事后回填结果；
**不因已知结局改写当时报告**"。这一条必须由程序保证，不能靠自觉：
允许重复裁定 = 允许拿已知结局回头改当时的判断，命中率可以这样刷出来。

本文件替代了另起一套影子台账的做法 —— 同一职责不重复实现两遍。
"""

import datetime

from agent.store import db, ledger
from test_store_db import make_pipeline_result


def _open_prediction(date='20250101', level=5):
    result = make_pipeline_result(date=date, weighted_level=level)
    result['risk_window'] = {
        'risk_emoji': '🔴', 'risk_level': '高',
        'adjusted_window_months': (1, 2), 'adjusted_window_label': '1-2个月',
    }
    analysis_id = db.save_analysis(result)
    return ledger.record_prediction(analysis_id, result)


class TestResolveImmutability:
    def test_first_resolution_succeeds(self):
        pid = _open_prediction()
        assert ledger.resolve_prediction(pid, 'hit', '文件落地')['ok']

    def test_second_resolution_rejected(self):
        pid = _open_prediction()
        ledger.resolve_prediction(pid, 'hit', '文件落地')
        again = ledger.resolve_prediction(pid, 'miss', '改口说没发生')
        assert not again['ok'], '已裁定的预测不得重复裁定'
        assert again['existing_outcome'] == 'hit'

    def test_original_verdict_survives_rejected_overwrite(self):
        pid = _open_prediction()
        ledger.resolve_prediction(pid, 'hit', '文件落地')
        ledger.resolve_prediction(pid, 'miss', '改口')
        row = [r for r in ledger.list_predictions() if r['id'] == pid][0]
        assert row['status'] == 'hit', '被拒绝的覆盖不得改动原判断'
        assert row['resolution_note'] == '文件落地'

    def test_rejection_explains_the_right_move(self):
        pid = _open_prediction()
        ledger.resolve_prediction(pid, 'hit')
        out = ledger.resolve_prediction(pid, 'miss')
        assert 'hint' in out and '作废' in out['hint']

    def test_hit_rate_cannot_be_inflated_by_re_resolving(self):
        """反向对照：连续改判不应把命中率刷上去。"""
        a, b = _open_prediction(), _open_prediction(date='20250102')
        ledger.resolve_prediction(a, 'miss')
        ledger.resolve_prediction(b, 'miss')
        for _ in range(3):
            ledger.resolve_prediction(a, 'hit')
            ledger.resolve_prediction(b, 'hit')
        stats = ledger.ledger_stats()
        assert stats['hit'] == 0, '重复裁定不得改变命中数'
        assert stats['miss'] == 2


class TestFrozenRuleVerifiability:
    """"规则已冻结"必须可核验，而不是靠声称。"""

    def test_algo_version_recorded_with_prediction(self):
        from agent.versioning import ALGO_VERSION
        pid = _open_prediction()
        row = [r for r in ledger.list_predictions() if r['id'] == pid][0]
        assert row['algo_version'] == ALGO_VERSION

    def test_mixed_versions_are_detectable(self):
        import agent.store.ledger as L
        pid_a = _open_prediction()
        original = L.ALGO_VERSION
        try:
            L.ALGO_VERSION = 'v9-experiment'
            pid_b = _open_prediction(date='20250102')
        finally:
            L.ALGO_VERSION = original
        rows = {r['id']: r['algo_version'] for r in ledger.list_predictions()}
        assert len({rows[pid_a], rows[pid_b]}) == 2, \
            '换了算法版本却看不出来，就无法判断这段影子运行是否冻结'

    def test_legacy_rows_are_not_claimed_frozen(self):
        """旧库补列后，历史行标 unknown —— 不假装它们冻结过。"""
        conn = db.get_conn()
        ledger._ensure_columns(conn)
        conn.execute(
            "INSERT INTO predictions (analysis_id, keywords, made_on, window_start, "
            "window_end, status, created_at) VALUES (1, '[]', '20250101', '20250101', "
            "'20250201', 'open', ?)", (datetime.datetime.now().isoformat(),))
        conn.commit()
        conn.close()
        ledger._ensure_columns(db.get_conn())
        versions = {r['algo_version'] for r in ledger.list_predictions()}
        assert 'unknown' in versions or None in versions
