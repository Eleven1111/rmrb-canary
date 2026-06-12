"""案例库回测校准 + 频率陈述 + 风险窗口集成。"""

from agent.calibration.backtest import (
    load_cases,
    signal_leads,
    observed_leads_by_level,
    get_calibrated_windows,
    frequency_statement,
    calibration_report,
)
from agent.tools.policy_clock import calculate_risk_window


class TestCaseLibrary:
    def test_all_cases_have_required_fields(self):
        for case in load_cases()['cases']:
            assert case['action_date']
            assert case['frame']
            assert case['milestones'], case['id']

    def test_signal_leads_positive(self):
        for row in signal_leads():
            assert row['lead_days'] > 0, f"{row['case']} 信号晚于行动日"

    def test_known_lead_politburo_antitrust(self):
        rows = [r for r in signal_leads() if r['case_id'] == 'platform_antitrust_2020'
                and r['intensity'] == 5]
        assert rows[0]['lead_days'] == 13

    def test_escalation_and_closing_excluded_from_leads(self):
        ids = {(r['case_id'], r['date']) for r in signal_leads()}
        assert ('pharma_2023', '2023-07-28') not in ids
        assert ('p2p_2016', '2020-11-27') not in ids


class TestCalibration:
    def test_levels_456_calibrated(self):
        windows = get_calibrated_windows()
        for level in (4, 5, 6):
            assert windows[level]['calibrated']
            assert windows[level]['n'] >= 2
            lo, hi = windows[level]['window']
            assert 0 < lo < hi

    def test_calibrated_window_tighter_than_default_for_level4(self):
        # 默认 4级=6-12个月；实测提前量远短于此
        lo, hi = get_calibrated_windows()[4]['window']
        assert hi < 6

    def test_report_contains_observations(self):
        report = calibration_report()
        assert '回测校准报告' in report
        assert '提前 13 天' in report


class TestFrequencyStatement:
    def test_level5_combo(self):
        freq = frequency_statement(5)
        assert freq['matched_cases'] >= 5
        assert freq['within_6mo'] >= freq['within_3mo']
        assert '3 个月内' in freq['statement']

    def test_ministry_filter_narrows(self):
        all_l = frequency_statement(5)
        l4_only = frequency_statement(5, 'L4')
        assert l4_only['matched_cases'] <= all_l['matched_cases']

    def test_unprecedented_combo(self):
        freq = frequency_statement(7, 'L5')
        assert freq['matched_cases'] == 0
        assert '无' in freq['statement']


class TestRiskWindowIntegration:
    def test_calibrated_basis_used(self):
        result = calculate_risk_window(
            intensity_level=5, ministry_compression=1.0,
            clock_coefficient=1.0, narrative_speed_modifier=1.0,
            ministry_level='L2',
        )
        assert result['base_window_basis']['source'] == 'backtest'
        assert result['frequency'] is not None

    def test_uncalibrated_level_falls_back(self):
        result = calculate_risk_window(
            intensity_level=1, ministry_compression=1.0,
            clock_coefficient=1.0, narrative_speed_modifier=1.0,
        )
        assert result['base_window_basis']['source'] == 'default'
        assert result['base_window'] == '12-18个月'

    def test_compression_factors_still_apply(self):
        loose = calculate_risk_window(5, 1.0, 1.0, 1.0)
        tight = calculate_risk_window(5, 0.4, 0.5, 0.5)
        assert tight['adjusted_window_months'][1] < loose['adjusted_window_months'][1]
