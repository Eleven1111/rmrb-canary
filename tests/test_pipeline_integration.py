"""管道端到端集成（网络层用 fixture 替身，验证全链路接线）。"""

import json

import pytest

import agent.agent as agent_mod
from agent.store import db, ledger


FIXTURE_ARTICLES = [
    {
        'title': '坚决遏制光伏行业内卷式竞争',
        'subtitle': '',
        'column': '人民时评',
        'page_no': 1,
        'article_no': 2,
        'position': '头版非头条',
        'content': (
            '光伏产业近年快速扩张，部分环节出现内卷式竞争。\n'
            '国家发展改革委、工业和信息化部、市场监管总局等五部门联合印发通知，'
            '要求坚决遏制低价无序竞争，规范光伏行业发展。\n'
            '业内人士表示，光伏行业整合是高质量发展的必经阶段。'
        ),
        'word_count': 300,
        'position_score': 3, 'word_score': 1, 'column_score': 2,
        'agenda_score': 6,
        'url': '', 'page_type': '头版', 'date': '20260611',
    },
    {
        'title': '某地乡村旅游火热',
        'subtitle': '',
        'column': '',
        'page_no': 7,
        'article_no': 3,
        'position': '专题版',
        'content': '乡村旅游持续升温。' * 60 + '顺带提了一句光伏板。',
        'word_count': 700,
        'position_score': 1, 'word_score': 1, 'column_score': 0,
        'agenda_score': 2,
        'url': '', 'page_type': '专题', 'date': '20260611',
    },
]


def fake_summary(date='20260611'):
    return {
        'date': date,
        'keywords_filter': ['光伏'],
        'total_articles': len(FIXTURE_ARTICLES),
        'total_pages': 8,
        'step1_agenda': {
            'high_priority_articles': [],
            'front_page_count': 1,
            'authority_column_hits': [
                {'title': FIXTURE_ARTICLES[0]['title'], 'column': '人民时评', 'score': 6},
            ],
        },
        'step4_regions': {'top_regions': []},
        'articles': [
            {k: v for k, v in a.items() if k != 'content'} for a in FIXTURE_ARTICLES
        ],
        'full_texts': [
            {'title': a['title'], 'column': a['column'],
             'page_no': a['page_no'], 'content': a['content']}
            for a in FIXTURE_ARTICLES
        ],
    }


@pytest.fixture
def patched_pipeline(monkeypatch):
    monkeypatch.setattr(
        agent_mod, 'fetch_rmrb',
        lambda keywords, date=None: fake_summary(date or '20260611'),
    )
    monkeypatch.setattr(agent_mod, 'collect_upstream', lambda keywords, days=180: {
        'central_docs': {'items': [
            {'title': '国务院办公厅关于光伏产业的指导意见', 'date': '20260520',
             'puborg': '国务院办公厅', 'is_joint': False},
        ]},
        'ministry_docs': {'items': [
            {'title': '五部门联合印发光伏规范通知', 'date': '20260601',
             'puborg': '国家发展改革委', 'is_joint': True},
        ]},
        'theory': {'items': []},
    })
    monkeypatch.setattr(agent_mod, 'fetch_media_sources',
                        lambda keywords, rmrb_summary=None: {'mocked': True})
    return agent_mod


class TestPipelineIntegration:
    def test_full_run_wiring(self, patched_pipeline):
        result = patched_pipeline.run_pipeline(['光伏'], skip_media=True)

        # 相关性过滤剔除了顺带一提的乡村旅游稿
        assert result['relevance']['stats']['kept_count'] == 1
        assert result['relevance']['stats']['dropped_count'] == 1

        # 加权强度来自头版人民时评的"坚决遏制"
        assert result['intensity']['weighted_max_level'] == 5

        # 联合发文检测触发 L2 下限
        assert result['ministry']['joint_issuance']['found']
        assert result['ministry']['coordination_level'] >= 'L2'

        # 传导链：中央文件 + 部委联合发文 + 日报火力 = 动员期
        assert result['transmission']['stage'] == '动员期'

        # 风险窗口带校准依据和频率陈述
        assert result['risk_window']['base_window_basis']['source'] == 'backtest'
        assert result['risk_window']['frequency'] is not None

        # 入库 + 预测台账 + 档案
        assert result['storage']['saved']
        assert result['prediction']['prediction_id'] is not None
        assert result['dossier_path']
        assert '动员期' in open(result['dossier_path'], encoding='utf-8').read()

        # 摘要行包含平滑与传导链
        assert '传导链=动员期' in result['summary_line']

    def test_second_run_has_baseline_and_context(self, patched_pipeline):
        patched_pipeline.run_pipeline(['光伏'], skip_media=True, date='20260610')
        result = patched_pipeline.run_pipeline(['光伏'], skip_media=True, date='20260611')

        assert result['trend']['has_baseline']
        assert result['rolling']['data_points'] >= 1
        assert result['dossier_context']['dossier_path'] is not None
        assert '回应上期判断' in result['dossier_context']['note']

    def test_skip_sources_degrades_gracefully(self, patched_pipeline):
        result = patched_pipeline.run_pipeline(
            ['光伏'], skip_media=True, skip_sources=True,
        )
        assert result['transmission']['stage'] == '未运行'
        assert result['storage']['saved']

    def test_record_judgment_roundtrip(self, patched_pipeline, tmp_path):
        result = patched_pipeline.run_pipeline(['光伏'], skip_media=True)
        judgment_file = tmp_path / 'judgment.json'
        judgment_file.write_text(json.dumps({
            'keywords': ['光伏'],
            'date': result['rmrb']['date'],
            'judgment': '行业进入整合期，整治指向低价竞争而非产业本身',
            'tension': '官方称规范发展，企业端价格战仍在继续',
        }, ensure_ascii=False), encoding='utf-8')

        patched_pipeline._cmd_record_judgment(str(judgment_file))

        result2 = patched_pipeline.run_pipeline(['光伏'], skip_media=True)
        prev = result2['dossier_context']['previous_judgments']
        assert prev and '整合期' in prev[0]['judgment']
