"""P1 判断稳定化：语境过滤 / 相关性评分 / 词库手术 / 共现分段修复。"""

from conftest import make_article

from agent.tools.context_filter import classify_occurrence, count_valid, has_valid_occurrence
from agent.tools.relevance import score_relevance, annotate_relevance
from agent.tools.weighting import get_article_weight, weighted_phrase_count
from agent.tools.narrative_frame import classify_narrative, NARRATIVE_FRAMES
from agent.tools.discourse_level import measure_intensity, INTENSITY_LEVELS
from agent.tools.cooccurrence import _split_paragraphs, analyze_cooccurrence


class TestContextFilter:
    def test_valid_occurrence(self):
        text = '有关部门将开展专项整治，维护市场秩序。'
        pos = text.find('专项整治')
        assert classify_occurrence(text, pos, '专项整治') == 'valid'

    def test_negated_occurrence(self):
        text = '要防止专项整治一刀切，保护正常经营。'
        pos = text.find('专项整治')
        assert classify_occurrence(text, pos, '专项整治') == 'negated'

    def test_retrospective_occurrence(self):
        text = '当年扫黑除恶专项斗争取得重大成果。'
        pos = text.find('扫黑除恶')
        assert classify_occurrence(text, pos, '扫黑除恶') == 'retrospective'

    def test_negation_only_within_same_sentence(self):
        text = '要防止形式主义。各地开展专项整治行动。'
        pos = text.find('专项整治')
        assert classify_occurrence(text, pos, '专项整治') == 'valid'

    def test_count_valid_mixed(self):
        text = '当年扫黑除恶成效显著。今年继续推进扫黑除恶常态化。'
        valid, filtered = count_valid(text, '扫黑除恶')
        assert valid == 1
        assert len(filtered) == 1
        assert filtered[0]['reason'] == 'retrospective'

    def test_has_valid_occurrence_false_when_all_filtered(self):
        text = '不搞运动式整治，避免专项整治扩大化。'
        assert not has_valid_occurrence(text, '专项整治')


class TestRelevance:
    def test_title_hit_gets_full_multiplier(self):
        art = make_article(title='光伏产业迎来新机遇', content='光伏发电装机量增长。' * 10)
        scored = score_relevance(art, ['光伏'])
        assert scored['title_hit']
        assert scored['multiplier'] == 1.0

    def test_passing_mention_dropped(self):
        filler = '今天天气晴朗，与会代表讨论了农业问题。' * 100
        art = make_article(title='农业农村工作会议', content=filler + '会上顺带提到光伏一次。')
        result = annotate_relevance([art], ['光伏'])
        assert result['stats']['kept_count'] == 0
        assert result['stats']['dropped_count'] == 1

    def test_dense_body_article_kept(self):
        content = '光伏组件出口创新高。光伏电站建设加速。光伏技术迭代。'
        art = make_article(title='能源转型观察', content=content)
        result = annotate_relevance([art], ['光伏'])
        assert result['stats']['kept_count'] == 1
        assert result['kept'][0]['relevance'] > 0.12

    def test_relevance_multiplier_feeds_weight(self):
        art = make_article(page_no=1, column='社论', relevance_multiplier=0.5)
        full = make_article(page_no=1, column='社论')
        assert get_article_weight(art) == get_article_weight(full) * 0.5


class TestLexiconSurgery:
    def test_generic_anquan_removed_from_security_frame(self):
        assert '安全' not in NARRATIVE_FRAMES['国家安全框架']['keywords']

    def test_food_safety_does_not_trigger_security_frame(self):
        art = make_article(title='加强食品安全监管', content='各地强化食品安全和生产安全检查。')
        result = classify_narrative([art])
        assert result['primary_frame'] != '国家安全框架'

    def test_national_security_still_triggers(self):
        art = make_article(title='坚持总体国家安全观', content='统筹发展和国家安全。')
        result = classify_narrative([art])
        assert result['primary_frame'] == '国家安全框架'

    def test_jinyibu_guifan_moved_to_level3(self):
        assert '进一步规范' in INTENSITY_LEVELS[3]['phrases']
        assert '进一步规范' not in INTENSITY_LEVELS[5]['phrases']

    def test_negated_intensity_phrase_not_counted(self):
        art = make_article(
            title='保持政策连续性',
            content='要防止专项整治一刀切，杜绝坚决遏制式的简单化操作。',
        )
        result = measure_intensity([art])
        assert result['distribution']['level_4']['count'] == 0
        assert result['distribution']['level_5']['count'] == 0

    def test_phrase_weighting_skips_retrospective(self):
        art = make_article(title='回望这五年', content='当年雷霆行动净化了市场。')
        score, matched = weighted_phrase_count(art, ['雷霆行动'])
        assert score == 0.0
        assert matched == []


class TestCooccurrenceFix:
    def test_single_newline_splits_paragraphs(self):
        text = '第一段说光伏发展取得突破，成效显著。\n第二段讨论别的议题，涉及农业生产和粮食丰收问题。\n第三段提到光伏存在风险，需要整治。'
        paras = _split_paragraphs(text)
        assert len(paras) == 3

    def test_sentiment_isolated_per_paragraph(self):
        content = (
            '光伏产业实现重大突破，技术领先世界。\n'
            '另一方面，部分地区光伏项目存在违规风险，已启动整治。'
        )
        art = make_article(title='能源观察', content=content)
        result = analyze_cooccurrence([art], ['光伏'])
        assert result['positive_ratio'] > 0
        assert result['negative_ratio'] > 0
        assert result['signal_conflict']

    def test_anchor_in_other_paragraph_not_counted(self):
        content = (
            '光伏装机数据平稳，按计划推进项目并网。\n'
            '化工行业出现重大违规风险，监管部门启动专项整治和严查。'
        )
        art = make_article(title='产业动态', content=content)
        result = analyze_cooccurrence([art], ['光伏'])
        assert result['negative_ratio'] == 0.0
