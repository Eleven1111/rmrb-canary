# 🗞️ RMRB Sentinel

<p align="center">
  <strong>人民日报政策信号分析框架</strong><br/>
  纯计算管道 · 零 LLM 调用 · 结构化 JSON 输出
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/LLM_calls-zero-orange?style=flat-square" alt="Zero LLM">
  <img src="https://img.shields.io/badge/Claude_Code-ready-purple?style=flat-square" alt="Claude Code">
</p>

---

> **读人民日报，不是读新闻，而是读信号。**
>
> 政策行动窗口往往在媒体噪音中被淹没。RMRB Sentinel 自动从人民日报版面权重、叙事框架、话语强度和部委协同度中提取结构化政策信号——把「情报」交给你，把「推理」交给 Claude。

---

## 它能告诉你什么

| 问题 | RMRB Sentinel 的回答 |
|------|---------------------|
| 这个行业被监管的概率有多高？ | 话语强度等级 + 部委协同度 + 风险窗口预测 |
| 现在是进入某赛道的好时机吗？ | 叙事框架 × 政策时钟阶段 |
| 信号是在加强还是减弱？ | 7 / 30 / 90 日滚动趋势 |
| 官方"不说"意味着什么？ | 沉默检测，识别刻意回避信号 |

---

## 核心能力

**叙事框架识别**
识别六大官方叙事框架（国家安全、共同富裕、高质量发展、自立自强、防范金融风险、社会治理），同一关键词在不同框架下的政策含义截然不同。

**话语强度七级定级**
从「研究探索（1级）」到「专项打击（7级）」精确定级，检测跳级突变（如从 2 级直跳 5 级），压缩行动预警时间。

**部委协同度检测**
L0–L5 五档分级，多部委联合出现自动压缩行动时间窗口。单部委关注是信号，三部委以上同步发声是倒计时。

**政策时钟校正**
六大年度节律阶段（两会定调期、整治高发期等）对基础窗口的系数调整，让同一信号在 3 月和 9 月产生完全不同的预警等级。

**共现语境分析**
段落级正/负情感锚词共现，区分「新能源 + 引领」与「新能源 + 整改」，避免孤立关键词计数的误判。

**沉默检测**
议题突然从高优先度报道中消失时告警——官方的沉默有时比报道更危险。

**多源交叉验证**
人民网 RSS + 微博热搜 + 百度热搜，官民张力越大，政策落地阻力越高。

---

## 快速开始

**安装依赖**

```bash
pip install requests beautifulsoup4
```

**克隆仓库**

```bash
git clone https://github.com/meta11/rmrb-sentinel.git
cd rmrb-sentinel
```

**运行分析**

```bash
python3 -m agent.agent --keyword 光伏 新能源 储能
```

---

## 使用示例

```bash
# 分析多个关键词（同一议题的多种表达）
python3 -m agent.agent --keyword 光伏 新能源 储能

# 指定历史日期
python3 -m agent.agent --keyword 教育 培训 --date 20260405

# 跳过多源交叉验证（更快，适合批量运行）
python3 -m agent.agent --keyword 光伏 --skip-media

# 精简输出（去掉原文全文，减少 token 消耗）
python3 -m agent.agent --keyword 光伏 --compact

# 查看历史分析记录
python3 -m agent.agent --history
```

JSON 输出到 `stdout`，进度日志输出到 `stderr`，可直接管道处理：

```bash
python3 -m agent.agent --keyword 光伏 --compact --skip-media 2>/dev/null \
  | jq '.summary_line'
```

---

## 输出结构

```json
{
  "narrative":        "叙事框架 + top 贡献文章",
  "intensity":        "话语强度七级 + 加权定级 + 高权重告警",
  "ministry":         "部委协同度 + 各部委加权得分",
  "cooccurrence":     "正/负情感比例 + 冲突信号检测",
  "silence":          "沉默/降温/升温信号",
  "rolling_trend":    "7/30/90 日滚动趋势",
  "clock":            "政策时钟阶段 + 系数",
  "risk_window":      "综合风险窗口预测",
  "cross_validation": "官媒联动 + 热搜 + 官民张力",
  "trend":            "与上期对比",
  "full_texts":       "原文全文（供 LLM 语义分析）",
  "summary_line":     "单行摘要"
}
```

---

## 与 Claude Code 协作

RMRB Sentinel 的设计哲学是**计算与推理分离**：

```
RMRB Sentinel（纯计算）                 Claude Code（推理）
─────────────────────────────────     ──────────────────────────
版面权重 / 关键词匹配 / 部委识别  →     语义三元组提取
话语强度定级 / 风险窗口计算       →     报告撰写
历史趋势对比 / 沉默检测           →     战略建议
```

在 Claude Code 中，通过 `SKILL.md`（即 `rmrb-sentinel` skill）加载完整分析框架，管道 JSON 作为输入。

---

## 目录结构

```
rmrb-sentinel/
├── agent/
│   ├── agent.py                  # 主管道（9 步，零 LLM 调用）
│   ├── tools/
│   │   ├── weighting.py          # 位置加权（版面 × 栏目 × 标题）
│   │   ├── narrative_frame.py    # 叙事框架分类
│   │   ├── discourse_level.py    # 话语强度七级
│   │   ├── ministry_signals.py   # 部委协同度
│   │   ├── cooccurrence.py       # 共现语境分析
│   │   ├── silence_detector.py   # 沉默检测 + 滚动趋势
│   │   ├── policy_clock.py       # 政策时钟
│   │   ├── history_compare.py    # 历史对比
│   │   ├── fetch_rmrb.py         # 人民日报采集
│   │   └── fetch_media.py        # 多源媒体采集
│   └── store/
│       └── db.py                 # SQLite 历史存储
├── scripts/
│   ├── rmrb_fetch.py             # 人民日报爬虫（独立运行）
│   └── media_fetch.py            # 多源媒体爬虫（独立运行）
├── SKILL.md                      # Claude Code / OpenClaw 分析框架
└── README.md
```

历史分析自动保存至 `~/.rmrb_sentinel/history.db`（SQLite），跨会话持久化，用于沉默检测和趋势对比。

---

## 重要局限

- **信号效度未经验证**：打分规则是启发式的，系数未经历史案例回溯标定。建议用已知结果案例（教育双减、互联网反垄断）自我验证后再用于决策。
- **基准率缺失**：相对风险判断（本期 vs 上期）优于绝对风险判断。
- **第四层数据缺失**：评论区情绪分析依赖人工观察，无自动采集。
- **地区差异被平均化**：全国性报道掩盖地方执行差异，地方决策需补充本地媒体分析。

---

## License

MIT
