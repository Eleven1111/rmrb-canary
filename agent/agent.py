#!/usr/bin/env python3
"""
RMRB-Canary Agent — 政策信号分析管道 v3

纯计算管道，不调用任何 LLM API。
输出结构化 JSON，由 Claude Code / OpenClaw 读取后完成推理和报告撰写。

v3 管道：相关性过滤 + 议题一致性 + 传导链定位 + 滚动平滑 +
提法追踪 + 预测台账 + 议题档案。

用法（Claude Code 内部 Bash 调用）：
  python3 -m agent.agent --keyword 新能源 光伏 储能
  python3 -m agent.agent --keyword 新能源 --date 20260405 --skip-media
  python3 -m agent.agent --history
  python3 -m agent.agent --ledger
  python3 -m agent.agent --resolve 12 hit --note "6月文件落地"
  python3 -m agent.agent --record-judgment judgment.json
"""

import json
import sys
import argparse

from agent.tools.fetch_rmrb import fetch_rmrb
from agent.tools.fetch_media import fetch_media_sources
from agent.tools.relevance import annotate_relevance
from agent.tools.topic_coherence import check_coherence
from agent.tools.narrative_frame import classify_narrative
from agent.tools.discourse_level import measure_intensity
from agent.tools.ministry_signals import detect_ministries
from agent.tools.policy_clock import get_policy_clock, calculate_risk_window
from agent.tools.history_compare import compare_history, get_history
from agent.tools.silence_detector import detect_silence, rolling_trend
from agent.tools.cooccurrence import analyze_cooccurrence
from agent.tools.rolling_window import rolling_verdict
from agent.tools.transmission_chain import locate_stage
from agent.tools.formulation_tracker import track_formulations
from agent.sources import collect_upstream
from agent.sources.theory_channel import extract_pd_theory
from agent.store.db import log_run, save_analysis_detailed
from agent.store import ledger, judgments, dossier

# ── P0–P3 合并引入的事件层 ─────────────────────────────────
from agent.topics import resolve_topic
from agent.tools.scoping import build_target_segments
from agent.tools.event_extract import extract_events
from agent.tools.evidence_check import verify_events
from agent.tools.signals import build_signals
from agent.tools.dedupe import merge_sources
from agent.tools.change_detect import detect_changes
from agent.tools.alerts import build_alerts


