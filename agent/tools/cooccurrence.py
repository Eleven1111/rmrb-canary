"""
Tool: 共现语境分析（纯代码）

同一关键词在不同语境中含义相反：
  "新能源+突破+引领" = 正面红利信号
  "新能源+风险+整改" = 负面监管信号

本模块以段落为窗口，检测用户关键词与情感锚词的共现模式，
输出正负情感比例，弥补话语强度检测中缺失的上下文维度。
"""

import re
from agent.tools.weighting import get_article_weight

# ── 情感锚词库 ───────────────────────────────────────────
POSITIVE_ANCHORS = [
    '突破', '引领', '显著提升', '高质量', '创新驱动', '领先',
    '成效显著', '稳步推进', '重大进展', '蓬勃发展', '成果丰硕',
    '走在前列', '新高', '增长', '利好', '红利', '机遇',
    '示范', '典型', '标杆', '世界一流', '跨越式',
]

NEGATIVE_ANCHORS = [
    '整治', '严查', '风险', '违规', '问责', '整改',
    '隐患', '乱象', '违法', '处罚', '约谈', '通报',
    '叫停', '关停', '淘汰', '追责', '曝光', '惩处',
    '亏损', '下滑', '萎缩', '困境', '挑战', '压力',
]


def _split_paragraphs(text: str) -> list[str]:
    """将文本按段落分割（以换行或句号分隔的段落）。"""
    paragraphs = re.split(r'\n{2,}|\r\n{2,}', text)
    # 过短段落合并到上一段
    result = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if result and len(p) < 30:
            result[-1] += p
        else:
            result.append(p)
    return result if result else [text]


def analyze_cooccurrence(articles: list[dict], keywords: list[str]) -> dict:
    """
    以段落为窗口分析关键词与情感锚词的共现模式。

    参数：
      articles: 文章列表，每篇需含 title, content, page_no, column 字段
      keywords: 用户搜索的关键词列表

    返回：
      {
        positive_ratio: float (0-1),
        negative_ratio: float (0-1),
        neutral_ratio: float (0-1),
        sentiment_label: str ('正面主导' | '负面主导' | '混合' | '中性'),
        positive_cooccurrences: [{keyword, anchor, paragraph_preview, article_title, weight}],
        negative_cooccurrences: [{...}],
        per_article: [{title, page_no, pos_score, neg_score, sentiment}],
        signal_conflict: bool,  # 同一议题正负信号共存
      }
    """
    total_pos_score = 0.0
    total_neg_score = 0.0
    total_neutral_score = 0.0

    positive_hits = []
    negative_hits = []
    per_article = []

    for a in articles:
        content = a.get('content', '')
        title = a.get('title', '')
        article_w = get_article_weight(a)

        paragraphs = _split_paragraphs(content)
        art_pos = 0.0
        art_neg = 0.0
        art_neutral = 0.0

        for para in paragraphs:
            # 检查段落是否包含用户关键词
            has_keyword = any(kw in para for kw in keywords)
            if not has_keyword:
                continue

            # 检测正面共现
            pos_found = [anchor for anchor in POSITIVE_ANCHORS if anchor in para]
            neg_found = [anchor for anchor in NEGATIVE_ANCHORS if anchor in para]

            if pos_found:
                score = len(pos_found) * article_w
                art_pos += score
                for anchor in pos_found[:2]:  # 每段最多记录2个
                    matched_kw = next((kw for kw in keywords if kw in para), keywords[0])
                    positive_hits.append({
                        'keyword': matched_kw,
                        'anchor': anchor,
                        'paragraph_preview': para[:80] + ('...' if len(para) > 80 else ''),
                        'article_title': title,
                        'weight': round(article_w, 1),
                    })

            if neg_found:
                score = len(neg_found) * article_w
                art_neg += score
                for anchor in neg_found[:2]:
                    matched_kw = next((kw for kw in keywords if kw in para), keywords[0])
                    negative_hits.append({
                        'keyword': matched_kw,
                        'anchor': anchor,
                        'paragraph_preview': para[:80] + ('...' if len(para) > 80 else ''),
                        'article_title': title,
                        'weight': round(article_w, 1),
                    })

            if not pos_found and not neg_found:
                art_neutral += article_w

        total_pos_score += art_pos
        total_neg_score += art_neg
        total_neutral_score += art_neutral

        if art_pos > 0 or art_neg > 0:
            art_total = art_pos + art_neg
            art_sentiment = (
                '正面' if art_pos > art_neg * 2
                else '负面' if art_neg > art_pos * 2
                else '混合'
            )
            per_article.append({
                'title': title,
                'page_no': a.get('page_no', 0),
                'pos_score': round(art_pos, 1),
                'neg_score': round(art_neg, 1),
                'sentiment': art_sentiment,
            })

    # 归一化比例
    total = total_pos_score + total_neg_score + total_neutral_score
    if total == 0:
        return {
            'positive_ratio': 0.0,
            'negative_ratio': 0.0,
            'neutral_ratio': 1.0,
            'sentiment_label': '无关键词命中',
            'positive_cooccurrences': [],
            'negative_cooccurrences': [],
            'per_article': [],
            'signal_conflict': False,
        }

    pos_ratio = total_pos_score / total
    neg_ratio = total_neg_score / total
    neutral_ratio = total_neutral_score / total

    # 情感标签
    if pos_ratio > 0.6:
        label = '正面主导'
    elif neg_ratio > 0.6:
        label = '负面主导'
    elif pos_ratio > 0.2 and neg_ratio > 0.2:
        label = '混合'
    else:
        label = '中性'

    # 信号冲突检测：正负信号同时显著
    signal_conflict = pos_ratio > 0.25 and neg_ratio > 0.25

    # 按权重排序，保留 top hits
    positive_hits.sort(key=lambda x: -x['weight'])
    negative_hits.sort(key=lambda x: -x['weight'])
    per_article.sort(key=lambda x: -(x['pos_score'] + x['neg_score']))

    return {
        'positive_ratio': round(pos_ratio, 3),
        'negative_ratio': round(neg_ratio, 3),
        'neutral_ratio': round(neutral_ratio, 3),
        'sentiment_label': label,
        'positive_cooccurrences': positive_hits[:10],
        'negative_cooccurrences': negative_hits[:10],
        'per_article': per_article[:10],
        'signal_conflict': signal_conflict,
    }
