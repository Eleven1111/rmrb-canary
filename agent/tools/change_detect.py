"""
Tool: 事件级变化对比（P1，对应方案 §6.3）

方案要求把最重要的洞察放在七个"变了吗"的问题上，而不是更复杂的总分。
本模块逐条回答，每条给出：旧证据 / 新证据 / 适用范围 / 替代解释。

三条硬规则：
  1. 没有基线 → 只能写"本系统首次观察"，**不得**写"官方首次提出"。
  2. 口径不可比（主题版本、算法版本、采集质量任一不同）→ 该条不给结论。
  3. 每条变化都必须带替代解释 —— 版面轮换、采集覆盖差异、同义改写、
     偶发单篇报道都可能造成"变化"的表象。
"""

# 每条变化都必须回答的替代解释。这不是免责声明，是必须逐条排除的竞争假设。
STANDARD_ALTERNATIVES = [
    '本期采集覆盖与上期不同，差异可能来自抓取而非议题',
    '同义改写：措辞变了但政策含义未变',
    '偶发单篇报道造成的波动，未形成连续证据',
    '版面与栏目轮换带来的呈现差异',
]

QUESTIONS = (
    ('措辞变了吗', '同一政策对象的方向词与义务表述是否变化'),
    ('范围变了吗', '试点扩围、对象新增、门槛变化、豁免取消'),
    ('责任变了吗', '是否首次出现牵头部门、配合单位、检查责任、问责条款'),
    ('资源变了吗', '是否出现预算、额度、采购、人员或具体项目安排'),
    ('程序推进了吗', '征求意见是否转正式文件，正式文件是否出现地方实施细则'),
    ('执行兑现了吗', '责任主体、细则、资源安排、执行记录是否补齐'),
    ('方向分化了吗', '是否支持行业总体发展，同时限制其中某种商业模式'),
)


def _dim(signals, name, key, default=None):
    d = ((signals or {}).get('dimensions') or {}).get(name) or {}
    return d.get(key, default)


def _delta(old_counts: dict, new_counts: dict) -> dict:
    old_counts = old_counts or {}
    new_counts = new_counts or {}
    added = sorted(k for k in new_counts if k not in old_counts)
    removed = sorted(k for k in old_counts if k not in new_counts)
    return {'added': added, 'removed': removed,
            'old': dict(old_counts), 'new': dict(new_counts),
            'changed': bool(added or removed)}


def _record(question, focus, changed, detail, old_evidence=None, new_evidence=None,
            status='ok'):
    return {
        'question': question,
        'focus': focus,
        'status': status,
        'changed': changed,
        'detail': detail,
        'old_evidence': old_evidence or [],
        'new_evidence': new_evidence or [],
        'alternative_explanations': list(STANDARD_ALTERNATIVES),
    }


