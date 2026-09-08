#!/usr/bin/env python3
"""
可观测运行器（P2，对应方案 §7.1）

按双节奏对多个主题跑分析，共享同一份文档缓存，输出一份可核验的运行报告：
每个主题的耗时、采集质量、缓存命中、新建/去重告警、失败原因。

**本模块不自带调度器，也不会自己跑起来。** 它只是一个可以被调度的入口。
按 CLAUDE.md §2，任何后台作业必须先登记到项目根目录 AUTOPILOT.md
（名称、周期、一行停止命令）并经用户确认后才能启动。
本轮只交付运行器与登记模板，没有启动任何东西。

用法：
  python3 -m agent.runner --topics ai vaccine --enterprise example-enterprise
  python3 -m agent.runner --topics ai --dry-run
  python3 -m agent.runner --pending          # 查看待投递告警队列
"""

import argparse
import datetime
import json
import sys
import time
import traceback

from agent.agent import run_pipeline
from agent.store import alertstore
from agent.tools.alerts import latency_metrics, render_alert


def run_batch(topic_keys: list[str], enterprise_key: str = None,
              date: str = None, as_of: str = None, dry_run: bool = False,
              skip_media: bool = False, skip_documents: bool = False) -> dict:
    """
    依次跑多个主题。文档缓存在进程内跨主题共享 ——
    §7.1「各主题共享抓取结果」：不要在每个关键词分析时重新抓全报。
    """
    started = datetime.datetime.now()
    results, failures = [], []

    for key in topic_keys:
        t0 = time.time()
        try:
            snap = run_pipeline(topic_key=key, enterprise_key=enterprise_key,
                                date=date, as_of=as_of, dry_run=dry_run,
                                skip_media=skip_media, skip_documents=skip_documents)
            alerts = snap.get('alerts') or {}
            results.append({
                'topic_key': key,
                'topic_label': snap.get('topic_label'),
                'date': snap.get('date'),
                'as_of': snap.get('as_of'),
                'elapsed_seconds': round(time.time() - t0, 1),
                'fetch_quality': (snap.get('fetch_quality') or {}).get('status'),
                'matched_articles': snap.get('total_articles'),
                'scanned_articles': snap.get('total_scanned'),
                'policy_documents_matched': (snap.get('policy_documents') or {}).get('matched_count'),
                'cache': snap.get('cache_report'),
                'events_verified': (snap.get('extraction') or {}).get('verified_count'),
                'events_unsupported': (snap.get('extraction') or {}).get('unsupported_count'),
                'needs_review': (snap.get('signals') or {}).get('needs_review_count'),
                'alerts_created': len(alerts.get('created', [])),
                'alerts_updated': len(alerts.get('updated', [])),
                'alerts_duplicate_suppressed': alerts.get('duplicate_suppressed'),
                'delivery_blocked': alerts.get('delivery_blocked'),
                'summary_line': snap.get('summary_line'),
            })
        except Exception as e:
            failures.append({'topic_key': key, 'error': str(e),
                             'traceback': traceback.format_exc()[-800:],
                             'elapsed_seconds': round(time.time() - t0, 1)})
            print(f'[运行器] 主题 {key} 失败：{e}', file=sys.stderr)

    all_alerts = alertstore.list_alerts(limit=500)
    return {
        'started_at': started.isoformat(),
        'finished_at': datetime.datetime.now().isoformat(),
        'total_seconds': round((datetime.datetime.now() - started).total_seconds(), 1),
        'topics_requested': topic_keys,
        'succeeded': len(results),
        'failed': len(failures),
        'results': results,
        'failures': failures,
        'pending_deliveries': len(alertstore.pending_deliveries()),
        'latency': latency_metrics(all_alerts),
        'note': ('失败主题单列，不通过剔除失败记录美化数据。'
                 '本运行器不自带调度；后台运行须先登记 AUTOPILOT.md 并经用户确认。'),
    }


def show_pending() -> dict:
    pending = alertstore.pending_deliveries()
    return {
        'pending_count': len(pending),
        'alerts': [render_alert(p) for p in pending],
        'note': '这些告警已确认但尚未收到发送回执，状态停在待投递。'
                '收到回执后用 alertstore.record_receipt() 推进到已通知。',
    }


def main():
    p = argparse.ArgumentParser(description='RMRB-Canary 批量运行器（不含调度器）')
    p.add_argument('--topics', nargs='+', help='主题名列表')
    p.add_argument('--enterprise', help='企业画像名')
    p.add_argument('--date', help='采集日期 YYYYMMDD')
    p.add_argument('--as-of', dest='as_of', help='分析截止时点 YYYYMMDD')
    p.add_argument('--dry-run', action='store_true', help='不写入历史库')
    p.add_argument('--skip-media', action='store_true')
    p.add_argument('--skip-documents', action='store_true')
    p.add_argument('--pending', action='store_true', help='查看待投递告警')
    a = p.parse_args()

    if a.pending:
        print(json.dumps(show_pending(), ensure_ascii=False, indent=2))
        return
    if not a.topics:
        p.print_help()
        return

    report = run_batch(a.topics, enterprise_key=a.enterprise, date=a.date,
                       as_of=a.as_of, dry_run=a.dry_run,
                       skip_media=a.skip_media, skip_documents=a.skip_documents)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
