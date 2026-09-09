"""
整期共享缓存是否真的接上了。

移植了 doccache 却没人调用 = 死代码，也等于"多主题不重复抓取"这个说法
没有任何东西守着。本文件专门守这条接线。
"""

import pytest

from agent.store import doccache
from agent.tools import fetch_rmrb as fr


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(doccache, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(doccache, 'CACHE_PATH', str(tmp_path / 'doccache.db'))
    return doccache.DocumentCache()


ISSUE = {
    'date': '20260908',
    'full_texts': [
        {'title': '人工智能标准建设', 'content': '推进人工智能标准。', 'page_no': 1},
        {'title': '粮食生产形势', 'content': '今年粮食产量稳步增长。', 'page_no': 2},
    ],
    'articles': [{'title': '人工智能标准建设'}, {'title': '粮食生产形势'}],
    'total_articles': 2,
}


def test_second_topic_does_not_refetch(cache, monkeypatch):
    calls = []

    def fake_fetch(date_str=None, keywords=None, output_dir=None):
        calls.append(keywords)
        return dict(ISSUE)

    monkeypatch.setattr(fr.rmrb_fetch, 'fetch', fake_fetch)
    monkeypatch.setattr(fr.rmrb_fetch, 'resolve_date',
                        lambda d=None: ('2026', '09', '08'))

    first = fr.fetch_rmrb(['人工智能'], date='20260908', cache=cache)
    second = fr.fetch_rmrb(['粮食'], date='20260908', cache=cache)

    assert len(calls) == 1, '同一期第二个主题不得再抓一次'
    assert calls[0] is None, '缓存模式下必须取整期，不能只取过滤后的结果'
    assert first['total_articles'] == 1 and second['total_articles'] == 1
    assert first['full_texts'][0]['title'] == '人工智能标准建设'
    assert second['full_texts'][0]['title'] == '粮食生产形势'
    assert second['filtered_from_cache']


def test_without_cache_behaviour_unchanged(monkeypatch):
    """反向对照：不传 cache 时行为与原来一致（由采集层自己过滤）。"""
    seen = {}

    def fake_fetch(date_str=None, keywords=None, output_dir=None):
        seen['keywords'] = keywords
        return dict(ISSUE)

    monkeypatch.setattr(fr.rmrb_fetch, 'fetch', fake_fetch)
    out = fr.fetch_rmrb(['人工智能'], date='20260908')
    assert seen['keywords'] == ['人工智能'], '无缓存时关键词仍下推给采集层'
    assert 'filtered_from_cache' not in out
