"""
Tool: 人民日报数据采集
封装 scripts/rmrb_fetch.py，返回结构化 summary dict。
"""

import sys
import os

# 让 scripts/ 下的模块可导入
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'scripts')
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import rmrb_fetch


def fetch_rmrb(keywords: list[str], date: str | None = None, cache=None) -> dict:
    """
    采集人民日报最新一期（或指定日期），按关键词过滤，返回结构化 summary。

    参数：
      keywords: 关键词列表，如 ["新能源", "光伏"]
      date: 可选，格式 YYYYMMDD，默认自动获取最新一期
      cache: 可选的 agent.store.doccache.DocumentCache。传入后按**整期**缓存：
        多个主题分析同一期只抓一次，关键词过滤在本地做。这样"同一期"对不同
        主题就是同一份证据，而不是两次抓取的两个快照。

    返回：
      summary dict，包含 step0_narrative / step1_agenda / step6_intensity /
      ministry_signals / articles / full_texts 等字段
    """
    output_dir = os.path.expanduser('~/.rmrb_canary/data')

    if cache is not None:
        year, month, day = rmrb_fetch.resolve_date(date)
        cached = cache.get_issue(f'{year}{month}{day}')
        if cached:
            return _filter_summary(cached, keywords)

    summary = rmrb_fetch.fetch(
        date_str=date,
        keywords=None if cache is not None else keywords,
        output_dir=output_dir,
    )
    if cache is not None:
        cache.put_issue(summary.get('date', ''), summary)
        return _filter_summary(summary, keywords)
    return summary


def _filter_summary(summary: dict, keywords: list[str]) -> dict:
    """在整期缓存上按关键词本地过滤，不重复抓取。"""
    if not keywords:
        return summary
    kept = [a for a in summary.get('full_texts', [])
            if any(k in (a.get('title') or '') or k in (a.get('content') or '')
                   for k in keywords)]
    titles = {a.get('title') for a in kept}
    return {
        **summary,
        'keywords_filter': keywords,
        'total_articles': len(kept),
        'full_texts': kept,
        'articles': [a for a in summary.get('articles', []) if a.get('title') in titles],
        'filtered_from_cache': True,
    }
