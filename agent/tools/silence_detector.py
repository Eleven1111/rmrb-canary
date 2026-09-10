"""
Tool: 沉默检测 + 多期滚动趋势（纯代码）

SKILL.md 核心认知："官方表述的沉默有时比发声更重要。"
本模块检测关键词在历史数据中的出现频率变化，识别三类信号：
  - 沉默：之前高频出现，本期突然消失
  - 降温：出现频率显著下降
  - 升温：出现频率显著上升

同时提供 7/30/90 日滚动趋势线。
"""

import datetime
from agent.store.db import get_conn
from agent.versioning import make_topic_id


def detect_silence(keywords: list[str], current_date: str, current_count: int,
                   topic_id: str = None, topic_version: str = None,
                   algo_version: str = None, quality_complete: bool = True) -> dict:
    """
    检测关键词在历史数据中的沉默/降温/升温信号。

    F03：历史窗口在 SQL 里就以 `date < current_date` 截断。原实现取的是
    全表最近 30 条、再在 Python 里剔掉等于当日的那条 —— 历史回放（as_of 在过去）
    时，比 current_date **更晚** 的记录会被当成"历史"算进平均值，
    于是"本期是不是异常沉默"这个判断用到了它当时还不可能知道的数据。

    F10：按 topic_id 精确匹配，不做关键词 LIKE 回退（会混入别的主题）。

    参数：
      keywords: 关键词列表
      current_date: 当前分析日期 YYYYMMDD
      current_count: 当前期匹配文章数
      topic_id: 可选，事件层已解析出主题时直接传入

    返回：
      {
        signal: str ('沉默' | '降温' | '正常' | '升温' | '首次'),
        signal_strength: float (0-1, 变化幅度归一化),
        current_count: int,
        historical_avg: float,
        historical_max: int,
        recent_counts: [{date, count}],  # 最近10期
        detail: str,
      }
    """
    if not quality_complete:
        return {'signal': '未知', 'signal_strength': None, 'current_count': current_count,
                'historical_avg': None, 'historical_max': None, 'recent_counts': [],
                'detail': '本期采集不完整，禁止用报道量给出沉默或升降结论。'}
    conn = get_conn()
    tid = topic_id or make_topic_id(keywords)
    # 质量未知的旧行不参加生产入口的判断（入口会传 quality_complete=False），
    # 但保留给显式调用者的兼容路径，避免升级前无该列的历史被静默抹掉。
    clauses = ['topic_id = ?', 'date < ?',
               "(quality_status = 'complete' OR quality_status IS NULL)"]
    params = [tid, current_date]
    if topic_version is not None:
        clauses.append('topic_version = ?'); params.append(topic_version)
    if algo_version is not None:
        clauses.append('algo_version = ?'); params.append(algo_version)
    rows = conn.execute('SELECT date, total_articles FROM analyses WHERE ' +
                        ' AND '.join(clauses) + ' ORDER BY date DESC LIMIT 30', params).fetchall()
    conn.close()

    historical = [{'date': r['date'], 'count': r['total_articles']} for r in rows]

    if not historical:
        return {
            'signal': '首次',
            'signal_strength': 0.0,
            'current_count': current_count,
            'historical_avg': 0.0,
            'historical_max': 0,
            'recent_counts': [],
            'detail': '无历史数据，当前为首次分析此关键词组。',
        }

    counts = [h['count'] for h in historical]
    avg = sum(counts) / len(counts)
    max_count = max(counts)

    # 计算变化信号
    if avg == 0:
        if current_count > 0:
            signal, strength = '升温', 1.0
        else:
            signal, strength = '正常', 0.0
    elif current_count == 0 and avg >= 2:
        signal, strength = '沉默', 1.0
    else:
        change_ratio = (current_count - avg) / avg
        if change_ratio <= -0.5:
            signal = '降温'
            strength = min(abs(change_ratio), 1.0)
        elif change_ratio >= 0.5:
            signal = '升温'
            strength = min(change_ratio, 1.0)
        else:
            signal = '正常'
            strength = abs(change_ratio)

    detail_map = {
        '沉默': f'关键词在历史平均 {avg:.1f} 篇/期，本期 {current_count} 篇——议题从报道中消失，可能表示官方刻意回避或阶段性结束。',
        '降温': f'关键词从历史平均 {avg:.1f} 篇/期降至 {current_count} 篇——议题热度下降，可能是整改期尾声或政策松动信号。',
        '升温': f'关键词从历史平均 {avg:.1f} 篇/期升至 {current_count} 篇——议题被加温，需关注后续是否伴随监管动作。',
        '正常': f'关键词本期 {current_count} 篇，历史平均 {avg:.1f} 篇/期——波动在正常范围内。',
        '首次': '无历史数据。',
    }

    return {
        'signal': signal,
        'signal_strength': round(strength, 2),
        'current_count': current_count,
        'historical_avg': round(avg, 1),
        'historical_max': max_count,
        'recent_counts': historical[:10],
        'detail': detail_map[signal],
    }


