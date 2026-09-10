"""
Tool: 传导链定位器（纯代码）

把核心认知一（人民日报是放大器不是信号源）和认知二（六阶段
运动式周期）变成可计算变量：对同一议题，测量它在四层传导链
中的出现情况与时序——

  理论层（求是/理论频道）→ 中央层（国务院文件/政治局）
  → 部委层（部门文件/联合发文）→ 放大器层（人民日报）

并据此定位议题当前处于哪个周期阶段：
  酝酿期 → 铺垫期 → 动员期 → 运动期 → 收尾期 → 常态化

阶段判定是启发式规则，每个判定附证据与置信度。
数据缺层（采集失败）时降置信度，不臆断。
"""

NORMALIZATION_PHRASES = ['长效机制', '常态化监管', '建章立制', '纳入日常监管', '常态化开展']
CLOSING_PHRASES = ['阶段性成效', '取得明显成效', '圆满收官', '专项行动总结', '整治成效']


def _scan_phrases(texts: list[str], phrases: list[str]) -> list[str]:
    found = []
    for phrase in phrases:
        if any(phrase in t for t in texts):
            found.append(phrase)
    return found


def _layer_summary(items: list[dict]) -> dict:
    dates = sorted(i['date'] for i in items if i.get('date'))
    return {
        'active': bool(items),
        'count': len(items),
        'first_seen': dates[0] if dates else None,
        'last_seen': dates[-1] if dates else None,
        'titles': [i['title'] for i in items[:3]],
    }


def _at_least(value, threshold) -> bool:
    """value >= threshold，但 value 为 None（未知）时一律返回 False。"""
    return value is not None and value >= threshold


def _at_most(value, threshold) -> bool:
    """
    value <= threshold，但 value 为 None（未知）时一律返回 False。

    注意这与 _at_least 不是对称关系：两个方向的"未知"都返回 False，
    因为"不知道强度"既不能证明它高、也不能证明它低。
    """
    return value is not None and value <= threshold


