"""
AnalysisSnapshot —— 本期分析的唯一结果对象（方案 §8.1 / F02）

旧版的问题：agent 算了一套加权结果用于展示，却把采集层那套等权旧结果
拿去做历史比较和持久化。三个出口不是同一个结论。

现在只有一个对象：报告渲染、历史比较、数据库存储全部以它为输入。
采集层只提供事实与元数据，不再维护第二套叙事与强度算法。

快照本身携带质量门槛与已知限制 —— 局限必须是程序里的门槛，
不是报告末尾的一句提示。
"""

import datetime

from agent.versioning import ALGO_VERSION

SNAPSHOT_SCHEMA_VERSION = 'snapshot-v2-p1'

# 明确未修复、下游不得当作已解决的问题。
KNOWN_LIMITATIONS = [
    'F07 media_fetch 未按时间范围过滤，且只覆盖人民网 RSS 单一来源，'
    '"近7天""多官媒聚合"不能按字面相信。',
    'F08 外部来源只有标题与摘要，没有全文，因此只进入来源归并与传播维度，'
    '**不参与事件抽取与机构识别**。跨体系印证数以 dedupe.cross_system_confirmed 为准。',
    'F09 版面类型由页码推断，未读取版面页实际版名；头条判断依赖文章枚举顺序。',
    '事件抽取是规则召回 + 预结构化，不是语义解析：跨句指代、复杂否定范围、'
    '隐含主体仍会出错，needs_review 事件必须人工或模型消解。',
    '正式文件来源覆盖有限：只接入了中国政府网两个入口（国务院层级最近若干件）。'
    '网信办 521、卫健委与药监局 412、政策文件库分页 403，主管部门文件全部缺失。'
    '因此"未检索到相关文件"只能表述为"已接入来源中没有"，不得表述为"没有出台文件"。',
    '语义准确率、变化检测精确率与预警提前量均未标定（P3）。',
]


def build_snapshot(*, topic, summary, scope, narrative, intensity, ministry,
                   cooccurrence, clock, timing, extraction=None, signals=None,
                   dedupe=None, enterprise=None, exposure=None,
                   cross_validation=None, as_of=None,
                   policy_documents=None, cache_report=None) -> dict:
    """
    组装唯一分析结果对象。

    参数全部为关键字参数：调用点必须显式写出每一项，避免再次出现
    "把旧 summary 传进比较函数"这类静默错配。
    """
    date = summary.get('date', '')
    as_of = as_of or summary.get('as_of') or date
    quality = summary.get('fetch_quality') or {'status': 'unknown',
                                               'silence_conclusion_allowed': False}

    total_articles = summary.get('total_articles', 0)
    total_scanned = quality.get('total_scanned_articles', 0)
    topic_share = (total_articles / total_scanned) if total_scanned else None

    evidence_state = (timing or {}).get('evidence_state', '⚪ 证据不足')
    has_evidence = intensity.get('status') == 'ok' or ministry.get('status') == 'ok'
    event_conclusions_ok = (signals or {}).get('status') == 'ok'

    return {
        'schema_version': SNAPSHOT_SCHEMA_VERSION,
        'algo_version': ALGO_VERSION,

        # ── 主题身份与口径版本 ─────────────────────────────
        'topic_id': topic['topic_id'],
        'topic_key': topic.get('topic_key'),
        'topic_version': topic.get('topic_version', 'adhoc'),
        'topic_label': topic.get('label'),
        'keywords': topic.get('search_terms') or topic.get('keywords') or [],
        'topic_definition': {
            'keywords': topic.get('keywords'),
            'synonyms': topic.get('synonyms'),
            'exclude_terms': topic.get('exclude_terms'),
            'region_scope': topic.get('region_scope'),
            'source': topic.get('source'),
        },

        # ── 企业画像与暴露映射 ─────────────────────────────
        'enterprise': ({'key': enterprise.get('enterprise_key'),
                        'name': enterprise.get('name'),
                        'industry': enterprise.get('industry'),
                        'chair_focus': enterprise.get('chair_focus'),
                        'chair_focus_confirmed': enterprise.get('chair_focus_confirmed')}
                       if enterprise else None),
        'exposure': exposure,

        'date': date,
        'as_of': as_of,
        'generated_at': datetime.datetime.now().isoformat(),

        # ── 事实层 ────────────────────────────────────────────
        'total_articles': total_articles,
        'total_scanned': total_scanned,
        'topic_share': round(topic_share, 4) if topic_share is not None else None,
        'fetch_quality': quality,
        'agenda': summary.get('step1_agenda', {}),
        'regions': summary.get('step4_regions', {}),
        'articles': summary.get('articles', []),

        # ── 目标相关性切分 ─────────────────────────────────────
        'scope': {
            'segment_count': scope.get('segment_count', 0),
            'articles_with_target': scope.get('articles_with_target', 0),
            'articles_scanned': scope.get('articles_scanned', 0),
            'dropped_sentences': scope.get('dropped_sentences', 0),
            'excluded_by_term': scope.get('excluded_by_term', 0),
            'has_target': scope.get('has_target', False),
            'method': scope.get('method'),
            'limitation': scope.get('limitation'),
        },

        # ── 事件层（P1）────────────────────────────────────────
        'extraction': ({k: v for k, v in extraction.items() if k != 'events'}
                       if extraction else None),
        'events': (extraction or {}).get('events', []),
        'signals': signals,
        'dedupe': dedupe,

        # ── 正式政策文件（事实锚点，P2）────────────────────────
        'policy_documents': policy_documents,
        'cache_report': cache_report,

        # ── 兼容特征（词面）────────────────────────────────────
        'narrative': narrative,
        'intensity': intensity,
        'ministry': ministry,
        'cooccurrence': cooccurrence,
        'clock': clock,
        'timing': timing,
        'cross_validation': cross_validation,

        'evidence_state': evidence_state,

        # ── 程序门槛（不是报告末尾的提示语）────────────────────
        'quality_gates': {
            'analysis_allowed': bool(has_evidence),
            'event_conclusions_allowed': event_conclusions_ok,
            'silence_conclusion_allowed': bool(quality.get('silence_conclusion_allowed')),
            'comparison_allowed': quality.get('status') == 'complete',
            'window_prediction_allowed': False,  # §7.4：未标定，一律不输出预测窗口
            'enterprise_impact_allowed': bool(
                exposure and exposure.get('relevance') in ('direct', 'indirect')),
            # 只有真的取到正式文件，才允许谈"正式规定了什么"。
            'official_document_claims_allowed': bool(
                (policy_documents or {}).get('matched_count')),
        },
        'known_limitations': list(KNOWN_LIMITATIONS),
    }
