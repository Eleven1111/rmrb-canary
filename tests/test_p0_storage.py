"""
P0 存储层：主题身份（F10）、同期幂等（F02）、as_of 截面（F03）。

每条用例针对的都是合并前**真实存在**的行为，不是假想缺陷：
  F10  `keywords LIKE '%光伏%'` 回退让 ["储能","光伏"] 成为 ["光伏"] 的历史
  F02  同一期重跑一次就多一行样本
  F03  silence_detector / rolling_trend 的查询完全没有日期上界，
       且滚动窗口从 datetime.now() 起算 —— 回放时读到的是"未来"

这些用例都做过变异检查：把对应修复改回原样，本文件必须变红。
"""

import json
import sqlite3

from agent.store import db
from agent.tools.rolling_window import rolling_verdict
from agent.tools.silence_detector import detect_silence, rolling_trend
from agent.versioning import ALGO_VERSION, LEGACY_ALGO_VERSION, make_topic_id
from test_store_db import make_pipeline_result


def seed(date='20260610', keywords=None, level=5, articles=3,
         topic_version=None, topic_key=None):
    """写入一条分析记录；topic_version 给定时模拟正式主题口径。"""
    result = make_pipeline_result(date=date, keywords=keywords or ['光伏'],
                                  weighted_level=level)
    result['rmrb']['total_articles'] = articles
    if topic_version or topic_key:
        result['topic'] = {
            'topic_id': make_topic_id(result['keywords']),
            'topic_key': topic_key,
            'topic_version': topic_version,
        }
    return db.save_analysis_detailed(result)


def count_analyses():
    conn = db.get_conn()
    n = conn.execute('SELECT COUNT(*) AS n FROM analyses').fetchone()['n']
    conn.close()
    return n


class TestTopicIdentityF10:
    """不同关键词组是不同主题，绝不因为"找不到"就退而接上别人的时间线。"""

    def test_superset_topic_is_not_previous_analysis(self):
        # 合并前：LIKE 回退让这条 ["储能","光伏"] 记录成为 ["光伏"] 的上一期。
        seed(date='20260609', keywords=['储能', '光伏'])
        assert db.get_previous_analysis(['光伏'], current_date='20260610') is None

    def test_exact_topic_still_found(self):
        # 负向对照：同一主题必须仍然找得到，否则上一条只是把功能关掉了。
        seed(date='20260609', keywords=['光伏'])
        prev = db.get_previous_analysis(['光伏'], current_date='20260610')
        assert prev is not None and prev['date'] == '20260609'

    def test_keyword_order_does_not_split_topic(self):
        seed(date='20260609', keywords=['储能', '光伏'])
        prev = db.get_previous_analysis(['光伏', '储能'], current_date='20260610')
        assert prev is not None

    def test_rolling_window_excludes_other_topic(self):
        for d in ('20260601', '20260602', '20260603'):
            seed(date=d, keywords=['储能', '光伏'])
        verdict = rolling_verdict(['光伏'], end_date='20260603', window_days=7)
        assert verdict['data_points'] == 0

    def test_silence_excludes_other_topic(self):
        for d in ('20260601', '20260602', '20260603'):
            seed(date=d, keywords=['储能', '光伏'], articles=9)
        result = detect_silence(['光伏'], current_date='20260604', current_count=0)
        # 别的主题的 9 篇/期不该让本主题的 0 篇被判成"沉默"。
        assert result['signal'] == '首次'
        assert result['historical_avg'] == 0.0


class TestSameIssueIdempotencyF02:
    """跑了几次，和有几个样本，是两件事。"""

    def test_rerun_replaces_instead_of_appending(self):
        first = seed(date='20260610', level=3)
        second = seed(date='20260610', level=6)
        assert first['action'] == 'inserted'
        assert second['action'] == 'replaced'
        assert second['analysis_id'] == first['analysis_id']
        assert count_analyses() == 1

    def test_rerun_keeps_latest_values(self):
        seed(date='20260610', level=3)
        seed(date='20260610', level=6)
        prev = db.get_previous_analysis(['光伏'], current_date='20260611')
        assert prev['weighted_max_intensity'] == 6

    def test_rerun_does_not_duplicate_child_rows(self):
        seed(date='20260610')
        seed(date='20260610')
        conn = db.get_conn()
        snaps = conn.execute('SELECT COUNT(*) AS n FROM intensity_snapshots').fetchone()['n']
        arts = conn.execute('SELECT COUNT(*) AS n FROM article_records').fetchone()['n']
        conn.close()
        # make_pipeline_result 里是 1 个强度档、1 篇文章。
        assert (snaps, arts) == (1, 1)

    def test_rerun_does_not_inflate_silence_baseline(self):
        # 幂等的意义在下游：同一期跑三次不该把历史均值算成三个样本。
        for _ in range(3):
            seed(date='20260601', articles=10)
        seed(date='20260602', articles=2)
        result = detect_silence(['光伏'], current_date='20260603', current_count=2)
        assert len(result['recent_counts']) == 2
        assert result['historical_avg'] == 6.0  # (10 + 2) / 2

    def test_different_date_is_a_new_sample(self):
        seed(date='20260610')
        seed(date='20260611')
        assert count_analyses() == 2

    def test_different_topic_version_is_a_separate_timeline(self):
        # 主题口径变了就是另一条时间线：并存，不互相替换。
        seed(date='20260610', topic_version='2026-06-01', topic_key='pv')
        seed(date='20260610', topic_version='2026-09-01', topic_key='pv')
        assert count_analyses() == 2

    def test_run_log_records_every_run(self):
        seed(date='20260610')
        seed(date='20260610')
        tid = make_topic_id(['光伏'])
        db.log_run(topic_id=tid, date='20260610', as_of='20260610',
                   quality_status='complete', outcome='replaced')
        conn = db.get_conn()
        runs = conn.execute('SELECT COUNT(*) AS n FROM run_log').fetchone()['n']
        conn.close()
        assert runs == 1 and count_analyses() == 1