def locate_stage(
    rmrb_signals: dict,
    upstream: dict,
    texts: list[str] = None,
) -> dict:
    """
    定位议题在六阶段周期中的位置。

    参数：
      rmrb_signals: {
        intensity_level: int,        # 加权最高强度
        article_count: int,
        coordination_level: str,     # L0-L5
        has_judicial: bool, has_discipline: bool,
        has_politburo: bool, joint_found: bool,
      }
      upstream: collect_upstream() 的输出
      texts: 当期命中文章全文（用于收尾/常态化短语扫描）

    返回：
      {stage, confidence, evidence, layers, lead_lag, degraded_layers}
    """
    texts = texts or []

    # 放大器层的两个数值都可能缺：article_count 缺省视为 0，
    # intensity_level 则区分"0 级"和"不知道"，后者不参与任何阈值比较。
    article_count = rmrb_signals.get('article_count') or 0
    intensity = rmrb_signals.get('intensity_level')
    intensity_known = isinstance(intensity, (int, float)) and not isinstance(intensity, bool)
    if not intensity_known:
        intensity = None

    central_docs = upstream.get('central_docs', {})
    ministry_docs = upstream.get('ministry_docs', {})
    theory = upstream.get('theory', {})

    degraded = [
        name for name, src in [
            ('中央层(国务院文件)', central_docs),
            ('部委层(部门文件)', ministry_docs),
            ('理论层', theory),
        ] if src.get('error')
    ]

    layers = {
        'theory': _layer_summary(theory.get('items', [])),
        'central': _layer_summary(central_docs.get('items', [])),
        'ministry': _layer_summary(ministry_docs.get('items', [])),
        'amplifier': {
            'active': article_count > 0,
            'count': article_count,
            'intensity': intensity,
            'intensity_known': intensity_known,
        },
    }

    # 强度未知时，任何以强度为条件的分支一律不成立 —— 而不是当成 1 级。
    # 证据不足时 measure_intensity 返回 max_level=None（F04：没证据不许落成
    # "1 级研究探索"）。若在这里把 None 补成 1，等于从后门把那个结论放回来：
    # "不知道强度"会被读成"强度极低"，收尾期/常态化两个分支会因此假成立。
    pd_hot = article_count >= 3 or _at_least(intensity, 4)
    central_active = (
        layers['central']['active']
        or rmrb_signals.get('has_politburo', False)
    )
    ministry_joint = (
        rmrb_signals.get('joint_found', False)
        or any(i.get('is_joint') for i in ministry_docs.get('items', []))
    )
    enforcement = (
        rmrb_signals.get('has_judicial', False)
        or rmrb_signals.get('has_discipline', False)
        or _at_least(intensity, 6)
    )
    normalization_hits = _scan_phrases(texts, NORMALIZATION_PHRASES)
    closing_hits = _scan_phrases(texts, CLOSING_PHRASES)

    # 阶段判定（自上而下首条命中）
    if enforcement and not closing_hits:
        stage = '运动期'
        evidence = '司法/纪检入轨或强度≥6级——执法已展开，窗口关闭。'
    elif closing_hits and _at_most(intensity, 4):
        stage = '收尾期'
        evidence = f"出现收尾叙事（{'/'.join(closing_hits)}）且强度回落——整治进入总结阶段，政策松动早期指标。"
    elif normalization_hits and _at_most(intensity, 3):
        stage = '常态化'
        evidence = f"出现制度化叙事（{'/'.join(normalization_hits)}）——运动转入长效机制，风险曲线走平。"
    elif (ministry_joint or (layers['ministry']['active'] and central_active)) and pd_hot:
        stage = '动员期'
        evidence = '部委执行文件已落地（含联合发文）且人民日报火力跟进——倒计时阶段。'
    elif central_active and (pd_hot or layers['ministry']['active']):
        stage = '铺垫期'
        evidence = '中央层信号已现、放大器开始造势但执行细则未齐——这是企业最有价值的预警窗口。'
    elif layers['ministry']['active'] and not central_active:
        stage = '铺垫期'
        evidence = '部委文件先行、中央层未公开表态——部门细化在前，属铺垫期变体。'
    elif layers['theory']['active'] and not pd_hot:
        stage = '酝酿期'
        evidence = '理论层已吹风、文件与日报均未跟进——议题在形成共识阶段，最早期信号。'
    else:
        stage = '未定位'
        evidence = '各层信号不足，议题可能未进入政策议程，或处于内参渠道（公开数据不可见）。'

    # 置信度：可用层数 + 信号清晰度
    available_layers = 4 - len(degraded)
    if stage == '未定位':
        confidence = 'low'
    elif available_layers >= 4:
        confidence = 'high'
    elif available_layers >= 3:
        confidence = 'medium'
    else:
        confidence = 'low'

    # 时序：各层首现排序，看传导方向
    lead_lag = sorted(
        (
            {'layer': name, 'first_seen': data['first_seen']}
            for name, data in [
                ('理论层', layers['theory']),
                ('中央层', layers['central']),
                ('部委层', layers['ministry']),
            ] if data.get('first_seen')
        ),
        key=lambda x: x['first_seen'],
    )

    if not intensity_known:
        # 说出来。少了一路证据却不标注，读者会把"未定位"读成"确实没信号"。
        evidence += '（本期无强度结论，凡以强度为条件的判定均未参与）'

    return {
        'stage': stage,
        'confidence': confidence,
        'evidence': evidence,
        'intensity_known': intensity_known,
        'layers': layers,
        'ministry_joint_docs': [
            i for i in ministry_docs.get('items', []) if i.get('is_joint')
        ][:5],
        'lead_lag': lead_lag,
        'degraded_layers': degraded,
        'normalization_hits': normalization_hits,
        'closing_hits': closing_hits,
    }
