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
            'active': rmrb_signals.get('article_count', 0) > 0,
            'count': rmrb_signals.get('article_count', 0),
            'intensity': rmrb_signals.get('intensity_level', 1),
        },
    }

    intensity = rmrb_signals.get('intensity_level', 1)
    pd_hot = rmrb_signals.get('article_count', 0) >= 3 or intensity >= 4
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
        or intensity >= 6
    )
    normalization_hits = _scan_phrases(texts, NORMALIZATION_PHRASES)
    closing_hits = _scan_phrases(texts, CLOSING_PHRASES)

    # 阶段判定（自上而下首条命中）
    if enforcement and not closing_hits:
        stage = '运动期'
        evidence = '司法/纪检入轨或强度≥6级——执法已展开，窗口关闭。'
    elif closing_hits and intensity <= 4:
        stage = '收尾期'
        evidence = f"出现收尾叙事（{'/'.join(closing_hits)}）且强度回落——整治进入总结阶段，政策松动早期指标。"
    elif normalization_hits and intensity <= 3:
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

    return {
        'stage': stage,
        'confidence': confidence,
        'evidence': evidence,
        'layers': layers,
        'ministry_joint_docs': [
            i for i in ministry_docs.get('items', []) if i.get('is_joint')
        ][:5],
        'lead_lag': lead_lag,
        'degraded_layers': degraded,
        'normalization_hits': normalization_hits,
        'closing_hits': closing_hits,
    }
