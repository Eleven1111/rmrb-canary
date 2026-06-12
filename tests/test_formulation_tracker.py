"""提法生命周期追踪。"""

from conftest import make_article

from agent.store import db
from agent.tools.formulation_tracker import (
    track_formulations,
    discover_new_formulations,
    _maximal_candidates,
)


class TestTracking:
    def test_watched_formulation_recorded(self):
        articles = [make_article(
            title='发展新质生产力的实践路径',
            content='各地因地制宜发展新质生产力。',
            page_no=2,
        )]
        result = track_formulations(articles, '20260610')
        phrases = {a['phrase'] for a in result['active']}
        assert '新质生产力' in phrases
        entry = next(a for a in result['active'] if a['phrase'] == '新质生产力')
        assert entry['peak'] == '标题'
        assert entry['first_seen'] == '20260610'

    def test_escalation_body_to_front_page_title(self):
        track_formulations(
            [make_article(title='经济观察', content='提到耐心资本一次。', page_no=6)],
            '20260601',
        )
        result = track_formulations(
            [make_article(title='壮大耐心资本', content='……', page_no=1)],
            '20260610',
        )
        entry = next(a for a in result['active'] if a['phrase'] == '耐心资本')
        assert entry['escalated']
        assert entry['peak'] == '头版标题'

    def test_retirement_after_90_days_silence(self):
        track_formulations(
            [make_article(title='坚持房住不炒', content='……', page_no=1)],
            '20260101',
        )
        result = track_formulations(
            [make_article(title='别的议题', content='无关内容。')],
            '20260601',
        )
        retired = {r['phrase'] for r in result['retiring']}
        assert '房住不炒' in retired

    def test_sighting_idempotent_same_day(self):
        art = [make_article(title='发展低空经济', content='……', page_no=3)]
        track_formulations(art, '20260610')
        track_formulations(art, '20260610')
        conn = db.get_conn()
        count = conn.execute(
            """SELECT COUNT(*) c FROM formulation_sightings s
               JOIN formulations f ON f.id = s.formulation_id
               WHERE f.phrase = '低空经济'"""
        ).fetchone()['c']
        conn.close()
        assert count == 1


class TestDiscovery:
    def test_new_ngram_in_multiple_titles_discovered(self):
        articles = [
            make_article(title='推动谷子经济健康发展'),
            make_article(title='谷子经济成为消费新增长点'),
        ]
        found = discover_new_formulations(articles)
        phrases = {f['phrase'] for f in found}
        assert any('谷子经济' in p for p in phrases)

    def test_historical_ngram_not_discovered(self):
        db.save_analysis({
            'keywords': ['测试'],
            'rmrb': {'date': '20260501', 'total_articles': 1, 'articles': [
                {'date': '20260501', 'page_no': 1, 'article_no': 1,
                 'title': '谷子经济观察', 'column': '', 'position': '',
                 'agenda_score': 5, 'word_count': 100},
            ]},
            'narrative': {}, 'intensity': {}, 'ministry': {},
            'risk_window': {}, 'relevance': {},
        })
        articles = [
            make_article(title='推动谷子经济健康发展'),
            make_article(title='谷子经济成为消费新增长点'),
        ]
        found = discover_new_formulations(articles)
        assert not any('谷子经济' in f['phrase'] for f in found)

    def test_single_title_not_enough(self):
        articles = [make_article(title='独家提法只出现一次')]
        assert discover_new_formulations(articles) == []

    def test_maximal_substring_dedupe(self):
        kept = _maximal_candidates({'质生产力': 2, '新质生产力': 2, '另一提法': 3})
        assert '新质生产力' in kept
        assert '质生产力' not in kept
