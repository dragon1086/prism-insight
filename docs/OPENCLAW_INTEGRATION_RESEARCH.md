# OpenClaw x PRISM-INSIGHT Integration Research

> **Date**: 2026-02-08 | **Status**: Research & Proposal

## 1. OpenClaw Overview

[OpenClaw](https://openclaw.ai/) (formerly Clawdbot/Moltbot) is a free, open-source autonomous AI agent by Peter Steinberger. It runs locally, connects to messaging platforms (WhatsApp, Telegram, Slack, Discord, iMessage, etc.), and executes real-world tasks via LLMs.

- **GitHub**: [openclaw/openclaw](https://github.com/openclaw/openclaw) — 145,000+ stars
- **License**: MIT
- **Architecture**: Node.js gateway, Docker-sandboxed execution
- **Model Support**: Claude, GPT, Ollama (model-agnostic)

### Key Capabilities

| Feature | Description |
|---------|-------------|
| **AgentSkills** | 700+ community plugins (SKILL.md format, AgentSkills standard) |
| **Cron** | Fixed-schedule task execution, persists across restarts |
| **Heartbeat** | Context-aware proactive monitoring (default 30min interval) |
| **Webhooks** | External service triggers via `POST /hooks/wake` |
| **MCP Adapter** | Model Context Protocol server integration |
| **Multi-channel** | WhatsApp, Telegram, Slack, Discord, iMessage, Signal, etc. |
| **ClawHub** | Official skill store at clawhub.ai (700+ skills) |

### Architecture Diagram

```
User (any messaging app)
  ↕
OpenClaw Gateway (local Node.js)
  ├── Cron scheduler
  ├── Heartbeat engine
  ├── Webhook receiver
  ├── Channel adapters (Telegram, WhatsApp, etc.)
  └── AgentSkills runtime
       ├── Shell/CLI tools
       ├── Browser automation (Playwright)
       ├── MCP server connections
       └── Custom skill folders (SKILL.md)
```

## 2. Connection Points with PRISM-INSIGHT

### Direct Overlaps

| PRISM-INSIGHT | OpenClaw | Integration Opportunity |
|---------------|----------|------------------------|
| Telegram bot delivery | Telegram channel adapter | Unified messaging layer |
| 13 AI agents (analysis) | AgentSkills (task execution) | PRISM as OpenClaw Skill |
| Cron-based trigger_batch | Cron + Heartbeat | Proactive alert system |
| GCP Pub/Sub signals | Webhook triggers | Event-driven notifications |
| PDF report generation | File delivery via channels | Multi-channel report delivery |
| SQLite portfolio DB | Persistent memory | Conversational portfolio access |
| KIS API trading | Shell/API tool execution | Conversational trading |

### Complementary Strengths

- **PRISM-INSIGHT**: Deep financial analysis (13 agents), Korean/US dual market, automated trading
- **OpenClaw**: Natural language interface, multi-channel presence, proactive scheduling, user context

## 3. Innovation Evaluation

### Scenario A: GCP Auto-Trading → OpenClaw Version
> Replace GCP Pub/Sub subscriber with OpenClaw skill for auto-trading

**Innovation Score: ★★☆☆☆ (Incremental)**

- Simply migrates infrastructure (Pub/Sub → OpenClaw)
- GCP Pub/Sub is superior for message reliability, retry, ordering
- AI agent autonomy in trade execution = risk, not innovation
- No new user value created

### Scenario B: Extended Signal Pipeline via OpenClaw
> Publish trigger batch alerts + PDF reports + trading signals to OpenClaw

**Innovation Score: ★★★☆☆ (Improvement)**

- More data exposed externally
- Still one-directional (PRISM → User)
- Channel expansion, not paradigm shift

### Scenario C: Report Generation via OpenClaw
> Use OpenClaw to receive and view reports

**Innovation Score: ★★★☆☆ (Improvement)**

- Already doing this via Telegram
- Adding one more channel is incremental

### Scenario D (Proposed): Conversational Investment Partner
> PRISM-INSIGHT as an OpenClaw Skill with bidirectional interaction

**Innovation Score: ★★★★★ (Paradigm Shift)**

See Section 4 for full proposal.

## 4. Proposed Architecture: Conversational Investment Partner

### Core Concept

Transform PRISM-INSIGHT from a "broadcast station" (one-way analysis delivery) into an "investment partner" (bidirectional, conversational, proactive).

```
User ↔ OpenClaw (natural language) ↔ PRISM-INSIGHT Skill (analysis engine)
```

### User Experience Scenarios

**Morning (Proactive)**
```
OpenClaw → User: "Morning signal detected 3 stocks:
  📈 NAVER (volume surge 350%)
  📈 Kakao (gap up +3.2%)
  📈 SK Hynix (closing strength)
  Want me to analyze any of these?"

User → OpenClaw: "Analyze NAVER"
OpenClaw → PRISM-INSIGHT: trigger analyze_stock("035420")
... 13 agents run ...
OpenClaw → User: [Summary] + [PDF attachment]
```

**Midday (On-demand)**
```
User → OpenClaw (via WhatsApp): "How's my portfolio doing?"
OpenClaw → PRISM-INSIGHT: query portfolio DB
OpenClaw → User: "Portfolio: 8/10 slots filled
  ✅ Samsung +3.2%  ✅ LG Energy +1.8%
  ⚠️ Celltrion -4.1% (approaching -5% stop-loss)
  Want me to sell Celltrion?"
```

**Trading Decision (Interactive)**
```
OpenClaw → User: "SK Hynix analysis complete.
  Buy score: 8.5/10
  Target: ₩195,000  Stop-loss: ₩172,000
  Entry: ₩183,000  Period: 3-6 months
  Proceed with purchase?"

User: "Why is the target 195K?"
OpenClaw: "60-day MA resistance at ₩193,000, with sector
  momentum suggesting breakout potential to ₩197,000.
  Conservative estimate at ₩195,000."

User: "OK, buy"
OpenClaw → KIS API: execute buy order
```

**Evening (Proactive monitoring)**
```
OpenClaw → User: "⚠️ Celltrion hit -5.0% stop-loss line.
  Current: ₩168,500 | Entry: ₩177,400
  Auto-sell triggered per your settings.
  Execution confirmed at ₩168,300."
```

### Technical Architecture

```
┌──────────────────────────────────────────────────────┐
│              User's Daily Channels                    │
│  WhatsApp │ Telegram │ iMessage │ Slack │ Discord    │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│               OpenClaw Gateway                        │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │  Cron   │  │Heartbeat │  │   Webhook        │    │
│  │(trigger │  │(portfolio│  │  (PRISM events)  │    │
│  │ batch)  │  │ monitor) │  │                  │    │
│  └────┬────┘  └────┬─────┘  └───────┬──────────┘    │
│       │            │                │                │
│       ▼            ▼                ▼                │
│  ┌───────────────────────────────────────────────┐   │
│  │       PRISM-INSIGHT AgentSkill                │   │
│  │   (skills/prism-insight/SKILL.md)             │   │
│  │                                               │   │
│  │   Tools:                                      │   │
│  │   • query_analysis  — request stock analysis  │   │
│  │   • check_portfolio — view holdings & P&L     │   │
│  │   • execute_trade   — buy/sell via KIS API    │   │
│  │   • get_triggers    — trigger batch results   │   │
│  │   • get_performance — track record & metrics  │   │
│  │   • generate_report — on-demand PDF report    │   │
│  └───────────────┬───────────────────────────────┘   │
└──────────────────┼───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│          PRISM-INSIGHT Backend (FastAPI)              │
│                                                      │
│  /api/analyze/{ticker}     — single stock analysis   │
│  /api/triggers/{mode}      — trigger batch results   │
│  /api/portfolio            — current holdings        │
│  /api/trade                — execute trade           │
│  /api/performance          — performance metrics     │
│  /api/report/{ticker}      — generate PDF report     │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐     │
│  │13 AI     │ │Trading   │ │ SQLite DB        │     │
│  │Agents    │ │(KIS API) │ │ (Holdings,       │     │
│  │          │ │          │ │  History, Perf)  │     │
│  └──────────┘ └──────────┘ └──────────────────┘     │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐     │
│  │Trigger   │ │PDF Gen   │ │ GCP Pub/Sub      │     │
│  │Batch     │ │(Playwr.) │ │ (still active)   │     │
│  └──────────┘ └──────────┘ └──────────────────┘     │
└──────────────────────────────────────────────────────┘
```

### Implementation Phases

| Phase | Scope | Deliverables |
|-------|-------|-------------|
| **1** | FastAPI wrapper + basic Skill | API endpoints, SKILL.md, on-demand analysis |
| **2** | Webhook integration | Trigger alert → OpenClaw proactive notification |
| **3** | Conversational trading | User confirmation → KIS API execution |
| **4** | Heartbeat portfolio monitoring | Stop-loss alerts, target price notifications |
| **5** | Feedback loop UI | Conversational performance review & config tuning |

### Phase 1 Implementation Sketch

**FastAPI wrapper** (`api_server.py`):
```python
from fastapi import FastAPI
app = FastAPI(title="PRISM-INSIGHT API")

@app.get("/api/analyze/{ticker}")
async def analyze_stock(ticker: str, market: str = "kr"):
    """Trigger full analysis pipeline for a single stock."""
    # Reuse existing cores/main.py::analyze_stock()
    ...

@app.get("/api/portfolio")
async def get_portfolio(market: str = "kr"):
    """Return current holdings with live P&L."""
    ...

@app.post("/api/trade")
async def execute_trade(ticker: str, action: str, market: str = "kr"):
    """Execute buy/sell via KIS API with user confirmation."""
    ...
```

**OpenClaw Skill** (`skills/prism-insight/SKILL.md`):
```yaml
---
name: prism_insight
description: AI-powered Korean & US stock analysis and trading assistant.
  Provides real-time market analysis using 13 specialized AI agents,
  portfolio monitoring, and conversational trading execution.
tools:
  - name: prism_api
    type: http
    config:
      baseUrl: http://localhost:8000/api
---

## When to use this skill

Use this skill when the user asks about:
- Stock analysis (Korean or US markets)
- Portfolio status, holdings, or P&L
- Trading decisions (buy/sell)
- Market triggers or signals
- Investment performance tracking

## How to use

1. For stock analysis: Call `GET /analyze/{ticker}?market=kr|us`
2. For portfolio: Call `GET /portfolio?market=kr|us`
3. For trading: Call `POST /trade` with user's explicit confirmation
4. For triggers: Call `GET /triggers/{mode}`
5. For performance: Call `GET /performance?days=30`

## Important rules

- NEVER execute trades without explicit user confirmation
- Always show risk metrics (stop-loss, target) before trade confirmation
- Use Korean (합쇼체) for Korean market analysis
- Use English for US market analysis
- When user asks "why", reference the Trading Scenario Agent's rationale
```

## 5. Innovation Comparison

| Aspect | Current (Telegram only) | OpenClaw Integration |
|--------|------------------------|---------------------|
| **Interface** | Read-only channel | Bidirectional conversation |
| **Initiative** | User must check channel | AI proactively alerts |
| **Channels** | Telegram only | WhatsApp, iMessage, Slack, etc. |
| **Trading** | Auto or manual via separate app | Conversational with confirmation |
| **Analysis** | Batch (all triggered stocks) | On-demand (any stock, anytime) |
| **Feedback** | Check DB manually | "How did we do this month?" |
| **Config** | Edit code/config files | "Change stop-loss to 6%" |
| **Context** | Each message is isolated | Persistent conversation memory |

## 6. Key Insight

> The paradigm shift is from **Tool → Partner**.
>
> PRISM-INSIGHT today is a tool: it runs, produces output, delivers it.
> With OpenClaw, it becomes a partner: it listens, responds, anticipates, and collaborates.
>
> This is the defining pattern of AI innovation in 2026:
> **Systems that adapt to the user's life, not the other way around.**

## 7. References

- [OpenClaw Official Site](https://openclaw.ai/)
- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [OpenClaw Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)
- [OpenClaw Skills Documentation](https://docs.openclaw.ai/tools/skills)
- [OpenClaw Cron Jobs](https://docs.openclaw.ai/automation/cron-jobs)
- [OpenClaw Webhooks](https://docs.openclaw.ai/automation/webhook)
- [OpenClaw Telegram Integration](https://docs.openclaw.ai/channels/telegram)
- [ClawHub Skill Directory](https://github.com/openclaw/clawhub)
- [Awesome OpenClaw Skills](https://github.com/VoltAgent/awesome-openclaw-skills)
- [OpenClaw MCP Adapter](https://github.com/androidStern-personal/openclaw-mcp-adapter)
- [CNBC: OpenClaw Rise](https://www.cnbc.com/2026/02/02/openclaw-open-source-ai-agent-rise-controversy-clawdbot-moltbot-moltbook.html)
- [DigitalOcean: What is OpenClaw](https://www.digitalocean.com/resources/articles/what-is-openclaw)
- [OpenClaw Trading Use Cases](https://medium.com/@luoyelittledream/building-an-ai-powered-automated-trading-system-from-scratch-making-clawdbot-openclaw-your-4294f0c05847)
- [OpenClaw + OpenAlgo Trading](https://medium.com/@openalgo/automating-trading-with-openalgo-and-openclaw-de55cc2b2d63)
