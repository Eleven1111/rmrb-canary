"""
Tool: 话语强度七级定级（纯代码）

替代旧版"情感四象限"，基于中国党政文本语言规律的七级量表。

v2: 加权版 — 头版社论中的"坚决遏制"比第8版普通文章的同一词权重高12倍。
"""

from agent.tools.weighting import weighted_phrase_count, get_article_weight

INTENSITY_LEVELS = {
    1: {
        'name': '研究探索',
        'phrases': ['研究制定', '鼓励试点', '积极探索', '研究探索', '鼓励发展', '有益尝试'],
        'window': '12个月+',
        'signal': '政策窗口开放',
    },
    2: {
        'name': '积极推进',
        'phrases': ['加快推进', '大力支持', '全面部署', '积极推进', '重点推进', '深入推进'],
        'window': '政策红利期',
        'signal': '正向信号',
    },
    3: {
        'name': '规范引导',
        'phrases': ['规范发展', '健全机制', '完善监管', '规范引导', '加强监管', '有序发展'],
        'window': '12个月+',
        'signal': '温和管控',
    },
    4: {
        'name': '有序整治',
        'phrases': ['专项整治', '集中清理', '有序规范', '重点整治', '严格执法', '集中整治'],
        'window': '6-12个月',
        'signal': '合规调整期',
    },
    5: {
        'name': '坚决整治',
        'phrases': ['坚决遏制', '严格管控', '决不允许', '进一步规范', '坚决整治', '严厉打击', '决不姑息'],
        'window': '3-6个月',
        'signal': '强执行信号',
    },
    6: {
        'name': '依法严惩',
        'phrases': ['依法查处', '追究责任', '司法追诉', '移送公安', '依法追责', '绝不姑息', '刑事追诉'],
        'window': '1-3个月',
        'signal': '已入法律轨道',
    },
    7: {
        'name': '专项打击',
        'phrases': ['雷霆行动', '清网行动', '扫黑除恶', '集中收网', '专项打击', '严打'],
        'window': '窗口关闭',
        'signal': '运动式执法',
    },
}


def measure_intensity(articles: list[dict]) -> dict:
    """
    对文章集合进行话语强度七级定级（加权版）。

    参数：
      articles: 文章列表，每篇需含 title, content, page_no, column 字段

    返回：
      {
        max_level: int,
        max_level_name: str,
        max_level_triggers: [str],
        weighted_max_level: int,       # 加权后的最高等级（可能与 max_level 不同）
        distribution: {level_N: {count, weighted_score, pct, triggers, name, window}},
        jump_alert: bool,
        jump_detail: str | None,
        action_window: str,
        high_weight_alerts: [{title, page_no, level, phrase, weight}],  # 高权重文章的高级别命中
      }
    """
    level_counts = {i: 0 for i in range(1, 8)}
    level_scores = {i: 0.0 for i in range(1, 8)}
    level_triggers = {i: set() for i in range(1, 8)}
    high_weight_alerts = []

    for a in articles:
        article_w = get_article_weight(a)
        for level, config in INTENSITY_LEVELS.items():
            score, matched = weighted_phrase_count(a, config['phrases'])
            if score > 0:
                level_counts[level] += 1
                level_scores[level] += score
                level_triggers[level].update(matched)

                # 高权重 + 高级别 = 核心警报
                if level >= 5 and article_w >= 4.0:
                    for phrase in matched:
                        high_weight_alerts.append({
                            'title': a.get('title', ''),
                            'page_no': a.get('page_no', 0),
                            'level': level,
                            'phrase': phrase,
                            'weight': round(score, 1),
                        })

    # 加权最高等级：按加权得分确定实际最高等级
    # 低权重文章的高级别短语不足以单独定级——需要加权得分 >= 2.0
    nonzero = sorted(lvl for lvl, cnt in level_counts.items() if cnt > 0)
    max_level = nonzero[-1] if nonzero else 1

    weighted_nonzero = sorted(
        lvl for lvl, sc in level_scores.items() if sc >= 2.0
    )
    weighted_max = weighted_nonzero[-1] if weighted_nonzero else (nonzero[-1] if nonzero else 1)

    total_score = sum(level_scores.values()) or 1.0

    # 跳级检测
    jump_alert = False
    jump_detail = None
    if len(nonzero) >= 2:
        gaps = []
        for i in range(len(nonzero) - 1):
            if nonzero[i + 1] - nonzero[i] >= 3:
                gaps.append((nonzero[i], nonzero[i + 1]))
        if gaps:
            jump_alert = True
            jump_detail = '; '.join(f'从{lo}级跳至{hi}级' for lo, hi in gaps)

    distribution = {}
    for level in range(1, 8):
        config = INTENSITY_LEVELS[level]
        distribution[f'level_{level}'] = {
            'name': config['name'],
            'count': level_counts[level],
            'weighted_score': round(level_scores[level], 1),
            'pct': round(level_scores[level] / total_score * 100, 1),
            'triggers': sorted(level_triggers[level]),
            'window': config['window'],
        }

    return {
        'max_level': max_level,
        'max_level_name': INTENSITY_LEVELS[max_level]['name'],
        'max_level_triggers': sorted(level_triggers[max_level]),
        'weighted_max_level': weighted_max,
        'distribution': distribution,
        'jump_alert': jump_alert,
        'jump_detail': jump_detail,
        'action_window': INTENSITY_LEVELS[weighted_max]['window'],
        'high_weight_alerts': high_weight_alerts[:10],
    }
