"""
信号源：理论层

中国政策的独特规律是"理论先行"——重大转向先在《求是》理论版面
吹风，再进入文件和日报新闻版。两路采集：

  1. 人民日报自身理论版/专论（主路，零额外网络——理论版文章
     已在 rmrb_fetch 采集结果中，按栏目/版面识别即可）
  2. 人民网理论频道 RSS（补充路，实测更新滞后，仅作增量）

求是网检索接口反爬（405），无法直连，已在局限中注明。
失败时返回 {'error': ...}。
"""

import datetime
import re
import sys

import requests
import bs4

THEORY_RSS = 'http://www.people.com.cn/rss/theory.xml'

HEADERS = {
    'user-agent': 'Mozilla/5.0 (compatible; RSSBot/1.0; rmrb-canary)',
    'accept': 'application/rss+xml, application/xml, text/xml, */*',
}

DATE_FORMATS = ('%Y-%m-%d', '%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S')


def _parse_pub_date(pub_str: str) -> datetime.datetime | None:
    pub_str = (pub_str or '').strip()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.datetime.strptime(pub_str[:25], fmt)
            return parsed.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def parse_rss(xml_text: str, keywords: list[str], days: int) -> list[dict]:
    """解析 RSS，按关键词与时间窗过滤。"""
    soup = bs4.BeautifulSoup(xml_text, 'xml')
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)

    items = []
    for item in soup.find_all('item'):
        title_el = item.find('title')
        link_el = item.find('link')
        desc_el = item.find('description')
        pub_el = item.find('pubDate')

        title = title_el.get_text().strip() if title_el else ''
        desc = re.sub(r'<[^>]+>', '', desc_el.get_text()) if desc_el else ''
        desc = re.sub(r'\s+', ' ', desc).strip()

        matched = [kw for kw in keywords if kw in title or kw in desc]
        if not matched:
            continue

        pub_dt = _parse_pub_date(pub_el.get_text() if pub_el else '')
        if pub_dt and pub_dt < cutoff:
            continue

        items.append({
            'title': title,
            'date': pub_dt.strftime('%Y%m%d') if pub_dt else '',
            'url': link_el.get_text().strip() if link_el else '',
            'summary': desc[:200],
            'matched_keywords': matched,
        })

    items.sort(key=lambda x: x['date'], reverse=True)
    return items


THEORY_COLUMN_MARKS = ['理论', '专论', '人民观察', '思想纵横', '学术随笔']


def extract_pd_theory(articles: list[dict], date: str) -> list[dict]:
    """
    从人民日报当期文章中识别理论版/专论文章（理论层主路信号）。

    参数 articles 为已按关键词过滤的文章（含 column / page_no / title）。
    按栏目名识别——理论版面文章的栏目均带明确标记。
    """
    items = []
    for a in articles:
        column = a.get('column', '') or ''
        page_no = a.get('page_no', 0)
        if any(mark in column for mark in THEORY_COLUMN_MARKS):
            items.append({
                'title': a.get('title', ''),
                'date': date,
                'url': '',
                'summary': f"人民日报第{page_no}版·{column}",
                'matched_keywords': [],
                'source': '人民日报理论版',
            })
    return items


def fetch_theory_articles(keywords: list[str], days: int = 90) -> dict:
    """
    采集人民网理论频道近 days 天与关键词相关的文章（补充路）。

    返回：{source, items, error?}
    """
    try:
        resp = requests.get(THEORY_RSS, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        resp.raise_for_status()
        items = parse_rss(resp.text, keywords, days)
        return {'source': '人民网理论频道(补充)', 'items': items}
    except Exception as e:
        print(f'  [理论层] 采集失败: {e}', file=sys.stderr)
        return {'source': '人民网理论频道(补充)', 'items': [], 'error': str(e)}
