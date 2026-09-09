"""
P1 语义与程序反例集

对应方案 §9.1 第一层：否定、历史回顾、不同对象、地方与中央机关、引述观点、
重复转载、证据未支持、口径不可比。

运行（在 skill 安装根目录）：
    python3 -m unittest discover -s tests -t . -v
"""

import unittest

from agent.tools.scoping import build_target_segments
from agent.tools.event_extract import extract_events
from agent.tools.evidence_check import verify_events, verify_claim
from agent.tools.signals import build_signals
from agent.tools.dedupe import merge_sources
from agent.tools.change_detect import detect_changes

TERMS = ['人工智能', '大模型']


def art(title='', content='', page_no=1, column=''):
    return {'title': title, 'content': content, 'page_no': page_no, 'column': column}


def events_of(articles, terms=TERMS):
    """走完整链路：切分 → 抽取 → 证据核验。测的是最终会被使用的那份结果。"""
    scope = build_target_segments(articles, terms)
    raw = extract_events(scope, terms)
    return verify_events(raw.get('events', []), articles)


def pick(extraction, needle):
    return [e for e in extraction['events'] if needle in (e.get('clause') or '')]


class TestNegation(unittest.TestCase):
    """"不得随意查封企业"约束的是执法行为，不是行业整治升级。"""

    def test_negated_enforcement_is_constraint_on_enforcement(self):
        arts = [art(content='开展人工智能监管时，不得随意查封企业。')]
        ev = pick(events_of(arts), '不得随意查封')
        self.assertTrue(ev)
        e = ev[0]
        self.assertTrue(e['negated'])
        self.assertIn('执法', e['policy_tools'])
        self.assertEqual(e['direction'], '约束执法行为')
        self.assertNotIn(e['direction'], ('限制', '规范'))
        self.assertFalse(e['is_new_action'], '被否定的执法不是本期新增执法行动')

    def test_plain_enforcement_still_reads_as_enforcement(self):
        """反向对照：没有否定时，执法就该读成执法，切分不能把方向一律压掉。"""
        arts = [art(content='市场监管总局依法查处人工智能领域违规行为。')]
        ev = pick(events_of(arts), '查处')
        self.assertTrue(ev)
        self.assertFalse(ev[0]['negated'])
        self.assertIn('执法', ev[0]['policy_tools'])


class TestTense(unittest.TestCase):
    """"去年依法查处……"是历史回顾，不等于今日新增行动。"""

    def test_historical_review_not_counted_as_new_action(self):
        arts = [art(content='去年有关部门依法查处人工智能违规应用一批。')]
        ev = pick(events_of(arts), '去年')
        self.assertTrue(ev)
        self.assertEqual(ev[0]['tense'], 'historical')
        self.assertFalse(ev[0]['is_new_action'])
        self.assertTrue(ev[0]['needs_review'])

    def test_current_action_is_counted(self):
        arts = [art(content='工业和信息化部部署推进人工智能标准建设。')]
        ev = pick(events_of(arts), '部署')
        self.assertTrue(ev)
        self.assertEqual(ev[0]['tense'], 'current')
        self.assertTrue(ev[0]['is_new_action'])

    def test_historical_events_excluded_from_dimensions(self):
        arts = [art(content='去年有关部门依法查处人工智能违规应用一批。')]
        signals = build_signals(events_of(arts))
        self.assertEqual(signals['status'], 'unknown')
        self.assertEqual(signals.get('historical_only'), 1,
                         '历史事件单独计数，不进入本期维度取值')


class TestObjectSplit(unittest.TestCase):
    """"支持行业发展，同时整治违法收费"必须拆成不同对象的两个事件。"""

    ARTICLE = art(content='国家发展改革委支持人工智能产业发展，同时整治违法收费行为。')

    def test_two_clauses_become_two_events(self):
        ex = events_of([self.ARTICLE])
        clauses = [e['clause'] for e in ex['events']]
        self.assertGreaterEqual(len(clauses), 2, '并列分句必须拆开')

    def test_off_topic_clause_flagged_and_excluded(self):
        ex = events_of([self.ARTICLE])
        support = pick(ex, '支持人工智能')
        fee = pick(ex, '违法收费')
        self.assertTrue(support and fee)
        self.assertTrue(support[0]['applies_to_topic'])
        self.assertFalse(fee[0]['applies_to_topic'],
                         '整治违法收费的对象不是人工智能，不得计入本主题方向')
        self.assertTrue(fee[0]['needs_review'])

    def test_direction_not_merged_across_objects(self):
        signals = build_signals(events_of([self.ARTICLE]))
        by_dir = signals['dimensions']['政策方向']['by_direction']
        self.assertIn('支持', by_dir)
        self.assertNotIn('规范', by_dir,
                         '另一对象的整治不得并入本主题的方向构成')


