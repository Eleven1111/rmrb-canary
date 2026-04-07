"""
Tool: 部委协同度检测（纯代码）

L0-L5 五档协同度，是"确定性行动"最强的单一预测变量。

v2: 加权版 — 部委出现在标题（文章主体）vs 正文背景提及，权重差 3 倍。
    版面位置进一步放大信号（头版部委信号 >> 后版提及）。
"""

from agent.tools.weighting import weighted_pattern_match, get_article_weight

MINISTRY_PATTERNS = {
    '国家发展改革委': {
        'patterns': ['发改委', '国家发展改革委', '发展改革'],
        'tier': 'ministry',
    },
    '工业和信息化部': {
        'patterns': ['工信部', '工业和信息化部'],
        'tier': 'ministry',
    },
    '财政部': {
        'patterns': ['财政部', '财政政策'],
        'tier': 'ministry',
    },
    '国家市场监督管理总局': {
        'patterns': ['市场监管总局', '市场监管', '反垄断'],
        'tier': 'ministry',
    },
    '国家互联网信息办公室': {
        'patterns': ['网信办', '网络安全和信息化'],
        'tier': 'ministry',
    },
    '中国人民银行': {
        'patterns': ['人民银行', '央行', '货币政策'],
        'tier': 'ministry',
    },
    '国家税务总局': {
        'patterns': ['税务总局', '税务机关'],
        'tier': 'ministry',
    },
    '生态环境部': {
        'patterns': ['生态环境部', '环保部门'],
        'tier': 'ministry',
    },
    '教育部': {
        'patterns': ['教育部', '教育主管'],
        'tier': 'ministry',
    },
    '国家能源局': {
        'patterns': ['国家能源局', '能源监管'],
        'tier': 'ministry',
    },
    '商务部': {
        'patterns': ['商务部', '外贸', '进出口管制'],
        'tier': 'ministry',
    },
    # 高信号层级
    '国务院': {
        'patterns': ['国务院常务会议', '国务院专题', '国务院部署', '国务院办公厅'],
        'tier': 'state_council',
    },
    '中央政治局': {
        'patterns': ['政治局会议', '政治局常委', '中央政治局', '政治局集体学习'],
        'tier': 'politburo',
    },
    # 司法/执法
    '公安部': {
        'patterns': ['公安部', '公安机关', '警方'],
        'tier': 'judicial',
    },
    '最高人民检察院': {
        'patterns': ['最高检', '检察院', '检察机关'],
        'tier': 'judicial',
    },
    '最高人民法院': {
        'patterns': ['最高法', '人民法院'],
        'tier': 'judicial',
    },
}

# 加权协同度阈值：低权重的部委提及不足以触发高协同等级
# 例如第8版文章背景提到"人民法院"不应直接触发 L5
TIER_WEIGHT_THRESHOLDS = {
    'judicial': 3.0,     # 司法信号需要足够权重才算有效
    'politburo': 2.0,    # 政治局信号阈值
    'state_council': 2.0,
    'ministry': 1.0,     # 普通部委门槛最低
}


def detect_ministries(articles: list[dict]) -> dict:
    """
    从文章集合中检测部委信号和协同等级（加权版）。

    参数：
      articles: 文章列表，每篇需含 title, content, page_no, column 字段

    返回：
      {
        coordination_level: str (L0-L5),
        coordination_label: str,
        ministry_count: int,
        ministries_found: [str],
        ministry_details: {部委名: {tier, total_score, hit_count, in_title}},
        tier_breakdown: {ministry: int, state_council: int, ...},
        has_state_council: bool,
        has_politburo: bool,
        has_judicial: bool,
        time_compression: float,
        high_signal_hits: [{ministry, title, page_no, score}],  # 高权重部委信号
      }
    """
    ministry_data = {}
    high_signal_hits = []

    for ministry, config in MINISTRY_PATTERNS.items():
        for a in articles:
            score, hit = weighted_pattern_match(a, config['patterns'])
            if hit:
                if ministry not in ministry_data:
                    ministry_data[ministry] = {
                        'tier': config['tier'],
                        'total_score': 0.0,
                        'hit_count': 0,
                        'in_title': False,
                    }
                ministry_data[ministry]['total_score'] += score
                ministry_data[ministry]['hit_count'] += 1

                # 标题命中标记
                title = a.get('title', '')
                if any(pat in title for pat in config['patterns']):
                    ministry_data[ministry]['in_title'] = True

                # 收集高权重命中
                if score >= 4.0:
                    high_signal_hits.append({
                        'ministry': ministry,
                        'title': a.get('title', ''),
                        'page_no': a.get('page_no', 0),
                        'score': round(score, 1),
                    })

    # 按阈值过滤：低权重的高层级信号降级处理
    effective_tiers = {}
    for ministry, data in ministry_data.items():
        tier = data['tier']
        threshold = TIER_WEIGHT_THRESHOLDS.get(tier, 1.0)
        if data['total_score'] >= threshold:
            effective_tiers[ministry] = tier
        else:
            # 权重不足，降级为普通 ministry 信号
            effective_tiers[ministry] = 'ministry'

    tier_breakdown = {}
    for ministry, tier in effective_tiers.items():
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1

    has_judicial = tier_breakdown.get('judicial', 0) > 0
    has_politburo = tier_breakdown.get('politburo', 0) > 0
    has_state_council = tier_breakdown.get('state_council', 0) > 0
    ministry_count = len(ministry_data)

    if has_judicial:
        level, label, compression = 'L5', '司法入轨，窗口<=30天', 0.1
    elif has_politburo:
        level, label, compression = 'L4', '政治局级，最高优先级', 0.4
    elif has_state_council:
        level, label, compression = 'L3', '国务院级，执行意志确认', 0.6
    elif ministry_count >= 3:
        level, label, compression = 'L2', '多部委协同，行动概率显著提升', 0.8
    elif ministry_count >= 1:
        level, label, compression = 'L1', '单部委关注，预警信号', 1.0
    else:
        level, label, compression = 'L0', '未检测到部委信号', 1.0

    # 详细数据（含每个部委的加权得分）
    ministry_details = {
        m: {
            'tier': effective_tiers.get(m, data['tier']),
            'original_tier': data['tier'],
            'total_score': round(data['total_score'], 1),
            'hit_count': data['hit_count'],
            'in_title': data['in_title'],
        }
        for m, data in ministry_data.items()
    }

    high_signal_hits.sort(key=lambda x: -x['score'])

    return {
        'coordination_level': level,
        'coordination_label': label,
        'ministry_count': ministry_count,
        'ministries_found': list(ministry_data.keys()),
        'ministry_details': ministry_details,
        'tier_breakdown': tier_breakdown,
        'has_state_council': has_state_council,
        'has_politburo': has_politburo,
        'has_judicial': has_judicial,
        'time_compression': compression,
        'high_signal_hits': high_signal_hits[:10],
    }
