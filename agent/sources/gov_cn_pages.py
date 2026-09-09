"""
gov_cn_pages.py — 中国政府网页面型政策文件采集（备用来源）

与 agent/sources/gov_policy.py 的关系：后者走政策文件库检索接口
（sousuo.www.gov.cn/search-gov/data，scope=zhengcelibrary_gw/bm），覆盖面更大；
本模块直接解析 gov.cn/zhengce/ 页面，只能拿到首页最近若干件，但不依赖该接口。
2026-09-08 实测：检索接口两种 scope 参数当日均返回 0 条，页面解析可用。
两者应并用 —— 接口不可用时本模块仍能给出事实锚点。

接入方案 §5.1 第一层来源：正式政策原文。人民日报回答"议程如何呈现"，
政策文件回答"谁正式规定了什么、对谁适用、何时生效"。

来源状态见 config/sources/policy_sources.json，那里记录的是 2026-09-08 的**实测结果**：
只有中国政府网的两个入口可解析；网信办 521、卫健委与药监局 412、政策文件库分页 403。
被拒的来源没有写解析器 —— 写一个跑不通的爬虫，比承认拿不到更糟。

覆盖边界（必须随结果一起呈现）：
  当前只能取到国务院层级最近若干件文件，**不是完整政策语料库**。
  主管部门（网信办/药监局/卫健委）文件全部缺失，因此"没有检索到相关文件"
  只能表述为"本框架已接入的来源中没有"，不能表述为"没有出台文件"。
"""

import datetime
import hashlib
import json
import os
import re
import sys
import time

import bs4
import requests

HEADERS = {
    'user-agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'accept-language': 'zh-CN,zh;q=0.9',
}

# 本文件位于 agent/sources/ 下，仓库根要退三级。
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'sources', 'policy_sources.json')

# 详情页的信息公开表字段。全部照抄原文，抽不到就是 None，不推断。
FIELD_PATTERNS = {
    'issuing_agency': re.compile(r'发文机关[：:]?\s*([^\s]{2,30})'),
    'doc_number': re.compile(r'发文字号[：:]?\s*([^\s]{2,40})'),
    'drafted_date': re.compile(r'成文日期[：:]?\s*(20\d{2}[-年]\d{1,2}[-月]\d{1,2})'),
    'published_date': re.compile(r'发布日期[：:]?\s*(20\d{2}[-年]\d{1,2}[-月]\d{1,2})'),
    'index_number': re.compile(r'索\s*引\s*号[：:]?\s*([^\s]{6,40})'),
    'subject_category': re.compile(r'主题分类[：:]?\s*([^\s]{2,40})'),
}

# 生效日期只从正文里的明确表述取，取不到就留空。
EFFECTIVE_PATTERNS = [
    re.compile(r'自(20\d{2})年(\d{1,2})月(\d{1,2})日起(?:施行|实施|执行|生效)'),
    re.compile(r'自(?:公布|发布)之日起(?:施行|实施|执行|生效)'),
]

# 废止/修订状态标记
STATUS_MARKERS = {
    '废止': ['予以废止', '同时废止', '自本决定施行之日起废止'],
    '修订': ['修订通过', '关于修改', '修正案', '予以修改'],
}


def load_sources() -> list[dict]:
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)['sources']


def enabled_sources() -> list[dict]:
    return [s for s in load_sources() if s.get('status') == 'enabled']


def fetch_url(url, retries=2, timeout=20):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            return r
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


def _normalize_date(raw: str) -> str | None:
    """把 2026年08月30 / 2026-09-04 统一成 YYYYMMDD。抽不到返回 None，不补造。"""
    if not raw:
        return None
    m = re.match(r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})', raw)
    if not m:
        return None
    y, mo, d = m.groups()
    return f'{y}{int(mo):02d}{int(d):02d}'


def list_documents(source: dict, limit: int = 30) -> dict:
    """列出一个来源当前可见的文件链接。返回条目与采集健康度。"""
    try:
        r = fetch_url(source['list_url'])
    except Exception as e:
        return {'status': 'failed', 'error': str(e), 'items': [],
                'source_key': source['key']}

    soup = bs4.BeautifulSoup(r.text, 'html.parser')
    pattern = re.compile(source['link_pattern'])
    items, seen = [], set()
    for a in soup.find_all('a'):
        href = a.get('href') or ''
        if not pattern.search(href):
            continue
        title = a.get_text(strip=True)
        if len(title) < 6:
            continue
        if href.startswith('/'):
            href = 'https://www.gov.cn' + href
        if href in seen:
            continue
        seen.add(href)
        items.append({'title': title, 'url': href, 'source_key': source['key'],
                      'system': source.get('system')})
        if len(items) >= limit:
            break

    return {'status': 'ok' if items else 'empty', 'items': items,
            'source_key': source['key'], 'listed_count': len(items)}


