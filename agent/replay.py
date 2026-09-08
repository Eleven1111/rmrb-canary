#!/usr/bin/env python3
"""
历史回放（P3，对应方案 §8.2 / §9.1 第三层）

逐日回放：对每一期把 `as_of` 设成当天，只使用当时可见的证据与历史记录。
这是标定的前提 —— 没有可信的回放，就没有可信的提前量与召回率。

程序保证的三条（不是靠自觉）：
  1. 每期的 `as_of` 等于该期日期，采集层拒绝晚于 `as_of` 的期数；
  2. 历史查询一律 `date < as_of`，当天与之后的记录都读不到；
  3. 回放产生的告警 `delivery_blocked=true`，不触发实时通知。

回放结束会跑一次**泄漏自检**：逐期核对它读到的历史序列里有没有不该看见的日期。
自检失败即视为整次回放作废 —— 混了未来信息的回放比没有回放更糟。

用法：
  python3 -m agent.replay --topic ai --dates 20260901 20260902 20260903
  python3 -m agent.replay --topic ai --from 20260901 --to 20260905
"""

import argparse
import datetime
import json
import sys
import traceback

from agent.agent import run_pipeline
from agent.store import ledger
from agent.tools.baselines import compare_baselines


def date_range(start: str, end: str) -> list[str]:
    d0 = datetime.datetime.strptime(start, '%Y%m%d')
    d1 = datetime.datetime.strptime(end, '%Y%m%d')
    if d1 < d0:
        raise ValueError('起始日期晚于结束日期')
    out, cur = [], d0
    while cur <= d1:
        out.append(cur.strftime('%Y%m%d'))
        cur += datetime.timedelta(days=1)
    return out


def check_leakage(snapshot: dict) -> dict:
    """
    泄漏自检：这一期读到的任何历史记录，日期都必须严格早于它的 as_of。
    """
    as_of = snapshot.get('as_of')
    violations = []

    series = (snapshot.get('rolling_trend') or {}).get('intensity_timeline') or []
    for row in series:
        if row.get('date') and row['date'] >= as_of:
            violations.append({'where': 'rolling_trend', 'date': row['date'], 'as_of': as_of})

    trend = snapshot.get('trend') or {}
    if trend.get('previous_date') and trend['previous_date'] >= as_of:
        violations.append({'where': 'trend.previous_date',
                           'date': trend['previous_date'], 'as_of': as_of})

    recent = (snapshot.get('coverage_change') or {}).get('recent_counts') or []
    for row in recent:
        if row.get('date') and row['date'] >= as_of:
            violations.append({'where': 'coverage_change', 'date': row['date'], 'as_of': as_of})

    if snapshot.get('date') and snapshot['date'] > as_of:
        violations.append({'where': 'snapshot.date', 'date': snapshot['date'], 'as_of': as_of})

    return {'clean': not violations, 'violations': violations}


def replay(topic_key: str, dates: list[str], enterprise_key: str = None,
           skip_media: bool = True, dry_run: bool = False) -> dict:
    """
    逐日回放。默认跳过外部媒体 —— 热搜榜只有"现在"，回放里读它必然是未来信息。
    """
    snapshots, failures, leaks, baseline_rows = [], [], [], []

    for date in dates:
        try:
            snap = run_pipeline(keywords=None, topic_key=topic_key, date=date,
                                as_of=date, skip_media=skip_media)
        except Exception as e:
            failures.append({'date': date, 'error': str(e),
                             'traceback': traceback.format_exc()[-400:]})
            print(f'[回放] {date} 失败：{e}', file=sys.stderr)
            continue

        leak = check_leakage(snap)
        if not leak['clean']:
            leaks.append({'date': date, **leak})

        snapshots.append(snap)
        baseline_rows.append(compare_baselines(snap))


    return {
        'topic_key': topic_key,
        'dates_requested': dates,
        'succeeded': len(snapshots),
        'failed': len(failures),
        'failures': failures,
        'leakage': {
            'clean': not leaks,
            'violations': leaks,
            'verdict': ('通过：每期只读到早于其 as_of 的记录'
                        if not leaks else
                        '**失败：回放读到了不该看见的日期，本次回放作废**'),
        },
        'series': [{
            'date': s.get('date'),
            'as_of': s.get('as_of'),
            'fetch_quality': (s.get('fetch_quality') or {}).get('status'),
            'matched_articles': s.get('total_articles'),
            'documents_matched': (s.get('policy_documents') or {}).get('matched_count'),
            'events_verified': (s.get('extraction') or {}).get('verified_count'),
            'signals_status': (s.get('signals') or {}).get('status'),
            'alerts_created': len((s.get('alerts') or {}).get('created', [])),
            'delivery_blocked': (s.get('alerts') or {}).get('delivery_blocked'),
        } for s in snapshots],
        'baselines': baseline_rows,
        'snapshots': snapshots,
        'ledger_stats': ledger.ledger_stats(),
        'note': '回放告警一律不投递。失败日期单列，不通过剔除失败来美化序列。'
                '预测台账由管道内部的 store/ledger 落账，本模块不另起一套。',
    }


def main():
    p = argparse.ArgumentParser(description='RMRB-Canary 历史回放')
    p.add_argument('--topic', required=True)
    p.add_argument('--enterprise')
    p.add_argument('--dates', nargs='+', help='显式日期列表 YYYYMMDD')
    p.add_argument('--from', dest='date_from')
    p.add_argument('--to', dest='date_to')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--with-media', action='store_true',
                   help='回放时也采外部媒体（不推荐：热搜只有"现在"，必然引入未来信息）')
    p.add_argument('--out', help='把完整结果写入文件')
    a = p.parse_args()

    if a.dates:
        dates = a.dates
    elif a.date_from and a.date_to:
        dates = date_range(a.date_from, a.date_to)
    else:
        p.error('需要 --dates 或 --from/--to')

    result = replay(a.topic, dates, enterprise_key=a.enterprise,
                    skip_media=not a.with_media, dry_run=a.dry_run)

    print(f"[回放] 成功 {result['succeeded']}/{len(dates)} 期，"
          f"泄漏自检：{result['leakage']['verdict']}", file=sys.stderr)

    payload = {k: v for k, v in result.items() if k != 'snapshots'}
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f'[回放] 完整结果 → {a.out}', file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
