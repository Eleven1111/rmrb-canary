"""
分析师判断沉淀 — 最值钱的产出不该随报告蒸发。

LLM 报告中的三元组、反直觉信号、张力判断结构化入库；
下次分析同议题时作为"上期分析师观点"注入上下文，
形成跨期对话而非每次失忆重来。
"""

import datetime
import json

from agent.store.db import get_conn


def record_judgment(
    keywords: list[str],
    date: str,
    judgment: str,
    triples: list[dict] = None,
    surprising_signal: str = '',
    tension: str = '',
    analysis_id: int = None,
) -> int:
    """写入一条分析师判断，返回 judgment_id。"""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO judgments
           (analysis_id, keywords, date, triples,
            surprising_signal, tension, judgment, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            analysis_id,
            json.dumps(sorted(keywords), ensure_ascii=False),
            date,
            json.dumps(triples or [], ensure_ascii=False),
            surprising_signal,
            tension,
            judgment,
            datetime.datetime.now().isoformat(),
        ),
    )
    conn.commit()
    judgment_id = cur.lastrowid
    conn.close()
    return judgment_id


def get_recent_judgments(keywords: list[str], limit: int = 3) -> list[dict]:
    """读取同议题最近的分析师判断（供管道注入上下文）。"""
    conn = get_conn()
    kw_json = json.dumps(sorted(keywords), ensure_ascii=False)
    rows = conn.execute(
        """SELECT * FROM judgments WHERE keywords = ?
           ORDER BY date DESC, id DESC LIMIT ?""",
        (kw_json, limit),
    ).fetchall()

    if not rows:
        like_clauses = ' AND '.join('keywords LIKE ?' for _ in keywords)
        like_params = [f'%{kw}%' for kw in keywords] + [limit]
        rows = conn.execute(
            f"""SELECT * FROM judgments WHERE {like_clauses}
                ORDER BY date DESC, id DESC LIMIT ?""",
            like_params,
        ).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d['triples'] = json.loads(d.get('triples') or '[]')
        result.append(d)
    return result
