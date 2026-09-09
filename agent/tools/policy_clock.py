"""
Tool: 政策时钟校正 + 风险窗口计算（纯代码）

同一信号在不同年度节律阶段，实际行动时差不同。

v3: 基础窗口优先采用历史案例回测校准值（agent/calibration），
    校准失败或样本不足时回退默认值并标注；窗口输出附频率陈述
    （历史同组合案例中 X 个月内行动的比例），替代伪精确点估计。
"""

import datetime
import sys

try:
    from agent.calibration.backtest import get_calibrated_windows, frequency_statement
    _CALIBRATION_AVAILABLE = True
except Exception as e:
    print(f'[policy_clock] 校准模块不可用，使用默认窗口: {e}', file=sys.stderr)
    _CALIBRATION_AVAILABLE = False

CLOCK_PHASES = {
    (1, 2): {
        'phase': '人事调整期',
        'coefficient': 1.5,
        'note': '大政策出台概率低，信号可靠性下降',
    },
    (3, 3): {
        'phase': '两会定调期',
        'coefficient': 1.0,
        'note': '政府工作报告关键词是全年政策锚点',
    },
    (4, 6): {
        'phase': '政策部署期',
        'coefficient': 0.9,
        'note': '部委细化文件密集，信号是执行前奏',
    },
    (7, 8): {
        'phase': '北戴河敏感期',
        'coefficient': 1.3,
        'note': '信号减少不等于风险降低',
    },
    (9, 10): {
        'phase': '整治高发期',
        'coefficient': 0.5,
        'note': '专项行动高峰，与3月信号叠加大概率当年行动',
    },
    (11, 12): {
        'phase': '中经会定调期',
        'coefficient': 0.8,
        'note': '次年议题定调，前瞻价值最高',
    },
}

# 话语强度 → 基础窗口（月）
BASE_WINDOWS = {
    1: (12, 18),
    2: (12, 18),
    3: (6, 12),
    4: (6, 12),
    5: (3, 6),
    6: (1, 3),
    7: (0, 1),
}


def get_policy_clock(date_str: str | None = None) -> dict:
    """
    根据日期返回当前政策时钟阶段和校正系数。

    参数：
      date_str: 格式 YYYYMMDD，默认今天

    返回：
      {phase, coefficient, note, month}
    """
    if date_str:
        d = datetime.datetime.strptime(date_str, '%Y%m%d')
    else:
        d = datetime.datetime.now()

    month = d.month
    for (start, end), config in CLOCK_PHASES.items():
        if start <= month <= end:
            return {
                'phase': config['phase'],
                'coefficient': config['coefficient'],
                'note': config['note'],
                'month': month,
            }

    return {'phase': '未知', 'coefficient': 1.0, 'note': '', 'month': month}


def _resolve_base_window(intensity_level: int) -> tuple:
    """
    基础窗口：优先用案例回测校准的 p25-p75 区间，
    样本不足的等级回退默认值。

    返回 (low, high, basis_dict)。
    """
    default = BASE_WINDOWS.get(intensity_level, (12, 18))
    if not _CALIBRATION_AVAILABLE:
        return default[0], default[1], {'source': 'default', 'note': '校准模块不可用'}

    calibrated = get_calibrated_windows().get(intensity_level)
    if calibrated and calibrated['calibrated']:
        lo, hi = calibrated['window']
        return lo, hi, {
            'source': 'backtest',
            'n': calibrated['n'],
            'observed_days': calibrated['observed_days'],
            'note': f"基于 {calibrated['n']} 个历史案例的 p25-p75 提前量",
        }
    n = calibrated['n'] if calibrated else 0
    return default[0], default[1], {
        'source': 'default',
        'n': n,
        'note': f'该等级历史观测仅 {n} 例，样本不足，使用未校准默认窗口',
    }


def calculate_risk_window(
    intensity_level: int,
    ministry_compression: float,
    clock_coefficient: float,
    narrative_speed_modifier: float,
    ministry_level: str = None,
) -> dict:
    """
    综合计算风险预测窗口。

    窗口 = 基础窗口（回测校准优先）× 部委压缩 × 时钟系数 × 叙事框架速度调整

    返回：
      {
        base_window: str,
        base_window_basis: {source, n, note},
        adjusted_window_months: (float, float),
        adjusted_window_label: str,
        risk_level: str,
        risk_emoji: str,
        frequency: {statement, matched_cases, ...},
        factors: {部委压缩, 时钟系数, 叙事框架调整},
      }
    """
    # F04：没有强度结论就没有窗口。让它走默认基础窗口，等于把"不知道"
    # 讲成"未来 X 个月内会有动作" —— 那是凭空造出来的预测。
    if intensity_level is None:
        return {
            'status': 'unknown',
            'reason': '无强度结论（证据不足），不计算风险窗口',
            'base_window': None,
            'base_window_basis': {'source': 'none', 'note': '证据不足，未取基础窗口'},
            'adjusted_window_months': None,
            'adjusted_window_label': '不适用（证据不足）',
            'risk_level': '未知',
            'risk_emoji': '⚪',
            'frequency': {'statement': '证据不足，不给频率陈述'},
            'factors': {},
        }

    base_lo, base_hi, basis = _resolve_base_window(intensity_level)
    base = (base_lo, base_hi)
    low = base[0] * ministry_compression * clock_coefficient * narrative_speed_modifier
    high = base[1] * ministry_compression * clock_coefficient * narrative_speed_modifier
    low = max(0, round(low, 1))
    high = max(low, round(high, 1))

    # 风险等级
    if intensity_level >= 7 or low < 1:
        risk_level, emoji = '高', '🔴'
    elif intensity_level >= 5 or low < 3:
        risk_level, emoji = '中高', '🟡'
    elif intensity_level >= 3 or low < 6:
        risk_level, emoji = '中', '🟡'
    else:
        risk_level, emoji = '低', '🟢'

    if low < 1:
        window_label = f'{int(low*30)}-{int(high*30)}天内'
    else:
        window_label = f'{low}-{high}个月'

    freq = None
    if _CALIBRATION_AVAILABLE and intensity_level >= 4:
        try:
            freq = frequency_statement(intensity_level, ministry_level)
        except Exception as e:
            print(f'[policy_clock] 频率陈述生成失败: {e}', file=sys.stderr)

    return {
        'base_window': f'{base[0]}-{base[1]}个月',
        'base_window_basis': basis,
        'adjusted_window_months': (low, high),
        'adjusted_window_label': window_label,
        'risk_level': risk_level,
        'risk_emoji': emoji,
        'frequency': freq,
        'factors': {
            'ministry_compression': ministry_compression,
            'clock_coefficient': clock_coefficient,
            'narrative_speed_modifier': narrative_speed_modifier,
        },
    }
