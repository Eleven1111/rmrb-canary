"""预测台账 + 分析师判断 + 议题档案。"""

import datetime
import os

from agent.store import db, ledger, judgments, dossier
from test_store_db import make_pipeline_result


def days_from_now(days: int) -> str:
    return (datetime.datetime.now() + datetime.timedelta(days=days)).strftime('%Y%m%d')


class TestLedger:
    def test_prediction_recorded_for_high_intensity(self):
        result = make_pipeline_result(weighted_level=5)
        result['risk_window'] = {
            'risk_emoji': '🟡', 'risk_level': '中高',
            'adjusted_window_months': (2, 4), 'adjusted_window_label': '2-4个月',
        }
        analysis_id = db.save_analysis(result)
        pid = ledger.record_prediction(analysis_id, result)
        assert pid is not None
        rows = ledger.list_predictions()
        assert rows[0]['status'] == 'open'
        assert rows[0]['intensity'] == 5

    def test_low_signal_not_recorded(self):
        result = make_pipeline_result(weighted_level=2)
        result['risk_window'] = {'risk_emoji': '🟢', 'adjusted_window_months': (12, 18)}
        assert ledger.record_prediction(1, result) is None

    def test_expired_prediction_flagged_due_review(self):
        result = make_pipeline_result(date='20250101', weighted_level=5)
        result['risk_window'] = {
            'risk_emoji': '🔴', 'risk_level': '高',
            'adjusted_window_months': (1, 2), 'adjusted_window_label': '1-2个月',
        }
        ledger.record_prediction(1, result)
        due = ledger.check_due_predictions(['光伏'])
        assert len(due) == 1
        assert due[0]['status'] == 'due_review'

    def test_resolve_and_stats(self):
        result = make_pipeline_result(date='20250101', weighted_level=5)
        result['risk_window'] = {
            'risk_emoji': '🔴', 'risk_level': '高',
            'adjusted_window_months': (1, 2), 'adjusted_window_label': '1-2个月',
        }
        pid = ledger.record_prediction(1, result)
        ledger.check_due_predictions(['光伏'])
        out = ledger.resolve_prediction(pid, 'hit', '6月文件落地')
        assert out['ok']
        stats = ledger.ledger_stats()
        assert stats['hit'] == 1
        assert stats['hit_rate'] == 1.0

    def test_resolve_invalid_outcome_rejected(self):
        out = ledger.resolve_prediction(999, 'maybe')
        assert not out['ok']

    def test_open_prediction_within_window_not_flagged(self):
        result = make_pipeline_result(
            date=datetime.datetime.now().strftime('%Y%m%d'), weighted_level=5,
        )
        result['risk_window'] = {
            'risk_emoji': '🟡', 'risk_level': '中高',
            'adjusted_window_months': (3, 6), 'adjusted_window_label': '3-6个月',
        }
        ledger.record_prediction(1, result)
        assert ledger.check_due_predictions(['光伏']) == []


class TestJudgments:
    def test_record_and_recall(self):
        judgments.record_judgment(
            keywords=['光伏'], date='20260601',
            judgment='框架从支持转向规范的早期迹象',
            triples=[{'s': '部分企业', 'v': '低价竞争', 'o': '行业生态'}],
            surprising_signal='头版沉默但理论版升温',
            tension='官方称有序，行业库存高企',
        )
        recalled = judgments.get_recent_judgments(['光伏'])
        assert len(recalled) == 1
        assert recalled[0]['triples'][0]['s'] == '部分企业'

    def test_fuzzy_fallback(self):
        judgments.record_judgment(['光伏', '储能'], '20260601', '判断A')
        recalled = judgments.get_recent_judgments(['光伏'])
        assert len(recalled) == 1


class TestDossier:
    def _full_result(self, date='20260610'):
        result = make_pipeline_result(date=date, weighted_level=5)
        result['risk_window'] = {
            'risk_emoji': '🟡', 'adjusted_window_label': '2-4个月',
            'adjusted_window_months': (2, 4),
        }
        result['rolling'] = {
            'data_points': 5, 'window_days': 7, 'intensity_smoothed': 4.2,
            'oscillation_label': '波动', 'frame_stability': 0.8,
        }
        result['transmission'] = {'stage': '铺垫期'}
        result['silence'] = {'signal': '正常'}
        result['formulation'] = {
            'active': [{'phrase': '新质生产力', 'peak': '标题', 'escalated': True}],
            'newly_discovered': [],
        }
        result['trend'] = {'narrative_drifted': False}
        result['prediction'] = {'prediction_id': 7, 'due_review': []}
        return result

    def test_dossier_created_and_appended(self):
        path = dossier.update_dossier(self._full_result('20260610'))
        dossier.update_dossier(self._full_result('20260611'))
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert content.count('## 2026-06-1') == 2
        assert '议题档案' in content
        assert '新质生产力(标题↑)' in content
        assert '传导链定位：铺垫期' in content

    def test_judgment_appended(self):
        dossier.update_dossier(self._full_result())
        path = dossier.append_judgment_to_dossier(
            ['光伏'], '20260610', '本期最反直觉的是部委层先于中央层发声。'
        )
        with open(path, encoding='utf-8') as f:
            assert '分析师判断（2026-06-10）' in f.read()

    def test_context_injection(self):
        dossier.update_dossier(self._full_result())
        judgments.record_judgment(['光伏'], '20260610', '上期判断内容', tension='张力X')
        ctx = dossier.dossier_context(['光伏'])
        assert ctx['dossier_path']
        assert '铺垫期' in ctx['dossier_tail']
        assert ctx['previous_judgments'][0]['judgment'] == '上期判断内容'
        assert '回应上期判断' in ctx['note']

    def test_first_run_context_empty(self):
        ctx = dossier.dossier_context(['全新议题'])
        assert ctx['dossier_path'] is None
        assert '首篇' in ctx['note']

    def test_slug_sanitization(self):
        slug = dossier.topic_slug(['光伏/储能', 'A B'])
        assert '/' not in slug
        assert ' ' not in slug
