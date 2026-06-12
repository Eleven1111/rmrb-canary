import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """每个测试用独立的临时数据库，绝不触碰 ~/.rmrb_canary/history.db。"""
    db_path = tmp_path / 'test_history.db'
    monkeypatch.setenv('RMRB_CANARY_DB', str(db_path))
    yield str(db_path)


def make_article(
    title='测试文章',
    content='',
    page_no=5,
    column='',
    relevance_multiplier=None,
    **extra,
):
    art = {
        'title': title,
        'content': content,
        'page_no': page_no,
        'column': column,
        **extra,
    }
    if relevance_multiplier is not None:
        art['relevance_multiplier'] = relevance_multiplier
    return art
