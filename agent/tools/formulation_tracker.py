"""
Tool: 提法生命周期追踪（纯代码）

"提法"的生灭比关键词频次更接近政策本质：
  - 新提法首现（"新质生产力"2023年首次出现）= 新政策方向的最早信号
  - 提法升格（正文 → 标题 → 头版标题）= 议程地位上升
  - 提法退场（"房住不炒"式淡出）= 政策方向松动，往往不会有任何
    官方声明，只能靠缺席检测

两路追踪：
  1. 观察清单（curated）：已知重要经济提法，逐期记录出现/升格/沉寂
  2. 自动发现：当期标题中出现、历史标题语料中从未见过的 3-6 字
     n-gram，作为候选新提法（需人工确认）

注意：追踪范围限于按关键词过滤后的文章（议题内提法），
非全报扫描。生命周期判断依赖每日基线积累。
"""

import datetime
import json
import re

from agent.store.db import get_conn

WATCHLIST = [
    '新质生产力', '高质量发展', '共同富裕', '全国统一大市场',
    '双循环', '房住不炒', '新型举国体制', '耐心资本',
    '人工智能+', '低空经济', '银发经济', '首发经济',
    '平急两用', '数据要素', '内卷式竞争', '以旧换新',
    '未来产业', '专精特新', '揭榜挂帅', '链长制',
]

GENERIC_NGRAMS = {
    '坚持以', '高水平', '现代化', '一体化', '数字化', '智能化',
    '把握好', '进一步', '不断提', '持续推', '加快建', '全面推',
    '新征程', '新时代', '总书记', '了解到', '负责人', '记者从',
}

PEAK_ORDER = {'正文': 0, '标题': 1, '头版标题': 2}

CJK_RUN = re.compile(r'[一-鿿]{3,}')


def _peak_of(article: dict, phrase: str) -> str | None:
    title = article.get('title', '')
    content = article.get('content', '')
    if phrase in title:
        return '头版标题' if article.get('page_no', 99) == 1 else '标题'
    if phrase in content:
        return '正文'
    return None


def _scan_phrase(articles: list[dict], phrase: str) -> dict | None:
    """返回 phrase 在当期文章中的最高出现形态与命中文章。"""
    best = None
    hits = []
    for a in articles:
        peak = _peak_of(a, phrase)
        if peak is None:
            continue
        hits.append({
            'title': a.get('title', ''),
            'page_no': a.get('page_no', 0),
            'peak': peak,
        })
        if best is None or PEAK_ORDER[peak] > PEAK_ORDER[best]:
            best = peak
    if best is None:
        return None
    return {'peak': best, 'hits': hits[:5], 'hit_count': len(hits)}


def _extract_title_ngrams(titles: list[str], n_min=3, n_max=6) -> dict:
    """提取标题 n-gram → 出现该 n-gram 的标题数。"""
    doc_freq = {}
    for title in titles:
        seen_in_title = set()
        for run in CJK_RUN.findall(title):
            for n in range(n_min, n_max + 1):
                for i in range(len(run) - n + 1):
                    seen_in_title.add(run[i:i + n])
        for gram in seen_in_title:
            doc_freq[gram] = doc_freq.get(gram, 0) + 1
    return doc_freq


def _maximal_candidates(candidates: dict) -> dict:
    """同频次时只保留最长串（"质生产力"⊂"新质生产力"则丢弃前者）。"""
    kept = {}
    grams = sorted(candidates, key=len, reverse=True)
    for gram in grams:
        if any(gram in longer and candidates[longer] >= candidates[gram]
               for longer in kept):
            continue
        kept[gram] = candidates[gram]
    return kept


def discover_new_formulations(articles: list[dict], min_titles: int = 2) -> list[dict]:
    """
    自动发现候选新提法：当期 ≥min_titles 个标题中出现、
    历史标题语料中从未出现的 n-gram。
    """
    titles = [a.get('title', '') for a in articles if a.get('title')]
    if not titles:
        return []

    doc_freq = _extract_title_ngrams(titles)
    candidates = {
        g: df for g, df in doc_freq.items()
        if df >= min_titles and g not in GENERIC_NGRAMS
    }
    if not candidates:
        return []

    conn = get_conn()
    historical_titles = [
        r['title'] for r in conn.execute(
            'SELECT DISTINCT title FROM article_records'
        ).fetchall() if r['title']
    ]
    known = {
        r['phrase'] for r in conn.execute(
            'SELECT phrase FROM formulations'
        ).fetchall()
    }
    conn.close()

    fresh = {}
    for gram, df in candidates.items():
        if gram in known or gram in WATCHLIST:
            continue
        if any(gram in t for t in historical_titles):
            continue
        fresh[gram] = df

    fresh = _maximal_candidates(fresh)
    return [
        {'phrase': g, 'title_count': df, 'note': '候选新提法，需人工确认'}
        for g, df in sorted(fresh.items(), key=lambda x: -x[1])
    ][:10]


