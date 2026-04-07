# RMRB Sentinel

**人民日报政策信号分析框架** — 纯计算管道，零 LLM 调用，输出结构化 JSON 供 Claude Code / OpenClaw 完成推理与报告撰写。

---

## 功能概览

- **叙事框架识别**：六大官方叙事框架（国家安全、共同富裕、高质量发展等），基于位置加权关键词匹配
- **话语强度七级定级**：从「研究探索」到「专项打击」，头版社论权重远高于后版普通文章
- **部委协同度检测**：L0–L5 五档，多部委联合出现自动压缩行动时间窗口
- **共现语境分析**：段落级正/负情感锚词共现，区分「新能源+引领」与「新能源+整改」
- **沉默检测**：议题突然从报道中消失时告警，识别官方刻意回避信号
- **多期滚动趋势**：7 / 30 / 90 日强度趋势线 + 叙事框架变化次数
- **多源交叉验证**：人民网 RSS 官媒聚合 + 微博 / 百度热搜

## 安装

**依赖**

```bash
pip install requests beautifulsoup4
```

**克隆**

```bash
git clone https://github.com/your-org/rmrb-sentinel.git
cd rmrb-sentinel
```

## 使用

```bash
# 基础分析
python3 -m agent.agent --keyword 光伏 新能源 储能

# 指定日期
python3 -m agent.agent --keyword 教育 培训 --date 20260405

# 跳过交叉验证（更快）
python3 -m agent.agent --keyword 光伏 --skip-media

# 精简输出（去掉原文全文）
python3 -m agent.agent --keyword 光伏 --compact

# 查看历史分析
python3 -m agent.agent --history
```

JSON 输出到 stdout，进度日志输出到 stderr，可直接管道处理：

```bash
python3 -m agent.agent --keyword 光伏 --compact --skip-media 2>/dev/null | jq '.summary_line'
```

## 输出结构

```
{
  "narrative":      叙事框架 + top 贡献文章,
  "intensity":      话语强度七级 + 加权定级 + 高权重告警,
  "ministry":       部委协同度 + 各部委加权得分,
  "cooccurrence":   正/负情感比例 + 冲突信号检测,
  "silence":        沉默/降温/升温信号,
  "rolling_trend":  7/30/90 日滚动趋势,
  "clock":          政策时钟阶段 + 系数,
  "risk_window":    综合风险窗口预测,
  "cross_validation": 官媒联动 + 热搜 + 官民张力,
  "trend":          与上期对比,
  "full_texts":     原文全文（供 LLM 语义分析）,
  "summary_line":   单行摘要
}
```

## 数据存储

分析历史自动保存至 `~/.rmrb_sentinel/history.db`（SQLite），跨会话持久化，用于沉默检测和趋势对比。

## 目录结构

```
rmrb-sentinel/
├── agent/
│   ├── agent.py               # 主管道（9 步，零 LLM 调用）
│   ├── tools/
│   │   ├── weighting.py       # 位置加权（版面 × 栏目 × 标题）
│   │   ├── narrative_frame.py # 叙事框架分类
│   │   ├── discourse_level.py # 话语强度七级
│   │   ├── ministry_signals.py# 部委协同度
│   │   ├── cooccurrence.py    # 共现语境分析
│   │   ├── silence_detector.py# 沉默检测 + 滚动趋势
│   │   ├── policy_clock.py    # 政策时钟
│   │   ├── history_compare.py # 历史对比
│   │   ├── fetch_rmrb.py      # 人民日报采集
│   │   └── fetch_media.py     # 多源媒体采集
│   └── store/
│       └── db.py              # SQLite 历史存储
├── scripts/
│   ├── rmrb_fetch.py          # 人民日报爬虫（独立运行）
│   └── media_fetch.py         # 多源媒体爬虫（独立运行）
├── SKILL.md                   # Claude Code / OpenClaw 分析框架
└── README.md
```

## 设计原则

**纯计算管道，零 LLM 调用。** 所有信号检测（版面权重、关键词匹配、部委识别、风险窗口计算）均为确定性代码，输出结构化 JSON。Claude Code / OpenClaw 接收 JSON 后负责语义推理、报告撰写和战略建议——各司其职，计算与推理分离。

## License

MIT