def parse_document(html: str, url: str, source: dict) -> dict:
    """
    解析一份政策文件详情页。

    抽不到的字段一律为 None —— 文号、日期、发文机关都不得推断（§6.2）。
    """
    soup = bs4.BeautifulSoup(html, 'html.parser')
    plain = re.sub(r'<[^>]+>', ' ', html)

    fields = {}
    for name, pattern in FIELD_PATTERNS.items():
        m = pattern.search(plain)
        fields[name] = m.group(1).strip() if m else None

    title = fields.get('title')
    m = re.search(r'标\s*题[：:]?\s*([^\n<]{4,80})', plain)
    if m:
        title = m.group(1).strip()
    if not title:
        h1 = soup.find('h1')
        title = h1.get_text(strip=True) if h1 else ''

    body_el = (soup.find('div', id='UCAP-CONTENT')
               or soup.find('div', class_=re.compile('pages_content|TRS_Editor|content'))
               or soup.find('body'))
    content = body_el.get_text('\n', strip=True) if body_el else ''

    effective_date = None
    for pattern in EFFECTIVE_PATTERNS:
        m = pattern.search(content)
        if m:
            if m.groups():
                y, mo, d = m.groups()
                effective_date = f'{y}{int(mo):02d}{int(d):02d}'
            else:
                effective_date = _normalize_date(fields.get('published_date'))
            break

    doc_status = 'in_force'
    status_evidence = []
    for label, markers in STATUS_MARKERS.items():
        hit = [mk for mk in markers if mk in content]
        if hit:
            doc_status = label
            status_evidence.extend(hit)

    published = _normalize_date(fields.get('published_date'))
    drafted = _normalize_date(fields.get('drafted_date'))

    return {
        'doc_id': hashlib.sha256(url.encode('utf-8')).hexdigest()[:16],
        'url': url,
        'title': title,
        'source_key': source.get('key'),
        'system': source.get('system'),
        'authority_tier': 'official_document',
        'issuing_agency': fields.get('issuing_agency'),
        'doc_number': fields.get('doc_number'),
        'index_number': fields.get('index_number'),
        'subject_category': fields.get('subject_category'),
        'published_at': published,
        'drafted_at': drafted,
        'effective_at': effective_date,
        'doc_status': doc_status,
        'doc_status_evidence': status_evidence,
        'content': content,
        'content_hash': hashlib.sha256(
            re.sub(r'\s+', '', content).encode('utf-8')).hexdigest()[:16],
        'word_count': len(re.sub(r'\s+', '', content)),
        'fetched_at': datetime.datetime.now().isoformat(),
        'parse_complete': bool(fields.get('issuing_agency') and published and content),
    }


def fetch_documents(topic_terms: list[str] = None, limit_per_source: int = 20,
                    as_of: str = None, fetch_delay: float = 0.8,
                    cache=None) -> dict:
    """
    采集已启用来源的政策文件，按主题词过滤。

    as_of：只保留发布日期不晚于该时点的文件（F03 同款约束，历史回放不读未来文件）。
    cache：可选的共享文档缓存（agent.store.doccache），命中则不重复抓取。
    """
    sources = enabled_sources()
    documents, failures = [], []
    listed_total = matched = 0
    from_cache = 0

    for source in sources:
        listing = list_documents(source, limit=limit_per_source)
        if listing['status'] == 'failed':
            failures.append({'source_key': source['key'], 'stage': 'list',
                             'error': listing.get('error')})
            continue
        listed_total += listing['listed_count']

        for item in listing['items']:
            try:
                cached = cache.get_document(item['url']) if cache else None
                if cached:
                    doc = cached
                    from_cache += 1
                else:
                    html = fetch_url(item['url']).text
                    doc = parse_document(html, item['url'], source)
                    if cache:
                        cache.put_document(item['url'], doc)
                    time.sleep(fetch_delay)

                if as_of and doc.get('published_at') and doc['published_at'] > as_of:
                    continue

                haystack = f"{doc.get('title', '')}\n{doc.get('content', '')}"
                hits = [t for t in (topic_terms or []) if t in haystack]
                if topic_terms and not hits:
                    continue
                matched += 1
                documents.append({**doc, 'matched_terms': hits})
            except Exception as e:
                failures.append({'source_key': source['key'], 'stage': 'detail',
                                 'url': item['url'], 'error': str(e)})

    blocked = [s for s in load_sources() if s.get('status') != 'enabled']
    return {
        'status': 'ok' if sources else 'no_sources',
        'documents': documents,
        'listed_count': listed_total,
        'matched_count': matched,
        'from_cache': from_cache,
        'failures': failures,
        'enabled_sources': [s['key'] for s in sources],
        'unavailable_sources': [
            {'key': s['key'], 'label': s.get('label'), 'status': s.get('status'),
             'probe_result': s.get('probe_result')} for s in blocked],
        'coverage_warning': (
            '已接入来源仅覆盖国务院层级最近若干件文件；主管部门（网信办/药监局/卫健委等）'
            '来源全部不可用。因此"未检索到相关文件"只能表述为"已接入来源中没有"，'
            '不得表述为"没有出台文件"。'),
        'as_of': as_of,
    }


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='正式政策文件采集')
    p.add_argument('--keyword', nargs='+', help='主题词过滤')
    p.add_argument('--as-of', dest='as_of')
    p.add_argument('--limit', type=int, default=10)
    a = p.parse_args()
    result = fetch_documents(topic_terms=a.keyword, limit_per_source=a.limit, as_of=a.as_of)
    print(f"列出 {result['listed_count']} 件，命中 {result['matched_count']} 件，"
          f"失败 {len(result['failures'])} 次", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))
