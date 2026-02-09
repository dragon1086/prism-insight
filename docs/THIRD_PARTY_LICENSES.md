# Third-Party Data Sources & Licenses

> **Updated**: 2026-02-09

PRISM-INSIGHT uses various third-party data sources and libraries. Users should be aware of the following license terms and restrictions before deploying this project.

## Data Sources

### US Market

| Source | License / Terms | Usage in PRISM | Notes |
|--------|----------------|----------------|-------|
| **yfinance** (library) | Apache 2.0 | OHLCV, company info, financials, holders | Library itself is open source |
| **Yahoo Finance** (data) | [Yahoo ToS](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html) | Data accessed via yfinance | **Personal use only. Redistribution prohibited.** See warning below |
| **SEC EDGAR** (data) | Public Domain | SEC filings, insider trading, financials | US government data. Free for any use |
| **Finnhub** (API) | [Finnhub ToS](https://finnhub.io/terms-of-service) | Company news, earnings (optional) | Free tier: personal/non-commercial |

### Korean Market

| Source | License / Terms | Usage in PRISM | Notes |
|--------|----------------|----------------|-------|
| **pykrx** (library) | MIT | KOSPI/KOSDAQ OHLCV, market cap | Open source, commercial use OK |
| **KRX** (data) | [KRX Terms](https://www.krx.co.kr/) | Data accessed via pykrx | Exchange data policies apply |
| **KIS API** | [KIS ToS](https://apiportal.koreainvestment.com/) | Trading execution, market data | Intended for personal account trading |

### MCP Servers

| Server | License | Notes |
|--------|---------|-------|
| **yahoo-finance-mcp** | MIT | Wrapper only; Yahoo Finance data ToS still applies |
| **sec-edgar-mcp** | AGPL-3.0 | Compatible with PRISM's AGPL-3.0 license |
| **firecrawl-mcp** | MIT | Firecrawl core is AGPL-3.0; cloud API has separate ToS |
| **kospi-kosdaq-stock-server** | MIT | Maintained by PRISM-INSIGHT author |

### AI / LLM Services

| Service | Terms | Notes |
|---------|-------|-------|
| **OpenAI API** | [OpenAI ToS](https://openai.com/policies/terms-of-use/) | Commercial use permitted under API terms |
| **Anthropic API** | [Anthropic ToS](https://www.anthropic.com/legal/aup) | Commercial use permitted under API terms |
| **Perplexity API** | [Perplexity ToS](https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service) | API customers: business use OK with AUP compliance |

---

## Important: Yahoo Finance Data Warning

**yfinance** is the primary US market data source in PRISM-INSIGHT. While the yfinance Python library is open source (Apache 2.0), the **data it retrieves is subject to Yahoo Finance's Terms of Service**, which restricts usage to **personal, non-commercial purposes** and **prohibits redistribution**.

This means:
- Running PRISM-INSIGHT locally for personal analysis is generally fine
- **Redistributing Yahoo Finance data** (e.g., via Telegram channels, public dashboards, or APIs) **may violate Yahoo's Terms of Service**
- Yahoo may block or restrict access at any time without notice
- yfinance is **not an official Yahoo product** and has no guaranteed uptime or stability

### What this means for you

If you are deploying PRISM-INSIGHT and sharing analysis results publicly:
1. You accept the risk that Yahoo may block yfinance access
2. Consider using alternative data sources with explicit commercial licenses (e.g., Polygon.io, licensed exchange data feeds) for production deployments
3. Framing outputs as "AI-generated analysis/opinions" rather than raw data redistribution may reduce (but not eliminate) legal risk

### If yfinance stops working

yfinance depends on Yahoo Finance's unofficial endpoints. If access is blocked:
- The surge detection module (`us_surge_detector.py`) will fail
- Company data and financials in reports will be unavailable
- Trading decisions that rely on real-time prices will be affected
- **SEC EDGAR data** (filings, financials) will continue to work as it's public domain
- **KIS API** market data can serve as a fallback for price data

---

## License Compatibility

PRISM-INSIGHT is licensed under **AGPL-3.0**. All software dependencies are license-compatible:

| Dependency License | Compatible with AGPL-3.0? |
|-------------------|--------------------------|
| MIT | Yes |
| Apache 2.0 | Yes |
| BSD | Yes |
| AGPL-3.0 | Yes (same license) |
| Public Domain | Yes |

**Note**: License compatibility applies to **software**. Data terms of service (Yahoo Finance, Finnhub, KRX, etc.) are separate from software licenses and must be independently evaluated for your use case.

---

## Recommendations by Use Case

| Use Case | Recommendation |
|----------|---------------|
| Personal local analysis | yfinance is fine |
| Open source development | yfinance is fine; warn users in documentation |
| Public Telegram channel | Acknowledge Yahoo ToS risk; consider paid data alternatives |
| Commercial SaaS | Use licensed data providers (Polygon.io, Bloomberg, Refinitiv) |