class TestSubjectLevel(unittest.TestCase):
    """地方机关、中央机关、市场主体、被引述观点不能混为一体。"""

    def test_central_subject(self):
        ev = pick(events_of([art(content='科技部印发人工智能发展指导意见。')]), '科技部')
        self.assertEqual(ev[0]['subject_level'], '中央')
        self.assertEqual(ev[0]['authority'], 'official')

    def test_local_subject(self):
        ev = pick(events_of([art(content='广东省人民政府开展人工智能应用试点。')]), '广东省')
        self.assertEqual(ev[0]['subject_level'], '地方')
        self.assertIn('试点', ev[0]['scope_labels'])

    def test_quoted_opinion_is_not_official_action(self):
        arts = [art(content='有专家建议加强人工智能监管，完善相关标准。')]
        ev = pick(events_of(arts), '专家建议')
        self.assertTrue(ev)
        self.assertEqual(ev[0]['authority'], 'quoted_opinion')
        self.assertFalse(ev[0]['is_new_action'])
        self.assertTrue(ev[0]['needs_review'])

    def test_quoted_opinion_excluded_from_dimensions(self):
        arts = [art(content='有专家建议加强人工智能监管，完善相关标准。')]
        signals = build_signals(events_of(arts))
        self.assertEqual(signals['status'], 'unknown',
                         '只有被引述观点时，不得输出任何维度取值')


class TestSubjectInheritance(unittest.TestCase):
    """真实样本实测：按分句独立判主体会把 88% 的事件判成"主体未识别"。
    中文行文里主体通常只在句首出现一次，同句后续分句是省略而非缺失。"""

    def test_subject_inherited_within_sentence(self):
        arts = [art(content='教育部将实施人工智能教师发展行动，并推动人工智能标准建设。')]
        ev = pick(events_of(arts), '推动人工智能标准')
        self.assertTrue(ev)
        self.assertEqual(ev[0]['subject'], '教育部')
        self.assertTrue(ev[0]['subject_inherited'])
        self.assertIn('主体由同句前文继承而来，需确认该分句确实沿用同一主体',
                      ev[0]['review_reasons'])

    def test_inherited_subject_event_passes_evidence_check(self):
        """继承主体的事件，证据范围必须覆盖到主体所在的前文，否则核验必然失败。"""
        arts = [art(content='教育部将实施人工智能教师发展行动，并推动人工智能标准建设。')]
        ev = pick(events_of(arts), '推动人工智能标准')
        self.assertTrue(ev[0]['subject_inherited'])
        self.assertTrue(ev[0]['evidence_verified'],
                        '继承主体时证据应扩到整句，而不是放宽核验')
        self.assertIn('教育部', ev[0]['evidence']['quote'])

    def test_subject_not_inherited_across_sentences(self):
        """跨句不继承 —— 否则会把上一句的主体安到下一句头上。"""
        arts = [art(content='教育部发布人工智能使用指南。企业推动人工智能应用落地。')]
        ev = pick(events_of(arts), '企业推动')
        self.assertTrue(ev)
        self.assertNotEqual(ev[0]['subject'], '教育部')

    def test_continuation_fragment_is_not_an_event(self):
        """"以促进地区和平发展繁荣"这类续写残片既无主体也无对象，不是独立事件。"""
        arts = [art(content='人工智能治理受到关注，以促进地区和平发展繁荣。')]
        clauses = [e['clause'] for e in events_of(arts)['events']]
        self.assertFalse(any('以促进地区和平' in c for c in clauses),
                         '既无主体又无主题词的残片不得计为事件')