def run_pipeline(
    keywords: list[str],
    date: str = None,
    skip_media: bool = False,
    skip_sources: bool = False,
    window_days: int = 7,
    topic_key: str = None,
    as_of: str = None,
    skip_events: bool = False,
) -> dict:
    """
    执行完整分析管道，返回结构化结果 dict。

    全程纯计算，零 LLM 调用。Claude Code 拿到结果后：
    1. 读 full_texts 做语义三元组提取
    2. 结合 dossier_context 回应上期判断，综合撰写报告
    3. 报告完成后用 --record-judgment 沉淀本期判断

    新增（P0–P3 合并，加法式，不改动原有 12 步）：
      topic_key   指定 config/topics/<key>.json 的主题口径；不给则由 keywords
                  生成临时口径（topic_version='adhoc'），不与正式主题历史混比。
      as_of       分析截止时点。历史回放必须显式指定。
      skip_events 跳过事件层，退回纯 v3 行为。

    事件层产出 result['events'/'signals'/'dedupe'/'changes'/'alerts']，
    与原有的 intensity/ministry/risk_window 并列，**不互相替代**：
    前者回答"哪个主体对哪个对象做了什么、证据在哪"，后者是词面强度特征。
    """
    result = {'keywords': keywords, 'date_requested': date}

    # ── 1. 采集人民日报 ────────────────────────────────────
    print('[1/12] 采集人民日报...', file=sys.stderr)
    summary = fetch_rmrb(keywords=keywords, date=date)
    analysis_date = summary.get('date', '')
    all_texts = summary.get('full_texts', [])

    # ── 2. 议题一致性检查 ──────────────────────────────────
    print('[2/12] 议题一致性检查...', file=sys.stderr)
    result['coherence'] = check_coherence(all_texts, keywords)
    if result['coherence']['warning']:
        print(f"  ⚠️ {result['coherence']['warning']}", file=sys.stderr)

    # ── 3. 相关性评分过滤 ──────────────────────────────────
    print('[3/12] 相关性评分过滤...', file=sys.stderr)
    rel = annotate_relevance(all_texts, keywords)
    kept = rel['kept']
    result['relevance'] = {'stats': rel['stats'], 'dropped': rel['dropped']}
    print(
        f"  保留 {rel['stats']['kept_count']}/{rel['stats']['input_count']} 篇"
        f"（剔除顺带一提 {rel['stats']['dropped_count']} 篇）",
        file=sys.stderr,
    )

    result['rmrb'] = {
        'date': analysis_date,
        'total_articles': len(kept),
        'total_articles_raw': summary.get('total_articles', 0),
        'total_pages': summary.get('total_pages', 0),
        'agenda': summary.get('step1_agenda', {}),
        'regions': summary.get('step4_regions', {}),
        'articles': summary.get('articles', []),
    }
    result['full_texts'] = kept

    # 主题身份要在入库之前定下来：storage / rolling / silence 都以 topic_id 为准，
    # 拿不到它就只能退回关键词匹配，那正是 F10 要消除的东西。
    topic = resolve_topic(topic_key=topic_key, keywords=keywords)
    effective_as_of = as_of or analysis_date
    result['topic'] = {'topic_id': topic['topic_id'],
                       'topic_key': topic.get('topic_key'),
                       'topic_version': topic.get('topic_version'),
                       'label': topic.get('label')}
    result['as_of'] = effective_as_of

    # ── 4-6. 叙事框架 / 话语强度 / 部委协同（加权+过滤后）────
    print('[4/12] 叙事框架分类...', file=sys.stderr)
    result['narrative'] = classify_narrative(kept)
    print('[5/12] 话语强度定级...', file=sys.stderr)
    result['intensity'] = measure_intensity(kept)
    print('[6/12] 部委协同检测（含联合发文）...', file=sys.stderr)
    result['ministry'] = detect_ministries(kept)

    # ── 7. 共现语境 ───────────────────────────────────────
    print('[7/12] 共现语境分析...', file=sys.stderr)
    result['cooccurrence'] = analyze_cooccurrence(kept, keywords)

    # ── 8. 政策时钟 + 风险窗口（回测校准+频率陈述）──────────
    print('[8/12] 政策时钟 + 风险窗口（回测校准）...', file=sys.stderr)
    result['clock'] = get_policy_clock(analysis_date or None)
    result['risk_window'] = calculate_risk_window(
        intensity_level=result['intensity']['weighted_max_level'],
        ministry_compression=result['ministry']['time_compression'],
        clock_coefficient=result['clock']['coefficient'],
        narrative_speed_modifier=result['narrative']['speed_modifier'],
        ministry_level=result['ministry']['coordination_level'],
    )

    # ── 9. 传导链定位（上游信号源）──────────────────────────
    if not skip_sources:
        print('[9/12] 上游信号源 + 传导链定位...', file=sys.stderr)
        try:
            upstream = collect_upstream(keywords)
            upstream['theory']['items'] = (
                extract_pd_theory(kept, analysis_date)
                + upstream['theory'].get('items', [])
            )
            result['transmission'] = locate_stage(
                rmrb_signals={
                    'intensity_level': result['intensity']['weighted_max_level'],
                    'article_count': len(kept),
                    'coordination_level': result['ministry']['coordination_level'],
                    'has_judicial': result['ministry']['has_judicial'],
                    'has_discipline': result['ministry']['has_discipline'],
                    'has_politburo': result['ministry']['has_politburo'],
                    'joint_found': result['ministry']['joint_issuance']['found'],
                },
                upstream=upstream,
                texts=[a.get('title', '') + '\n' + a.get('content', '') for a in kept],
            )
            result['upstream'] = {
                'central_docs': upstream['central_docs'].get('items', [])[:8],
                'ministry_docs': upstream['ministry_docs'].get('items', [])[:8],
                'theory': upstream['theory'].get('items', [])[:8],
            }
            print(
                f"  传导链定位：{result['transmission']['stage']}"
                f"（置信度 {result['transmission']['confidence']}）",
                file=sys.stderr,
            )
        except Exception as e:
            print(f'  [跳过] 传导链定位失败: {e}', file=sys.stderr)
            result['transmission'] = {'stage': '未运行', 'error': str(e)}
    else:
        result['transmission'] = {'stage': '未运行', 'note': '--skip-sources'}

    # ── 9.5. 交叉验证（可选）──────────────────────────────
    if not skip_media:
        print('[9.5/12] 多源交叉验证...', file=sys.stderr)
        try:
            result['cross_validation'] = fetch_media_sources(
                keywords=keywords, rmrb_summary=summary,
            )
        except Exception as e:
            print(f'  [跳过] 交叉验证失败: {e}', file=sys.stderr)
            result['cross_validation'] = {'error': str(e)}

    # ── 10. 历史对比 + 存储 + 滚动平滑 ─────────────────────
    print('[10/12] 历史对比 + 存储 + 滚动平滑...', file=sys.stderr)
    result['trend'] = compare_history(result, keywords)
    stored = save_analysis_detailed(result)
    result['storage'] = {'analysis_id': stored['analysis_id'], 'saved': True,
                         'action': stored['action'],
                         'topic_id': stored['topic_id'],
                         'algo_version': stored['algo_version']}
    log_run(topic_id=stored['topic_id'], date=analysis_date,
            as_of=effective_as_of,
            quality_status=(result.get('fetch_quality') or {}).get('status'),
            outcome=stored['action'],
            topic_version=stored['topic_version'])
    result['rolling'] = rolling_verdict(
        keywords, end_date=analysis_date or None, window_days=window_days,
        topic_id=topic['topic_id'],
    )

    # ── 11. 沉默检测 + 趋势 + 提法追踪 ─────────────────────
    print('[11/12] 沉默检测 + 提法追踪...', file=sys.stderr)
    result['silence'] = detect_silence(
        keywords=keywords,
        current_date=analysis_date,
        current_count=len(kept),
        topic_id=topic['topic_id'],
    )
    result['rolling_trend'] = rolling_trend(
        keywords, as_of=effective_as_of, topic_id=topic['topic_id'])
    result['formulation'] = track_formulations(kept, analysis_date)

    # ── 11.5 事件层：抽取 → 证据核验 → 多维信号 → 变化 → 告警 ──
    if not skip_events:
        print('[11.5/12] 事件抽取 + 证据核验 + 多维信号...', file=sys.stderr)
        # topic / effective_as_of 已在步骤 3 之后解析，这里直接复用 ——
        # 同一次运行里解析两遍，就有解析出两个不同身份的可能。
        terms = topic['search_terms']

        scope = build_target_segments(kept, terms,
                                      exclude_terms=topic.get('exclude_terms'))
        raw_events = extract_events(scope, terms, source_layer='report')
        extraction = verify_events(raw_events.get('events', []), kept)
        extraction['counts'] = raw_events.get('counts', {})

        dedupe_res = merge_sources(kept, [])
        signals = build_signals(extraction, agenda=summary.get('step1_agenda'),
                                dedupe=dedupe_res, narrative=result.get('narrative'))

        result['scope'] = {k: v for k, v in scope.items()
                           if k not in ('segments', 'target_articles')}
        result['events'] = extraction.get('events', [])
        result['extraction'] = {k: v for k, v in extraction.items() if k != 'events'}
        result['signals'] = signals
        result['dedupe'] = dedupe_res

        event_snapshot = {
            'topic_id': topic['topic_id'],
            'topic_version': topic.get('topic_version'),
            'topic_label': topic.get('label'),
            'date': analysis_date,
            'as_of': effective_as_of,
            'events': result['events'],
            'signals': signals,
        }
        result['changes'] = detect_changes(event_snapshot, None)
        result['alerts'] = build_alerts(event_snapshot)
        result['event_layer_note'] = (
            '事件层与词面强度层并列呈现，不互相替代。'
            'needs_review 的事件不得直接写进结论。')

    # ── 12. 预测台账 + 议题档案 ────────────────────────────
    print('[12/12] 预测台账 + 议题档案...', file=sys.stderr)
    due = ledger.check_due_predictions(keywords)
    prediction_id = ledger.record_prediction(stored['analysis_id'], result)
    result['prediction'] = {
        'prediction_id': prediction_id,
        'due_review': due,
        'ledger_stats': ledger.ledger_stats(),
    }
    if due:
        print(f'  ⏰ {len(due)} 条预测窗口已到期待复盘', file=sys.stderr)

    result['dossier_context'] = dossier.dossier_context(keywords)
    dossier_file = dossier.update_dossier(result)
    result['dossier_path'] = dossier_file

    # ── 摘要 ─────────────────────────────────────────────
    rolling = result['rolling']
    smoothed = (
        f"平滑={rolling.get('intensity_smoothed')}级({rolling.get('oscillation_label')})"
        if rolling.get('data_points', 0) > 1 else '平滑=无基线'
    )
    result['summary_line'] = (
        f"日期={analysis_date} "
        f"文章={len(kept)}(剔{rel['stats']['dropped_count']}) "
        f"框架={result['narrative']['primary_frame']} "
        f"强度={result['intensity']['weighted_max_level']}级 {smoothed} "
        f"部委={result['ministry']['coordination_level']} "
        f"传导链={result['transmission'].get('stage', '?')} "
        f"时钟={result['clock']['phase']}(×{result['clock']['coefficient']}) "
        f"窗口={result['risk_window']['adjusted_window_label']} "
        f"{result['risk_window']['risk_emoji']}"
    )
    print(f'\n[完成] {result["summary_line"]}', file=sys.stderr)
    return result


