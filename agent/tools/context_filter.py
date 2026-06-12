"""
Tool: 语境过滤器（纯代码）

解决关键词匹配的两类假阳性：
  - 否定语境："不搞运动式'减碳'"中的"运动式"不是整治信号
  - 回顾语境："当年扫黑除恶专项斗争"是历史叙述，不是当期信号

设计：以句子为单位判断。短语命中后，检查同句中短语之前的文本，
否定标记需紧邻（10字内），回顾标记同句即生效。
"""

import re

NEGATION_MARKERS = [
    '不搞', '不能搞', '不得搞', '防止', '避免', '反对',
    '杜绝', '纠正', '摒弃', '克服', '警惕', '切忌',
]

RETRO_MARKERS = [
    '当年', '去年', '前年', '前些年', '此前', '曾经', '曾',
    '历史上', '回顾', '回望', '那时', '过去几年', '一度',
]

SENTENCE_SPLIT = re.compile(r'[。！？；\n]')
NEGATION_WINDOW = 10


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    """返回包含 pos 的句子边界 [start, end)。"""
    start = 0
    for m in SENTENCE_SPLIT.finditer(text, 0, pos):
        start = m.end()
    m = SENTENCE_SPLIT.search(text, pos)
    end = m.start() if m else len(text)
    return start, end


def classify_occurrence(text: str, pos: int, phrase: str) -> str:
    """
    判定 text[pos:] 处命中的 phrase 属于何种语境。

    返回 'valid' | 'negated' | 'retrospective'
    """
    start, _ = _sentence_bounds(text, pos)
    before = text[start:pos]

    near = before[-NEGATION_WINDOW:]
    if any(marker in near for marker in NEGATION_MARKERS):
        return 'negated'

    if any(marker in before for marker in RETRO_MARKERS):
        return 'retrospective'

    return 'valid'


def count_valid(text: str, phrase: str) -> tuple[int, list[dict]]:
    """
    统计 phrase 在 text 中的有效出现次数。

    返回 (有效次数, 被过滤的出现 [{pos, reason}])。
    """
    valid = 0
    filtered = []
    pos = text.find(phrase)
    while pos != -1:
        verdict = classify_occurrence(text, pos, phrase)
        if verdict == 'valid':
            valid += 1
        else:
            filtered.append({'pos': pos, 'reason': verdict})
        pos = text.find(phrase, pos + len(phrase))
    return valid, filtered


def has_valid_occurrence(text: str, phrase: str) -> bool:
    """phrase 是否在 text 中有至少一次有效（非否定/非回顾）出现。"""
    valid, _ = count_valid(text, phrase)
    return valid > 0