def detect_changes(current: dict, previous: dict = None,
                   comparable: bool = True) -> dict:
    """
    比较当期与上期的事件级信号。

    参数：
      current / previous: AnalysisSnapshot（previous 可为 None）
      comparable: 由存储层给出的可比性判断（同主题版本、同算法版本、同采集质量）
    """
    cur_sig = (current or {}).get('signals') or {}

    if cur_sig.get('status') != 'ok':
        return {
            'status': 'unknown',
            'reason': cur_sig.get('reason', '本期无合格事件'),
            'changes': [],
            'note': '本期没有可用于比较的事件，不输出变化结论。',
        }

    if not previous:
        return {
            'status': 'first_observation',
            'changes': [_record(q, f, None,
                                '无历史基线：本系统首次观察到该主题的这一维度。'
                                '不得表述为"官方首次提出"。',
                                status='no_baseline')
                        for q, f in QUESTIONS],
            'note': '同主题、同口径下无更早记录。首次观察只说明本系统的观察起点。',
        }

    if not comparable:
        return {
            'status': 'not_comparable',
            'previous_date': previous.get('date'),
            'changes': [_record(q, f, None,
                                '两期口径不可比（主题版本 / 算法版本 / 采集质量不一致），'
                                '差异可能来自口径而非议题。',
                                status='blocked')
                        for q, f in QUESTIONS],
            'note': '只有同 topic_id、同 topic_version、同算法版本、同采集质量的两期可比。',
        }

    prev_sig = (previous or {}).get('signals') or {}
    if prev_sig.get('status') != 'ok':
        return {
            'status': 'baseline_unusable',
            'previous_date': previous.get('date'),
            'changes': [_record(q, f, None, '上期没有合格事件，无法构成基线。',
                                status='no_baseline')
                        for q, f in QUESTIONS],
            'note': '上期 signals 状态非 ok，基线不可用。',
        }

    changes = []

    # 1. 措辞
    old_dir = {k: v['count'] for k, v in (_dim(prev_sig, '政策方向', 'by_direction') or {}).items()}
    new_dir = {k: v['count'] for k, v in (_dim(cur_sig, '政策方向', 'by_direction') or {}).items()}
    d = _delta(old_dir, new_dir)
    old_ob = _dim(prev_sig, '约束与动作', 'counts')
    new_ob = _dim(cur_sig, '约束与动作', 'counts')
    d_ob = _delta(old_ob, new_ob)
    changes.append(_record(
        '措辞变了吗', QUESTIONS[0][1], d['changed'] or d_ob['changed'],
        f"方向构成 {d['old']} → {d['new']}；约束层级 {d_ob['old']} → {d_ob['new']}",
        new_evidence=[e for v in (_dim(cur_sig, '政策方向', 'by_direction') or {}).values()
                      for e in v.get('evidence', [])][:3],
    ))

    # 2. 范围
    d = _delta(_dim(prev_sig, '适用范围', 'counts'), _dim(cur_sig, '适用范围', 'counts'))
    old_regions = set(_dim(prev_sig, '适用范围', 'regions') or [])
    new_regions = set(_dim(cur_sig, '适用范围', 'regions') or [])
    region_added = sorted(new_regions - old_regions)
    changes.append(_record(
        '范围变了吗', QUESTIONS[1][1],
        d['changed'] or bool(region_added),
        f"范围标签 {d['old']} → {d['new']}；新增地域 {region_added or '无'}",
        new_evidence=_dim(cur_sig, '适用范围', 'exceptions') or [],
    ))

    # 3. 责任
    old_resp = (_dim(prev_sig, '约束与动作', 'counts') or {}).get('责任', 0)
    new_resp = (_dim(cur_sig, '约束与动作', 'counts') or {}).get('责任', 0)
    first_time = old_resp == 0 and new_resp > 0
    changes.append(_record(
        '责任变了吗', QUESTIONS[2][1], old_resp != new_resp,
        f"责任类表述 {old_resp} → {new_resp}"
        + ('（本系统首次观察到责任条款）' if first_time else ''),
        new_evidence=((_dim(cur_sig, '执行证据', 'components') or {})
                      .get('责任主体', {}).get('evidence', [])),
    ))

    # 4. 资源
    resource_tools = ('财政', '税收', '信贷', '采购')
    old_res = {t: (_dim(prev_sig, '政策工具', 'counts') or {}).get(t, 0) for t in resource_tools}
    new_res = {t: (_dim(cur_sig, '政策工具', 'counts') or {}).get(t, 0) for t in resource_tools}
    changes.append(_record(
        '资源变了吗', QUESTIONS[3][1], old_res != new_res,
        f"资源类工具 {old_res} → {new_res}",
        new_evidence=((_dim(cur_sig, '执行证据', 'components') or {})
                      .get('资源安排', {}).get('evidence', [])),
    ))

    # 5. 程序
    d = _delta(_dim(prev_sig, '程序状态', 'counts'), _dim(cur_sig, '程序状态', 'counts'))
    changes.append(_record(
        '程序推进了吗', QUESTIONS[4][1], d['changed'],
        f"程序状态 {d['old']} → {d['new']}；新增 {d['added'] or '无'}。"
        '程序允许跳转与并行，新增某状态不等于线性推进。',
    ))

    # 6. 执行
    old_exec = {k: v.get('present') for k, v in
                (_dim(prev_sig, '执行证据', 'components') or {}).items()}
    new_exec = {k: v.get('present') for k, v in
                (_dim(cur_sig, '执行证据', 'components') or {}).items()}
    newly = sorted(k for k, v in new_exec.items() if v and not old_exec.get(k))
    lost = sorted(k for k, v in old_exec.items() if v and not new_exec.get(k))
    changes.append(_record(
        '执行兑现了吗', QUESTIONS[5][1], bool(newly or lost),
        f"执行构件 {_dim(prev_sig, '执行证据', 'components_present')} → "
        f"{_dim(cur_sig, '执行证据', 'components_present')}；"
        f"新补齐 {newly or '无'}，本期缺失 {lost or '无'}。"
        '构件缺失可能只是本期未报道，不等于执行倒退。',
    ))

    # 7. 方向分化
    old_co = bool(_dim(prev_sig, '政策方向', 'coexists'))
    new_co = bool(_dim(cur_sig, '政策方向', 'coexists'))
    changes.append(_record(
        '方向分化了吗', QUESTIONS[6][1], old_co != new_co,
        f"支持与限制/规范并存：{old_co} → {new_co}。"
        '并存说明同一议题内不同对象受到不同对待，必须分对象写，不能合成一个方向。',
        new_evidence=[e for v in (_dim(cur_sig, '政策方向', 'by_direction') or {}).values()
                      for e in v.get('evidence', [])][:3],
    ))

    return {
        'status': 'ok',
        'previous_date': previous.get('date'),
        'changed_count': sum(1 for c in changes if c['changed']),
        'changes': changes,
        'note': '每条变化都附替代解释，必须逐条排除后才能写成结论。'
                '没有对应旧版本的项只能写"本系统首次观察"。',
    }