def _cmd_ledger(status: str = None):
    rows = ledger.list_predictions(status=status)
    stats = ledger.ledger_stats()
    print(json.dumps(
        {'stats': stats, 'predictions': rows},
        ensure_ascii=False, indent=2,
    ))


def _cmd_resolve(prediction_id: int, outcome: str, note: str):
    out = ledger.resolve_prediction(prediction_id, outcome, note)
    print(json.dumps(out, ensure_ascii=False))
    if not out.get('ok'):
        sys.exit(1)


def _cmd_record_judgment(path: str):
    """从 JSON 文件读入分析师判断并沉淀（judgments 表 + 议题档案）。"""
    if path == '-':
        data = json.load(sys.stdin)
    else:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)

    required = ('keywords', 'date', 'judgment')
    missing = [k for k in required if not data.get(k)]
    if missing:
        print(json.dumps(
            {'ok': False, 'error': f'缺少字段: {missing}，需含 keywords/date/judgment'},
            ensure_ascii=False,
        ))
        sys.exit(1)

    judgment_id = judgments.record_judgment(
        keywords=data['keywords'],
        date=data['date'],
        judgment=data['judgment'],
        triples=data.get('triples'),
        surprising_signal=data.get('surprising_signal', ''),
        tension=data.get('tension', ''),
        analysis_id=data.get('analysis_id'),
    )
    dossier_file = dossier.append_judgment_to_dossier(
        data['keywords'], data['date'], data['judgment'],
    )
    print(json.dumps(
        {'ok': True, 'judgment_id': judgment_id, 'dossier': dossier_file},
        ensure_ascii=False,
    ))