def track_formulations(articles: list[dict], date: str) -> dict:
    """
    逐期追踪提法生命周期，写入 formulations / formulation_sightings 表。

    返回：
      {
        active: [{phrase, peak, hit_count, escalated, first_seen}],
        newly_discovered: [{phrase, title_count}],
        retiring: [{phrase, last_seen, days_silent}],
        data_note: str,
      }
    """
    conn = get_conn()
    now_iso = datetime.datetime.now().isoformat()

    tracked = {
        r['phrase']: dict(r) for r in conn.execute(
            'SELECT * FROM formulations'
        ).fetchall()
    }
    phrases = list(dict.fromkeys(WATCHLIST + list(tracked.keys())))

    active = []
    for phrase in phrases:
        scan = _scan_phrase(articles, phrase)
        if scan is None:
            continue

        row = tracked.get(phrase)
        escalated = False
        if row is None:
            conn.execute(
                """INSERT INTO formulations
                   (phrase, first_seen, first_context, last_seen,
                    peak_status, status, watch, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (phrase, date,
                 json.dumps(scan['hits'][0], ensure_ascii=False),
                 date, scan['peak'],
                 1 if phrase in WATCHLIST else 0, now_iso),
            )
            first_seen = date
        else:
            prev_peak = row.get('peak_status') or '正文'
            if PEAK_ORDER[scan['peak']] > PEAK_ORDER.get(prev_peak, 0):
                escalated = True
                new_peak = scan['peak']
            else:
                new_peak = prev_peak
            conn.execute(
                """UPDATE formulations
                   SET last_seen = ?, peak_status = ?, status = 'active', updated_at = ?
                   WHERE phrase = ?""",
                (date, new_peak, now_iso, phrase),
            )
            first_seen = row.get('first_seen') or date

        form_id = conn.execute(
            'SELECT id FROM formulations WHERE phrase = ?', (phrase,)
        ).fetchone()['id']
        for hit in scan['hits']:
            conn.execute(
                """INSERT OR IGNORE INTO formulation_sightings
                   (formulation_id, date, in_title, page_no, article_title)
                   VALUES (?, ?, ?, ?, ?)""",
                (form_id, date,
                 1 if hit['peak'] != '正文' else 0,
                 hit['page_no'], hit['title']),
            )

        active.append({
            'phrase': phrase,
            'peak': scan['peak'],
            'hit_count': scan['hit_count'],
            'escalated': escalated,
            'first_seen': first_seen,
        })

    # 退场检测：曾活跃但 90 天未见
    current_dt = datetime.datetime.strptime(date, '%Y%m%d')
    retiring = []
    for phrase, row in tracked.items():
        last_seen = row.get('last_seen')
        if not last_seen or phrase in {a['phrase'] for a in active}:
            continue
        days_silent = (current_dt - datetime.datetime.strptime(last_seen, '%Y%m%d')).days
        if days_silent >= 90:
            conn.execute(
                "UPDATE formulations SET status = 'retired', updated_at = ? WHERE phrase = ?",
                (now_iso, phrase),
            )
            retiring.append({
                'phrase': phrase,
                'last_seen': last_seen,
                'days_silent': days_silent,
            })
        elif days_silent >= 30:
            conn.execute(
                "UPDATE formulations SET status = 'dormant', updated_at = ? WHERE phrase = ?",
                (now_iso, phrase),
            )

    conn.commit()
    conn.close()

    newly = discover_new_formulations(articles)

    data_points = len(tracked)
    data_note = (
        '提法生命周期依赖每日基线积累，当前历史提法记录 '
        f'{data_points} 条' + ('，退场/升格判断可信。' if data_points >= 10
                              else '，样本不足，首现/退场判断仅供参考。')
    )

    return {
        'active': sorted(active, key=lambda x: -PEAK_ORDER[x['peak']]),
        'newly_discovered': newly,
        'retiring': retiring,
        'data_note': data_note,
    }
