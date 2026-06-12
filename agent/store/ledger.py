"""
预测台账 — 让系统积累自己的信誉记录。

没有命中率记录的预警系统永远只是观点。每次分析的窗口预测
自动落账；到期未复盘的预测自动标记 due_review 并在下次分析时
浮出，由分析师裁定 hit / miss；累计输出命中率与平均提前量。

状态机：open → due_review →（hit | miss | void）
"""

import datetime
import json

from agent.store.db import get_conn


def record_prediction(analysis_id: int, result: dict) -> int | None:
    """
    从管道结果落一条窗口预测。强度 <4 且风险灯绿时不落账（无预测价值）。

    返回 prediction_id 或 None。
    """
    risk = result.get('risk_window', {})
    intensity = result.get('intensity', {})
    level = intensity.get('weighted_max_level', intensity.get('max_level', 1))
    emoji = risk.get('risk_emoji', '🟢')

    if level < 4 and emoji == '🟢':
        return None

    date_str = result.get('rmrb', {}).get('date', '')
    if not date_str:
        return None
    made_on = datetime.datetime.strptime(date_str, '%Y%m%d')

    lo, hi = risk.get('adjusted_window_months', (3, 6))
    window_start = made_on + datetime.timedelta(days=int(lo * 30.4))
    window_end = made_on + datetime.timedelta(days=int(hi * 30.4))

    ministry_level = result.get('ministry', {}).get('coordination_level', 'L0')
    statement = (
        f"基于强度{level}级+协同{ministry_level}，预测该议题在 "
        f"{window_start.strftime('%Y-%m-%d')} 至 {window_end.strftime('%Y-%m-%d')} "
        f"之间出现实质监管行动（{risk.get('adjusted_window_label', '')}）"
    )

    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO predictions
           (analysis_id, keywords, made_on, window_start, window_end,
            risk_level, intensity, ministry_level, statement, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (
            analysis_id,
            json.dumps(sorted(result.get('keywords', [])), ensure_ascii=False),
            date_str,
            window_start.strftime('%Y%m%d'),
            window_end.strftime('%Y%m%d'),
            risk.get('risk_level', ''),
            level,
            ministry_level,
            statement,
            datetime.datetime.now().isoformat(),
        ),
    )
    conn.commit()
    prediction_id = cur.lastrowid
    conn.close()
    return prediction_id


def check_due_predictions(keywords: list[str] = None) -> list[dict]:
    """
    将窗口已过期的 open 预测标记为 due_review，返回待复盘列表。

    keywords 提供时只检查该议题（管道内调用）；否则全量（CLI 用）。
    """
    conn = get_conn()
    today = datetime.datetime.now().strftime('%Y%m%d')

    params = [today]
    query = "SELECT * FROM predictions WHERE status = 'open' AND window_end < ?"
    if keywords:
        query += " AND keywords = ?"
        params.append(json.dumps(sorted(keywords), ensure_ascii=False))

    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    for row in rows:
        conn.execute(
            "UPDATE predictions SET status = 'due_review' WHERE id = ?",
            (row['id'],),
        )
        row['status'] = 'due_review'
    conn.commit()

    pending = [dict(r) for r in conn.execute(
        "SELECT * FROM predictions WHERE status = 'due_review'"
        + (" AND keywords = ?" if keywords else ""),
        ([json.dumps(sorted(keywords), ensure_ascii=False)] if keywords else []),
    ).fetchall()]
    conn.close()
    return pending


def resolve_prediction(prediction_id: int, outcome: str, note: str = '') -> dict:
    """
    分析师裁定预测结果。outcome ∈ {hit, miss, void}。

    hit  = 窗口内发生实质行动；miss = 窗口过后未发生；
    void = 预测前提失效（如议题定义变化），不计入命中率。
    """
    if outcome not in ('hit', 'miss', 'void'):
        return {'ok': False, 'error': f'无效结果: {outcome}，须为 hit/miss/void'}

    conn = get_conn()
    row = conn.execute(
        'SELECT id, status FROM predictions WHERE id = ?', (prediction_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {'ok': False, 'error': f'预测 #{prediction_id} 不存在'}

    conn.execute(
        """UPDATE predictions
           SET status = ?, resolved_at = ?, resolution_note = ?
           WHERE id = ?""",
        (outcome, datetime.datetime.now().isoformat(), note, prediction_id),
    )
    conn.commit()
    conn.close()
    return {'ok': True, 'prediction_id': prediction_id, 'outcome': outcome}


def ledger_stats() -> dict:
    """累计命中率统计。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT status, COUNT(*) c FROM predictions GROUP BY status"
    ).fetchall()]
    conn.close()

    by_status = {r['status']: r['c'] for r in rows}
    hits = by_status.get('hit', 0)
    misses = by_status.get('miss', 0)
    resolved = hits + misses

    return {
        'total': sum(by_status.values()),
        'open': by_status.get('open', 0),
        'due_review': by_status.get('due_review', 0),
        'hit': hits,
        'miss': misses,
        'void': by_status.get('void', 0),
        'hit_rate': round(hits / resolved, 2) if resolved else None,
        'note': (
            f'已复盘 {resolved} 条，命中率 {hits}/{resolved}'
            if resolved else '尚无已复盘预测，命中率不可用'
        ),
    }


def list_predictions(status: str = None, limit: int = 30) -> list[dict]:
    """列出预测记录（CLI 用）。"""
    conn = get_conn()
    if status:
        rows = conn.execute(
            'SELECT * FROM predictions WHERE status = ? ORDER BY id DESC LIMIT ?',
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM predictions ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
