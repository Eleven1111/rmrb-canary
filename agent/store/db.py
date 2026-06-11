"""
SQLite 历史存储层 — rmrb-canary agent（schema v2）

表：
  analyses             — 每次分析摘要（v3 起存加权口径，engine_version 区分）
  intensity_snapshots  — 七级话语强度分布快照
  article_records      — 每篇文章核心字段（含标题，供提法追踪回溯）
  predictions          — 预测台账（store/ledger.py 维护）
  judgments            — 分析师判断沉淀（store/judgments.py 维护）
  formulations         — 提法生命周期（tools/formulation_tracker.py 维护）
  formulation_sightings— 提法逐日出现记录

数据库位置：~/.rmrb_canary/history.db
可用环境变量 RMRB_CANARY_DB 覆盖（测试隔离用）。
"""

import sqlite3
import json
import os
import datetime

ENGINE_VERSION = 'v3'

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    keywords TEXT NOT NULL,
    primary_frame TEXT,
    secondary_frame TEXT,
    max_intensity INTEGER,
    intensity_triggers TEXT,
    ministry_level TEXT,
    ministry_list TEXT,
    risk_signal TEXT,
    total_articles INTEGER,
    report_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intensity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    count INTEGER NOT NULL,
    pct REAL NOT NULL,
    triggers TEXT,
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS article_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    date TEXT,
    page_no INTEGER,
    article_no INTEGER,
    title TEXT,
    column_name TEXT,
    position TEXT,
    agenda_score INTEGER,
    word_count INTEGER,
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    keywords TEXT NOT NULL,
    made_on TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    risk_level TEXT,
    intensity INTEGER,
    ministry_level TEXT,
    statement TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    resolved_at TEXT,
    resolution_note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER,
    keywords TEXT NOT NULL,
    date TEXT NOT NULL,
    triples TEXT,
    surprising_signal TEXT,
    tension TEXT,
    judgment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS formulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL UNIQUE,
    first_seen TEXT,
    first_context TEXT,
    last_seen TEXT,
    peak_status TEXT,
    status TEXT DEFAULT 'active',
    watch INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS formulation_sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    formulation_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    in_title INTEGER DEFAULT 0,
    page_no INTEGER,
    article_title TEXT,
    UNIQUE(formulation_id, date, article_title),
    FOREIGN KEY (formulation_id) REFERENCES formulations(id)
);

