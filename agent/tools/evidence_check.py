"""
Tool: 证据核验（P1，对应方案 §6.2 / §9.2「证据完整率」）

方案的分工是：规则召回 → 模型结构化抽取 → **程序核验抽取结果是否有原文支持**。
本模块是第三步。它对规则抽取和模型抽取一视同仁 —— 谁产出的都要过同一道关。

核验四件事：
  1. 引文必须在原文中逐字存在（不是"意思差不多"）。
  2. 结构化字段里出现的每个词，必须在它自己的引文里出现。
     模型不能凭常识写上"国务院"却引一段没有国务院的话。
  3. 文号与日期不得凭空补全 —— **文件级**身份（文号/发文机关/发布日期/生效日期）
     必须与真正解析过的那份文件一致；**分句级**主张必须出现在自己的引文里。
     把这两层混在一起核验，会把正确的文件元数据误判成编造。
  4. 字符位置若给出，必须真的指向那段引文。

未通过核验的事件不是"可能有问题"，而是**不得进入已确认结论**：
它会被强制标成 needs_review 并写明原因，由人工或模型回原文消解。
"""

import re

# 文号形态：〔2026〕12号 / [2026]12号 / 第12号令 / 国办发〔2026〕5号
DOC_NUMBER_PATTERN = re.compile(r'[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号|第?\s*\d+\s*号令')
DATE_PATTERN = re.compile(r'\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}年底')

# 分句级主张：必须逐词出现在**这条事件自己的引文**里。
GROUNDED_FIELDS = (
    'tool_terms', 'direction_terms', 'negation_terms',
    'object_terms', 'regions',
)

# 文件级身份：文号、发文机关、日期属于**文件的元数据**，不是分句里的字。
# 要求它们出现在分句引文中是把核验层级搞错了 —— 正确的问法是：
# 这个取值是不是来自我们真的解析过的那份文件？
DOCUMENT_LEVEL_FIELDS = ('doc_number', 'issuing_agency', 'published_at',
                         'effective_at', 'doc_status')


def build_source_index(articles: list[dict]) -> dict:
    """
    建立原文索引。key 用标题，value 是标题 + 正文的拼接文本。
    同名标题合并，避免转载副本互相遮蔽。
    """
    index = {}
    for a in articles or []:
        title = (a.get('title') or '').strip()
        content = a.get('content') or ''
        # 字符偏移由 scoping/evidence_of 相对于正文生成；标题仅作为索引键，
        # 不能塞进被定位的字符串，否则所有偏移都会平移一个标题长度。
        blob = content
        if title in index:
            index[title] = index[title] + '\n' + blob
        else:
            index[title] = blob
    index['__ALL__'] = '\n'.join(index.values())
    return index


def _as_terms(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v).strip()]
    return []


def build_document_index(documents: list[dict]) -> dict:
    """按标题索引已解析的政策文件，用于核验文件级身份字段。"""
    return {(d.get('title') or '').strip(): d for d in documents or []}


