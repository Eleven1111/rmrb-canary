"""
告警存储（P2，对应方案 §7.2）

去重键是"主题 + 政策事件 + 关键变化版本"，**不是文章标题**。
转载与重复抓取不重复通知；责任、范围、期限有实质变化则产生更新。

投递状态与通知状态分开：发送失败保留待投递，收到发送回执后才标记已通知。
没有回执就写"已通知"，等于用一次尝试冒充一次送达。
"""

import datetime
import json
import os
import sqlite3

DB_DIR = os.path.expanduser('~/.rmrb_sentinel')
DB_PATH = os.path.join(DB_DIR, 'alerts.db')

# 方案 §7.2 的状态流转。允许的迁移写死在这里，非法迁移会被拒绝。
STATES = ('观察', '待核验', '已确认', '待投递', '已通知', '跟踪', '关闭', '撤回')
ALLOWED_TRANSITIONS = {
    '观察': {'待核验', '关闭', '撤回'},
    '待核验': {'已确认', '观察', '关闭', '撤回'},
    '已确认': {'待投递', '跟踪', '关闭', '撤回'},
    '待投递': {'已通知', '待投递', '关闭', '撤回'},
    '已通知': {'跟踪', '关闭', '撤回'},
    '跟踪': {'待核验', '已确认', '关闭', '撤回'},
    '关闭': set(),
    '撤回': set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    topic_version TEXT,
    event_fingerprint TEXT NOT NULL,
    change_version TEXT NOT NULL,
    state TEXT NOT NULL,
    priority TEXT,
    reason TEXT,
    payload_json TEXT NOT NULL,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    confirmed_at TEXT,
    notified_at TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    is_replay INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_dedupe ON alerts(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_alert_state ON alerts(state);
CREATE INDEX IF NOT EXISTS idx_alert_topic ON alerts(topic_id, topic_version);
"""


def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_alert(alert: dict) -> dict:
    """
    按 dedupe_key 落库。

    - 键已存在且 change_version 相同 → **不重复通知**，只累加一次观察。
    - 键已存在但 change_version 变化 → 记为更新，退回"待核验"重新走确认流程。
    - 键不存在 → 新建。

    返回 {'action': 'created'|'duplicate'|'updated', 'alert_id', 'state'}
    """
    now = datetime.datetime.now().isoformat()
    conn = get_conn()
    row = conn.execute('SELECT * FROM alerts WHERE dedupe_key = ?',
                       (alert['dedupe_key'],)).fetchone()

    if row is None:
        cur = conn.execute(
            'INSERT INTO alerts (dedupe_key, topic_id, topic_version, event_fingerprint, '
            'change_version, state, priority, reason, payload_json, published_at, '
            'first_seen_at, is_replay, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (alert['dedupe_key'], alert['topic_id'], alert.get('topic_version'),
             alert['event_fingerprint'], alert['change_version'], alert.get('state', '观察'),
             alert.get('priority'), alert.get('reason'),
             json.dumps(alert.get('payload', {}), ensure_ascii=False, default=str),
             alert.get('published_at'), alert.get('first_seen_at') or now,
             1 if alert.get('is_replay') else 0, now, now))
        alert_id = cur.lastrowid
        conn.execute('INSERT INTO alert_transitions (alert_id, from_state, to_state, note, at) '
                     'VALUES (?,?,?,?,?)',
                     (alert_id, None, alert.get('state', '观察'), '新建', now))
        action, state = 'created', alert.get('state', '观察')
    elif row['change_version'] == alert['change_version']:
        # 同一事件、同一变化版本：重复抓取或转载，不重复通知。
        conn.execute('UPDATE alerts SET updated_at = ? WHERE id = ?', (now, row['id']))
        action, alert_id, state = 'duplicate', row['id'], row['state']
    else:
        conn.execute(
            'UPDATE alerts SET change_version=?, state=?, priority=?, reason=?, '
            'payload_json=?, published_at=?, notified_at=NULL, updated_at=? WHERE id=?',
            (alert['change_version'], '待核验', alert.get('priority'), alert.get('reason'),
             json.dumps(alert.get('payload', {}), ensure_ascii=False, default=str),
             alert.get('published_at'), now, row['id']))
        conn.execute('INSERT INTO alert_transitions (alert_id, from_state, to_state, note, at) '
                     'VALUES (?,?,?,?,?)',
                     (row['id'], row['state'], '待核验', '关键变化版本更新', now))
        action, alert_id, state = 'updated', row['id'], '待核验'

    conn.commit()
    conn.close()
    return {'action': action, 'alert_id': alert_id, 'state': state}


def transition(alert_id: int, to_state: str, note: str = '') -> dict:
    """状态迁移。非法迁移直接拒绝，不静默改状态。"""
    if to_state not in STATES:
        return {'ok': False, 'error': f'未知状态 {to_state}'}
    now = datetime.datetime.now().isoformat()
    conn = get_conn()
    row = conn.execute('SELECT * FROM alerts WHERE id = ?', (alert_id,)).fetchone()
    if not row:
        conn.close()
        return {'ok': False, 'error': f'告警 {alert_id} 不存在'}

    if to_state not in ALLOWED_TRANSITIONS.get(row['state'], set()):
        conn.close()
        return {'ok': False, 'error': f'不允许从「{row["state"]}」迁移到「{to_state}」',
                'from_state': row['state']}

    extra = ''
    params = [to_state, now, alert_id]
    if to_state == '已确认':
        extra = ', confirmed_at = ?'
        params = [to_state, now, now, alert_id]
    conn.execute(f'UPDATE alerts SET state = ?, updated_at = ?{extra} WHERE id = ?', params)
    conn.execute('INSERT INTO alert_transitions (alert_id, from_state, to_state, note, at) '
                 'VALUES (?,?,?,?,?)', (alert_id, row['state'], to_state, note, now))
    conn.commit()
    conn.close()
    return {'ok': True, 'from_state': row['state'], 'to_state': to_state}


def mark_delivery_attempt(alert_id: int, error: str = None) -> dict:
    """
    记录一次投递尝试。**尝试不等于送达** —— 状态停在"待投递"，
    只有收到回执才由 record_receipt 推进到"已通知"。
    """
    now = datetime.datetime.now().isoformat()
    conn = get_conn()
    conn.execute(
        'UPDATE alerts SET delivery_attempts = delivery_attempts + 1, last_error = ?, '
        'updated_at = ? WHERE id = ?', (error, now, alert_id))
    conn.commit()
    row = conn.execute('SELECT state, delivery_attempts FROM alerts WHERE id = ?',
                       (alert_id,)).fetchone()
    conn.close()
    return {'state': row['state'], 'delivery_attempts': row['delivery_attempts'],
            'note': '投递尝试已记录；未收到回执前状态仍为待投递。'}


def record_receipt(alert_id: int, receipt_id: str) -> dict:
    """收到发送回执后才标记已通知，并记录 notified_at 供通知延迟统计。"""
    now = datetime.datetime.now().isoformat()
    result = transition(alert_id, '已通知', note=f'收到回执 {receipt_id}')
    if not result.get('ok'):
        return result
    conn = get_conn()
    conn.execute('UPDATE alerts SET notified_at = ? WHERE id = ?', (now, alert_id))
    conn.commit()
    conn.close()
    return {**result, 'notified_at': now, 'receipt_id': receipt_id}


def pending_deliveries() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM alerts WHERE state = '待投递' ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_alerts(topic_id: str = None, state: str = None, limit: int = 50) -> list[dict]:
    conn = get_conn()
    query, params = 'SELECT * FROM alerts WHERE 1=1', []
    if topic_id:
        query += ' AND topic_id = ?'
        params.append(topic_id)
    if state:
        query += ' AND state = ?'
        params.append(state)
    query += ' ORDER BY updated_at DESC LIMIT ?'
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def transitions_of(alert_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        'SELECT from_state, to_state, note, at FROM alert_transitions '
        'WHERE alert_id = ? ORDER BY id ASC', (alert_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
