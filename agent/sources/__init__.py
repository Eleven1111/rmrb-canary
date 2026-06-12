"""
上游信号源采集层。

核心认知一说"人民日报是放大器，不是信号源"——真正的信号来自
中央文件、国务院/部委发文、理论刊物吹风。本包把这三层接进管道，
让"传导链定位"从读报哲学变成可计算变量。

  gov_policy      — 中国政府网政策文件库（国务院文件 + 部门文件）
  theory_channel  — 人民网理论频道 RSS（求是/学习时报等理论文章的代理源）

所有采集器网络失败时返回 {'error': ...}，不中断管道。
"""

from agent.sources.gov_policy import search_policy_library
from agent.sources.theory_channel import fetch_theory_articles


def collect_upstream(keywords: list[str], days: int = 365) -> dict:
    """
    采集全部上游信号源。

    返回：
      {
        central_docs:  {items, error?},   # 国务院文件
        ministry_docs: {items, error?},   # 部门文件
        theory:        {items, error?},   # 理论层（代理源）
      }
    """
    return {
        'central_docs': search_policy_library(keywords, scope='gw', days=days),
        'ministry_docs': search_policy_library(keywords, scope='bm', days=days),
        'theory': fetch_theory_articles(keywords, days=min(days, 90)),
    }
