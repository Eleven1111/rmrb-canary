"""
议题档案（dossier）— 从一次性报告到持续生长的情报资产。

每个议题一份 Markdown 档案：信号时间线、框架漂移史、预测与
复盘、分析师判断，逐期追加。下次分析时档案尾部与上期判断
注入 LLM 上下文，形成跨期对话。

位置：与数据库同目录的 dossiers/<slug>.md
"""

import datetime
import hashlib
import os
import re

from agent.store.db import get_db_path
from agent.store.judgments import get_recent_judgments


def _dossier_dir() -> str:
    return os.path.join(os.path.dirname(get_db_path()), 'dossiers')


def topic_slug(keywords: list[str]) -> str:
    joined = '-'.join(sorted(keywords))
    slug = re.sub(r'[^\w一-鿿-]', '', joined)
    if len(slug) > 40:
        digest = hashlib.md5(joined.encode()).hexdigest()[:8]
        slug = slug[:32] + '-' + digest
    return slug or 'untitled'


def dossier_path(keywords: list[str]) -> str:
    return os.path.join(_dossier_dir(), topic_slug(keywords) + '.md')


def _fmt_formulations(formulation: dict) -> str:
    parts = []
    for f in (formulation or {}).get('active', [])[:5]:
        mark = '↑' if f.get('escalated') else ''
        parts.append(f"{f['phrase']}({f['peak']}{mark})")
    for f in (formulation or {}).get('newly_discovered', [])[:3]:
        parts.append(f"🆕{f['phrase']}?")
    return '、'.join(parts) if parts else '—'


def build_entry(result: dict) -> str:
    """从管道结果构建一期档案条目。"""
    date = result.get('rmrb', {}).get('date', '')
    date_fmt = f'{date[:4]}-{date[4:6]}-{date[6:]}' if len(date) == 8 else date

    intensity = result.get('intensity', {})
    rolling = result.get('rolling', {})
    narrative = result.get('narrative', {})
    ministry = result.get('ministry', {})
    risk = result.get('risk_window', {})
    transmission = result.get('transmission', {})
    silence = result.get('silence', {})
    trend = result.get('trend', {})

    lines = [f'## {date_fmt}', '']
    if rolling.get('data_points', 0) > 1:
        lines.append(
            f"- 平滑强度：{rolling.get('intensity_smoothed')}级"
            f"（{rolling.get('window_days')}日窗·n={rolling.get('data_points')}·{rolling.get('oscillation_label')}）"
            f"｜当日 {intensity.get('weighted_max_level', '?')}级"
        )
    else:
        lines.append(
            f"- 强度：{intensity.get('weighted_max_level', '?')}级"
            f"（{intensity.get('max_level_name', '')}）｜首期或窗口数据不足"
        )
    frame_stab = (
        f"（稳定度{rolling.get('frame_stability'):.0%}）"
        if rolling.get('frame_stability') is not None else ''
    )
    lines.append(f"- 框架：{narrative.get('primary_frame', '未识别')}{frame_stab}")
    if trend.get('narrative_drifted'):
        lines.append(f"- ⚠️ 框架漂移：{trend.get('narrative_drift')}")
    lines.append(
        f"- 部委协同：{ministry.get('coordination_level', 'L0')}"
        f"｜传导链定位：{transmission.get('stage', '未运行')}"
    )
    lines.append(
        f"- 风险窗口：{risk.get('adjusted_window_label', '—')} {risk.get('risk_emoji', '')}"
    )
    if silence.get('signal') not in (None, '正常', '首次'):
        lines.append(f"- 沉默信号：{silence.get('signal')}（{silence.get('detail', '')}）")
    lines.append(f"- 提法动态：{_fmt_formulations(result.get('formulation'))}")

    prediction_id = result.get('prediction', {}).get('prediction_id')
    if prediction_id:
        lines.append(f"- 预测落账：#{prediction_id}")
    due = result.get('prediction', {}).get('due_review', [])
    if due:
        ids = ', '.join(f"#{p['id']}" for p in due)
        lines.append(f"- ⏰ 待复盘预测：{ids}（窗口已到期，用 --resolve 裁定）")
    lines.append('')
    return '\n'.join(lines)


def update_dossier(result: dict) -> str:
    """将本期条目追加进议题档案，返回档案路径。"""
    keywords = result.get('keywords', [])
    path = dossier_path(keywords)
    os.makedirs(_dossier_dir(), exist_ok=True)

    if not os.path.exists(path):
        header = (
            f"# 议题档案：{' / '.join(sorted(keywords))}\n\n"
            f"创建：{datetime.date.today().isoformat()}\n"
            f"说明：逐期追加的信号时间线与判断沉淀，由 rmrb-canary 管道维护。\n\n"
        )
        with open(path, 'w', encoding='utf-8') as f:
            f.write(header)

    with open(path, 'a', encoding='utf-8') as f:
        f.write(build_entry(result))
    return path


def append_judgment_to_dossier(keywords: list[str], date: str, judgment_text: str):
    """分析师判断录入后同步写进档案。"""
    path = dossier_path(keywords)
    if not os.path.exists(path):
        os.makedirs(_dossier_dir(), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"# 议题档案：{' / '.join(sorted(keywords))}\n\n")
    date_fmt = f'{date[:4]}-{date[4:6]}-{date[6:]}' if len(date) == 8 else date
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f'### 分析师判断（{date_fmt}）\n\n{judgment_text}\n\n')
    return path


def dossier_context(keywords: list[str], tail_chars: int = 2500) -> dict:
    """
    供 LLM 注入的跨期上下文：档案尾部 + 最近分析师判断。
    """
    path = dossier_path(keywords)
    tail = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            content = f.read()
        tail = content[-tail_chars:]

    judgments = get_recent_judgments(keywords, limit=2)
    return {
        'dossier_path': path if os.path.exists(path) else None,
        'dossier_tail': tail,
        'previous_judgments': [
            {
                'date': j['date'],
                'judgment': j['judgment'],
                'surprising_signal': j['surprising_signal'],
                'tension': j['tension'],
            }
            for j in judgments
        ],
        'note': (
            '以上为该议题历史档案与上期分析师判断，撰写本期报告时'
            '应回应上期判断（验证/修正/推翻），不要失忆重来。'
            if (tail or judgments) else '该议题无历史档案，本期为首篇。'
        ),
    }
