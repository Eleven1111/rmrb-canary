"""
主题定义与企业画像（P1，对应方案 §8.2 / §5）

主题身份与主题版本是两回事：
  - `topic_id` 由 topic_key 固定，跨版本不变 —— 这是"这条时间线是谁的"。
  - `topic_version` 记录关键词、同义词、排除词、地域约束的那一版取值。
    口径变了就是新版本，**跨版本记录不允许直接比较**（§4.1 最后一条）。

临时关键词模式（不指定主题文件）仍然可用，其 topic_id 由关键词哈希得到，
topic_version 固定为 'adhoc'：临时口径不与正式主题的历史混在一起。
"""

import json
import os

from agent.versioning import make_topic_id, normalize_keywords

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')
TOPIC_DIR = os.path.join(CONFIG_DIR, 'topics')
ENTERPRISE_DIR = os.path.join(CONFIG_DIR, 'enterprises')

REQUIRED_TOPIC_FIELDS = ('topic_key', 'topic_version', 'label', 'keywords')
REQUIRED_ENTERPRISE_FIELDS = ('enterprise_key', 'name', 'value_chain_segments')


def _load_json(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def list_topics() -> list[str]:
    if not os.path.isdir(TOPIC_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(TOPIC_DIR) if f.endswith('.json'))


def list_enterprises() -> list[str]:
    if not os.path.isdir(ENTERPRISE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(ENTERPRISE_DIR) if f.endswith('.json'))


def load_topic(topic_key: str) -> dict:
    """
    读取主题定义并展开检索词。

    返回值中的 `search_terms` 是关键词 + 同义词的并集，供采集与片段切分使用；
    `exclude_terms` 在片段切分后用于剔除明显误召回的句子。
    """
    path = os.path.join(TOPIC_DIR, f'{topic_key}.json')
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f'未找到主题定义 {topic_key}。可用主题：{list_topics() or "（无）"}')
    cfg = _load_json(path)

    missing = [f for f in REQUIRED_TOPIC_FIELDS if not cfg.get(f)]
    if missing:
        raise ValueError(f'主题定义 {topic_key} 缺少必填字段：{missing}')

    terms = list(cfg['keywords'])
    for base, syns in (cfg.get('synonyms') or {}).items():
        terms.extend(syns)
    cfg['search_terms'] = normalize_keywords(terms)
    cfg['topic_id'] = f"topic:{cfg['topic_key']}"
    cfg['exclude_terms'] = cfg.get('exclude_terms') or []
    cfg['owner_agencies'] = cfg.get('owner_agencies') or []
    cfg['source'] = 'topic_file'
    return cfg


def load_enterprise(enterprise_key: str) -> dict:
    path = os.path.join(ENTERPRISE_DIR, f'{enterprise_key}.json')
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f'未找到企业画像 {enterprise_key}。可用：{list_enterprises() or "（无）"}')
    cfg = _load_json(path)
    missing = [f for f in REQUIRED_ENTERPRISE_FIELDS if not cfg.get(f)]
    if missing:
        raise ValueError(f'企业画像 {enterprise_key} 缺少必填字段：{missing}')
    return cfg


def adhoc_topic(keywords) -> dict:
    """把一组临时关键词包装成主题对象，口径版本标为 adhoc。"""
    norm = normalize_keywords(keywords)
    return {
        'topic_key': 'adhoc',
        'topic_version': 'adhoc',
        'label': '/'.join(norm) or '（空）',
        'keywords': norm,
        'search_terms': norm,
        'exclude_terms': [],
        'owner_agencies': [],
        'topic_id': make_topic_id(norm),
        'stance_hint': None,
        'region_scope': None,
        'source': 'adhoc_keywords',
        'notes': '临时关键词口径，不与正式主题的历史序列合并比较。',
    }


def resolve_topic(topic_key: str = None, keywords=None) -> dict:
    """
    统一入口：给了主题名就用主题文件，否则用临时关键词。
    两者都没有则报错 —— 不允许在没有主题身份的情况下写入历史。
    """
    if topic_key:
        return load_topic(topic_key)
    if keywords:
        return adhoc_topic(keywords)
    raise ValueError('必须指定 --topic 或 --keyword 之一。')


def enterprise_exposure(enterprise: dict, topic_key: str) -> dict:
    """
    取企业在该主题上的暴露映射。

    没有登记映射时返回 relevance='unmapped' —— 这是"未建立关系"，
    不是"无关"，报告不得据此宣称与企业无关。
    """
    if not enterprise:
        return {'relevance': 'no_profile',
                'note': '未提供企业画像，本期不做企业影响推导。'}
    mapping = (enterprise.get('topic_exposure') or {}).get(topic_key)
    if not mapping:
        return {
            'relevance': 'unmapped',
            'segments': [],
            'note': f'企业画像中尚未登记主题 {topic_key} 的暴露环节。'
                    '这是"关系未建立"，不是"无关"；需人工补登记后才能做影响推导。',
        }
    return mapping
