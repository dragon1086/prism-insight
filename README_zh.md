<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.6-green.svg" alt="Anthropic">
  <img src="https://img.shields.io/badge/ChatGPT_Plus-Codex_OAuth-ff6b35.svg" alt="ChatGPT Plus">
</div>

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **AI 驱动的股票市场分析与交易系统**
>
> 13+ 个专业 AI 代理协同工作，检测异动股票、生成分析师级别的研究报告，并自动执行交易。

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

---

### 铂金赞助商

<div align="center">
<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

**WrksAI** 的开发者 **AI3** —— 专为职场人士打造的 AI 助手，<br>
自豪地赞助 **PRISM-INSIGHT** —— 专为投资者打造的 AI 助手。
</div>

---

## 新功能：支持 ChatGPT Plus/Pro 订阅

**没有 API 密钥？没关系。** PRISM-INSIGHT 现在支持通过 **Codex OAuth 代理**直接使用您的 ChatGPT Plus（$20/月）或 Pro（$200/月）订阅进行分析。

```bash
# 一次性登录
python -m cores.chatgpt_proxy.oauth_login

# 使用 ChatGPT 订阅运行分析
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning
```

零 API 账单。同等强大的分析能力。让您现有的订阅发挥价值。

详细配置说明：[docs/chatgpt-oauth/setup.md](docs/chatgpt-oauth/setup.md)

---

## 移动端应用

<div align="center">

**随时随地获取 AI 股票分析**

<a href="https://play.google.com/store/apps/details?id=com.prisminsight.prism_mobile">
  <img src="https://img.shields.io/badge/Google_Play-下载-green?style=for-the-badge&logo=google-play" alt="Google Play">
</a>
<a href="https://apps.apple.com/us/app/prism-insight-stock-analysis/id6759331074">
  <img src="https://img.shields.io/badge/App_Store-下载-blue?style=for-the-badge&logo=apple" alt="App Store">
</a>

</div>

- **智能筛选** — 只接收您关心的 Telegram 提醒
- **PDF 报告** — 移动端优化的 AI 分析报告
- **限时优惠（截止 2026 年 4 月 23 日）** — 立即安装，获得 **20 积分免费赠送**（平时仅赠 10 积分）

---

## 产品演示视频

