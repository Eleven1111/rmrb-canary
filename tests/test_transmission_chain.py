"""传导链定位 + 信号源解析逻辑。"""

import datetime

from agent.sources.gov_policy import parse_items
from agent.sources.theory_channel import parse_rss
from agent.tools.transmission_chain import locate_stage


def recent(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime('%Y%m%d')


def rss_date(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime('%Y-%m-%d')


def make_upstream(central=None, ministry=None, theory=None,
                  central_err=None, ministry_err=None, theory_err=None):
    def wrap(items, err):
        out = {'items': items or []}
        if err:
            out['error'] = err
        return out
    return {
        'central_docs': wrap(central, central_err),
        'ministry_docs': wrap(ministry, ministry_err),
        'theory': wrap(theory, theory_err),
    }


def make_rmrb(intensity=2, articles=2, level='L1', judicial=False,
              discipline=False, politburo=False, joint=False):
    return {
        'intensity_level': intensity,
        'article_count': articles,
        'coordination_level': level,
        'has_judicial': judicial,
        'has_discipline': discipline,
        'has_politburo': politburo,
        'joint_found': joint,
    }


class TestGovPolicyParsing:
    def test_parse_items_strips_em_and_filters_window(self):
        payload = {'searchVO': {'listVO': [
            {'title': '关于<em>光伏</em>发电的通知', 'pubtimeStr': '2026.06.01',
             'puborg': '国家能源局', 'pcode': 'X〔2026〕1号', 'url': 'http://a', 'summary': 's'},
            {'title': '十年前的旧文件', 'pubtimeStr': '2016.01.01',
             'puborg': '发改委', 'pcode': '', 'url': 'http://b', 'summary': ''},
        ]}}
        items = parse_items(payload, days=365)
        assert len(items) == 1
        assert items[0]['title'] == '关于光伏发电的通知'

    def test_joint_flag_detected(self):
        payload = {'searchVO': {'listVO': [
            {'title': '五部门联合印发整治方案', 'pubtimeStr': '2026.06.01',
             'puborg': '', 'pcode': '', 'url': 'http://a', 'summary': '教育部等5个部门联合印发'},
        ]}}
        items = parse_items(payload, days=365)
        assert items[0]['is_joint']


class TestTheoryParsing:
    def test_rss_keyword_filter(self):
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item><title>深刻把握新质生产力的理论内涵</title>
            <link>http://t1</link><description>发展新质生产力</description>
            <pubDate>{rss_date(5)}</pubDate></item>
          <item><title>不相关文章</title>
            <link>http://t2</link><description>别的话题</description>
            <pubDate>{rss_date(5)}</pubDate></item>
        </channel></rss>"""
        items = parse_rss(xml, ['新质生产力'], days=90)
        assert len(items) == 1
        assert items[0]['matched_keywords'] == ['新质生产力']


class TestStageLocation:
    def test_incubation_theory_only(self):
        result = locate_stage(
            make_rmrb(intensity=1, articles=1),
            make_upstream(theory=[{'title': '理论文章', 'date': recent(10)}]),
        )
        assert result['stage'] == '酝酿期'

    def test_groundwork_central_plus_pd(self):
        result = locate_stage(
            make_rmrb(intensity=4, articles=5, politburo=True),
            make_upstream(central=[{'title': '国务院文件', 'date': recent(20)}]),
        )
        assert result['stage'] == '铺垫期'

    def test_mobilization_joint_ministry_docs(self):
        result = locate_stage(
            make_rmrb(intensity=5, articles=6),
            make_upstream(
                central=[{'title': '国务院部署', 'date': recent(40)}],
                ministry=[{'title': '五部门联合印发方案', 'date': recent(10), 'is_joint': True}],
            ),
        )
        assert result['stage'] == '动员期'
        assert result['ministry_joint_docs']

    def test_campaign_judicial(self):
        result = locate_stage(
            make_rmrb(intensity=6, articles=8, judicial=True, level='L5'),
            make_upstream(),
        )
        assert result['stage'] == '运动期'

    def test_closing_phase(self):
        result = locate_stage(
            make_rmrb(intensity=3, articles=4),
            make_upstream(ministry=[{'title': '部门文件', 'date': recent(60)}]),
            texts=['专项整治取得阶段性成效，下一步将巩固成果。'],
        )
        assert result['stage'] == '收尾期'

    def test_normalization_phase(self):
        result = locate_stage(
            make_rmrb(intensity=2, articles=2),
            make_upstream(),
            texts=['建立长效机制，常态化监管成为主基调。'],
        )
        assert result['stage'] == '常态化'

    def test_degraded_sources_lower_confidence(self):
        result = locate_stage(
            make_rmrb(intensity=4, articles=5, politburo=True),
            make_upstream(
                central_err='timeout', ministry_err='timeout', theory_err='405',
            ),
        )
        assert result['degraded_layers']
        assert result['confidence'] == 'low'

    def test_lead_lag_ordering(self):
        result = locate_stage(
            make_rmrb(intensity=4, articles=5),
            make_upstream(
                theory=[{'title': 't', 'date': recent(80)}],
                central=[{'title': 'c', 'date': recent(40)}],
                ministry=[{'title': 'm', 'date': recent(10)}],
            ),
        )
        seq = [x['layer'] for x in result['lead_lag']]
        assert seq == ['理论层', '中央层', '部委层']

    def test_no_signal_unlocated(self):
        result = locate_stage(make_rmrb(intensity=1, articles=0), make_upstream())
        assert result['stage'] == '未定位'
        assert result['confidence'] == 'low'


class TestPdTheoryExtraction:
    def test_theory_column_detected(self):
        from agent.sources.theory_channel import extract_pd_theory
        articles = [
            {'title': '深化要素市场化改革', 'column': '人民观察', 'page_no': 9},
            {'title': '普通新闻', 'column': '要闻', 'page_no': 2},
        ]
        items = extract_pd_theory(articles, '20260612')
        assert len(items) == 1
        assert items[0]['source'] == '人民日报理论版'
        assert items[0]['date'] == '20260612'