def main():
    parser = argparse.ArgumentParser(
        description='RMRB-Canary — 政策信号分析管道 v3（纯计算，零 LLM 调用）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
在 Claude Code 中使用：
  1. python3 -m agent.agent --keyword 新能源 光伏     # 跑管道拿 JSON
  2. 读 JSON（含 dossier_context 上期判断），完成语义推理和报告
  3. 报告完成后沉淀判断：
     python3 -m agent.agent --record-judgment judgment.json
  4. 有到期预测时裁定：
     python3 -m agent.agent --resolve 12 hit --note "文件落地"
        """,
    )
    parser.add_argument('--keyword', nargs='+', help='关键词列表')
    parser.add_argument('--date', help='指定日期 YYYYMMDD，默认最新一期')
    parser.add_argument('--skip-media', action='store_true', help='跳过多源交叉验证')
    parser.add_argument('--skip-sources', action='store_true', help='跳过上游信号源/传导链')
    parser.add_argument('--window', type=int, default=7, help='滚动平滑窗口天数，默认 7')
    parser.add_argument('--compact', action='store_true', help='精简输出（去掉 full_texts）')
    parser.add_argument('--history', action='store_true', help='查看分析历史')
    parser.add_argument('--ledger', action='store_true', help='查看预测台账与命中率')
    parser.add_argument('--ledger-status', help='台账过滤状态 open/due_review/hit/miss')
    parser.add_argument('--resolve', nargs=2, metavar=('ID', 'OUTCOME'),
                        help='裁定预测：ID hit|miss|void')
    parser.add_argument('--note', default='', help='配合 --resolve 的复盘说明')
    parser.add_argument('--record-judgment', metavar='FILE',
                        help='沉淀分析师判断（JSON 文件路径，- 为 stdin）')

    args = parser.parse_args()

    if args.history:
        records = get_history(limit=20)
        if not records['records']:
            print('暂无历史分析记录。', file=sys.stderr)
        else:
            print(json.dumps(records, ensure_ascii=False, indent=2))
        return
    if args.ledger or args.ledger_status:
        _cmd_ledger(args.ledger_status)
        return
    if args.resolve:
        _cmd_resolve(int(args.resolve[0]), args.resolve[1], args.note)
        return
    if args.record_judgment:
        _cmd_record_judgment(args.record_judgment)
        return

    if not args.keyword:
        parser.print_help()
        return

    result = run_pipeline(
        keywords=args.keyword,
        date=args.date,
        skip_media=args.skip_media,
        skip_sources=args.skip_sources,
        window_days=args.window,
    )

    if args.compact:
        result.pop('full_texts', None)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
