"""
事件抽取与 context_filter 的合并是否真的生效。

两边词表互有缺漏，合并取并集：
  context_filter 独有否定词：反对 / 纠正 / 摒弃 / 克服 / 警惕 / 切忌 / 不能搞 / 不得搞
  context_filter 独有回顾词：当年 / 前年 / 前些年 / 历史上 / 回顾 / 回望 / 那时 / 一度
  event_extract 独有否定词：不得 / 严禁 / 禁止 / 不再 / 不予
  event_extract 独有回顾词：以来 / 累计 / 已经 / 同比 / 截至目前 / 历年

本文件专挑**只有对方词表才认得**的词，因此任何一侧被摘掉都会变红 ——
否则"已合并"就是一句没有检查的声称。
"""

import pytest

from agent.tools.scoping import build_target_segments
from agent.tools.event_extract import extract_events

TERMS = ['人工智能']


def events(content):
    arts = [{'title': '', 'content': content, 'page_no': 1, 'column': ''}]
    scope = build_target_segments(arts, TERMS)
    return extract_events(scope, TERMS)['events']


def one(content, needle):
    got = [e for e in events(content) if needle in e['clause']]
    assert got, f'未抽出含「{needle}」的事件：{[e["clause"] for e in events(content)]}'
    return got[0]


class TestNegationFromContextFilter:
    """只有 context_filter 词表认得的否定词。"""

    @pytest.mark.parametrize('marker', ['警惕', '切忌', '反对', '纠正'])
    def test_context_filter_only_negation_markers(self, marker):
        e = one(f'{marker}以整治人工智能之名层层加码。', '整治')
        assert e['context_class'] == 'negated'
        assert e['negated'], f'「{marker}」是 context_filter 独有否定词，摘掉合并就会漏判'
        assert not e['is_new_action']

    def test_own_marker_still_works(self):
        """反向对照：本模块独有的否定词不能因为合并而丢。"""
        e = one('不得随意查封人工智能企业。', '查封')
        assert e['negated']
        assert e['direction'] == '约束执法行为'


class TestRetrospectiveFromContextFilter:
    """只有 context_filter 词表认得的回顾词。"""

    @pytest.mark.parametrize('marker', ['当年', '历史上', '一度', '前些年'])
    def test_context_filter_only_retro_markers(self, marker):
        e = one(f'{marker}开展人工智能专项整治取得成效。', '整治')
        assert e['context_class'] == 'retrospective'
        assert e['tense'] == 'historical', \
            f'「{marker}」是 context_filter 独有回顾词，摘掉合并会把历史当成本期新增'
        assert not e['is_new_action']

    def test_own_marker_still_works(self):
        e = one('近年来持续推进人工智能标准建设。', '推进')
        assert e['tense'] == 'historical'

    def test_current_action_unaffected(self):
        """空操作对照：没有任何否定/回顾词时，两套判定都不该改变结论。"""
        e = one('科技部印发人工智能发展指导意见。', '印发')
        assert e['context_class'] == 'valid'
        assert not e['negated']
        assert e['tense'] == 'current'


class TestNegationDoesNotBleed:
    """
    否定不得溢出到相邻动作上 —— 这是合并后实测到的行为，比预期更好，钉住它。

    "防止一哄而上，扎实推进X" 里的"防止"管的是"一哄而上"，
    不是"推进X"。分句切分 + context_filter 的邻近窗口共同保证了这一点：
    前者让"防止"留在自己的分句，后者即便跨分句回看也要求紧邻。
    """

    def test_negation_stays_in_its_own_clause(self):
        # 带主体，顺带验证主体跨分句继承与"否定不溢出"能同时成立。
        e = one('科技部防止一哄而上，扎实推进人工智能标准建设。', '推进人工智能标准')
        assert not e['negated'], '"防止一哄而上"不应把"推进标准建设"也判成否定'
        assert e['context_class'] == 'valid'
        assert e['subject'] == '科技部' and e['subject_inherited']
        assert e['is_new_action'], '未被否定的官方动作应计为本期新增'

    def test_subjectless_clause_is_not_a_new_action(self):
        """反向对照：没有主体就判不出"谁在做"，不得计为新增动作。"""
        e = one('防止一哄而上，扎实推进人工智能标准建设。', '推进人工智能标准')
        assert e['authority'] == 'unknown'
        assert not e['is_new_action']

    def test_negation_still_applies_within_its_own_clause(self):
        """反向对照：否定在自己分句内必须生效，不能因为防溢出而一并失灵。"""
        e = one('切忌以整治人工智能之名层层加码。', '整治')
        assert e['negated']
        assert not e['is_new_action']
