"""DB v2：加权口径入库、迁移、跨版本对比。"""

import json
import sqlite3

from agent.store import db


def make_pipeline_result(date='20260610', keywords=None, weighted_level=5,
                         frame='高质量发展框架', ministry='L2'):
    keywords = keywords or ['光伏']
    return {
        'keywords': keywords,
        'rmrb': {
            'date': date,
            'total_articles': 3,
            'articles': [
                {'date': date, 'page_no': 1, 'article_no': 1, 'title': '光伏头条',
                 'column': '要闻', 'position': '头条', 'agenda_score': 8, 'word_count': 2000},
            ],
        },
        'narrative': {'primary_frame': frame, 'secondary_frame': None},
        'intensity': {
            'max_level': 5,
            'weighted_max_level': weighted_level,
            'max_level_triggers': ['坚决遏制'],
            'distribution': {
                'level_5': {'count': 2, 'pct': 100.0, 'triggers': ['坚决遏制']},
            },
        },
        'ministry': {'coordination_level': ministry, 'ministries_found': ['财政部']},
        'risk_window': {'risk_emoji': '🟡'},
        'relevance': {'stats': {'avg_relevance': 0.7}},
        'transmission': {'stage': '铺垫期'},
    }


class TestSaveAndLoad:
    def test_save_stores_weighted_caliber(self):
        analysis_id = db.save_analysis(make_pipeline_result(weighted_level=4))
        prev = db.get_previous_analysis(['光伏'], current_date='20260611')
        assert prev['id'] == analysis_id
        assert prev['weighted_max_intensity'] == 4
        assert prev['engine_version'] == 'v3'

    def test_keywords_sorted_for_stable_grouping(self):
        db.save_analysis(make_pipeline_result(keywords=['储能', '光伏']))
        prev = db.get_previous_analysis(['光伏', '储能'], current_date='20260611')
        assert prev is not None

    def test_compare_uses_weighted_levels(self):
        db.save_analysis(make_pipeline_result(date='20260609', weighted_level=3))
        current = make_pipeline_result(date='20260610', weighted_level=5)
        trend = db.compare_with_previous(current, ['光伏'])
        assert trend['intensity_direction'] == '上升'
        assert '3级→5级' in trend['intensity_change']
        assert trend['caliber_note'] is None

    def test_compare_flags_old_caliber(self, isolated_db):
        conn = sqlite3.connect(isolated_db)
        conn.executescript(db.SCHEMA)
        conn.execute(
            """INSERT INTO analyses
               (date, keywords, primary_frame, max_intensity, intensity_triggers,
                ministry_level, ministry_list, risk_signal, total_articles, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('20260601', json.dumps(['光伏'], ensure_ascii=False), '国家安全框架',
             5, '[]', 'L5', '[]', '🔴', 10, '2026-06-01T08:00:00'),
        )
        conn.commit()
        conn.close()

        current = make_pipeline_result(date='20260610')
        trend = db.compare_with_previous(current, ['光伏'])
        assert trend is not None
        assert trend['caliber_note'] is not None

    def test_migration_adds_columns_to_legacy_db(self, isolated_db):
        conn = sqlite3.connect(isolated_db)
        conn.execute(
            """CREATE TABLE analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, keywords TEXT NOT NULL,
                primary_frame TEXT, secondary_frame TEXT,
                max_intensity INTEGER, intensity_triggers TEXT,
                ministry_level TEXT, ministry_list TEXT,
                risk_signal TEXT, total_articles INTEGER,
                report_json TEXT, created_at TEXT NOT NULL)"""
        )
        conn.commit()
        conn.close()

        conn = db.get_conn()
        cols = {r[1] for r in conn.execute('PRAGMA table_info(analyses)')}
        conn.close()
        assert 'weighted_max_intensity' in cols
        assert 'engine_version' in cols

    def test_new_tables_exist(self):
        conn = db.get_conn()
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        conn.close()
        assert {'predictions', 'judgments', 'formulations', 'formulation_sightings'} <= tables
