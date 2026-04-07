"""
Tool: 叙事框架分类器（纯代码，不需要 LLM）

六大叙事框架识别，基于加权关键词匹配。
框架决定后续所有信号的解读基准和传导速度调整。

v2: 逐篇加权评分（版面×栏目×标题位置），替代旧版等权语料库匹配。
"""

from agent.tools.weighting import weighted_keyword_count, get_article_weight

NARRATIVE_FRAMES = {
    '国家安全框架': {
        'keywords': [
            '安全', '自主可控', '卡脖子', '供应链安全', '数据安全',
            '网络安全', '粮食安全', '能源安全', '国家安全', '总体安全观',
        ],
        'negotiation_room': '极低',
        'speed_modifier': 0.5,  # 传导速度压缩50%
    },
    '共同富裕框架': {
        'keywords': [
            '共同富裕', '资本无序', '垄断', '平台经济', '过度逐利',
            '防止资本', '收入分配', '三次分配',
        ],
        'negotiation_room': '低',
        'speed_modifier': 0.7,
    },
    '高质量发展框架': {
        'keywords': [
            '高质量发展', '绿色低碳', '转型升级', '新质生产力',
            '淘汰落后', '产业升级', '供给侧',
        ],
        'negotiation_room': '中',
        'speed_modifier': 1.0,
    },
    '自立自强框架': {
        'keywords': [
            '自立自强', '核心技术', '国产替代', '弯道超车',
            '科技创新', '关键核心', '自主创新', '科技自立',
        ],
        'negotiation_room': '中高',
        'speed_modifier': 1.0,
    },
    '防范金融风险框架': {
        'keywords': [
            '系统性风险', '杠杆', '债务风险', '流动性', '房住不炒',
            '防范化解', '金融风险', '债务化解', '隐性债务',
        ],
        'negotiation_room': '低',
        'speed_modifier': 0.7,
    },
    '社会治理框架': {
        'keywords': [
            '基层治理', '社会稳定', '民生保障', '群众利益',
            '维护稳定', '社会治理', '平安建设',
        ],
        'negotiation_room': '低-中',
        'speed_modifier': 0.9,
    },
}


def classify_narrative(articles: list[dict]) -> dict:
    """
    对文章集合进行叙事框架分类（加权版）。

    参数：
      articles: 文章列表，每篇需含 title, content, page_no, column 字段

    返回：
      {
        primary_frame: str,
        secondary_frame: str | None,
        frame_hits: {框架名: {score, count, matched_keywords, ...}},
        negotiation_room: str,
        speed_modifier: float,
        top_articles: [{title, page_no, frame, weight}],  # 贡献最大的文章
      }
    """
    frame_scores = {}

    for frame_name, config in NARRATIVE_FRAMES.items():
        total_score = 0.0
        match_count = 0
        all_matched_kw = set()
        article_contributions = []

        for a in articles:
            score = weighted_keyword_count(a, config['keywords'])
            if score > 0:
                match_count += 1
                total_score += score
                # 记录哪些关键词被命中
                title = a.get('title', '')
                content = a.get('content', '')
                for kw in config['keywords']:
                    if kw in title or kw in content:
                        all_matched_kw.add(kw)
                article_contributions.append({
                    'title': a.get('title', ''),
                    'page_no': a.get('page_no', 0),
                    'score': round(score, 1),
                })

        if total_score > 0:
            # 按贡献度排序，保留 top 3
            article_contributions.sort(key=lambda x: -x['score'])
            frame_scores[frame_name] = {
                'score': round(total_score, 1),
                'count': match_count,
                'matched_keywords': sorted(all_matched_kw),
                'negotiation_room': config['negotiation_room'],
                'speed_modifier': config['speed_modifier'],
                'top_contributors': article_contributions[:3],
            }

    sorted_frames = sorted(
        frame_scores.items(), key=lambda x: -x[1]['score']
    )

    primary = sorted_frames[0] if sorted_frames else None
    secondary = sorted_frames[1] if len(sorted_frames) > 1 else None

    # 收集所有框架中贡献最大的文章（跨框架去重）
    top_articles = []
    seen_titles = set()
    for _, data in sorted_frames[:3]:
        for contrib in data.get('top_contributors', []):
            if contrib['title'] not in seen_titles:
                seen_titles.add(contrib['title'])
                top_articles.append(contrib)
    top_articles.sort(key=lambda x: -x['score'])

    return {
        'primary_frame': primary[0] if primary else '未识别',
        'secondary_frame': secondary[0] if secondary else None,
        'frame_hits': {k: v for k, v in sorted_frames},
        'negotiation_room': primary[1]['negotiation_room'] if primary else '未知',
        'speed_modifier': primary[1]['speed_modifier'] if primary else 1.0,
        'top_articles': top_articles[:5],
    }
