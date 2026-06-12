"""
Tool: 文章相关性评分（纯代码）

解决"顺带一提"污染：一篇顺带提了一句关键词的文章，
不应与专论该议题的社论获得同等信号权重。

评分维度：
  - 标题命中（0.6）：关键词出现在标题 = 文章主题就是该议题
  - 导语命中（0.25）：关键词出现在正文前 200 字
  - 密度（0.15）：每千字关键词出现次数，3 次/千字封顶

权重换算：relevance >= 0.6（标题命中）= 满权重；
低于阈值（默认 0.12）的文章从分析中剔除。
"""

TITLE_COMPONENT = 0.6
LEAD_COMPONENT = 0.25
DENSITY_COMPONENT = 0.15
LEAD_CHARS = 200
DENSITY_CAP_PER_K = 3.0
FULL_WEIGHT_AT = 0.6
DEFAULT_THRESHOLD = 0.12


def score_relevance(article: dict, keywords: list[str]) -> dict:
    """
    对单篇文章计算关键词相关性。

    返回：
      {relevance, multiplier, title_hit, lead_hit, total_hits, density_per_k}
    """
    title = article.get('title', '')
    content = article.get('content', '')

    title_hit = any(kw in title for kw in keywords)
    lead_hit = any(kw in content[:LEAD_CHARS] for kw in keywords)

    total_hits = sum(content.count(kw) + title.count(kw) for kw in keywords)
    chars = max(len(content), 1)
    density_per_k = total_hits / (chars / 1000)
    density_score = min(density_per_k / DENSITY_CAP_PER_K, 1.0)

    relevance = (
        (TITLE_COMPONENT if title_hit else 0.0)
        + (LEAD_COMPONENT if lead_hit else 0.0)
        + DENSITY_COMPONENT * density_score
    )
    relevance = round(min(relevance, 1.0), 3)

    return {
        'relevance': relevance,
        'multiplier': round(min(relevance / FULL_WEIGHT_AT, 1.0), 3),
        'title_hit': title_hit,
        'lead_hit': lead_hit,
        'total_hits': total_hits,
        'density_per_k': round(density_per_k, 2),
    }


def annotate_relevance(
    articles: list[dict],
    keywords: list[str],
    threshold: float = DEFAULT_THRESHOLD,
) -> dict:
    """
    为文章集合标注相关性，剔除低相关文章。

    每篇保留文章被原地写入 relevance / relevance_multiplier 字段，
    供 weighting.get_article_weight 读取。

    返回：
      {kept: [articles], dropped: [{title, relevance}], stats: {...}}
    """
    kept = []
    dropped = []

    for a in articles:
        scored = score_relevance(a, keywords)
        if scored['relevance'] >= threshold:
            a['relevance'] = scored['relevance']
            a['relevance_multiplier'] = scored['multiplier']
            a['relevance_detail'] = scored
            kept.append(a)
        else:
            dropped.append({
                'title': a.get('title', ''),
                'page_no': a.get('page_no', 0),
                'relevance': scored['relevance'],
                'total_hits': scored['total_hits'],
            })

    avg = (
        round(sum(a['relevance'] for a in kept) / len(kept), 3)
        if kept else 0.0
    )
    return {
        'kept': kept,
        'dropped': dropped,
        'stats': {
            'input_count': len(articles),
            'kept_count': len(kept),
            'dropped_count': len(dropped),
            'avg_relevance': avg,
            'threshold': threshold,
        },
    }
