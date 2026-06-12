"""
rmrb_fetch.py — 人民日报最新数据采集脚本
基于 caspiankexin/people-daily-crawler-date 第3版，针对 rmrb-canary skill 优化

优化点：
  1. 自动获取最新一期（今天/昨天自动回退，无需手动输入日期）
  2. 输出结构化 JSON，直接对应 Step 1 议程优先度评分维度
  3. 提取栏目名（h3）、版面位置、字数——三项打分数据一次采集到位
  4. 可按关键词过滤，只返回目标议题的相关文章

用法：
  python rmrb_fetch.py                         # 采集最新一期，输出到 ./rmrb_data/
  python rmrb_fetch.py --date 20250401         # 指定日期
  python rmrb_fetch.py --keyword 教育 房地产   # 只保留包含关键词的文章
  python rmrb_fetch.py --output /tmp/rmrb/     # 自定义输出目录
"""

import requests
import bs4
import os
import json
import datetime
import time
import argparse
import re
import sys


HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36',
}

BASE_URL = 'http://paper.people.com.cn/rmrb/pc'

# 版面类型映射：页码 → 版面类型（用于议程优先度评分）
PAGE_TYPE_MAP = {
    1: '头版',
    2: '要闻', 3: '要闻', 4: '要闻',
}
AUTHORITY_COLUMNS = {'社论', '钟声', '人民时评', '评论员文章', '本报评论员'}