[![PRISM-INSIGHT Demo](https://img.youtube.com/vi/zAywb1G0wRA/maxresdefault.jpg)](https://www.youtube.com/watch?v=zAywb1G0wRA)

---

## 立即体验（无需安装）

### 1. 实时仪表盘
实时查看 AI 交易绩效：
**[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. Telegram 频道
每日获取异动股票提醒和 AI 分析报告：
- **[英语频道](https://t.me/prism_insight_global_en)**
- **[韩语频道](https://t.me/stock_ai_agent)**
- **[日语频道](https://t.me/prism_insight_ja)**
- **[中文频道](https://t.me/prism_insight_zh)**
- **[西班牙语频道](https://t.me/prism_insight_es)**

### 3. 示例报告
观看 AI 生成的 Apple Inc. 分析报告：

[![示例报告 - Apple Inc. 分析](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## 60 秒快速上手（美股）

体验 PRISM-INSIGHT 的最快方式。仅需 **OpenAI API 密钥**。

```bash
# Clone and run the quickstart script
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

以上命令将生成 Apple (AAPL) 的 AI 分析报告。尝试分析其他股票：
```bash
python3 demo.py MSFT              # Microsoft
python3 demo.py NVDA              # NVIDIA
python3 demo.py TSLA --language ko  # Tesla (Korean report)
```

> **获取 OpenAI API 密钥**：访问 [OpenAI Platform](https://platform.openai.com/api-keys)
>
> **可选**：将 [Perplexity API 密钥](https://www.perplexity.ai/) 添加到 `mcp_agent.config.yaml` 以启用新闻分析功能

AI 生成的 PDF 报告将保存在 `prism-us/pdf_reports/` 目录中。

<details>
<summary>或使用 Docker（无需 Python 环境）</summary>

```bash
# 1. Set your OpenAI API key
export OPENAI_API_KEY=sk-your-key-here

# 2. Build and start the local quickstart image
docker compose -f docker-compose.quickstart.yml up --build -d

# 3. Run analysis
docker exec -it prism-quickstart python3 demo.py NVDA
```

首次运行会在本地构建镜像，因此可能需要几分钟时间。

报告将保存到 `./quickstart-output/` 目录。

</details>

---

## 完整安装

### 前提条件
- Python 3.10+ 或 Docker
- OpenAI API 密钥（[在此获取](https://platform.openai.com/api-keys)）或 ChatGPT Plus/Pro 订阅

### 方式 A：Python 安装

```bash
# 1. Clone & Install
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Install Playwright for PDF generation
python3 -m playwright install chromium

# 3. Install perplexity-ask MCP server
cd perplexity-ask && npm install && npm run build && cd ..

# 4. Setup config
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit mcp_agent.secrets.yaml with your OpenAI API key
# Edit mcp_agent.config.yaml with KRX credentials (Kakao account)

# 5. Run analysis (no Telegram required!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 方式 B：Docker（推荐用于生产环境）

```bash
# 1. Clone & Configure
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit config files with your API keys

# 2. Build & Run
docker-compose up -d

# 3. Run analysis manually (optional)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

**完整安装指南**：[docs/SETUP.md](docs/SETUP.md)

---

## 什么是 PRISM-INSIGHT？

PRISM-INSIGHT 是一个**完全开源、免费**的 AI 驱动股票分析系统，支持**韩国（KOSPI/KOSDAQ）**和**美国（NYSE/NASDAQ）**市场。

### 核心功能
- **异动股票检测** — 自动检测成交量/价格异常波动的股票
- **AI 分析报告** — 由 13 个专业 AI 代理生成的专业分析师级别报告
- **交易模拟** — AI 驱动的买卖决策与投资组合管理
- **自动交易** — 通过韩国投资证券 API 实际执行交易
- **Telegram 集成** — 实时提醒与多语言播报
- **宏观智能** — 市场状态识别、板块轮动分析、风险事件监测

### AI 模型
- **分析与交易**：OpenAI GPT-5 / GPT-5.4-mini（通过 API 或 ChatGPT Plus 订阅）
- **报告生成**：Anthropic Claude Sonnet 4.6
- **翻译**：OpenAI GPT-5（支持英语、日语、中文、西班牙语）

---

## AI 代理系统

13+ 个专业代理以团队形式协作：

| 团队 | 代理数量 | 职责 |
|------|----------|------|
| **宏观** | 1 个代理 | 市场状态判断、板块轮动、风险事件 |
| **分析** | 6 个代理 | 技术分析、财务分析、行业分析、新闻分析、市场分析 |
| **策略** | 1 个代理 | 投资策略综合 |
| **通信** | 3 个代理 | 摘要生成、质量评估、翻译 |
| **交易** | 3 个代理 | 买卖决策、交易日志 |
| **咨询** | 2 个代理 | 通过 Telegram 进行用户交互 |

<details>
<summary>查看代理工作流程图</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="Agent Workflow" width="700">
</details>

**代理系统详细文档**：[docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md)

---

## 主要特性

| 特性 | 说明 |
|------|------|
| **AI 分析** | 通过 GPT-5 多代理系统进行专家级股票分析 |
| **异动检测** | 通过早盘/午盘市场趋势分析自动生成观察列表 |
| **Telegram** | 实时分析报告分发至频道 |
| **交易模拟** | AI 驱动的投资策略模拟 |
| **自动交易** | 通过韩国投资证券 API 执行交易 |
| **仪表盘** | 透明的投资组合、交易记录和绩效追踪 |
| **自我进化** | 交易日志反馈回路 —— 历史触发胜率自动影响未来买入决策（[详情](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)） |
| **美股市场** | 全面支持 NYSE/NASDAQ 分析 |
| **宏观智能** | 市场状态识别与板块轮动，提升选股精准度 |
| **移动端应用** | iOS 和 Android 应用，支持智能筛选与 PDF 报告 |

<details>
<summary>查看仪表盘截图</summary>
<br>
<img src="docs/images/dashboard_portfolio.png" alt="投资组合概览" width="700">
<br><br>
<img src="docs/images/dashboard_trades.png" alt="交易模拟器" width="700">
<br><br>
<img src="docs/images/dashboard_performance.png" alt="AI 交易场景" width="700">
</details>

---

## 交易绩效

### 韩国市场 — 第二赛季

| 指标 | 数值 |
|------|------|
| 统计周期 | 2025.09.30 ~ 2026.03.24 |
| 总交易次数 | 86 |
| 胜率 | 45.35% |
| 每笔平均收益 | +2.84% |
| **累计收益率** | **+244.63%** |
| 当前持仓 | 5 只股票 |

### 美国市场（测试阶段）

| 指标 | 数值 |
|------|------|
| 统计周期 | 2026.01.28 ~ 2026.03.21 |
| 总交易次数 | 13 |
| 当前持仓 | 6 只股票 |

**[实时仪表盘](https://analysis.stocksimulation.kr/)**

---

## 美股模块

同样的 AI 驱动工作流程，适用于美国市场：

```bash
# Run US analysis
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# With English reports
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

**数据来源**：yahoo-finance-mcp、sec-edgar-mcp（SEC 文件、内幕交易）

---

## 文档

| 文档 | 说明 |
|------|------|
| [docs/SETUP.md](docs/SETUP.md) | 完整安装指南 |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | AI 代理系统详情 |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | 异动检测算法 |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | 交易记忆系统 |
| [docs/chatgpt-oauth/setup.md](docs/chatgpt-oauth/setup.md) | ChatGPT OAuth 代理配置 |

---

## 前端示例

### 仪表盘
实时投资组合跟踪与绩效仪表盘。

**[在线演示](https://analysis.stocksimulation.kr/)**

```bash
cd examples/dashboard
npm install
npm run dev
# Visit http://localhost:3000
```

**特性**：投资组合概览、交易历史、绩效指标、市场选择器（韩国/美国）、与 KOSPI/KOSDAQ 收益对比

**仪表盘安装指南**：[examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

---

## MCP 服务器

### 韩国市场
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)** — KRX 股票数据
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** — 网页爬取
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** — 网络搜索
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** — 交易模拟数据库

### 美国市场
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** — OHLCV、财务数据
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** — SEC 文件、内幕交易

---

## 参与贡献

1. Fork 本项目
2. 创建功能分支（`git checkout -b feature/amazing-feature`）
3. 提交更改（`git commit -m 'Add amazing feature'`）
4. 推送到分支（`git push origin feature/amazing-feature`）
5. 创建 Pull Request

---

## 许可证

**双重许可：**

### 个人与开源使用
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

个人使用、非商业项目和开源开发免费使用，遵循 AGPL-3.0 协议。

### 商业 SaaS 使用
SaaS 公司需要单独的商业许可证。

**联系方式**：dragon1086@naver.com
**详情**：[LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)

---

## 免责声明

分析信息仅供参考，不构成投资建议。所有投资决策及由此产生的盈亏均由投资者自行承担。

---

## 赞助支持

### 支持本项目

每月运营成本（约 $310/月）：
- OpenAI API：约 $235/月
- Anthropic API：约 $11/月
- Firecrawl + Perplexity：约 $35/月
- 服务器基础设施：约 $30/月

目前免费服务 450+ 用户。

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="在 GitHub 上赞助">
  </a>
</div>

---

## 项目成长

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**如果本项目对您有帮助，请给我们一个 Star！**

**联系方式**：[GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [Telegram](https://t.me/stock_ai_agent) | [Discussions](https://github.com/dragon1086/prism-insight/discussions)
