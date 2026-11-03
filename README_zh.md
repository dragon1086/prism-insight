<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="240">
  &nbsp;&nbsp;
  <a href="assets/characters/priso/README.md">
    <img src="assets/characters/priso/v1.0/priso_master_transparent.png" alt="PRISM 吉祥物 Priso" width="240">
  </a>
  <br>
  <sub><strong>Priso</strong> · PRISM 官方吉祥物</sub>
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--6-green.svg" alt="OpenAI GPT-6">
  <img src="https://img.shields.io/badge/Anthropic-Claude_Sonnet_5.5_(optional)-green.svg" alt="Anthropic Claude Sonnet 5.5（可选）">
  <img src="https://img.shields.io/badge/ChatGPT_Plus-Codex_OAuth-ff6b35.svg" alt="ChatGPT Plus">
</div>

[![CI](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml/badge.svg)](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/2f8fd766b0634c068ff9da57ccda00c6)](https://app.codacy.com/gh/dragon1086/prism-insight/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **AI 驱动的股票市场分析与交易系统**
>
> 13 个以上的专业 AI 代理协同工作，发现异动股、生成分析师级报告，并自动执行交易。

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

### 白金赞助商

<div align="center">
<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

打造职场人士 AI 助手 **WrksAI** 的 **AI3**，<br>
荣幸赞助投资者的 AI 助手 **PRISM-INSIGHT**。
</div>

---

## NEW：Stance — 现在哪种系统交易策略表现最好？

<p align="center">
  <img src="docs/images/stance-ecosystem-en.png" alt="按收益、最大回撤、平均投入比例和记录率比较韩国与美国系统交易策略的 Stance 排行榜" width="100%">
</p>

**过去的业绩？我们不收。** 每条 Stance 记录都从注册那一刻开始，不接受上传历史业绩，也不允许补录。之后的决策与结果会连成一条连续的公开记录，展示的是策略**真实的能力与风险**，而不是精挑细选的高光片段。排行榜分为韩国和美国两部分，在收益旁边同时显示最大回撤、平均投入比例和记录率。

- **找到当下有效的策略** — 用同一套规则比较所有策略
- **不止看收益数字** — 同时查看风险、实际投入比例和缺失的记录
- **可信的时间线** — 服务器锁定决策时间和价格，再自动计算后续结果
- **让你的策略参赛** — 由编程代理完成发现、注册、接入和测试

**[查看实时排行榜](https://analysis.stocksimulation.kr/?tab=stance)** · **[提交我的策略](https://analysis.stocksimulation.kr/?tab=stance)** · **[快速入门](stance/QUICKSTART.md)**

<details>
<summary><strong>我的策略如何加入？</strong></summary>

<p align="center">
  <img src="docs/images/stance-integration-en.png" alt="打开策略项目，把一段指令粘贴到编程代理中，确认检测到的策略和简介并批准后，自动完成注册与接入的流程" width="100%">
</p>

用 **Codex CLI、Cursor、Claude Code 等编程代理** 打开你的策略项目，然后把从 Stance 仪表盘复制的指令粘贴到对话中即可。代理会找出独立的策略以及韩国、美国投资组合，只询问公开资料中缺少的信息，先展示注册计划，在你批准后才会保存密钥、修改代码并运行测试。

从第一笔决策开始，你的策略就会出现在 **“记录积累中”**。股票市场的正式排名需要 **记录满 63 个交易日，并完成 20 笔每笔至少动用 1% 资产的交易** 后才开始。记录从接入当天开始，无法补录历史业绩。不需要真实券商账户、余额或券商密钥。
</details>

---

## NEW：支持 ChatGPT Plus/Pro 订阅

**没有 API 密钥？没关系。** PRISM-INSIGHT 现已支持通过 **Codex OAuth 代理**，直接使用你的 ChatGPT Plus（$20/月）或 Pro（$200/月）订阅运行分析。

```bash
# 首次登录（浏览器会自动打开进行 ChatGPT 认证）
python -m cores.chatgpt_proxy.oauth_login

# 需要重新认证时（切换账号、令牌过期等）
python -m cores.chatgpt_proxy.oauth_login --force

# 使用 ChatGPT 订阅运行
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning
```

> 令牌会在后台自动刷新，只有在更换 ChatGPT 账号或修改密码时才需要重新登录。

零 API 账单，同样强大的分析，你现有的订阅就能搞定。

---

## 移动应用

<div align="center">

**随时随地获取 AI 股票分析**

<a href="https://play.google.com/store/apps/details?id=com.prisminsight.prism_mobile">
  <img src="https://img.shields.io/badge/Google_Play-下载-green?style=for-the-badge&logo=google-play" alt="Google Play">
</a>
<a href="https://apps.apple.com/us/app/prism-insight-stock-analysis/id6759331074">
  <img src="https://img.shields.io/badge/App_Store-下载-blue?style=for-the-badge&logo=apple" alt="App Store">
</a>

</div>

- **智能筛选** — 只接收你关心的 Telegram 提醒
- **PDF 报告** — 针对移动端优化的 AI 分析报告

---

## 观看 PRISM-INSIGHT 演示

[![PRISM-INSIGHT Demo](https://img.youtube.com/vi/zAywb1G0wRA/maxresdefault.jpg)](https://www.youtube.com/watch?v=zAywb1G0wRA)

---

## 立即体验（无需安装）

### 1. 实时仪表盘
实时查看 AI 交易表现：
**[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. Telegram 频道
每天接收异动股提醒和 AI 分析报告：
- **[英文频道](https://t.me/prism_insight_global_en)**
- **[韩文频道](https://t.me/stock_ai_agent)**
- **[日文频道](https://t.me/prism_insight_ja)**
- **[中文频道](https://t.me/prism_insight_zh)**
- **[西班牙文频道](https://t.me/prism_insight_es)**

### 3. 示例报告
观看 AI 生成的 Apple Inc. 分析报告：

[![示例报告 - Apple Inc. 分析](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## 60 秒快速体验（美股）

体验 PRISM-INSIGHT 最快的方式，只需要一个 **OpenAI API 密钥**。

```bash
# 克隆并运行快速启动脚本
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

这会生成 Apple（AAPL）的 AI 分析报告。也可以试试其他股票：
```bash
python3 demo.py MSFT              # Microsoft
python3 demo.py NVDA              # NVIDIA
python3 demo.py TSLA --language ko  # Tesla（韩文报告）
```

> **获取 OpenAI API 密钥**：[OpenAI Platform](https://platform.openai.com/api-keys)
>
> **可选**：在 `mcp_agent.config.yaml` 中添加 [Perplexity API 密钥](https://www.perplexity.ai/) 以启用新闻分析
>
> **可选**：添加 `ADANOS_API_KEY`，可为美股新闻分析补充结构化的社交情绪信息

AI 生成的 PDF 报告保存在 `prism-us/pdf_reports/`。

<details>
<summary>或使用 Docker（无需配置 Python）</summary>

```bash
# 1. 设置 OpenAI API 密钥
export OPENAI_API_KEY=sk-your-key-here

# 2. 构建并启动本地快速体验镜像
docker compose -f docker-compose.quickstart.yml up --build -d

# 3. 运行分析
docker exec -it prism-quickstart python3 demo.py NVDA
```

首次运行需要在本地构建镜像，可能需要几分钟。

报告保存在 `./quickstart-output/`。

</details>

---

## 完整安装

### 前置条件
- Python 3.10+ 或 Docker
- OpenAI API 密钥（[在此获取](https://platform.openai.com/api-keys)）或 ChatGPT Plus/Pro 订阅

### 方案 A：Python 安装

```bash
# 1. 克隆并安装
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. 安装 Playwright（用于生成 PDF）
python3 -m playwright install chromium

# 3. MCP 服务器（Firecrawl、Perplexity 等）按 mcp_agent.config.yaml 的配置
#    由 npx/uv 按需启动 — 无需单独安装

# 4. 配置
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp trading/config/kis_devlp.yaml.example trading/config/kis_devlp.yaml
# 在 mcp_agent.secrets.yaml 中填写 OpenAI API 密钥
# 在 trading/config/kis_devlp.yaml 中填写韩国投资证券（KIS）API 密钥（用于韩国市场数据）

# 5. 运行分析（无需配置 Telegram！）
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

美股分析的运行方式如下：

```bash
# 运行美股分析
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# 生成英文报告
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

### 方案 B：Docker（生产环境推荐）

```bash
# 准备好上面第 4 步的配置文件后：
docker compose up -d
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

**完整安装指南**：[docs/SETUP.md](docs/SETUP.md)

---

## 什么是 PRISM-INSIGHT？

PRISM-INSIGHT 是一个面向 **韩国（KOSPI/KOSDAQ）** 和 **美国（NYSE/NASDAQ）** 市场的 **完全开源、免费** 的 AI 股票分析系统。

### 核心能力
- **异动股发现** — 自动发现成交量或价格异常波动的股票
- **AI 分析报告** — 由专业 AI 代理生成的分析师级报告
- **交易模拟** — 结合组合管理的 AI 买卖决策
- **自动交易** — 通过韩国投资证券 API 实盘执行
- **Telegram 集成** — 实时提醒与多语言推送
- **宏观情报** — 市场状态判断、板块轮动分析、风险事件监控
- **自我改进** — 交易日志反馈循环 — 过去各触发器的胜率会自动影响之后的买入决策（[详情](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)）

### AI 模型
以下是实际运行中使用的模型，均可在 `.env` 中修改（参见 [.env.example](.env.example)）。

| 角色 | 默认模型 |
|------|---------|
| 报告各部分、投资策略、摘要、宏观分析 | OpenAI **GPT-6 Luna**（`REPORT_MODEL`） |
| 买卖决策 | OpenAI **GPT-6.1 Sol**（`PRISM_BUY_CODEX_MODEL`、`PRISM_SELL_CODEX_MODEL`） |
| Telegram 问答 | OpenAI **GPT-6.1 Sol**（`TELEGRAM_ANALYSIS_MODEL`） |
| 翻译（英、日、中、西）、交易日志 | OpenAI **GPT-6 Luna** |
| 可选：按需洞察代理 | Anthropic **Claude Sonnet 5.5**（`INSIGHT_MODEL`） |

所有功能都可以通过 OpenAI API 密钥或 ChatGPT Plus/Pro 订阅（Codex OAuth）运行。

---

## AI 代理系统

按执行路径而非固定数量对代理进行分组：

| 团队 | 代理 | 作用 |
|------|------|------|
| **宏观** | KR / US | 在基于规则的市场状态之上，补充领涨板块、风险和事件研究 |
| **个股分析** | 每个市场 6 个基础部分 | 技术面、资金流向、公司、行业、新闻、市场 |
| **策略与摘要** | 运行时动态生成 | 将基础部分整合为投资策略和核心摘要 |
| **交易** | KR / US 买入与卖出 | 将 AI 情景与评分、组合和再入场关卡结合 |
| **日志与记忆** | 复盘、压缩、原则 | 把已平仓结果作为下一次决策的依据 |
| **沟通与咨询** | 评估、优化、翻译、追问 | Telegram 摘要与用户对话 |

<details>
<summary>查看代理工作流程图</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="代理工作流程" width="700">
</details>

**详细文档**：[流水线架构（韩文）](docs/PIPELINE_ARCHITECTURE_ko.md) | [AI 代理系统](docs/CLAUDE_AGENTS.md)

---

## 交易业绩 — 第二赛季

![PRISM-INSIGHT 第二赛季：10 槽位账户已实现收益与 KOSPI/KOSDAQ、S&P 500/纳斯达克对比](docs/images/season2-performance-en.png)

我们用两种方式展示同一批已平仓交易：

- **单笔收益率之和** — 把每笔已平仓交易的收益率直接相加。不计复利，也不按仓位加权。
- **10 槽位账户收益** — 把模拟账户平均分成 10 个槽位后的已实现收益（不足一个槽位的买入按其占比计算）。只计已平仓交易，不计复利。

| | 韩国（第二赛季） | 美国 |
|---|---|---|
| 期间 | 2025-09-30 ~ 2026-10-02 | 2026-01-28 ~ 2026-10-02 |
| 已平仓交易 | 211 笔 | 127 笔 |
| 胜率 | 40.3%（85 胜） | 33.1%（42 胜） |
| 单笔平均收益率 | +1.68% | +0.65% |
| 单笔收益率之和 | +355.3% | +82.8% |
| **10 槽位账户收益** | **+35.2%** | **+8.3%** |
| 账户曲线最大回落 | −9.3 个百分点 | −13.7 个百分点 |
| 同期指数 | KOSPI +103.5%（3,431 → 6,982）<br>KOSDAQ +5.3%（847 → 892） | S&P 500 +10.8%（6,969 → 7,723）<br>纳斯达克 +14.8%（23,685 → 27,191） |
| 收益最高的已平仓交易 | 三星电机 +86.8%<br>SK 海力士 +73.8%<br>SK Square +57.6% | 美光 +105.7%、美光 +52.8%<br>IBM +27.0% |

**这一期间账户收益落后于 KOSPI，我们希望坦诚说明这一点。** KOSPI 几乎翻倍，而 KOSDAQ 只上涨约 5%，这是一轮由大盘半导体股领涨的行情。PRISM 在 2026 年 10 月复盘自己的平仓记录后，找到了最大的差距：入场后 60 个交易日内上涨 30% 以上的韩国股票中，已实现收益的中位数是 +2%，而最高涨幅的中位数是 +60%。系统卖掉领涨股太早了。下一节的调整正是针对这个问题，我们也会持续公开这两项指标，方便大家检验效果。

> 来源：实时仪表盘数据（[韩国](https://analysis.stocksimulation.kr/dashboard_data.json)、[美国](https://analysis.stocksimulation.kr/us_dashboard_data.json)），生成于 2026-10-02（韩国）和 2026-10-03 KST（美国）。指数涨跌以仪表盘曲线的第一个数据点为基准（韩国 2025-09-29、美国 2026-01-29）。不含持仓中的股票。以上为模拟交易结果，不构成投资建议。

**[实时仪表盘](https://analysis.stocksimulation.kr/)**

---

## PRISM 现在如何交易（2026 年 10 月）

![PRISM 交易流程：筛选、AI 分析、买入决策、小额首次买入、情景加仓、领涨股持有、再入场、每周复盘](docs/images/how-prism-trades-en.png)

**投资方向。** PRISM 遵循欧奈尔（O'Neil）式趋势跟踪。大多数交易保持小仓位、及时止损，账户则依靠少数大涨的股票阶梯式成长。交易越多，找到这类股票的机会越大，但如果反复止损，账户也会被慢慢消耗。所以核心能力是 **选对股票、买对股票的准确度**。

| 步骤 | 内容 |
|------|------|
| **1. 筛选** | 早盘和午盘的触发器挑选价格与成交量动能异常的股票。每个触发器都会根据 PRISM 自己最近 180 天的记录（候选股达到 +20% 的比例和已实现盈亏的平均值）获得质量权重（0.7–1.3），较弱的触发器不再保证进入最终名单。 |
| **2. AI 分析** | 专业代理撰写报告（技术面、资金流向、财务、行业、新闻、市场），随后汇总投资策略。 |
| **3. 买入决策** | 买入代理按照成文的评分表给出 1–10 分，考察基本面（盈利能力、资产负债、成长性、业务清晰度）、动量信号和趋势检查。入场还需要达到当前市场状态的最低分数、满足风险收益比下限，并且止损幅度不超过该市场状态的上限（−5% 至 −7%）。 |
| **4. 小额首次买入** | 账户平均分为 10 个槽位。新仓位根据股票波动率从一个槽位的 30–80% 开始，来自最强触发器的高分形态会再大一档起步。 |
| **5. 情景加仓** | 买入时，AI 会写出 2–4 个加仓情景（例如突破、回调后重新站稳），并每天更新。代码只在条件满足时、价格高于平均成本时加仓，每次不超过上一笔买入，并保持在最初的风险预算内，最多加到一个完整槽位。对已确认的强势走势（当天第一次加仓后，较首次入场价高 8% 以上且成交量达到平时的 1.5 倍），同一交易时段可以再加仓一次。 |
| **6. 领涨股持有** | 买入后 4–15 个交易日内，收盘价比首次买入价高出 20% 以上，且没有过度偏离 50 日均线的股票，会被视为领涨股。在最多 40 个交易日内，只有收盘跌破 50 日线或跌破首次买入价时才卖出。1–3 个交易日内急涨 20% 的股票，仍沿用常规的利润保护止损。 |
| **7. 再入场** | 对止损卖出或因价格位置而暂缓买入的股票，最多观察 60 个交易日。如果在收盘前（韩国 14:00、美国 13:50）重新站上关键价位，需要 AI 复核批准后才买入。每个观察期最多 3 次，每个市场每天最多 2 笔。 |
| **8. 复盘循环** | 每周领涨股报告追踪大牛股的捕捉情况、错过的大涨股、止损成本以及各触发器的表现。两周复盘（2026 年 10 月 18 日）将按同样的标准评估 10 月的每项调整。 |

这些调整大多在 2026 年 10 月 2 日至 4 日投入实际运行，10 月 6 日是所有调整全部生效后的第一个交易日，因此上面的第二赛季数据大部分反映的是调整之前的结果。设计文档（韩文）：[投资方向](docs/TRADING_CHANGE_REVIEW_HARNESS.md) · [触发器优先级](docs/TRIGGER_QUALITY_PRIORITY_ko.md) · [小额首次买入](docs/micro-split/B3_LIVE_ko.md) · [情景加仓](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) · [领涨股持有](docs/RUNNER_HOLD_RULE_ko.md) · [再入场](docs/REENTRY_V3_LIVE_ko.md) · [每周报告](docs/WEEKLY_RUNNER_REPORT_ko.md) · [两周复盘](docs/TWO_WEEK_REVIEW_ko.md)

---

## 交易系统如何从失败中学习

韩国市场的交易历史出现过两种相反的失败模式：一开始过度回避入场，
后来又在缺乏足够状态控制的情况下承担风险。从 v1.16.7 到 v2.18 的各个版本，
逐步把系统从提示词层面的偏差修正，推进到对市场状态、平仓状态和再入场的确定性保护。

![PRISM-INSIGHT 交易系统从观望偏差走向基于状态的风险控制](docs/images/trading-evolution-en.png)

> 图中数字仅用于诊断。累计收益是单笔交易收益率之和，未入场候选股的表现是事后观察值，
> 并非时间加权的组合收益，也不是可实现的回测结果。

### 2026 年 10 月：我们验证、采用和放弃了什么

PRISM 在修改规则之前，会先用自己过去的候选股和交易重放这条规则，并把结论写进经验台账，避免同一个问题被重复验证。

**放弃**（没有胜过现行规则）：
- **口袋支点（pocket pivot）和成交量确认触发器**（2018–2026）：成交量条件在两个市场都没有带来任何效果。
- **下跌股同时收复 50 日线和 200 日线时买入**：没有优势，在韩国反而更差。
- **直接套用欧奈尔 8 周规则、一直持有到 50 日线**：结果变差（按单笔收益率之和计算，韩国 −43 个百分点，美国约 −40 个百分点）。1–3 个交易日内急涨 20% 的股票，在等待远处 50 日线的过程中几乎回吐了全部收益。
- **按 60 分钟检查止损、按波动率（ATR）设定止损、只在收盘时执行上调后的止损线**：都比现行止损更差。

**采用**：
- **领涨股持有** 只适用于 4–15 个交易日内达到 +20%、且没有过度偏离 50 日线的股票（韩国受影响的 7 笔交易合计 +57 个百分点；样本小且为样本内结果，每周报告会持续追踪）。
- 用 **小额首次买入加 AI 撰写的加仓情景** 取代固定的 +2% / +4% 阶梯，对已确认的强势走势更快加仓。
- **每个观察期最多 3 次再入场**。原来的“只能再入场一次”限制，切掉了本可盈利的再入场（韩国 7 笔中的 4 笔，美国 43 笔中的 13 笔）。
- **按 PRISM 自身记录设定触发器优先级**，并把成交量激增触发器修正为只在股价上涨时触发。

完整经验台账（韩文）：[docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md)

---

## 文档

| 文档 | 说明 |
|------|------|
| [docs/SETUP.md](docs/SETUP.md) | 完整安装指南 |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | AI 代理系统详情 |
| [docs/PIPELINE_ARCHITECTURE_ko.md](docs/PIPELINE_ARCHITECTURE_ko.md) | 筛选 → 分析 → 交易 → 反馈的设计（韩文） |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | 异动发现算法 |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | 交易记忆系统 |
| [docs/TRADING_CHANGE_REVIEW_HARNESS.md](docs/TRADING_CHANGE_REVIEW_HARNESS.md) | 投资方向与交易规则变更审查流程（韩文） |
| [docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md) | 研究经验台账：验证、采用与放弃的记录（韩文） |
| [docs/TRIGGER_QUALITY_PRIORITY_ko.md](docs/TRIGGER_QUALITY_PRIORITY_ko.md) | 基于 PRISM 自身记录的触发器优先级（韩文） |
| [docs/micro-split/B3_LIVE_ko.md](docs/micro-split/B3_LIVE_ko.md) | 小额首次买入与实盘建仓（韩文） |
| [docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) | AI 加仓情景与快速加仓（韩文） |
| [docs/RUNNER_HOLD_RULE_ko.md](docs/RUNNER_HOLD_RULE_ko.md) | 领涨股持有规则（韩文） |
| [docs/REENTRY_V3_LIVE_ko.md](docs/REENTRY_V3_LIVE_ko.md) | 再入场规则（韩文） |
| [docs/WEEKLY_RUNNER_REPORT_ko.md](docs/WEEKLY_RUNNER_REPORT_ko.md) | 每周领涨股报告（韩文） |
| [docs/TWO_WEEK_REVIEW_ko.md](docs/TWO_WEEK_REVIEW_ko.md) | 10 月调整的两周复盘（韩文） |

---

## 前端示例

### 仪表盘
实时组合追踪与业绩仪表盘。

```bash
cd examples/dashboard
npm install
npm run dev
# 访问 http://localhost:3000
```

**功能**：组合概览、交易历史、业绩指标、市场切换（KR/US）、与 KOSPI/KOSDAQ 的收益对比

**仪表盘配置指南**：[examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

<details>
<summary>查看仪表盘截图</summary>
<br>
<img src="docs/images/dashboard_portfolio.png" alt="组合概览" width="700">
<br><br>
<img src="docs/images/dashboard_trades.png" alt="交易模拟器" width="700">
<br><br>
<img src="docs/images/dashboard_performance.png" alt="AI 交易情景" width="700">
</details>

---

## MCP 服务器

### 韩国市场
- **kospi_kosdaq** — 基于韩国投资证券（KIS）API 的内置韩国市场数据服务器（`cores/market_data`）
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** — 网页抓取
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** — 网页搜索
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** — 交易模拟数据库

### 美国市场
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** — OHLCV、财务数据
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** — SEC 文件、内部人交易

---

## 参与贡献

1. Fork 本项目
2. 创建功能分支（`git checkout -b feature/amazing-feature`）
3. 提交更改（`git commit -m 'Add amazing feature'`）
4. 推送到分支（`git push origin feature/amazing-feature`）
5. 创建 Pull Request

### 贡献者与支持者

**代码贡献者** — 感谢每一位帮助改进 PRISM-INSIGHT 的朋友：

[@dragon1086](https://github.com/dragon1086) · [@rocky-mun](https://github.com/rocky-mun) · [@tkgo11](https://github.com/tkgo11) · [@alexander-schneider](https://github.com/alexander-schneider) · [@bonggu-kang](https://github.com/bonggu-kang) · [@willagio](https://github.com/willagio) · [@lifrary](https://github.com/lifrary) · [@cjinzy](https://github.com/cjinzy) · [@don9x2E](https://github.com/don9x2E) · [@jk5745](https://github.com/jk5745) · [@sungwoowi](https://github.com/sungwoowi)

**Gold Supporter** — [@tkgo11](https://github.com/tkgo11)

感谢对本项目的支持。

---

## 许可证

**双重许可：**

### 个人与开源使用
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

个人使用、非商业项目和开源开发可在 AGPL-3.0 下免费使用。

### 商业 SaaS 使用
SaaS 企业需要另行获得商业许可。

**联系方式**：dragon1086@naver.com
**详情**：[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)

第三方开源组件仍适用其各自的许可条款。
相关声明、源码链接和许可证全文请参见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 免责声明

分析信息仅供参考，不构成投资建议。所有投资决策及由此产生的盈亏均由投资者自行承担。

---

## 赞助

### 支持本项目

每月运营成本（截至 2026 年 1 月，约 $313/月）：
- OpenAI API：约 $234/月
- Anthropic API：约 $11/月
- Firecrawl + Perplexity：约 $36/月
- 服务器基础设施：约 $32/月

目前免费服务 450 多位用户。

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="在 GitHub 上赞助">
  </a>
</div>

---

## 项目成长

[![Star History Chart](https://star-history.dera.page/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.dera.page/#dragon1086/prism-insight&type=Date)

---

**如果这个项目对你有帮助，请给我们一个 Star！**

**联系方式**：[GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [Telegram](https://t.me/stock_ai_agent) | [Discussions](https://github.com/dragon1086/prism-insight/discussions)