def fetch_url(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def get_page_links(year, month, day):
    """返回当天所有版面的 URL 列表"""
    url = f'{BASE_URL}/layout/{year}{month}/{day}/node_01.html'
    html = fetch_url(url)
    soup = bs4.BeautifulSoup(html, 'html.parser')

    container = soup.find('div', attrs={'id': 'pageList'}) or \
                soup.find('div', attrs={'class': 'swiper-container'})
    if not container:
        return []

    pages = container.find_all('div', attrs={'class': ['right_title-name', 'swiper-slide']})
    links = []
    for page in pages:
        if page.a:
            link = page.a['href']
            links.append(f'{BASE_URL}/layout/{year}{month}/{day}/{link}')
    return links


def get_article_links(year, month, day, page_url):
    """返回某版面内所有文章的 URL 列表"""
    html = fetch_url(page_url)
    soup = bs4.BeautifulSoup(html, 'html.parser')

    container = soup.find('div', attrs={'id': 'titleList'}) or \
                soup.find('ul', attrs={'class': 'news-list'})
    if not container:
        return []

    links = []
    for item in container.find_all('li'):
        for a in item.find_all('a'):
            href = a['href']
            if 'content' in href:
                links.append(f'{BASE_URL}/content/{year}{month}/{day}/{href}')
    return links


def parse_article(html, url, page_no, article_no):
    """
    解析单篇文章，返回结构化数据。

    评分相关字段（对应 Step 1）：
      - page_no:        版面页码
      - page_type:      头版 / 要闻 / 专题
      - position:       头条 / 非头条
      - position_score: 版面位置分（0-4）
      - word_count:     正文字数
      - word_score:     篇幅密度分（0-3）
      - column:         栏目名（h3 文本）
      - column_score:   权威栏目分（0-3）
      - agenda_score:   议程优先度总分（position + word + column，满分 10）
    """
    soup = bs4.BeautifulSoup(html, 'html.parser')

    # 标题三段
    column = soup.h3.text.strip() if soup.h3 else ''
    title  = soup.h1.text.strip() if soup.h1 else ''
    sub    = soup.h2.text.strip() if soup.h2 else ''

    # 正文
    zoom = soup.find('div', attrs={'id': 'ozoom'})
    paragraphs = [p.text.strip() for p in zoom.find_all('p')] if zoom else []
    content = '\n'.join(paragraphs)
    word_count = len(re.sub(r'\s+', '', content))

    # 版面位置分
    page_type = PAGE_TYPE_MAP.get(page_no, '专题')
    if page_no == 1 and article_no == 1:
        position = '头条'
        position_score = 4
    elif page_no == 1:
        position = '头版非头条'
        position_score = 3
    elif page_no <= 4:
        position = '要闻版'
        position_score = 2
    else:
        position = '专题版'
        position_score = 1

    # 篇幅密度分
    if word_count > 3000:
        word_score = 3
    elif word_count > 1500:
        word_score = 2
    elif word_count > 500:
        word_score = 1
    else:
        word_score = 0

    # 权威栏目分
    col_score = 0
    if any(k in column for k in ['社论', '钟声']):
        col_score = 3
    elif '人民时评' in column or '评论员' in column:
        col_score = 2
    elif any(k in column for k in ['观察', '调查', '深度']):
        col_score = 1

    agenda_score = position_score + word_score + col_score

    return {
        'url': url,
        'page_no': page_no,
        'page_type': page_type,
        'article_no': article_no,
        'position': position,
        'column': column,
        'title': title,
        'subtitle': sub,
        'content': content,
        'word_count': word_count,
        'position_score': position_score,
        'word_score': word_score,
        'column_score': col_score,
        'agenda_score': agenda_score,
    }


def resolve_date(date_str=None):
    """自动回退：今天没发布则用昨天"""
    if date_str:
        d = datetime.datetime.strptime(date_str, '%Y%m%d')
        return d.strftime('%Y'), d.strftime('%m'), d.strftime('%d')

    for delta in [0, 1]:
        d = datetime.datetime.now() - datetime.timedelta(days=delta)
        y, m, day = d.strftime('%Y'), d.strftime('%m'), d.strftime('%d')
        url = f'{BASE_URL}/layout/{y}{m}/{day}/node_01.html'
        try:
            r = requests.head(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return y, m, day
        except Exception:
            continue

    raise RuntimeError('无法获取最新期，请手动指定 --date YYYYMMDD')


def fetch(date_str=None, keywords=None, output_dir='./rmrb_data'):
    year, month, day = resolve_date(date_str)
    date_label = f'{year}{month}{day}'
    print(f'[rmrb_fetch] 采集日期：{year}年{month}月{day}日', file=sys.stderr)

    out_dir = os.path.join(output_dir, date_label)
    os.makedirs(out_dir, exist_ok=True)

    articles = []
    page_links = get_page_links(year, month, day)
    print(f'[rmrb_fetch] 共 {len(page_links)} 个版面', file=sys.stderr)

    for page_no, page_url in enumerate(page_links, start=1):
        try:
            art_links = get_article_links(year, month, day, page_url)
            for art_no, art_url in enumerate(art_links, start=1):
                try:
                    html = fetch_url(art_url)
                    art = parse_article(html, art_url, page_no, art_no)
                    art['date'] = date_label

                    # 关键词过滤：如果指定了关键词，只保留命中的文章
                    if keywords:
                        hit = any(
                            kw in art['title'] or kw in art['content']
                            for kw in keywords
                        )
                        if not hit:
                            continue

                    articles.append(art)

                    # 保存原文 txt（兼容原版格式）
                    txt_name = f'{date_label}-{str(page_no).zfill(2)}-{str(art_no).zfill(2)}.txt'
                    with open(os.path.join(out_dir, txt_name), 'w', encoding='utf-8') as f:
                        f.write(f'{art["column"]}\n{art["title"]}\n{art["subtitle"]}\n\n{art["content"]}')

                    time.sleep(0.5)
                except Exception as e:
                    print(f'  [跳过] 版面{page_no} 文章{art_no}：{e}', file=sys.stderr)
        except Exception as e:
            print(f'  [跳过] 版面{page_no}：{e}', file=sys.stderr)

        time.sleep(1)

    # 生成 summary.json —— 直接对应 Step 1 评分维度
    summary = build_summary(date_label, articles, keywords)
    summary_path = os.path.join(out_dir, 'summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f'[rmrb_fetch] 完成：{len(articles)} 篇文章 → {out_dir}', file=sys.stderr)
    print(f'[rmrb_fetch] 评分摘要已写入 summary.json', file=sys.stderr)
    return summary


def build_summary(date_label, articles, keywords):
    """
    构建采集摘要：议程优先度 + 地域映射 + 原文。

    叙事框架/话语强度/部委协同的打分统一由 agent/tools/ 下的
    加权版模块完成——本函数不再内嵌未加权的旧版打分逻辑，
    避免双轨实现导致历史库与报告口径不一致。
    """
    # ── 地域提及 ──────────────────────────────────────────────────
    province_pattern = re.compile(
        r'(北京|上海|广东|浙江|江苏|四川|湖北|湖南|河南|河北|山东|陕西|福建|安徽|辽宁|吉林|黑龙江|云南|贵州|广西|内蒙古|新疆|西藏|青海|甘肃|宁夏|海南|重庆|天津)'
    )
    region_count = {}
    for a in articles:
        for m in province_pattern.findall(a['content'] + a['title']):
            region_count[m] = region_count.get(m, 0) + 1
    top_regions = sorted(region_count.items(), key=lambda x: -x[1])[:10]

    # ── 议程优先度 ────────────────────────────────────────────────
    high_priority = [
        {'title': a['title'], 'column': a['column'], 'page': a['page_no'],
         'position': a['position'], 'score': a['agenda_score'], 'word_count': a['word_count']}
        for a in articles if a['agenda_score'] >= 7
    ]
    authority_hits = [
        {'title': a['title'], 'column': a['column'], 'score': a['agenda_score']}
        for a in articles if a['column_score'] >= 2
    ]

    return {
        'date': date_label,
        'keywords_filter': keywords or [],
        'total_articles': len(articles),
        'total_pages': max((a['page_no'] for a in articles), default=0),

        # Step 1：议程优先度
        'step1_agenda': {
            'high_priority_articles': high_priority,
            'front_page_count': sum(1 for a in articles if a['page_no'] == 1),
            'authority_column_hits': authority_hits,
        },

        # Step 4：地域映射
        'step4_regions': {
            'top_regions': [{'region': r, 'count': c} for r, c in top_regions],
        },

        # 原始评分列表（供逐篇核验）
        'articles': [
            {k: v for k, v in a.items() if k != 'content'}
            for a in articles
        ],

        # 全文内容（语义分析 + 加权打分用）
        'full_texts': [
            {'title': a['title'], 'column': a['column'],
             'page_no': a['page_no'], 'content': a['content']}
            for a in articles
        ],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='人民日报最新数据采集（rmrb-canary 专用）')
    parser.add_argument('--date',    help='指定日期 YYYYMMDD，默认自动获取最新一期')
    parser.add_argument('--keyword', nargs='+', dest='keywords', help='关键词过滤（空格分隔多个词）')
    parser.add_argument('--output',  default='./rmrb_data', help='输出目录，默认 ./rmrb_data')
    args = parser.parse_args()

    fetch(date_str=args.date, keywords=args.keywords, output_dir=args.output)
