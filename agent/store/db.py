"""
SQLite 历史存储层 — rmrb-canary agent（schema v2 + P0 身份层）

表：
  analyses             — 每次分析摘要（v3 起存加权口径，engine_version 区分）
  intensity_snapshots  — 七级话语强度分布快照
  article_records      — 每篇文章核心字段（含标题，供提法追踪回溯）
  run_log              — 每次运行的日志（不参与统计样本）
  predictions          — 预测台账（store/ledger.py 维护）
  judgments            — 分析师判断沉淀（store/judgments.py 维护）
  formulations         — 提法生命周期（tools/formulation_tracker.py 维护）
  formulation_sightings— 提法逐日出现记录

P0 身份层（方案 F02 / F03 / F10 / §8.2）——三条，都对应真实缺陷：

  F10：主题身份由 topic_id 固定，历史查询不再用 `keywords LIKE '%光伏%'` 回退。
       原实现里，一次 ["光伏"] 的分析会把 ["储能","光伏"] 甚至 ["光伏发电"]
       的记录当成自己的上一期 —— 两个不同主题的时间线被静默拼成一条。

  F02：幂等键 (topic_id, topic_version, date, algo_version)。同一主题、同一口径、
       同一期重复运行 **替换** 记录而不是追加。原实现每跑一次就多一行，
       沉默检测和滚动趋势会把同一期算成多个样本。每次运行仍单独记入 run_log。

  F03：as_of 截面。历史回放时所有查询必须有日期上界，否则读到的是"未来"。
       原实现的 silence_detector / rolling_trend 完全没有上界。

旧记录（升级前写入的行）标记 algo_version='v1-legacy'、topic_version='legacy'，
topic_id 由其 keywords 反算补齐 —— 它们仍可被查到并附口径警告，但幂等唯一索引
只作用于当前算法版本的行，不去动历史数据。

数据库位置：~/.rmrb_canary/history.db
可用环境变量 RMRB_CANARY_DB 覆盖（测试隔离用）。
"""

import sqlite3
import json
import os
import sys
import datetime

from agent.versioning import ALGO_VERSION, LEGACY_ALGO_VERSION, make_topic_id

ENGINE_VERSION = 'v3'

# 旧记录的主题口径版本。与 LEGACY_ALGO_VERSION 分开：
# 前者说"这条记录的主题定义未知"，后者说"这条记录的算法版本未知"。
LEGACY_TOPIC_VERSION = 'legacy'

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

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT,
    topic_version TEXT,
    date TEXT,
    as_of TEXT,
    algo_version TEXT,
    quality_status TEXT,
    outcome TEXT,
    ran_at TEXT NOT NULL
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
    # P0 身份层
    ('analyses', 'topic_id', 'TEXT'),
    ('analyses', 'topic_key', 'TEXT'),
    ('analyses', 'topic_version', 'TEXT'),
    ('analyses', 'algo_version', 'TEXT'),
    ('analyses', 'as_of', 'TEXT'),
    ('analyses', 'quality_status', 'TEXT'),
    ('analyses', 'total_scanned', 'INTEGER'),
    ('analyses', 'updated_at', 'TEXT'),
]

# 幂等唯一索引只覆盖当前算法版本的行。
# 用 partial index 而不是全表唯一：旧库里可能已经存在同日重复行，
# 全表唯一会让整个迁移失败，等于让历史数据把新装置卡死。
IDENTITY_INDEX_SQL = (
    'CREATE UNIQUE INDEX IF NOT EXISTS idx_analyses_identity '
    'ON analyses(topic_id, topic_version, date, algo_version) '
    f"WHERE algo_version = '{ALGO_VERSION}'"
)


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
    _backfill_identity(conn)
    try:
        conn.execute(IDENTITY_INDEX_SQL)
    except sqlite3.OperationalError as e:
        # 说出来。静默跳过等于"幂等已生效"这句话变成假的。
        print(f'[db] 幂等唯一索引未能建立，同期重跑会重复计样本：{e}',
              file=sys.stderr)
    conn.commit()


def _backfill_identity(conn):
    """
    给升级前写入的行补上身份字段。旧行的数值结论一个字都不改。

    topic_id 由 keywords 反算：旧行的 keywords 已经是排序后的 JSON 列表，
    与 make_topic_id 的归一化口径一致，所以反算得到的 ID 与新行可对齐 ——
    旧记录因此仍能被查到，只是带着 v1-legacy 的口径标签。
    """
    conn.execute(
        'UPDATE analyses SET algo_version = ? WHERE algo_version IS NULL',
        (LEGACY_ALGO_VERSION,),
    )
    conn.execute(
        'UPDATE analyses SET topic_version = ? WHERE topic_version IS NULL',
        (LEGACY_TOPIC_VERSION,),
    )
    rows = conn.execute(
        'SELECT id, keywords FROM analyses WHERE topic_id IS NULL'
    ).fetchall()
    for row in rows:
        try:
            kws = json.loads(row['keywords'] or '[]')
        except (ValueError, TypeError):
            kws = []
        conn.execute('UPDATE analyses SET topic_id = ? WHERE id = ?',
                     (make_topic_id(kws), row['id']))


