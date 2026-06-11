"""
回测校准：用已知结局的历史案例标定风险窗口。

SKILL.md 在局限一里写了两年"建议用已知结果的行业案例进行
自我验证"——本模块就是那个验证回路：

  1. observed_leads_by_level — 各强度等级的实际提前量分布
     （信号日 → 行动日，按案例库 milestone 计算）
  2. get_calibrated_windows  — 用观察分位数替换拍脑袋窗口；
     样本 <2 的等级保留默认值并标注 uncalibrated
  3. frequency_statement     — 把"3-6个月"式伪精确换成频率陈述：
     "9 例中出现≥5级信号的 N 例，M 例在 3 个月内行动"

数据源 cases.json 为人工整理 + 回溯标注，样本量小（n=9），
输出一律附 n 和原始观测，供分析师自行掂量。
"""

import datetime
import json
import os

CASES_PATH = os.path.join(os.path.dirname(__file__), 'cases.json')
DAYS_PER_MONTH = 30.4

_cache = {}


def load_cases() -> dict:
    if 'cases' not in _cache:
        with open(CASES_PATH, encoding='utf-8') as f:
            _cache['cases'] = json.load(f)
    return _cache['cases']


def _to_date(s: str) -> datetime.date:
    return datetime.datetime.strptime(s, '%Y-%m-%d').date()


def _ministry_rank(level: str) -> int:
    try:
        return int((level or 'L0')[1])
    except (ValueError, IndexError):
        return 0


def signal_leads() -> list[dict]:
    """展开案例库：每条前导信号一行，含提前量。"""
    rows = []
    for case in load_cases()['cases']:
        action = _to_date(case['action_date'])
        for m in case['milestones']:
            if m.get('role') != 'signal':
                continue
            lead = (action - _to_date(m['date'])).days
            rows.append({
                'case': case['name'],
                'case_id': case['id'],
                'frame': case['frame'],
                'date': m['date'],
                'event': m['event'],
                'intensity': m['retro_intensity'],
                'ministry': m.get('retro_ministry', 'L0'),
                'lead_days': lead,
                'date_precision': m.get('date_precision', 'day'),
            })
    return rows


def observed_leads_by_level() -> dict:
    """各强度等级的实际提前量（天）观测值。"""
    by_level = {}
    for row in signal_leads():
        by_level.setdefault(row['intensity'], []).append(row['lead_days'])
    return {lvl: sorted(days) for lvl, days in sorted(by_level.items())}


def _quantile(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def get_calibrated_windows() -> dict:
    """
    返回 {level: {'window': (low_mo, high_mo), 'n': int,
                  'observed_days': [...], 'calibrated': bool}}。

    校准方法：≥2 个观测的等级用 p25-p75 分位区间（月）；
    观测不足的等级 calibrated=False，调用方应回退默认窗口。
    """
    if 'windows' in _cache:
        return _cache['windows']

    result = {}
    for level, days in observed_leads_by_level().items():
        if len(days) >= 2:
            low = round(_quantile(days, 0.25) / DAYS_PER_MONTH, 1)
            high = round(_quantile(days, 0.75) / DAYS_PER_MONTH, 1)
            result[level] = {
                'window': (max(low, 0.1), max(high, low + 0.1)),
                'n': len(days),
                'observed_days': days,
                'calibrated': True,
            }
        else:
            result[level] = {
                'window': None,
                'n': len(days),
                'observed_days': days,
                'calibrated': False,
            }
    _cache['windows'] = result
    return result


def frequency_statement(intensity_level: int, ministry_level: str = None) -> dict:
    """
    频率陈述：历史案例中出现同等或更强信号组合的案例里，
    多少比例在 3/6 个月内发生实质行动。
    """
    cases = load_cases()['cases']
    total = len(cases)
    rows = signal_leads()

    matched = {}
    for row in rows:
        if row['intensity'] < intensity_level:
            continue
        if ministry_level and _ministry_rank(row['ministry']) < _ministry_rank(ministry_level):
            continue
        cid = row['case_id']
        if cid not in matched or row['lead_days'] < matched[cid]['lead_days']:
            matched[cid] = row

    n = len(matched)
    if n == 0:
        return {
            'matched_cases': 0,
            'total_cases': total,
            'statement': (
                f'案例库 {total} 例中无"强度≥{intensity_level}级'
                + (f' + 协同≥{ministry_level}' if ministry_level else '')
                + '"的先例，频率陈述不可用——这是超出历史经验的信号组合，本身值得警惕。'
            ),
        }

    within_3 = sum(1 for r in matched.values() if r['lead_days'] <= 92)
    within_6 = sum(1 for r in matched.values() if r['lead_days'] <= 183)
    combo = f'强度≥{intensity_level}级' + (f'且协同≥{ministry_level}' if ministry_level else '')

    return {
        'matched_cases': n,
        'total_cases': total,
        'within_3mo': within_3,
        'within_6mo': within_6,
        'case_names': [r['case'] for r in matched.values()],
        'statement': (
            f'历史案例库 {total} 例中，出现"{combo}"信号的 {n} 例；'
            f'其中 {within_3} 例在 3 个月内、{within_6} 例在 6 个月内发生实质行动。'
            f'（样本量小，仅供参照）'
        ),
    }


def calibration_report() -> str:
    """生成完整校准报告（CLI 用）。"""
    lines = ['# 风险窗口回测校准报告', '']
    meta = load_cases()
    lines.append(f"案例库版本：{meta['version']}　案例数：{len(meta['cases'])}")
    lines.append(f"方法：{meta['methodology']}")
    lines.append('')
    lines.append('## 各强度等级实际提前量（信号日→行动日）')
    for level, info in get_calibrated_windows().items():
        days_str = ', '.join(f'{d}天' for d in info['observed_days'])
        if info['calibrated']:
            lo, hi = info['window']
            lines.append(
                f'- {level}级：n={info["n"]}，观测 [{days_str}]，'
                f'校准窗口 p25-p75 = {lo}-{hi} 个月'
            )
        else:
            lines.append(
                f'- {level}级：n={info["n"]}，观测 [{days_str}]——样本不足，保留默认窗口'
            )
    lines.append('')
    lines.append('## 逐信号明细')
    for row in signal_leads():
        precision = '（仅月份精确）' if row['date_precision'] == 'month' else ''
        lines.append(
            f"- [{row['case']}] {row['date']}{precision} "
            f"{row['intensity']}级/{row['ministry']} → 提前 {row['lead_days']} 天｜{row['event']}"
        )
    return '\n'.join(lines)


if __name__ == '__main__':
    print(calibration_report())
