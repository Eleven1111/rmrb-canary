"""
Tool: 议题一致性检查（纯代码）

十个关键词一锅炖（企业服务+AI+网络安全+政务服务…）会把多个
议题的文章混在一起打分，是框架/强度日间震荡的主因之一。

本模块按"关键词在哪些文章共同出现"做共现聚类：
  - 两个关键词命中的文章集合 Jaccard ≥ 0.2 或共现 ≥ 2 篇 → 同簇
  - 连通分量 = 议题簇；簇数 > 1 即告警，建议拆分后分别分析
"""

JACCARD_THRESHOLD = 0.2
COOCCUR_THRESHOLD = 2


def _keyword_article_sets(articles: list[dict], keywords: list[str]) -> dict:
    hits = {}
    for kw in keywords:
        matched = {
            i for i, a in enumerate(articles)
            if kw in a.get('title', '') or kw in a.get('content', '')
        }
        hits[kw] = matched
    return hits


def _connected_components(nodes: list[str], edges: set[tuple]) -> list[list[str]]:
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return sorted(groups.values(), key=len, reverse=True)


def check_coherence(articles: list[dict], keywords: list[str]) -> dict:
    """
    检查关键词组的议题一致性。

    返回：
      {
        coherent: bool,
        cluster_count: int,
        clusters: [{keywords, article_count}],
        unused_keywords: [str],
        avg_jaccard: float,
        warning: str | None,
      }
    """
    hits = _keyword_article_sets(articles, keywords)
    used = [kw for kw, s in hits.items() if s]
    unused = [kw for kw, s in hits.items() if not s]

    if len(used) <= 1:
        return {
            'coherent': True,
            'cluster_count': 1 if used else 0,
            'clusters': (
                [{'keywords': used, 'article_count': len(hits[used[0]])}] if used else []
            ),
            'unused_keywords': unused,
            'avg_jaccard': 1.0 if used else 0.0,
            'warning': None,
        }

    edges = set()
    jaccards = []
    for i, kw_a in enumerate(used):
        for kw_b in used[i + 1:]:
            inter = hits[kw_a] & hits[kw_b]
            union = hits[kw_a] | hits[kw_b]
            jac = len(inter) / len(union) if union else 0.0
            jaccards.append(jac)
            if jac >= JACCARD_THRESHOLD or len(inter) >= COOCCUR_THRESHOLD:
                edges.add((kw_a, kw_b))

    components = _connected_components(used, edges)
    clusters = [
        {
            'keywords': comp,
            'article_count': len(set().union(*(hits[kw] for kw in comp))),
        }
        for comp in components
    ]
    avg_jaccard = round(sum(jaccards) / len(jaccards), 3) if jaccards else 0.0

    warning = None
    if len(components) > 1:
        cluster_desc = '；'.join(
            f"簇{i + 1}[{'/'.join(c['keywords'])}]({c['article_count']}篇)"
            for i, c in enumerate(clusters)
        )
        warning = (
            f'关键词组覆盖 {len(components)} 个互不相关的议题簇：{cluster_desc}。'
            '混杂议题会互相污染框架与强度评分，建议按簇拆分后分别分析。'
        )

    return {
        'coherent': len(components) <= 1,
        'cluster_count': len(components),
        'clusters': clusters,
        'unused_keywords': unused,
        'avg_jaccard': avg_jaccard,
        'warning': warning,
    }