def topic_identity(result: dict) -> dict:
    """
    从管道结果中取出主题身份。事件层已解析出 topic 时用它，
    否则由关键词生成临时口径（topic_version='adhoc'）—— 临时口径不与
    正式主题的历史混比，因为 topic_version 参与身份键。
    """
    topic = result.get('topic') or {}
    keywords = result.get('keywords', [])
    return {
        'topic_id': topic.get('topic_id') or make_topic_id(keywords),
        'topic_key': topic.get('topic_key'),
        'topic_version': topic.get('topic_version') or 'adhoc',
    }


def save_analysis(result: dict) -> int:
    """将管道结果写入数据库，返回 analysis_id。见 save_analysis_detailed。"""
    return save_analysis_detailed(result)['analysis_id']


def save_analysis_detailed(result: dict) -> dict:
    """
    将管道结果（加权口径）写入数据库，返回 {analysis_id, action, topic_id, ...}。

    F02 幂等：同一 (topic_id, topic_version, date, algo_version) 已有记录时
    **替换**该行并重建其子表，不新增样本行；action 返回 'replaced'。
    这样同一期重跑多少次，沉默检测和滚动趋势看到的都仍是一个样本。

    参数 result 为 agent.run_pipeline 的输出 dict，
    读取 narrative / intensity / ministry / rmrb / risk_window / topic 等字段。
    """
    conn = get_conn()
    now = datetime.datetime.now().isoformat()

    narrative = result.get('narrative', {})
    intensity = result.get('intensity', {})
    ministry = result.get('ministry', {})
    rmrb = result.get('rmrb', {})
    risk = result.get('risk_window', {})
    relevance_stats = result.get('relevance', {}).get('stats', {})
    quality = result.get('fetch_quality') or {}

    identity = topic_identity(result)
    date = rmrb.get('date', '')
    report_copy = {k: v for k, v in result.items() if k != 'full_texts'}

    columns = (
        'date', 'keywords', 'primary_frame', 'secondary_frame',
        'max_intensity', 'weighted_max_intensity', 'intensity_triggers',
        'ministry_level', 'ministry_list',
        'risk_signal', 'total_articles', 'avg_relevance',
        'transmission_stage', 'engine_version', 'report_json',
        'topic_id', 'topic_key', 'topic_version', 'algo_version',
        'as_of', 'quality_status', 'total_scanned', 'updated_at',
    )
    values = (
        date,
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
        identity['topic_id'],
        identity['topic_key'],
        identity['topic_version'],
        ALGO_VERSION,
        result.get('as_of') or date,
        quality.get('status'),
        quality.get('total_scanned_articles'),
        now,
    )

    existing = conn.execute(
        'SELECT id FROM analyses WHERE topic_id = ? AND topic_version = ? '
        'AND date = ? AND algo_version = ?',
        (identity['topic_id'], identity['topic_version'], date, ALGO_VERSION),
    ).fetchone()

    if existing:
        analysis_id = existing['id']
        assignments = ', '.join(f'{c}=?' for c in columns)
        conn.execute(f'UPDATE analyses SET {assignments} WHERE id=?',
                     values + (analysis_id,))
        # 子表整体重建：留着旧行会让强度分布变成两期叠加。
        conn.execute('DELETE FROM intensity_snapshots WHERE analysis_id = ?',
                     (analysis_id,))
        conn.execute('DELETE FROM article_records WHERE analysis_id = ?',
                     (analysis_id,))
        action = 'replaced'
    else:
        placeholders = ', '.join('?' for _ in columns)
        cur = conn.execute(
            f"INSERT INTO analyses ({', '.join(columns)}, created_at) "
            f'VALUES ({placeholders}, ?)',
            values + (now,),
        )
        analysis_id = cur.lastrowid
        action = 'inserted'

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
    return {'analysis_id': analysis_id, 'action': action,
            'topic_id': identity['topic_id'],
            'topic_version': identity['topic_version'],
            'algo_version': ALGO_VERSION}