class TestAsOfCrossSectionF03:
    """历史回放不得读到 as_of 之后的记录。"""

    def _seed_ten_days(self):
        for i in range(1, 11):
            seed(date=f'202606{i:02d}', articles=i)

    def test_silence_ignores_records_after_current_date(self):
        self._seed_ten_days()
        result = detect_silence(['光伏'], current_date='20260605', current_count=1)
        dates = [r['date'] for r in result['recent_counts']]
        assert dates and max(dates) < '20260605'
        # 只有 1..4 篇进入历史：均值 2.5。若读到未来（1..10 去掉第 5 天）则是 5.9。
        assert result['historical_avg'] == 2.5

    def test_rolling_trend_ignores_records_after_as_of(self):
        self._seed_ten_days()
        trend = rolling_trend(['光伏'], as_of='20260605')
        dates = [r['date'] for r in trend['intensity_timeline']]
        assert dates and max(dates) <= '20260605'
        assert trend['data_points'] == 5

    def test_rolling_trend_window_anchored_on_as_of_not_today(self):
        # 合并前窗口起点是 datetime.now()，回放 2026-06 的数据时
        # "近 7 天"窗口里一条都不落，count 恒为 0 —— 趋势静默失效。
        self._seed_ten_days()
        trend = rolling_trend(['光伏'], as_of='20260610', windows=[7])
        assert trend['windows']['7d']['count'] > 0

    def test_rolling_trend_defaults_to_today(self):
        # 负向对照：不给 as_of 时行为不变（以今天为锚），旧调用方不受影响。
        self._seed_ten_days()
        trend = rolling_trend(['光伏'])
        assert trend['data_points'] == 10


class TestLegacyMigration:
    """升级前的行保留原值，补身份字段，并在对比时带口径警告。"""

    def _insert_legacy(self, conn, date='20260601', keywords=('光伏',)):
        conn.execute(
            """INSERT INTO analyses
               (date, keywords, primary_frame, max_intensity, intensity_triggers,
                ministry_level, ministry_list, risk_signal, total_articles, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (date, json.dumps(sorted(keywords), ensure_ascii=False), '国家安全框架',
             5, '[]', 'L5', '[]', '🔴', 10, '2026-06-01T08:00:00'),
        )

    def test_legacy_row_gets_identity_backfilled(self, isolated_db):
        conn = sqlite3.connect(isolated_db)
        conn.executescript(db.SCHEMA)
        self._insert_legacy(conn)
        conn.commit()
        conn.close()

        prev = db.get_previous_analysis(['光伏'], current_date='20260610')
        assert prev is not None
        assert prev['topic_id'] == make_topic_id(['光伏'])
        assert prev['algo_version'] == LEGACY_ALGO_VERSION
        assert prev['topic_version'] == db.LEGACY_TOPIC_VERSION

    def test_legacy_comparison_is_flagged_not_silent(self, isolated_db):
        conn = sqlite3.connect(isolated_db)
        conn.executescript(db.SCHEMA)
        self._insert_legacy(conn)
        conn.commit()
        conn.close()

        trend = db.compare_with_previous(make_pipeline_result(date='20260610'), ['光伏'])
        assert trend is not None
        assert trend['comparable'] is False
        assert LEGACY_ALGO_VERSION in trend['caliber_note']

    def test_duplicate_legacy_rows_do_not_block_migration(self, isolated_db):
        # 旧库里同日重复行是存在的。全表唯一索引会让迁移直接失败，
        # 等于历史数据把新装置卡死 —— 所以幂等索引是 partial 的。
        conn = sqlite3.connect(isolated_db)
        conn.executescript(db.SCHEMA)
        self._insert_legacy(conn)
        self._insert_legacy(conn)
        conn.commit()
        conn.close()

        assert count_analyses() == 2          # 迁移没有报错，也没有删数据
        assert seed(date='20260610')['action'] == 'inserted'
        assert seed(date='20260610')['action'] == 'replaced'   # 新行仍然幂等

    def test_current_rows_carry_current_algo_version(self):
        seed(date='20260610')
        rows = db.list_analyses(algo_version=ALGO_VERSION)
        assert len(rows) == 1 and rows[0]['algo_version'] == ALGO_VERSION