class TestEvidenceCheck(unittest.TestCase):
    """抽取结果必须有原文支持：引文、机构、文号、日期都不得补全。"""

    ARTICLES = [art(title='人工智能产业观察',
                    content='工业和信息化部推进人工智能标准建设。')]

    def _fake(self, **over):
        base = {
            'clause': '工业和信息化部推进人工智能标准建设',
            'subject': '工业和信息化部',
            'tool_terms': ['标准'],
            'object_terms': ['人工智能'],
            'evidence': {'article_title': '人工智能产业观察',
                         'quote': '工业和信息化部推进人工智能标准建设。',
                         'char_start': 0, 'char_end': 20},
        }
        base.update(over)
        return base

    def test_grounded_event_passes(self):
        r = verify_events([self._fake()], self.ARTICLES)
        self.assertEqual(r['verified_count'], 1)
        self.assertEqual(r['evidence_completeness'], 1.0)

    def test_fabricated_quote_rejected(self):
        e = self._fake()
        e['evidence'] = {**e['evidence'], 'quote': '国务院决定全面放开人工智能应用。'}
        r = verify_events([e], self.ARTICLES)
        self.assertEqual(r['unsupported_count'], 1)
        self.assertTrue(r['events'][0]['needs_review'])

    def test_fabricated_subject_rejected(self):
        r = verify_events([self._fake(subject='国务院')], self.ARTICLES)
        self.assertEqual(r['unsupported_count'], 1)
        self.assertIn('国务院', r['events'][0]['evidence_failures'][0])

    def test_fabricated_doc_number_rejected(self):
        r = verify_events([self._fake(doc_number='国办发〔2026〕5号')], self.ARTICLES)
        self.assertEqual(r['unsupported_count'], 1)
        self.assertTrue(any('文号' in f or 'doc_number' in f
                            for f in r['events'][0]['evidence_failures']))

    def test_fabricated_date_rejected(self):
        r = verify_events([self._fake(effective_date='2026年10月1日')], self.ARTICLES)
        self.assertEqual(r['unsupported_count'], 1)

    def test_verify_claim_catches_unsupported_term(self):
        r = verify_claim(['国务院'], '工业和信息化部推进人工智能标准建设。', self.ARTICLES)
        self.assertFalse(r['verified'])

    def test_unsupported_events_excluded_from_dimensions(self):
        e = self._fake(subject='国务院')
        e.update({'applies_to_topic': True, 'authority': 'official',
                  'direction': '支持', 'tense': 'current', 'policy_tools': ['标准']})
        signals = build_signals(verify_events([e], self.ARTICLES))
        self.assertEqual(signals['status'], 'unknown',
                         '未通过证据核验的事件不得进入维度汇总')


class TestReprintMerge(unittest.TestCase):
    """转载是一个事件的多个来源，不是多个独立证据。"""

    def test_reprints_collapse_into_one_cluster(self):
        rmrb = [{'title': '推进人工智能标准建设', 'content': '正文', 'page_no': 1}]
        external = [
            {'title': '推进人工智能标准建设', 'summary': '摘要', 'media': '人民网'},
            {'title': '推进人工智能标准建设！', 'summary': '摘要', 'media': '人民网'},
        ]
        r = merge_sources(rmrb, external)
        self.assertEqual(r['original_count'], 1, '同一条报道的多次转载只算一条原发')
        self.assertEqual(r['reprint_count'], 2)
        self.assertEqual(r['item_count'], 3)

    def test_same_system_not_counted_as_independent(self):
        rmrb = [{'title': '推进人工智能标准建设', 'content': '正文', 'page_no': 1}]
        external = [{'title': '推进人工智能标准建设', 'summary': '', 'media': '人民网'}]
        r = merge_sources(rmrb, external)
        self.assertEqual(r['independent_system_count'], 1,
                         '人民日报与人民网同属一家，不构成独立印证')
        self.assertEqual(r['cross_system_confirmed'], 0)

    def test_cross_system_is_recognized(self):
        rmrb = [{'title': '推进人工智能标准建设', 'content': '正文', 'page_no': 1}]
        external = [{'title': '推进人工智能标准建设', 'summary': '', 'media': '新华社'}]
        r = merge_sources(rmrb, external)
        self.assertEqual(r['cross_system_confirmed'], 1)


class TestSignalDimensions(unittest.TestCase):
    """支持与监管可以同时增强，不共用一根标尺。"""

    def test_support_and_regulation_coexist(self):
        arts = [art(content='国家发展改革委支持人工智能产业发展。'),
                art(content='国家互联网信息办公室加强监管人工智能服务。')]
        signals = build_signals(events_of(arts))
        self.assertEqual(signals['status'], 'ok')
        self.assertTrue(signals['dimensions']['政策方向']['coexists'])

    def test_execution_components_missing_is_reported(self):
        arts = [art(content='工业和信息化部推进人工智能标准建设。')]
        signals = build_signals(events_of(arts))
        comps = signals['dimensions']['执行证据']['components']
        self.assertTrue(comps['细则或标准']['present'])
        self.assertFalse(comps['资源安排']['present'])
        self.assertEqual(signals['dimensions']['执行证据']['components_present'], '1/4')

    def test_no_events_yields_unknown_dimensions(self):
        signals = build_signals({'events': []})
        self.assertEqual(signals['status'], 'unknown')
        self.assertTrue(all(v['status'] == 'unknown'
                            for v in signals['dimensions'].values()))


