"""
Tool: 多源媒体数据采集
封装 scripts/media_fetch.py，返回 cross_validation dict。
"""

import sys
import os

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'scripts')
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import media_fetch


def fetch_media_sources(keywords: list[str], days: int = 7, rmrb_summary: dict = None) -> dict:
    """
    采集人民网RSS官媒聚合 + 微博/百度热搜，返回交叉验证数据。

    参数：
      keywords: 关键词列表
      days: 回溯天数
      rmrb_summary: 人民日报 summary（用于交叉验证中的人民日报计数）

    返回：
      cross_validation dict，含 official_media / weibo / cross_analysis
    """
    official = media_fetch.fetch_official_media(keywords, days=days)
    weibo = media_fetch.fetch_weibo_hot(keywords)

    # build_cross_validation 需要 rmrb_summary 作为第一个参数
    if rmrb_summary is None:
        rmrb_summary = {'total_articles': 0, 'keywords_filter': keywords}

    cross = media_fetch.build_cross_validation(
        rmrb_summary, official, weibo, keywords,
    )
    return cross
