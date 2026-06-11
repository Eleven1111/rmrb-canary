# 🗞️ RMRB-Canary

<p align="center">
  <strong>政策信号早期预警系统</strong><br/>
  比市场早一步读懂官方叙事
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/LLM_calls-zero-orange?style=flat-square" alt="Zero LLM">
  <img src="https://img.shields.io/badge/Claude_Code-ready-purple?style=flat-square" alt="Claude Code">
</p>

---

> **当一个行业被点名，往往已经太晚了。**
>
> RMRB-Canary 在政策信号进入公众视野之前捕捉它——通过对官方媒体叙事的系统性解读，将预警时间从「运动期」前移到「铺垫期」。

---

## 它解决什么问题

大多数企业在看到媒体密集报道时才意识到监管压力——那时政策已进入执行阶段，调整窗口所剩无几。

RMRB-Canary 的目标是**把预警时间前移**：在关键词出现频次还不高、但信号结构已经在变化时，给出可操作的风险判断。

---

## 核心能力

**传导链定位**
人民日报是放大器，不是信号源。系统同时监测上游——国务院文件库、部委发文、理论版面——测量议题在「理论层 → 中央层 → 部委层 → 放大器层」的传导位置，直接定位政策周期六阶段（酝酿/铺垫/动员/运动/收尾/常态化）。

**叙事框架识别**
同一议题在不同官方叙事框架下含义截然不同。RMRB-Canary 自动判断目标议题的叙事归属，这是后续所有信号解读的基准。

**话语强度分级（语境过滤）**
官方表述有温度之分，从「研究探索」到「专项打击」之间存在可量化的梯度。否定语境（"不搞运动式整治"）与历史回顾（"当年扫黑除恶"）自动剔除，不污染信号。

**回测校准的风险窗口**
4/5/6 级信号的预测窗口不是拍脑袋数字，而是 9 个已知结局案例（双减、平台反垄断、虚拟货币清退等）实际提前量的分位区间，并输出频率陈述：「历史同信号组合的 N 例中，M 例在 3 个月内行动」。

**部委协同检测（联合发文）**
「等N部门联合印发」是跨部委协调完成的直接证据。系统显式识别联合发文、2023 年后新机构（金融监管总局、国家数据局）与纪委监委信号。

**滚动平滑判定**
单日报纸是小样本，逐日打分天然震荡。系统以 7 日指数衰减平滑输出判定，并量化震荡指数——告诉你今天的数字可信几分。

**提法生命周期追踪**
新提法首现、升格（正文→标题→头版）、退场（"房住不炒"式淡出）——提法的生灭比关键词频次更接近政策本质。

**沉默信号检测**
议题从高频报道中突然消失，有时是比点名更危险的信号。系统追踪报道缺失并告警。

**预测台账与议题档案**
每次窗口预测自动落账，到期复盘，累计命中率——系统积累自己的信誉记录。每个议题一份持续生长的档案，分析师判断跨期沉淀。

**多源交叉验证**
官方媒体联动 + 社交平台热度，官民叙事张力越大，政策落地阻力越高。

---

## 快速开始

**安装依赖**

```bash
pip install requests beautifulsoup4
```

**克隆仓库**

```bash
git clone https://github.com/Eleven1111/rmrb-canary.git
cd rmrb-canary
```

**运行分析**

```bash
python3 -m agent.agent --keyword 光伏 新能源 储能
```

---

## 使用示例

```bash
# 分析多个关键词
python3 -m agent.agent --keyword 光伏 新能源 储能

# 指定历史日期
python3 -m agent.agent --keyword 教育 培训 --date 20260405

# 跳过多源交叉验证 / 上游信号源（更快）
python3 -m agent.agent --keyword 光伏 --skip-media --skip-sources

# 精简输出（适合管道处理）
python3 -m agent.agent --keyword 光伏 --compact

# 查看历史记录 / 预测台账与命中率
python3 -m agent.agent --history
python3 -m agent.agent --ledger

# 裁定到期预测（积累命中率）
python3 -m agent.agent --resolve 12 hit --note "6月文件落地"

# 查看风险窗口回测校准报告
python3 -m agent.calibration.backtest
```

**每日基线（推荐）：** 沉默检测、滚动平滑、提法生命周期都依赖连续基线。配置观察清单 `~/.rmrb_canary/watchlist.json` 后用 cron 定时运行：

```bash
# 每天 07:30（人民日报电子版发布后）
30 7 * * * cd /path/to/rmrb-canary && python3 scripts/daily_baseline.py >> ~/.rmrb_canary/baseline.log 2>&1
```

JSON 输出到 `stdout`，进度日志输出到 `stderr`：

```bash
python3 -m agent.agent --keyword 光伏 --compact --skip-media 2>/dev/null \
  | jq '.summary_line'
```

---

## 与 Claude Code 协作

RMRB-Canary 只做计算，不做推理。它输出结构化 JSON，由 Claude Code 完成语义解读、报告撰写和战略建议。两者职责清晰，互不越界。

详细分析框架和使用指南请参见 [SKILL.md](./SKILL.md)（需配合 Claude Code 使用）。

---

## 目录结构

```
rmrb-canary/
├── agent/
│   ├── agent.py          # 主管道（12 步）
│   ├── tools/            # 分析模块（框架/强度/部委/传导链/提法/平滑…）
│   ├── sources/          # 上游信号源（gov.cn 政策库、理论层）
│   ├── calibration/      # 历史案例库 + 回测校准
│   └── store/            # 历史库 / 预测台账 / 判断沉淀 / 议题档案
├── scripts/              # 爬虫脚本 + 每日基线入口
├── tests/                # pytest 测试套件
├── SKILL.md              # Claude Code 分析框架
└── README.md
```

历史分析保存至 `~/.rmrb_canary/history.db`（SQLite），议题档案在 `~/.rmrb_canary/dossiers/`，均跨会话持久化。

---

## License

MIT