class TestChangeDetection(unittest.TestCase):
    """无基线只能写"本系统首次观察"；口径不可比不给结论。"""

    def _snap(self, arts):
        return {'signals': build_signals(events_of(arts)), 'date': '20260902'}

    def test_no_baseline_is_first_observation(self):
        cur = self._snap([art(content='科技部印发人工智能发展指导意见。')])
        r = detect_changes(cur, None)
        self.assertEqual(r['status'], 'first_observation')
        self.assertTrue(all(c['status'] == 'no_baseline' for c in r['changes']))
        self.assertIn('本系统首次观察', r['changes'][0]['detail'])
        self.assertIn('不得表述为"官方首次提出"', r['changes'][0]['detail'])
        self.assertTrue(all(c['changed'] is None for c in r['changes']),
                        '无基线时不得给出任何"变了/没变"的结论')

    def test_not_comparable_blocks_conclusions(self):
        cur = self._snap([art(content='科技部印发人工智能发展指导意见。')])
        prev = self._snap([art(content='科技部研究制定人工智能相关规则。')])
        r = detect_changes(cur, prev, comparable=False)
        self.assertEqual(r['status'], 'not_comparable')
        self.assertTrue(all(c['changed'] is None for c in r['changes']))

    def test_procedural_advance_detected_with_alternatives(self):
        prev = self._snap([art(content='科技部研究制定人工智能相关规则。')])
        cur = self._snap([art(content='科技部印发人工智能发展指导意见。')])
        r = detect_changes(cur, prev, comparable=True)
        self.assertEqual(r['status'], 'ok')
        proc = next(c for c in r['changes'] if c['question'] == '程序推进了吗')
        self.assertTrue(proc['changed'])
        self.assertTrue(proc['alternative_explanations'],
                        '每条变化都必须带替代解释')

    def test_every_change_carries_alternatives(self):
        prev = self._snap([art(content='科技部研究制定人工智能相关规则。')])
        cur = self._snap([art(content='科技部印发人工智能发展指导意见。')])
        r = detect_changes(cur, prev, comparable=True)
        for c in r['changes']:
            self.assertTrue(c['alternative_explanations'], c['question'])


class TestTopicConfig(unittest.TestCase):
    """主题口径与企业画像。"""

    def test_pilot_topics_load(self):
        from agent.topics import load_topic
        for key, label in (('ai', '人工智能'), ('vaccine', '疫苗')):
            t = load_topic(key)
            self.assertEqual(t['label'], label)
            self.assertEqual(t['topic_id'], f'topic:{key}')
            self.assertTrue(t['topic_version'])
            self.assertTrue(set(t['keywords']).issubset(set(t['search_terms'])))

    def test_synonyms_expand_into_search_terms(self):
        from agent.topics import load_topic
        self.assertIn('AI', load_topic('ai')['search_terms'])

    def test_exclude_terms_drop_segments(self):
        arts = [art(content='人工智能大赛在本市举行。'),
                art(content='科技部印发人工智能发展指导意见。')]
        scope = build_target_segments(arts, ['人工智能'],
                                      exclude_terms=['人工智能大赛'])
        self.assertEqual(scope['excluded_by_term'], 1)
        self.assertEqual(scope['segment_count'], 1)

    def test_adhoc_topic_is_isolated_version(self):
        from agent.topics import adhoc_topic
        t = adhoc_topic(['光伏', '储能'])
        self.assertEqual(t['topic_version'], 'adhoc')

    def test_template_direct_and_indirect_exposure(self):
        from agent.topics import load_enterprise, enterprise_exposure
        ent = load_enterprise('example-enterprise')
        self.assertEqual(enterprise_exposure(ent, 'ai')['relevance'], 'direct')
        vaccine = enterprise_exposure(ent, 'vaccine')
        self.assertEqual(vaccine['relevance'], 'indirect',
                         '模板中疫苗登记为间接暴露')
        self.assertIn('间接', vaccine['caveat'])

    def test_unmapped_topic_is_not_irrelevant(self):
        from agent.topics import load_enterprise, enterprise_exposure
        r = enterprise_exposure(load_enterprise('example-enterprise'), 'nonexistent-topic')
        self.assertEqual(r['relevance'], 'unmapped')
        self.assertIn('不是"无关"', r['note'])

    def test_missing_topic_raises(self):
        from agent.topics import load_topic
        with self.assertRaises(FileNotFoundError):
            load_topic('no-such-topic')


if __name__ == '__main__':
    unittest.main(verbosity=2)
