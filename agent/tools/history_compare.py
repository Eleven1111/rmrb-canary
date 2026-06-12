"""
Tool: 历史对比
读取 SQLite 历史数据，对比当前分析与上期，输出趋势变化。
"""

from agent.store.db import compare_with_previous, get_previous_analysis, list_analyses


def compare_history(current_result: dict, keywords: list[str]) -> dict:
    """
    对比当前管道结果与最近一次同议题历史分析（统一加权口径）。

    current_result 需含 rmrb / narrative / intensity / ministry 字段。
    返回趋势变化 dict；无历史数据时 has_baseline=False。
    趋势包含：话语强度变化、叙事框架漂移、部委协同升级、综合趋势警告。
    """
    result = compare_with_previous(current_result, keywords)
    if result is None:
        return {
            'has_baseline': False,
            'message': '无历史数据，当前为首次分析。趋势判断不可用。',
        }

    return {
        'has_baseline': True,
        **result,
    }


def get_history(keywords: list[str] = None, limit: int = 10) -> dict:
    """
    查看分析历史记录。

    参数：
      keywords: 可选，过滤特定关键词的历史
      limit: 返回条数

    返回：
      最近的分析列表
    """
    records = list_analyses(limit=limit)
    if keywords:
        import json
        kw_set = set(keywords)
        records = [
            r for r in records
            if kw_set & set(json.loads(r.get('keywords', '[]')))
        ]
    return {
        'total': len(records),
        'records': records,
    }
