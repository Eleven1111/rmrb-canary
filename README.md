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

**不输出风险倒计时**
词面强度、政策时钟和叙事速度只能作为观察特征。未经独立人工标注和影子运行标定，系统不把它们相乘成“多少天内会出台政策”的预测窗口。

**部委协同检测（联合发文）**
「等N部门联合印发」是跨部委协调完成的直接证据。系统显式识别联合发文、2023 年后新机构（金融监管总局、国家数据局）与纪委监委信号。

**滚动平滑判定**
单日报纸是小样本，逐日打分天然震荡。系统以 7 日指数衰减平滑输出判定，并量化震荡指数——告诉你今天的数字可信几分。

**提法生命周期追踪**
新提法首现、升格（正文→标题→头版）、退场（"房住不炒"式淡出）——提法的生灭比关键词频次更接近政策本质。

**沉默信号检测**
议题从高频报道中突然消失，有时是比点名更危险的信号。系统追踪报道缺失并告警。

**议题档案与人工判断台账**
每个议题一份持续生长的档案，分析师判断可跨期沉淀；任何准确率或提前量结论都须来自独立标注与影子运行，不能由规则自身输出推导。

**多源交叉验证**
官方媒体联动 + 社交平台热度，官民叙事张力越大，政策落地阻力越高。

---

## 事件层（v3.1 新增）

在原有 12 步管道之外**并列**加了一层：把判断单位从"整篇文章"降到"分句"，
每条判断都带可定位的原文证据。两层不互相替代 —— 词面强度回答"措辞多重"，
事件层回答"哪个主体对哪个对象做了什么、证据在哪"。

| 能力 | 说明 |
|---|---|
| 事件抽取 | 主体 / 动作 / 对象 / 政策工具 / 方向 / 程序状态 / 约束强度 / 适用范围 |
| 证据核验 | 分句级主张必须出现在自己的引文里；文号、发文机关、日期必须与解析到的文件一致，**不得补全** |
| 多维信号 | 政策方向 / 工具 / 程序 / 约束 / 范围 / 执行证据 / 传播，七维并列，**不合成总分** |
| 来源归并 | 转载算传播强度，不算独立证据；同体系来源不构成相互印证 |
| 变化对比 | 措辞 / 范围 / 责任 / 资源 / 程序 / 执行 / 方向分化 七问，每条附替代解释 |
| 事件告警 | 去重键 = 主题 + 事件指纹；状态机含"待投递"，**收到回执才算已通知** |
| 历史回放 | 每期 `as_of` 设为当天，带泄漏自检；读到未来记录即判整次回放作废 |
| 共享缓存 | 多主题分析同一期只抓一次；内容变化新增版本，修订可检出 |

否定与历史回顾的判定复用既有的 `agent/tools/context_filter.py`（按命中位置回看、
否定要求紧邻），两边词表取并集。

```bash
python3 -m agent.agent --keyword 人工智能 --topic ai      # 带主题口径
python3 -m agent.replay --topic ai --from 20260901 --to 20260908
```

**未标定声明：** 事件抽取的准确率、召回率、预警提前量**均为 UNKNOWN**，
需要人工标注（`agent/tools/labeling.py` 可生成待标注清单）与足量影子运行才能得出。
不得用规则自身输出充当标准答案。

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

# 查看历史记录 / 人工判断台账
python3 -m agent.agent --history
python3 -m agent.agent --ledger

# 裁定人工判断
python3 -m agent.agent --resolve 12 hit --note "6月文件落地"
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
│   └── store/            # 历史库 / 判断沉淀 / 议题档案
├── scripts/              # 爬虫脚本 + 每日基线入口
├── tests/                # pytest 测试套件
├── SKILL.md              # Claude Code 分析框架
└── README.md
```

历史分析保存至 `~/.rmrb_canary/history.db`（SQLite），议题档案在 `~/.rmrb_canary/dossiers/`，均跨会话持久化。

---

## 局限

- 事件抽取是规则召回 + 预结构化，不是语义解析。跨句指代、复杂否定范围、
  隐含主体仍会出错，`needs_review` 的事件必须人工或模型消解后才能进结论。
- 正式文件来源覆盖有限。`agent/sources/gov_policy.py` 走政策文件库检索接口，
  `agent/sources/gov_cn_pages.py` 解析 gov.cn 页面（只有首页最近若干件）；
  2026-09-08 实测检索接口两种 scope 参数当日均返回 0 条。
  主管部门站点（网信办 521、卫健委与药监局 412）取不到。
  因此"未检索到相关文件"只能表述为"**已接入来源中没有**"。
- 企业画像 `config/enterprises/` 只提供模板。真实画像含客户业务信息，
  应留在本地，不要提交到公开仓库。

## License

MIT