def log_run(topic_id, date, as_of, quality_status, outcome,
            topic_version='adhoc', algo_version=ALGO_VERSION):
    """
    记录一次运行。与 analyses 分开：运行日志按次追加，
    analyses 按 (主题, 期, 版本) 幂等 —— 跑了几次和有几个样本是两件事（§8.2）。
    """
    conn = get_conn()
    conn.execute(
        'INSERT INTO run_log (topic_id, topic_version, date, as_of, algo_version, '
        'quality_status, outcome, ran_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (topic_id, topic_version, date, as_of, algo_version, quality_status,
         outcome, datetime.datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_topic_series(topic_id: str, as_of: str, limit: int = 90,
                     topic_version: str = None,
                     algo_version: str = ALGO_VERSION) -> list[dict]:
    """
    取同一主题、严格早于 as_of 的历史序列，**按日期升序**返回。

    F03：`date < as_of`，历史回放不会读到 as_of 当天及之后的记录。
    F01：升序是趋势方向计算的前提，调用方不得再自行倒序。
    topic_version=None 表示不限口径版本（列全部时间线）；
    要做逐期比较时必须传入具体版本，跨口径的两期不可比。
    """
    conn = get_conn()
    query = ('SELECT id, date, topic_id, topic_version, algo_version, primary_frame, '
             'COALESCE(weighted_max_intensity, max_intensity) AS intensity, '
             'max_intensity, ministry_level, risk_signal, total_articles, '
             'quality_status, total_scanned FROM analyses '
             'WHERE topic_id = ? AND algo_version = ? AND date < ?')
    params = [topic_id, algo_version, as_of]
    if topic_version is not None:
        query += ' AND topic_version = ?'
        params.append(topic_version)
    query += ' ORDER BY date ASC LIMIT ?'
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_previous_analysis(keywords, current_date=None, topic_id=None):
    """
    查找同一主题的上一次分析，返回 dict 或 None。

    F10：按 topic_id 精确匹配，**不做关键词 LIKE 模糊回退**。
    模糊回退会把 ["储能","光伏"] 的历史当成 ["光伏"] 的上一期 ——
    那不是"找不到时退而求其次"，那是把另一个主题的时间线接到本主题上。
    宁可返回 None（首次分析），也不要接错线。
    """
    conn = get_conn()
    tid = topic_id or make_topic_id(keywords)

    query = "SELECT * FROM analyses WHERE topic_id = ?"
    params = [tid]
    if current_date:
        query += " AND date < ?"
        params.append(current_date)
    query += " ORDER BY date DESC LIMIT 1"

    row = conn.execute(query, params).fetchone()

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
        'topic_id': row_d.get('topic_id'),
        'topic_version': row_d.get('topic_version'),
        'algo_version': row_d.get('algo_version') or LEGACY_ALGO_VERSION,
        'quality_status': row_d.get('quality_status'),
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
    identity = topic_identity(current)
    prev = get_previous_analysis(keywords, current.get('rmrb', {}).get('date'),
                                 topic_id=identity['topic_id'])
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

    # 口径警告分两层：engine_version 说采集/加权口径，algo_version 说判定语义。
    # 两者任一不同，这次"上升/下降"就可能来自换版本而不是议题本身变化。
    caliber_reasons = []
    if prev.get('engine_version') != ENGINE_VERSION:
        caliber_reasons.append(
            f"上期为 {prev.get('engine_version')} 采集口径（未加权/旧词库）")
    if prev.get('algo_version') != ALGO_VERSION:
        caliber_reasons.append(
            f"上期为 {prev.get('algo_version')} 判定口径")
    prev_tv = prev.get('topic_version')
    if prev_tv and prev_tv != identity['topic_version']:
        caliber_reasons.append(
            f"上期主题口径为 {prev_tv}，本期为 {identity['topic_version']}")
    caliber_note = (
        '；'.join(caliber_reasons) + ' —— 跨口径对比仅供方向参考，'
        '变化可能来自版本差异而非议题变化'
    ) if caliber_reasons else None

    return {
        'previous_date': prev['date'],
        'previous_id': prev['id'],
        'topic_id': identity['topic_id'],
        'previous_algo_version': prev.get('algo_version'),
        'previous_topic_version': prev_tv,
        'comparable': not caliber_reasons,
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


def list_analyses(limit=20, algo_version=None):
    """
    列出最近的分析历史。algo_version 给定时只列该版本的记录
    （传 ALGO_VERSION 可排除升级前的 v1-legacy 行）。
    """
    cols = ("id, date, topic_id, topic_key, topic_version, algo_version, keywords, "
            "primary_frame, max_intensity, weighted_max_intensity, ministry_level, "
            "risk_signal, total_articles, quality_status, engine_version, created_at")
    conn = get_conn()
    if algo_version:
        rows = conn.execute(
            f"SELECT {cols} FROM analyses WHERE algo_version = ? "
            "ORDER BY date DESC LIMIT ?", (algo_version, limit)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM analyses ORDER BY date DESC LIMIT ?",
            (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
