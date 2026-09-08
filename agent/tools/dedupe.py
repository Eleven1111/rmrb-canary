"""
Tool: 来源归并与转载识别（P1，对应方案 §5.2 / §7.2）

一条报道被多家转载，是**一个事件关联多个来源**，不是多个独立决策证据。
旧版把外部条目数直接当"官媒联动强度"，等于把转载次数读成了独立证实次数。

本模块把各来源的条目聚成故事簇（story cluster），并明确区分：
  - 原发条数（簇数）——可以进入证据计数
  - 转载条数（簇内多余条目）——只计入传播强度
  - 独立信源数——同一体系的来源不计为独立（人民日报与人民网同属一家）

判定是词面归并（标题归一化 + 正文哈希），不是语义去重；
标题被改写的转载会漏掉，属已知召回损失。
"""

import hashlib
import re

# 同一体系的来源不构成相互印证。key 为体系名，值为该体系下的来源标识。
SAME_SYSTEM_GROUPS = {
    '人民日报社': ['人民日报', '人民网', '人民日报社'],
    '新华社': ['新华社', '新华网'],
    '中央广播电视总台': ['央视', '央视网', '总台', '中央广播电视总台'],
}

_PUNCT = re.compile(r'[\s“”"\'‘’·—\-–，,。.、；;：:！!？?（）()《》〈〉\[\]【】]+')


def normalize_title(title: str) -> str:
    """标题归一：去标点空白，用于判断是否同一条报道。"""
    return _PUNCT.sub('', title or '')


def content_hash(text: str) -> str:
    body = _PUNCT.sub('', text or '')
    return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16] if body else ''


def _system_of(media: str) -> str:
    for system, names in SAME_SYSTEM_GROUPS.items():
        if any(n in (media or '') for n in names):
            return system
    return media or '未知来源'


def merge_sources(rmrb_articles: list[dict], external_items: list[dict] = None) -> dict:
    """
    归并人民日报文章与外部来源条目。

    参数：
      rmrb_articles: full_texts 形态，含 title / content / page_no
      external_items: media_fetch 的 official_media 条目，含 title / summary / media / url

    返回：
      {clusters, original_count, reprint_count, item_count,
       independent_systems, cross_system_confirmed, note}
    """
    clusters = {}

    def add(item):
        norm = normalize_title(item.get('title', ''))
        chash = content_hash(item.get('content') or item.get('summary') or '')
        key = norm or chash or id(item)
        cluster = clusters.setdefault(key, {
            'canonical_title': item.get('title', ''),
            'sources': [],
            'systems': set(),
        })
        cluster['sources'].append(item)
        cluster['systems'].add(_system_of(item.get('media', '')))

    for a in rmrb_articles or []:
        add({'title': a.get('title', ''), 'content': a.get('content', ''),
             'media': '人民日报', 'page_no': a.get('page_no'),
             'url': a.get('url', ''), 'source_layer': 'rmrb'})

    for it in external_items or []:
        add({'title': it.get('title', ''), 'summary': it.get('summary', ''),
             'media': it.get('media', ''), 'url': it.get('url', ''),
             'pub_time': it.get('pub_time', ''), 'source_layer': 'external'})

    out = []
    item_count = 0
    for cluster in clusters.values():
        item_count += len(cluster['sources'])
        systems = sorted(cluster['systems'])
        out.append({
            'canonical_title': cluster['canonical_title'],
            'source_count': len(cluster['sources']),
            'systems': systems,
            'is_reprint': len(cluster['sources']) > 1,
            'cross_system': len(systems) > 1,
            'sources': [{'media': s.get('media'), 'url': s.get('url'),
                         'layer': s.get('source_layer'), 'page_no': s.get('page_no'),
                         'pub_time': s.get('pub_time')}
                        for s in cluster['sources']],
        })

    out.sort(key=lambda c: -c['source_count'])
    all_systems = sorted({s for c in out for s in c['systems']})
    cross_system = [c for c in out if c['cross_system']]

    return {
        'status': 'ok',
        'clusters': out[:50],
        'cluster_count': len(out),
        'original_count': len(out),
        'reprint_count': item_count - len(out),
        'item_count': item_count,
        'independent_systems': all_systems,
        'independent_system_count': len(all_systems),
        'cross_system_confirmed': len(cross_system),
        'note': '原发条数=簇数，可作证据计数；转载条数只计入传播强度。'
                '同一体系（如人民日报与人民网）不计为独立印证；'
                '归并按标题词面进行，改写标题的转载会漏掉。',
    }
