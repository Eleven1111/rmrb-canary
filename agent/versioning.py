"""
版本与主题身份（P0：统一比较口径）

方案 F10 / §8.2：关键词顺序变化、重复运行、扩大关键词组都会干扰历史比较。
本模块提供稳定的 topic_id 与算法版本号，所有存储与查询都以它们为准；
不同算法版本的记录不允许无标记地混合比较。
"""

import hashlib
import json

# 分析算法版本。任何改变打分/判定语义的修改都必须提升此版本号，
# 否则新旧结果会在历史比较中被当成同一口径。
# v2-p0 → v3-p1：新增事件抽取、证据核验、七维信号、来源归并、变化对比，
# 判定语义整体改变，因此必须换版本号，v2-p0 的记录不与之比较。
ALGO_VERSION = 'v3-p1'

# 采集层数据契约版本（rmrb_fetch.build_summary 的输出结构）。
FETCH_SCHEMA_VERSION = 'fetch-v2-p0'

# 旧版记录（升级前写入的行）统一标记为该版本，永不参与新版比较。
LEGACY_ALGO_VERSION = 'v1-legacy'


def normalize_keywords(keywords) -> list[str]:
    """关键词归一：去空白、去重、排序。顺序不再影响主题身份。"""
    if not keywords:
        return []
    cleaned = {kw.strip() for kw in keywords if kw and kw.strip()}
    return sorted(cleaned)


def make_topic_id(keywords) -> str:
    """
    由归一化关键词组生成稳定主题 ID。

    同一组关键词无论输入顺序如何，topic_id 恒定；
    增删关键词会产生新的 topic_id —— 这是有意的：关键词组变了就是另一个主题，
    不应与旧主题的历史直接比较。
    """
    norm = normalize_keywords(keywords)
    if not norm:
        return 'topic:empty'
    payload = json.dumps(norm, ensure_ascii=False, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]
    return f'topic:{digest}'


def make_idempotency_key(topic_id: str, date: str, algo_version: str = ALGO_VERSION) -> str:
    """
    幂等键：同一主题、同一期、同一算法版本只保留一条分析记录。

    §8.2：重复运行允许记录运行日志，但不能重复增加同日主题统计样本。
    """
    return f'{topic_id}|{date}|{algo_version}'
