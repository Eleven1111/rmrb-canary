#!/usr/bin/env python3
"""
daily_baseline.py — 每日基线采集入口

沉默检测、滚动平滑、提法生命周期全部依赖连续的每日基线。
本脚本按观察清单逐组跑管道（跳过交叉验证以求稳），供 cron /
launchd 定时调用。

观察清单：~/.rmrb_canary/watchlist.json
  {"groups": [["光伏", "新能源"], ["教育", "培训"]]}

cron 示例（每天 07:30，人民日报电子版发布后）：
  30 7 * * * cd /path/to/rmrb-canary && /usr/bin/python3 scripts/daily_baseline.py >> ~/.rmrb_canary/baseline.log 2>&1

macOS launchd 用户建议用 launchctl 配置 com.rmrb-canary.daily.plist，
StartCalendarInterval 设 Hour=7 Minute=30。
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import run_pipeline

WATCHLIST_PATH = os.path.expanduser('~/.rmrb_canary/watchlist.json')


def load_watchlist() -> list[list[str]]:
    if not os.path.exists(WATCHLIST_PATH):
        print(
            f'观察清单不存在：{WATCHLIST_PATH}\n'
            '请创建，格式：{"groups": [["光伏", "新能源"], ["教育", "培训"]]}',
            file=sys.stderr,
        )
        return []
    with open(WATCHLIST_PATH, encoding='utf-8') as f:
        data = json.load(f)
    return data.get('groups', [])


def main():
    groups = load_watchlist()
    if not groups:
        sys.exit(1)

    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'===== 每日基线 {stamp}，共 {len(groups)} 组 =====', file=sys.stderr)

    failures = 0
    for keywords in groups:
        try:
            result = run_pipeline(
                keywords=keywords,
                skip_media=True,
            )
            print(f'[基线] {result["summary_line"]}')
        except Exception as e:
            failures += 1
            print(f'[基线] 失败 {keywords}: {e}', file=sys.stderr)

    print(
        f'===== 完成：成功 {len(groups) - failures}/{len(groups)} =====',
        file=sys.stderr,
    )
    if failures:
        sys.exit(1)


if __name__ == '__main__':
    main()
