"""
Tool: 滚动窗口聚合判定（纯代码）

单日报纸是小样本，逐日打分天然震荡（实测同一关键词组一周内
强度在 2-5 级、协同在 L1-L5 之间跳动）。本模块以近 N 日全部
分析记录为窗口，输出平滑判定：

  - 强度平滑值：按时近指数衰减加权（半衰期 3 天）
  - 主导框架 + 框架稳定度（主导框架占比）
  - 部委协同：窗口内最高档 + 众数档
  - 震荡指数：窗口内强度标准差，量化"今天的数字可信几分"

判定以平滑值为准，单日值仅作增量提示。
"""

import json
import math
import datetime

from agent.store.db import get_conn

HALF_LIFE_DAYS = 3.0
MIN_POINTS_HIGH = 5
MIN_POINTS_MEDIUM = 3


def _load_window_rows(keywords: list[str], end_date: str, window_days: int) -> list[dict]:
    conn = get_conn()
    kw_json = json.dumps(sorted(keywords), ensure_ascii=False)
    end = datetime.datetime.strptime(end_date, '%Y%m%d')
    cutoff = (end - datetime.timedelta(days=window_days - 1)).strftime('%Y%m%d')

    query = """SELECT id, date, primary_frame,
                      COALESCE(weighted_max_intensity, max_intensity) AS intensity,
                      ministry_level, total_articles
               FROM analyses
               WHERE keywords = ? AND date >= ? AND date <= ?
               ORDER BY date DESC, id DESC"""
    rows = conn.execute(query, (kw_json, cutoff, end_date)).fetchall()

    if len(rows) < 2:
        like_conditions = ' AND '.join('keywords LIKE ?' for _ in keywords)
        like_params = [f'%{kw}%' for kw in keywords] + [cutoff, end_date]
        rows = conn.execute(
            f"""SELECT id, date, primary_frame,
                       COALESCE(weighted_max_intensity, max_intensity) AS intensity,
                       ministry_level, total_articles
                FROM analyses
                WHERE {like_conditions} AND date >= ? AND date <= ?
                ORDER BY date DESC, id DESC""",
            like_params,
        ).fetchall()
    conn.close()

    # 同日多次分析只取最新一条
    seen_dates = set()
    deduped = []
    for r in rows:
        if r['date'] in seen_dates:
            continue
        seen_dates.add(r['date'])
        deduped.append(dict(r))
    return deduped


def rolling_verdict(keywords: list[str], end_date: str = None, window_days: int = 7) -> dict:
    """
    输出近 window_days 日的平滑判定。

    需在当期分析入库之后调用，窗口含当期。

    返回：
      {
        data_points, window_days, dates,
        intensity_smoothed, intensity_today, intensity_std,
        oscillation_label, dominant_frame, frame_stability,
        ministry_max, ministry_mode, confidence, guidance,
      }
    """
    if end_date is None:
        end_date = datetime.datetime.now().strftime('%Y%m%d')

    rows = _load_window_rows(keywords, end_date, window_days)
    if not rows:
        return {
            'data_points': 0,
            'window_days': window_days,
            'confidence': 'none',
            'guidance': '窗口内无分析记录，无法平滑。建议建立每日基线采集。',
        }

    end = datetime.datetime.strptime(end_date, '%Y%m%d')
    weighted_sum = 0.0
    weight_total = 0.0
    intensities = []
    for r in rows:
        age = (end - datetime.datetime.strptime(r['date'], '%Y%m%d')).days
        w = 0.5 ** (age / HALF_LIFE_DAYS)
        weighted_sum += (r['intensity'] or 0) * w
        weight_total += w
        intensities.append(r['intensity'] or 0)

    smoothed = weighted_sum / weight_total if weight_total else 0.0

    mean = sum(intensities) / len(intensities)
    variance = sum((x - mean) ** 2 for x in intensities) / len(intensities)
    std = math.sqrt(variance)
    if std < 0.6:
        oscillation = '稳定'
    elif std < 1.2:
        oscillation = '波动'
    else:
        oscillation = '震荡'

    frames = [r['primary_frame'] for r in rows if r['primary_frame']]
    dominant_frame = max(set(frames), key=frames.count) if frames else '未识别'
    frame_stability = round(frames.count(dominant_frame) / len(frames), 2) if frames else 0.0

    levels = [r['ministry_level'] or 'L0' for r in rows]
    ministry_max = max(levels)
    ministry_mode = max(set(levels), key=levels.count)

    n = len(rows)
    if n >= MIN_POINTS_HIGH:
        confidence = 'high'
    elif n >= MIN_POINTS_MEDIUM:
        confidence = 'medium'
    else:
        confidence = 'low'

    guidance_parts = [f'窗口内 {n} 期数据，平滑强度 {smoothed:.1f} 级。']
    if oscillation == '震荡':
        guidance_parts.append(
            f'强度标准差 {std:.1f}，日间震荡明显——单日等级不可作为趋势依据，以平滑值为准。'
        )
    if frame_stability < 0.7 and len(frames) >= 3:
        guidance_parts.append(
            f'框架稳定度仅 {frame_stability:.0%}，框架判定受单日版面噪声影响，需人工复核框架漂移是否真实。'
        )
    if confidence == 'low':
        guidance_parts.append('数据点不足 3 期，平滑值参考意义有限，建议先积累每日基线。')

    return {
        'data_points': n,
        'window_days': window_days,
        'dates': [r['date'] for r in rows],
        'intensity_smoothed': round(smoothed, 1),
        'intensity_today': rows[0]['intensity'],
        'intensity_std': round(std, 2),
        'oscillation_label': oscillation,
        'dominant_frame': dominant_frame,
        'frame_stability': frame_stability,
        'ministry_max': ministry_max,
        'ministry_mode': ministry_mode,
        'confidence': confidence,
        'guidance': ' '.join(guidance_parts),
    }