def verify_event(event: dict, source_index: dict, doc_index: dict = None) -> dict:
    """核验单个事件。返回 {verified, failures, checked}。"""
    failures = []
    evidence = event.get('evidence') or {}
    quote = (evidence.get('quote') or '').strip()
    title = (evidence.get('article_title') or '').strip()

    if not quote:
        return {'verified': False, 'failures': ['缺少引文，无法核验'], 'checked': 0}

    # 省略号是本系统截断引文时加的，核验时按前缀匹配处理。
    probe = quote[:-3] if quote.endswith('...') else quote

    # 标题是证据定位的一部分。找不到标题时搜索全库会让一条真实引文
    # 被错误绑定到虚构文章，报告也无法带读者回到正确来源。
    haystack = source_index.get(title, '') if title else source_index.get('__ALL__', '')
    if title and title not in source_index:
        failures.append(f'证据标题不存在于原始来源：{title}')
    if probe and probe not in haystack:
        failures.append(f'引文在原文中不存在（标题：{title or "未给出"}）')

    checked = 0
    for field in GROUNDED_FIELDS:
        for term in _as_terms(event.get(field)):
            checked += 1
            if term not in quote:
                failures.append(f'字段 {field} 的取值「{term}」未出现在自己的引文中')

    # 主体：正常情况下必须在引文里；但正式文件常省略主语，主体来自发文机关
    # 这项文件元数据。此时核验的对象是"元数据是否与解析到的文件一致"。
    subject = event.get('subject')
    if subject:
        checked += 1
        if event.get('subject_source') == 'document_metadata':
            doc = (doc_index or {}).get((evidence.get('article_title') or '').strip())
            if not doc:
                failures.append(f'主体「{subject}」标称来自文件元数据，但找不到对应的已解析文件')
            elif (doc.get('issuing_agency') or '') != subject:
                failures.append(
                    f'主体「{subject}」与文件解析到的发文机关「{doc.get("issuing_agency")}」不一致')
        elif subject not in quote:
            failures.append(f'字段 subject 的取值「{subject}」未出现在自己的引文中')

    # 文件级身份：取值必须与我们真的解析过的那份文件一致，
    # 不是"出现在分句里"。模型不得凭空写一个文号或生效日期。
    doc = (doc_index or {}).get((evidence.get('article_title') or '').strip())
    for field in DOCUMENT_LEVEL_FIELDS:
        value = event.get(field)
        if value in (None, ''):
            continue
        checked += 1
        if not doc:
            failures.append(f'{field}「{value}」没有对应的已解析文件作为来源：不得补全')
        elif str(doc.get(field) or '') != str(value):
            failures.append(
                f'{field}「{value}」与文件解析值「{doc.get(field)}」不一致：不得补全')

    # 旧式字段名（模型可能用这些）仍按引文核对。
    for field in ('effective_date', 'deadline', 'issued_date'):
        value = event.get(field)
        if not value:
            continue
        checked += 1
        if str(value) not in quote:
            failures.append(f'{field}「{value}」未出现在引文中：文号与日期不得补全')

    for pattern, label in ((DOC_NUMBER_PATTERN, '文号'), (DATE_PATTERN, '日期')):
        for m in pattern.finditer(str(event.get('clause') or '')):
            checked += 1
            if m.group() not in quote:
                failures.append(f'分句中的{label}「{m.group()}」未出现在引文中')

    start, end = evidence.get('char_start'), evidence.get('char_end')
    if isinstance(start, int) and isinstance(end, int):
        if end <= start:
            failures.append(f'字符位置无效：char_start={start} >= char_end={end}')
        # 旧导入数据可能没有标题/文档 ID；此时只能核验引文存在，不能把
        # 多篇无标题文章拼接后的偏移假装成可定位坐标。
        elif title and (start < 0 or end > len(haystack) or haystack[start:end] != probe):
            failures.append('字符位置没有定位到该引文')

    return {'verified': not failures, 'failures': failures, 'checked': checked}


def verify_events(events: list[dict], articles: list[dict],
                  documents: list[dict] = None) -> dict:
    """
    批量核验。未通过的事件被强制打上 needs_review，并写明核验失败原因。

    返回：
      {status, events（已回填核验结果）, verified_count, unsupported_count,
       evidence_completeness, unsupported_samples, note}
    """
    # 正式文件也是原始证据；统一进入索引后，分句引文才能被定位到其文件，
    # 而不是错误地只在人民日报文章中搜索。
    index = build_source_index((articles or []) + (documents or []))
    doc_index = build_document_index(documents)
    out = []
    verified_count = 0

    for e in events or []:
        result = verify_event(e, index, doc_index)
        e = dict(e)
        e['evidence_verified'] = result['verified']
        e['evidence_failures'] = result['failures']
        if result['verified']:
            verified_count += 1
        else:
            e['needs_review'] = True
            e['review_reasons'] = list(e.get('review_reasons') or []) + [
                '证据核验未通过：' + '；'.join(result['failures'])
            ]
            e['confidence'] = 'low'
        out.append(e)

    total = len(out)
    return {
        'status': 'ok' if total else 'no_events',
        'events': out,
        'verified_count': verified_count,
        'unsupported_count': total - verified_count,
        # 方案 §9.2 的"证据完整率"：关键结论中有可定位原文的比例。
        'evidence_completeness': round(verified_count / total, 3) if total else None,
        'unsupported_samples': [
            {'clause': e.get('clause'), 'failures': e['evidence_failures']}
            for e in out if not e['evidence_verified']
        ][:10],
        'note': '未通过核验的事件不得进入已确认结论，只能作为待核验候选呈现。',
    }


def verify_claim(claim_terms, quote: str, articles: list[dict]) -> dict:
    """
    给模型写报告时用的通用核验：一句结论 + 它引用的原文。

    claim_terms 是这句结论里所有需要有原文支撑的具体词（机构名、日期、文号、
    数量、地域）。任何一个在引文里找不到，这句结论就不能写成已确认。
    """
    index = build_source_index(articles)
    failures = []
    probe = quote[:-3] if quote.endswith('...') else quote
    if not probe or probe not in index.get('__ALL__', ''):
        failures.append('引文在本期原文中不存在')
    for term in _as_terms(claim_terms):
        if term not in quote:
            failures.append(f'结论中的「{term}」没有出现在引文里')
    return {'verified': not failures, 'failures': failures}
