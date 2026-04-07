"""
Tool: 沉默检测 + 多期滚动趋势（纯代码）

SKILL.md 核心认知："官方表述的沉默有时比发声更重要。"
本模块检测关键词在历史数据中的出现频率变化，识别三类信号：
  - 沉默：之前高频出现，本期突然消失
  - 降温：出现频率显著下降
  - 升温：出现频率显著上升

同时提供 7/30/90 日滚动趋势线。
"""

import sqlite3
import json
import datetime
from agent.store.db import get_conn


def detect_silence(keywords: list[str], current_date: str, current_count: int) -> dict:
    """
    检测关键词在历史数据中的沉默/降温/升温信号。

    参数：
      keywords: 关键词列表
      current_date: 当前分析日期 YYYYMMDD
      current_count: 当前期匹配文章数

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
    conn = get_conn()

    # 查询同关键词组的历史分析（按日期倒序）
    kw_json = json.dumps(sorted(keywords), ensure_ascii=False)
    rows = conn.execute(
        """SELECT date, total_articles, keywords FROM analyses
           WHERE keywords = ?
           ORDER BY date DESC LIMIT 30""",
        (kw_json,)
    ).fetchall()

    # 回退：模糊匹配
    if len(rows) < 3:
        like_conditions = ' AND '.join(
            'keywords LIKE ?' for _ in keywords
        )
        like_params = [f'%{kw}%' for kw in keywords]
        rows = conn.execute(
            f"""SELECT date, total_articles, keywords FROM analyses
                WHERE {like_conditions}
                ORDER BY date DESC LIMIT 30""",
            like_params,
        ).fetchall()

    conn.close()

    # 排除当前日期
    historical = [
        {'date': r['date'], 'count': r['total_articles']}
        for r in rows
        if r['date'] != current_date
    ]

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


def rolling_trend(keywords: list[str], windows: list[int] = None) -> dict:
    """
    多期滚动趋势分析。

    参数：
      keywords: 关键词列表
      windows: 滚动窗口天数列表，默认 [7, 30, 90]

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

    conn = get_conn()
    kw_json = json.dumps(sorted(keywords), ensure_ascii=False)

    # 获取所有历史分析
    rows = conn.execute(
        """SELECT date, primary_frame, max_intensity, total_articles,
                  risk_signal, ministry_level
           FROM analyses
           WHERE keywords = ?
           ORDER BY date DESC LIMIT 90""",
        (kw_json,)
    ).fetchall()

    # 回退模糊匹配
    if len(rows) < 3:
        like_conditions = ' AND '.join('keywords LIKE ?' for _ in keywords)
        like_params = [f'%{kw}%' for kw in keywords]
        rows = conn.execute(
            f"""SELECT date, primary_frame, max_intensity, total_articles,
                       risk_signal, ministry_level
                FROM analyses
                WHERE {like_conditions}
                ORDER BY date DESC LIMIT 90""",
            like_params,
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
    now = datetime.datetime.now()

    for w in windows:
        cutoff = (now - datetime.timedelta(days=w)).strftime('%Y%m%d')
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

        intensities = [r['max_intensity'] for r in in_window]
        articles = [r['total_articles'] for r in in_window]
        frames = [r['primary_frame'] for r in in_window]

        # 趋势方向：前半 vs 后半平均强度
        mid = len(intensities) // 2
        if mid > 0:
            first_half_avg = sum(intensities[:mid]) / mid
            second_half_avg = sum(intensities[mid:]) / (len(intensities) - mid)
            delta = second_half_avg - first_half_avg
            if delta > 0.5:
                direction = '上升'
            elif delta < -0.5:
                direction = '下降'
            else:
                direction = '持平'
        else:
            direction = '数据不足'

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
        }

    return {
        'windows': window_results,
        'intensity_timeline': intensity_timeline[:30],
        'frame_timeline': frame_timeline[:30],
        'data_points': len(all_records),
    }
