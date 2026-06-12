"""
信号源：中国政府网政策文件库（sousuo.www.gov.cn/search-gov/data）

覆盖两层传导链：
  scope='gw' — 国务院文件（中央执行层信号）
  scope='bm' — 部门文件（部委执行层信号，联合发文在此最先可见）

接口为公开 JSON API，无需鉴权。失败时返回 {'error': ...}，不抛异常。
"""

import datetime
import re
import sys

import requests

API_URL = 'https://sousuo.www.gov.cn/search-gov/data'

SCOPE_MAP = {
    'gw': 'zhengcelibrary_gw',
    'bm': 'zhengcelibrary_bm',
}

HEADERS = {
    'user-agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
    'accept': 'application/json',
}

EM_TAG = re.compile(r'</?em>')
JOINT_MARK = re.compile(r'等[0-9一二三四五六七八九十]+个?部门|联合印发|联合发布|联合出台')


def parse_items(payload: dict, days: int) -> list[dict]:
    """从 API 响应中解析文件列表，按时间窗过滤。"""
    list_vo = (payload.get('searchVO') or {}).get('listVO') or []
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)

    items = []
    for entry in list_vo:
        title = EM_TAG.sub('', entry.get('title') or '')
        pub_str = (entry.get('pubtimeStr') or '').strip()
        try:
            pub_dt = datetime.datetime.strptime(pub_str, '%Y.%m.%d')
        except ValueError:
            continue
        if pub_dt < cutoff:
            continue

        summary = EM_TAG.sub('', entry.get('summary') or '')
        items.append({
            'title': title,
            'date': pub_dt.strftime('%Y%m%d'),
            'puborg': entry.get('puborg') or '',
            'pcode': entry.get('pcode') or '',
            'url': entry.get('url') or '',
            'summary': summary[:200],
            'is_joint': bool(JOINT_MARK.search(title + summary)),
        })

    items.sort(key=lambda x: x['date'], reverse=True)
    return items


def search_policy_library(
    keywords: list[str],
    scope: str = 'bm',
    days: int = 180,
    per_keyword: int = 10,
) -> dict:
    """
    按关键词检索政策文件库。

    返回：
      {scope, items: [{title, date, puborg, pcode, url, summary, is_joint}],
       total_reported, error?}
    """
    t_param = SCOPE_MAP.get(scope)
    if not t_param:
        return {'scope': scope, 'items': [], 'error': f'未知 scope: {scope}'}

    all_items = []
    seen_urls = set()
    total_reported = 0
    errors = []

    for kw in keywords:
        params = {
            't': t_param,
            'q': kw,
            'timetype': 'timeqb',
            'sort': 'pubtime',
            'searchfield': 'title',
            'p': 1,
            'n': per_keyword,
        }
        try:
            resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            errors.append(f'{kw}: {e}')
            continue

        total_reported += (payload.get('searchVO') or {}).get('totalCount') or 0
        for item in parse_items(payload, days):
            if item['url'] in seen_urls:
                continue
            seen_urls.add(item['url'])
            item['keyword'] = kw
            all_items.append(item)

    all_items.sort(key=lambda x: x['date'], reverse=True)
    result = {
        'scope': scope,
        'items': all_items,
        'total_reported': total_reported,
    }
    if errors and not all_items:
        result['error'] = '; '.join(errors)
        print(f'  [政策库:{scope}] 采集失败: {result["error"]}', file=sys.stderr)
    return result
