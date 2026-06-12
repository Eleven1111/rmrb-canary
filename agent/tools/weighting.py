"""
Tool: 位置加权计算（纯代码）

统一权重函数，供 narrative_frame / discourse_level / ministry_signals 调用。
基于版面位置、栏目类型、标题命中、文章相关性四个维度加权。

设计原则：
  头版社论的一个关键词 ≈ 第8版普通文章的12倍信号量。
  标题中出现的关键词 ≈ 正文中的3倍信号量。
  顺带一提的文章（低相关性）信号按 relevance_multiplier 折减。
  话语强度短语经语境过滤（否定/回顾语境不计入）。
"""

from agent.tools.context_filter import has_valid_occurrence

# ── 版面权重 ─────────────────────────────────────────────
# page_no → weight
PAGE_WEIGHTS = {
    1: 4.0,   # 头版
    2: 3.0,   # 要闻版
    3: 2.5,
    4: 2.0,   # 评论版（常规位置）
}
DEFAULT_PAGE_WEIGHT = 1.0  # 5版及以后


# ── 栏目权重 ─────────────────────────────────────────────
COLUMN_KEYWORDS = {
    3.0: ['社论', '钟声', '评论员文章'],
    2.0: ['人民时评', '评论员', '任仲平'],
    1.5: ['观察', '调查', '深度', '记者手记', '调研'],
}
DEFAULT_COLUMN_WEIGHT = 1.0


# ── 位置权重（标题 vs 正文）────────────────────────────────
TITLE_WEIGHT = 3.0
BODY_WEIGHT = 1.0


def get_page_weight(page_no: int) -> float:
    """版面位置权重。"""
    return PAGE_WEIGHTS.get(page_no, DEFAULT_PAGE_WEIGHT)


def get_column_weight(column: str) -> float:
    """栏目类型权重。"""
    for weight, keywords in COLUMN_KEYWORDS.items():
        if any(kw in column for kw in keywords):
            return weight
    return DEFAULT_COLUMN_WEIGHT


def get_article_weight(article: dict) -> float:
    """
    综合文章权重 = 版面权重 × 栏目权重 × 相关性乘数。

    参数：
      article: 需含 page_no (int) 和 column (str)；
               可选 relevance_multiplier (float, 由 relevance.annotate_relevance 写入，
               缺省 1.0 保持向后兼容)

    返回：
      综合权重倍数（基线 1.0 = 5版以后普通栏目、满相关）
    """
    page_w = get_page_weight(article.get('page_no', 99))
    col_w = get_column_weight(article.get('column', ''))
    rel_m = article.get('relevance_multiplier', 1.0)
    return page_w * col_w * rel_m


def weighted_keyword_count(article: dict, keywords: list[str]) -> float:
    """
    加权关键词计数：标题命中权重 3x，正文命中权重 1x，再乘文章权重。

    返回总加权得分（float）。
    """
    title = article.get('title', '')
    content = article.get('content', '')
    article_w = get_article_weight(article)

    score = 0.0
    for kw in keywords:
        if kw in title:
            score += TITLE_WEIGHT * article_w
        elif kw in content:
            score += BODY_WEIGHT * article_w

    return score


def weighted_phrase_count(article: dict, phrases: list[str]) -> tuple[float, list[str]]:
    """
    加权短语计数（用于话语强度检测）。

    返回 (加权得分, 命中短语列表)。
    标题命中 3x，正文命中 1x，再乘文章权重。
    否定语境（"防止专项整治一刀切"）和回顾语境（"当年扫黑除恶"）
    的命中经 context_filter 剔除，不计入信号。
    """
    title = article.get('title', '')
    content = article.get('content', '')
    article_w = get_article_weight(article)

    score = 0.0
    matched = []
    for phrase in phrases:
        if phrase in title and has_valid_occurrence(title, phrase):
            score += TITLE_WEIGHT * article_w
            matched.append(phrase)
        elif phrase in content and has_valid_occurrence(content, phrase):
            score += BODY_WEIGHT * article_w
            matched.append(phrase)

    return score, matched


def weighted_pattern_match(article: dict, patterns: list[str]) -> tuple[float, bool]:
    """
    加权模式匹配（用于部委检测）。

    返回 (加权得分, 是否命中)。
    标题命中权重更高——部委出现在标题意味着该部委是文章主体，
    而非仅被提及。
    """
    title = article.get('title', '')
    content = article.get('content', '')
    article_w = get_article_weight(article)

    for pat in patterns:
        if pat in title:
            return TITLE_WEIGHT * article_w, True
        if pat in content:
            return BODY_WEIGHT * article_w, True

    return 0.0, False
