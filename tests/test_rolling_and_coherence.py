"""滚动窗口聚合 + 议题一致性检查。"""

from conftest import make_article

from agent.store import db
from agent.tools.rolling_window import rolling_verdict
from agent.tools.topic_coherence import check_coherence
from test_store_db import make_pipeline_result


class TestRollingVerdict:
    def _seed_week(self, levels, frames=None, ministries=None):
        frames = frames or ['国家安全框架'] * len(levels)
        ministries = ministries or ['L2'] * len(levels)
        for i, (lvl, frame, ml) in enumerate(zip(levels, frames, ministries)):
            db.save_analysis(make_pipeline_result(
                date=f'2026060{i + 1}', weighted_level=lvl,
                frame=frame, ministry=ml,
            ))

    def test_smoothing_dampens_oscillation(self):
        # 实测震荡模式：2,4,2,5,5,4 — 单日在2-5级跳动
        self._seed_week([2, 4, 2, 5, 5, 4])
        verdict = rolling_verdict(['光伏'], end_date='20260606', window_days=7)
        assert verdict['data_points'] == 6
        assert 2.5 < verdict['intensity_smoothed'] < 5.0
        assert verdict['oscillation_label'] in ('波动', '震荡')

    def test_stable_series_labeled_stable(self):
        self._seed_week([4, 4, 4, 4, 4])
        verdict = rolling_verdict(['光伏'], end_date='20260605', window_days=7)
        assert verdict['oscillation_label'] == '稳定'
        assert verdict['intensity_smoothed'] == 4.0
        assert verdict['confidence'] == 'high'

    def test_frame_stability_reported(self):
        self._seed_week(
            [4, 4, 4, 4],
            frames=['国家安全框架', '国家安全框架', '国家安全框架', '自立自强框架'],
        )
        verdict = rolling_verdict(['光伏'], end_date='20260604', window_days=7)
        assert verdict['dominant_frame'] == '国家安全框架'
        assert verdict['frame_stability'] == 0.75

    def test_recency_weighting_favors_recent(self):
        self._seed_week([1, 1, 1, 5, 5, 5])
        verdict = rolling_verdict(['光伏'], end_date='20260606', window_days=7)
        assert verdict['intensity_smoothed'] > 3.0

    def test_same_day_duplicates_deduped(self):
        db.save_analysis(make_pipeline_result(date='20260601', weighted_level=2))
        db.save_analysis(make_pipeline_result(date='20260601', weighted_level=5))
        verdict = rolling_verdict(['光伏'], end_date='20260601', window_days=7)
        assert verdict['data_points'] == 1
        assert verdict['intensity_today'] == 5

    def test_empty_window(self):
        verdict = rolling_verdict(['不存在的词'], end_date='20260601')
        assert verdict['data_points'] == 0
        assert verdict['confidence'] == 'none'


class TestTopicCoherence:
    def test_single_topic_coherent(self):
        articles = [
            make_article(title='光伏出口增长', content='光伏与储能协同发展。'),
            make_article(title='储能产业观察', content='储能配套光伏电站。'),
        ]
        result = check_coherence(articles, ['光伏', '储能'])
        assert result['coherent']
        assert result['cluster_count'] == 1

    def test_mixed_topics_split_into_clusters(self):
        articles = [
            make_article(title='光伏出口增长', content='光伏与储能协同。'),
            make_article(title='储能新政', content='储能项目光伏配套。'),
            make_article(title='政务服务优化', content='政务服务一网通办，智能客服上线。'),
            make_article(title='智能客服应用', content='政务服务大厅引入智能客服。'),
        ]
        result = check_coherence(
            articles, ['光伏', '储能', '政务服务', '智能客服']
        )
        assert not result['coherent']
        assert result['cluster_count'] == 2
        assert result['warning'] is not None

    def test_unused_keywords_reported(self):
        articles = [make_article(title='光伏产业', content='光伏装机增长。')]
        result = check_coherence(articles, ['光伏', '元宇宙'])
        assert result['unused_keywords'] == ['元宇宙']
        assert result['coherent']

    def test_no_articles(self):
        result = check_coherence([], ['光伏'])
        assert result['cluster_count'] == 0