def rolling_trend(keywords: list[str], windows: list[int] = None,
                  as_of: str = None, topic_id: str = None) -> dict:
    """
    多期滚动趋势分析。

    F03 两处：查询以 `date <= as_of` 截断；窗口起点也从 as_of 往回推，
    而不是从 `datetime.now()`。原实现两处都没有 as_of ——
    回放 2026-06-01 时，"近 7 天"算的是今天往前 7 天，窗口里一条记录都不该有，
    却因为没有上界把之后所有期都当成了历史。

    参数：
      keywords: 关键词列表
      windows: 滚动窗口天数列表，默认 [7, 30, 90]
      as_of: 截止时点 YYYYMMDD，默认今天。历史回放必须显式给出。
      topic_id: 可选，事件层已解析出主题时直接传入

    返回：
      {
        windows: {
          '7d':  {count, avg_intensity, avg_articles, trend_direction, frame_changes},
          '30d': {...},
          '90d': {...},
        },
        intensity_timeline: [{date, max_level, level_name}],  # 强度时间线
        frame_timeline: [{date, primary_frame}],               # 框架时间线
      }
    """
    if windows is None:
        windows = [7, 30, 90]
    if as_of is None:
        as_of = datetime.datetime.now().strftime('%Y%m%d')

    conn = get_conn()
    tid = topic_id or make_topic_id(keywords)
    rows = conn.execute(
        """SELECT date, primary_frame,
                  COALESCE(weighted_max_intensity, max_intensity) AS max_intensity,
                  total_articles, risk_signal, ministry_level
           FROM analyses
           WHERE topic_id = ? AND date <= ?
           ORDER BY date DESC LIMIT 90""",
        (tid, as_of)
    ).fetchall()
    conn.close()

    if not rows:
        return {
            'windows': {},
            'intensity_timeline': [],
            'frame_timeline': [],
            'data_points': 0,
        }

    all_records = [dict(r) for r in rows]

    # 时间线
    intensity_timeline = [
        {'date': r['date'], 'max_level': r['max_intensity'],
         'level_name': r.get('risk_signal', '')}
        for r in all_records
    ]
    frame_timeline = [
        {'date': r['date'], 'primary_frame': r['primary_frame']}
        for r in all_records
    ]

    # 按窗口聚合
    window_results = {}
    anchor = datetime.datetime.strptime(as_of, '%Y%m%d')

    for w in windows:
        cutoff = (anchor - datetime.timedelta(days=w)).strftime('%Y%m%d')
        in_window = [r for r in all_records if r['date'] >= cutoff]

        if not in_window:
            window_results[f'{w}d'] = {
                'count': 0,
                'avg_intensity': 0,
                'avg_articles': 0,
                'trend_direction': '无数据',
                'frame_changes': 0,
            }
            continue

        # F01：查询是 ORDER BY date DESC（新→旧），必须先转成时间升序再分半。
        # 原实现直接在倒序列表上取"前半 vs 后半"，前半其实是**较新**的一段，
        # 于是 2、2、6、6（由旧到新）会被报成"下降"—— 方向整个反了。
        ordered = sorted(in_window, key=lambda r: r['date'])

        intensities = [r['max_intensity'] for r in ordered]
        articles = [r['total_articles'] for r in ordered]
        frames = [r['primary_frame'] for r in ordered]

        # 趋势方向：较早半段 vs 较近半段的平均强度
        mid = len(intensities) // 2
        if mid > 0:
            earlier_avg = sum(intensities[:mid]) / mid          # 较早
            later_avg = sum(intensities[mid:]) / (len(intensities) - mid)  # 较近
            delta = later_avg - earlier_avg
            if delta > 0.5:
                direction = '上升'
            elif delta < -0.5:
                direction = '下降'
            else:
                direction = '持平'
            comparison = {
                'earlier_window': f"{ordered[0]['date']}–{ordered[mid - 1]['date']}",
                'later_window': f"{ordered[mid]['date']}–{ordered[-1]['date']}",
                'earlier_avg_intensity': round(earlier_avg, 2),
                'later_avg_intensity': round(later_avg, 2),
                'delta': round(delta, 2),
            }
        else:
            direction = '数据不足'
            comparison = None

        # 框架变化次数
        frame_changes = sum(
            1 for i in range(1, len(frames)) if frames[i] != frames[i - 1]
        )

        window_results[f'{w}d'] = {
            'count': len(in_window),
            'avg_intensity': round(sum(intensities) / len(intensities), 1),
            'avg_articles': round(sum(articles) / len(articles), 1),
            'trend_direction': direction,
            'frame_changes': frame_changes,
            # 把比较窗口一并给出：只报"上升/下降"而不说比的是哪两段，
            # 读者无法核对方向对不对。
            'comparison': comparison,
        }

    return {
        'windows': window_results,
        'intensity_timeline': intensity_timeline[:30],
        'frame_timeline': frame_timeline[:30],
        'data_points': len(all_records),
    }