CREATE INDEX IF NOT EXISTS idx_analyses_date ON analyses(date);
CREATE INDEX IF NOT EXISTS idx_analyses_keywords ON analyses(keywords);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
CREATE INDEX IF NOT EXISTS idx_sightings_date ON formulation_sightings(date);
"""

# v2→v3 增量列：旧库缺失时补齐
MIGRATION_COLUMNS = [
    ('analyses', 'engine_version', 'TEXT'),
    ('analyses', 'weighted_max_intensity', 'INTEGER'),
    ('analyses', 'avg_relevance', 'REAL'),
    ('analyses', 'transmission_stage', 'TEXT'),
]


def get_db_path() -> str:
    override = os.environ.get('RMRB_CANARY_DB')
    if override:
        return override
    return os.path.join(os.path.expanduser('~/.rmrb_canary'), 'history.db')


def get_conn():
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    for table, column, coltype in MIGRATION_COLUMNS:
        try:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {coltype}')
        except sqlite3.OperationalError:
            pass
    conn.commit()


def save_analysis(result: dict) -> int:
    """
    将管道结果（加权口径）写入数据库，返回 analysis_id。

    参数 result 为 agent.run_pipeline 的输出 dict，
    读取 narrative / intensity / ministry / rmrb / risk_window 等字段。
    """
    conn = get_conn()
    now = datetime.datetime.now().isoformat()

    narrative = result.get('narrative', {})
    intensity = result.get('intensity', {})
    ministry = result.get('ministry', {})
    rmrb = result.get('rmrb', {})
    risk = result.get('risk_window', {})
    relevance_stats = result.get('relevance', {}).get('stats', {})

    report_copy = {k: v for k, v in result.items() if k != 'full_texts'}

    cur = conn.execute(
        """INSERT INTO analyses
           (date, keywords, primary_frame, secondary_frame,
            max_intensity, weighted_max_intensity, intensity_triggers,
            ministry_level, ministry_list,
            risk_signal, total_articles, avg_relevance,
            transmission_stage, engine_version, report_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rmrb.get('date', ''),
            json.dumps(sorted(result.get('keywords', [])), ensure_ascii=False),
            narrative.get('primary_frame', ''),
            narrative.get('secondary_frame', ''),
            intensity.get('max_level', 0),
            intensity.get('weighted_max_level', intensity.get('max_level', 0)),
            json.dumps(intensity.get('max_level_triggers', []), ensure_ascii=False),
            ministry.get('coordination_level', ''),
            json.dumps(ministry.get('ministries_found', []), ensure_ascii=False),
            risk.get('risk_emoji', '🟢'),
            rmrb.get('total_articles', 0),
            relevance_stats.get('avg_relevance'),
            result.get('transmission', {}).get('stage'),
            ENGINE_VERSION,
            json.dumps(report_copy, ensure_ascii=False, default=str),
            now,
        )
    )
    analysis_id = cur.lastrowid

    for level_key, data in intensity.get('distribution', {}).items():
        conn.execute(
            """INSERT INTO intensity_snapshots
               (analysis_id, level, count, pct, triggers)
               VALUES (?, ?, ?, ?, ?)""",
            (
                analysis_id,
                int(level_key.replace('level_', '')),
                data.get('count', 0),
                data.get('pct', 0.0),
                json.dumps(data.get('triggers', []), ensure_ascii=False),
            )
        )

    for art in rmrb.get('articles', []):
        conn.execute(
            """INSERT INTO article_records
               (analysis_id, date, page_no, article_no, title,
                column_name, position, agenda_score, word_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                analysis_id,
                art.get('date', ''),
                art.get('page_no', 0),
                art.get('article_no', 0),
                art.get('title', ''),
                art.get('column', ''),
                art.get('position', ''),
                art.get('agenda_score', 0),
                art.get('word_count', 0),
            )
        )

    conn.commit()
    conn.close()
    return analysis_id


def get_previous_analysis(keywords, current_date=None):
    """查找同一关键词组的上一次分析，返回 dict 或 None。"""
    conn = get_conn()
    kw_json = json.dumps(sorted(keywords), ensure_ascii=False)

    query = "SELECT * FROM analyses WHERE keywords = ?"
    params = [kw_json]
    if current_date:
        query += " AND date < ?"
        params.append(current_date)
    query += " ORDER BY date DESC LIMIT 1"

    row = conn.execute(query, params).fetchone()
    if not row:
        like_clauses = ' AND '.join('keywords LIKE ?' for _ in keywords)
        like_params = [f'%{kw}%' for kw in keywords]
        fallback_q = f"SELECT * FROM analyses WHERE {like_clauses}"
        if current_date:
            fallback_q += " AND date < ?"
            like_params.append(current_date)
        fallback_q += " ORDER BY date DESC LIMIT 1"
        row = conn.execute(fallback_q, like_params).fetchone()

    if not row:
        conn.close()
        return None

    snapshots = conn.execute(
        "SELECT * FROM intensity_snapshots WHERE analysis_id = ? ORDER BY level",
        (row['id'],)
    ).fetchall()
    conn.close()

    row_d = dict(row)
    effective_intensity = row_d.get('weighted_max_intensity') or row_d.get('max_intensity', 0)
    return {
        'id': row_d['id'],
        'date': row_d['date'],
        'keywords': json.loads(row_d['keywords']),
        'primary_frame': row_d['primary_frame'],
        'secondary_frame': row_d['secondary_frame'],
        'max_intensity': row_d['max_intensity'],
        'weighted_max_intensity': effective_intensity,
        'engine_version': row_d.get('engine_version') or 'v2',
        'intensity_triggers': json.loads(row_d['intensity_triggers'] or '[]'),
        'ministry_level': row_d['ministry_level'],
        'ministry_list': json.loads(row_d['ministry_list'] or '[]'),
        'risk_signal': row_d['risk_signal'],
        'total_articles': row_d['total_articles'],
        'intensity_distribution': {
            s['level']: {'count': s['count'], 'pct': s['pct']}
            for s in snapshots
        },
    }


def compare_with_previous(current: dict, keywords: list) -> dict | None:
    """
    对比当前管道结果与上次分析（统一加权口径），输出趋势变化。

    current 为 run_pipeline 的部分结果，需含 rmrb.date / narrative / intensity / ministry。
    旧版（v2 之前）记录无加权强度，回退用 max_intensity 并标注口径差异。
    """
    prev = get_previous_analysis(keywords, current.get('rmrb', {}).get('date'))
    if not prev:
        return None

    curr_max = current.get('intensity', {}).get(
        'weighted_max_level',
        current.get('intensity', {}).get('max_level', 0),
    )
    prev_max = prev.get('weighted_max_intensity', 0)
    intensity_delta = curr_max - prev_max

    curr_frame = current.get('narrative', {}).get('primary_frame', '')
    prev_frame = prev.get('primary_frame', '')
    frame_drift = curr_frame != prev_frame

    curr_ml = current.get('ministry', {}).get('coordination_level', 'L0')
    prev_ml = prev.get('ministry_level') or 'L0'
    ministry_escalated = curr_ml > prev_ml

    caliber_note = None
    if prev.get('engine_version') != ENGINE_VERSION:
        caliber_note = (
            f"上期数据为 {prev.get('engine_version')} 口径（未加权/旧词库），"
            "跨版本对比仅供方向参考"
        )

    return {
        'previous_date': prev['date'],
        'previous_id': prev['id'],
        'intensity_change': f"{'+' if intensity_delta > 0 else ''}{intensity_delta}级（{prev_max}级→{curr_max}级）",
        'intensity_direction': '上升' if intensity_delta > 0 else ('下降' if intensity_delta < 0 else '持平'),
        'narrative_drift': f"从'{prev_frame}'漂移到'{curr_frame}'" if frame_drift else '框架稳定',
        'narrative_drifted': frame_drift,
        'ministry_change': f"{prev_ml}→{curr_ml}",
        'ministry_escalated': ministry_escalated,
        'previous_risk': prev['risk_signal'],
        'previous_articles': prev['total_articles'],
        'caliber_note': caliber_note,
        'trend_warning': (
            intensity_delta >= 2 or frame_drift or ministry_escalated
        ),
    }


def list_analyses(limit=20):
    """列出最近的分析历史。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, keywords, primary_frame, max_intensity, "
        "weighted_max_intensity, ministry_level, risk_signal, "
        "total_articles, engine_version, created_at "
        "FROM analyses ORDER BY date DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
