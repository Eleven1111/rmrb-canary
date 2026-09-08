"""
Tool: 目标相关片段抽取（P0，对应方案 F05）

问题：旧版把整篇文章当成一个判断单位。一篇同时写了"大力支持光伏发展"和
"公安机关依法查处电信诈骗"的文章，会让诈骗执法的强度和司法信号被记到光伏头上。

P0 的修法不是完整事件抽取（那是 P1），而是先把判断单位从"整篇文章"缩小到
"包含目标关键词的句子"，并为每一次命中保留可定位的原文证据。

限制（必须随结果一起呈现，不得省略）：
  - 这是词面相关性切分，不是主体-动作-对象的事件抽取。
  - 不处理否定（"不得随意查封企业"）、历史回顾（"去年依法查处……"）、
    引述观点。这些属于 P1 语义抽取的职责。
  - 跨句指代（"该行业……"）会被漏掉，属已知召回损失。
"""

import re

# 句子边界：中文句号/问号/叹号/分号 + 换行。保留分隔符归属前句。
_SENT_SPLIT = re.compile(r'[^。！？；!?;\n]*[。！？；!?;\n]|[^。！？；!?;\n]+')


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """
    将正文切成句子，返回 [(char_start, char_end, sentence_text)]。
    字符位置相对于传入的 text，用于证据定位。
    """
    if not text:
        return []
    out = []
    for m in _SENT_SPLIT.finditer(text):
        raw = m.group()
        if not raw.strip():
            continue
        out.append((m.start(), m.end(), raw.strip()))
    return out


def build_target_segments(articles: list[dict], keywords: list[str],
                          exclude_terms: list[str] = None) -> dict:
    """
    从文章集合中抽出与目标关键词直接相关的片段。

    标题单独成段（source='title'），正文按句成段（source='body'）。
    片段沿用 article 的 page_no / column，因此可直接喂给 weighting 里的加权函数：
      - 标题片段带 title 字段 → 命中时享受标题权重
      - 正文片段 title 为空 → 只享受正文权重，标题不会被重复计权

    返回：
      {
        segments: [片段],
        segment_count: int,
        articles_with_target: int,      # 至少有一个目标片段的文章数
        articles_scanned: int,
        dropped_sentences: int,         # 被判定与目标无关而排除的句子数
        has_target: bool,
        keywords: [...],
      }
    """
    kws = [k for k in (keywords or []) if k and k.strip()]
    excludes = [e for e in (exclude_terms or []) if e and e.strip()]
    segments = []
    excluded_by_term = 0
    target_articles = []
    articles_with_target = 0
    dropped = 0

    for idx, a in enumerate(articles or []):
        title = a.get('title', '') or ''
        content = a.get('content', '') or ''
        page_no = a.get('page_no', 99)
        column = a.get('column', '') or ''
        hit_in_article = False

        if kws:
            title_hits = [k for k in kws if k in title]
        else:
            title_hits = []
        if title_hits:
            hit_in_article = True
            segments.append({
                'article_index': idx,
                'article_title': title,
                'page_no': page_no,
                'column': column,
                'source': 'title',
                'title': title,
                'content': '',
                'text': title,
                'char_start': 0,
                'char_end': len(title),
                'matched_keywords': title_hits,
            })

        for start, end, sent in split_sentences(content):
            hits = [k for k in kws if k in sent] if kws else []
            if not hits:
                dropped += 1
                continue
            # 主题定义里的排除词：明显误召回的语境直接剔除，并单独计数，
            # 让"排除掉了多少"可见，而不是静默消失。
            if any(x in sent for x in excludes):
                excluded_by_term += 1
                continue
            hit_in_article = True
            segments.append({
                'article_index': idx,
                'article_title': title,
                'page_no': page_no,
                'column': column,
                'source': 'body',
                'title': '',
                'content': sent,
                'text': sent,
                'char_start': start,
                'char_end': end,
                'matched_keywords': hits,
            })

        if hit_in_article:
            articles_with_target += 1
            # 整篇留档：句级切分会漏掉不含关键词但可能相关的信息（典型是
            # "本办法自X年X月X日起施行"这类期限句）。这些内容只能作为
            # "同篇文章中的线索"呈现，适用对象需人工确认，不得直接归给目标议题。
            target_articles.append({
                'article_index': idx,
                'article_title': title,
                'page_no': page_no,
                'column': column,
                'content': content,
            })

    return {
        'segments': segments,
        'target_articles': target_articles,
        'segment_count': len(segments),
        'articles_with_target': articles_with_target,
        'articles_scanned': len(articles or []),
        'dropped_sentences': dropped,
        'excluded_by_term': excluded_by_term,
        'has_target': bool(segments),
        'keywords': kws,
        'method': 'keyword-sentence-scoping',
        'limitation': '词面相关性切分，未做否定/历史回顾/引述识别（P1 语义抽取职责）',
    }


def evidence_of(segment: dict, matched: str = '') -> dict:
    """把一个片段压成可写入报告的证据记录。"""
    text = segment.get('text', '')
    return {
        'article_title': segment.get('article_title', ''),
        'page_no': segment.get('page_no', 0),
        'column': segment.get('column', ''),
        'source': segment.get('source', ''),
        'char_start': segment.get('char_start', 0),
        'char_end': segment.get('char_end', 0),
        'quote': text if len(text) <= 120 else text[:117] + '...',
        'matched': matched,
    }
