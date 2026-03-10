# Macro Intelligence Integration Plan (RALPLAN-DR)

> **Version**: 2.0 | **Date**: 2026-03-10 | **Status**: Draft (Iteration 2)
> **Scope**: Paradigm shift from pure quantitative screening to macro-aware stock selection and buy decisions

---

## RALPLAN-DR Summary

### Principles

1. **Macro Context First**: No stock should be bought or rejected without considering the current macro-economic regime and sector dynamics.
2. **Minimal Disruption**: All changes must be additive -- existing trigger types, data flows, and agent interfaces must continue to work unchanged when macro data is unavailable (graceful degradation).
3. **Market Separation**: KR and US markets have fundamentally different data sources, macro indicators, and sector taxonomies. They must have fully separated logic paths.
4. **Generalizable Logic**: Macro intelligence must NOT be hardcoded to any specific geopolitical event (e.g., US-Iran war). It must generalize across any market regime.
5. **Evidence-Based Scoring**: Every score adjustment (sector bonus/penalty, regime shift) must be traceable to a specific data point from the macro intelligence output.

### Decision Drivers (Top 3)

1. **Zero entry rate is the critical problem**: The system screens stocks but never buys them because the buy agent lacks macro context to justify entry in uncertain markets. Without macro context, every stock looks risky.
2. **Market regime is too simplistic**: Binary bull/bear (based solely on 20-day MA + 2-week change) misses nuance like "rotating bull" (some sectors strong, others weak), "defensive posture" (risk-off but not full bear), or "recovery phase."
3. **Trigger batch is blind to themes**: Pure OHLCV screening cannot identify that a defense stock surging during a military conflict is a sector leader vs. a random bounce. Sector context transforms noise into signal.

### Viable Options

**Option A: Standalone Macro Intelligence Agent (Recommended)**
- Create a new agent that runs BEFORE trigger batch
- Outputs structured JSON consumed by both trigger batch and buy agent
- Pros: Clean separation of concerns, cacheable output, testable independently, can be reused for weekly reports
- Cons: Adds one agent call per pipeline run (~30-60 seconds latency), requires new file

**Option B: Embed Macro Logic into Existing Market Analysis Agent**
- Extend `market_index_agents.py` to output structured sector/regime JSON alongside its report
- Pros: No new agent, reuses existing perplexity call
- Cons: Market analysis agent already has a long prompt (290+ lines), mixing structured output with free-text report creates parsing fragility, market analysis runs AFTER trigger batch (would need pipeline reorder anyway)

**Option C: Hybrid -- Lightweight Pre-Scan + Enhanced Market Agent**
- Add a lightweight pre-scan function (no LLM) that checks index data for regime
- Enhance market analysis agent to output structured sector data
- Pros: Fastest pre-scan, reuses existing agent
- Cons: Pre-scan without LLM cannot identify sector themes or geopolitical context (the core missing piece)

**Decision: Option A** -- Standalone Macro Intelligence Agent. Clean architecture, testable, and the only option that provides macro context BEFORE stock screening begins.

**Why Option B was invalidated**: The market analysis agent (`market_index_agents.py`) runs AFTER the trigger batch during stock analysis, not before. Reordering the pipeline to run it first would require major architectural changes. Additionally, its 290+ line prompt already mixes free-text output with data collection; adding structured JSON output would create parsing fragility. Option A keeps concerns cleanly separated.

**Why Option C was invalidated**: The core missing piece is LLM-interpreted sector themes and geopolitical context. A non-LLM pre-scan can only check index price vs. moving average (which the buy agent already does). Without LLM interpretation of news/events, this option fails to address Decision Driver #3 (theme blindness).

### Duplicate Perplexity Call Resolution (DECIDED)

The new macro agent AND existing `market_index_agents.py` both call `perplexity_ask`. **Decision: Keep both calls.** They serve different purposes:
- **Macro agent** (runs BEFORE trigger batch): Broad macro regime + sector rotation query. Output is structured JSON cached for the entire pipeline run. Focus: "What is the market regime? Which sectors lead/lag?"
- **Market index agent** (runs DURING each stock analysis): Stock-specific market context query. Output is free-text report section. Focus: "What specific market factors affect this stock today?"

**Precedence rule**: The macro agent's `market_regime` classification takes precedence for the buy agent's min_score determination. The market index agent's narrative enriches the report but does not override the regime classification. The buy agent prompt will explicitly state: "Use the regime from the macro intelligence summary for min_score; use the market analysis narrative for qualitative context."

### Pre-Mortem (4 Failure Scenarios)

**Failure 1: Macro agent produces unreliable sector classifications**
- Risk: Perplexity returns vague or contradictory sector data; "leading sectors" change daily
- Mitigation: Define a fixed sector taxonomy (GICS-based for US, KOSPI sector codes for KR). Macro agent must map to these fixed categories. Include "confidence" field -- low confidence means no sector adjustment applied.
- Detection: Log sector classifications daily and alert if >50% of sectors flip leading/lagging between consecutive runs.

**Failure 2: Over-buying in response to macro signal**
- Risk: System goes from 0% entry rate to buying every screened stock because macro context inflates scores
- Mitigation: Sector bonus/penalty is capped at +/-1 score point. min_score reduction is the bigger lever but is bounded (strong_bull: 5, not lower). Existing stop-loss rules remain strict.
- Detection: Track entry rate per week. If >60% of screened stocks are bought, trigger alert.

**Failure 3: Pipeline latency becomes unacceptable**
- Risk: Adding macro agent before trigger batch adds 60+ seconds, making the pipeline miss market-open windows
- Mitigation: Cache macro intelligence output for the entire run (already done for market_index_analysis). Macro agent runs ONCE per pipeline execution, not per stock. Use `gpt-5-mini` to reduce latency.
- Detection: Measure and log macro agent execution time. Alert if >90 seconds.

**Failure 4: Regime misclassification is systematically biased**
- Risk: Perplexity defaults to optimistic language in its responses, causing the macro agent to systematically return `moderate_bull` even in sideways/bearish conditions. This would silently lower the entry bar without justification.
- Mitigation: The macro agent prompt must explicitly require index data (20-day MA position, 2-week change %) as PRIMARY regime evidence, with perplexity narrative as SECONDARY confirmation. Include explicit regime boundary thresholds in the prompt (e.g., "if index is below 20d MA AND 2-week change < -2%, regime CANNOT be bull regardless of narrative tone").
- Detection: Daily comparison of macro agent regime vs. simple 20-day MA regime classification. Log both. If they agree >90% of the time over 2 weeks, the macro agent adds no value beyond the simple check. If they disagree >50% of the time, one is systematically wrong -- investigate which. Alert on either condition.

### Score Arithmetic Model

The min_score reduction is the BIGGER lever for fixing zero entry rate. Sector bonus provides fine-tuning.

**KR Market Score Impact by Regime:**

| Regime | Stock Score (typical) | Leading Sector Bonus | Total | min_score | Result |
|--------|----------------------|---------------------|-------|-----------|--------|
| strong_bull | 5 | +1 | 6 | 5 | PASS (comfortable, +1 margin) |
| moderate_bull | 5 | +1 | 6 | 6 | PASS (exact match) |
| sideways | 5 | +1 | 6 | 6 | PASS (exact match) |
| sideways | 5 | 0 (no sector match) | 5 | 6 | FAIL (-1 deficit) |
| moderate_bear | 5 | +1 | 6 | 7 | FAIL (-1 deficit) |
| strong_bear | 5 | +1 | 6 | 8 | FAIL (-2 deficit) |

**Analysis**: The current zero-entry problem occurs because even in sideways markets, a typical stock scores 5 against min_score=7 (current bear threshold). With the new 5-tier regime:
- In sideways markets (most common), min_score drops from 7 to 6, making marginal stocks with sector bonus passable.
- In moderate_bear, min_score=7 remains strict -- only strong stocks (score 7+) or exceptional leading-sector stocks (6+1=7) pass. This is intentional.
- The +/-1 bonus is sufficient because the min_score reduction does the heavy lifting. A +/-2 bonus would risk over-buying in bull markets.

**US Market Score Impact by Regime:**

| Regime | Stock Score (typical) | Leading Sector Bonus | Total | min_score | Result |
|--------|----------------------|---------------------|-------|-----------|--------|
| strong_bull | 5 | +1 | 6 | 4 | PASS (comfortable, +2 margin) |
| moderate_bull | 5 | +1 | 6 | 5 | PASS (comfortable, +1 margin) |
| sideways | 5 | +1 | 6 | 5 | PASS (comfortable) |
| sideways | 5 | 0 | 5 | 5 | PASS (exact match) |
| moderate_bear | 5 | +1 | 6 | 6 | PASS (exact match) |
| strong_bear | 5 | +1 | 6 | 7 | FAIL (-1 deficit) |

**Note**: US thresholds are 1 point lower than KR across the board (current baseline: bull=5, bear=6 vs. KR bull=6, bear=7). This reflects US market's higher liquidity and institutional depth.

### Expanded Test Plan

**Unit Tests:**
- Macro intelligence agent returns valid JSON schema (all required fields present, correct types)
- Market regime classification produces one of: `strong_bull`, `moderate_bull`, `sideways`, `moderate_bear`, `strong_bear`
- Sector bonus/penalty calculation is bounded within +/-1 point
- Trigger batch `select_final_tickers` still produces valid output when `macro_context=None` (graceful degradation)
- Buy agent prompt correctly includes macro context section when provided
- Buy agent prompt works unchanged when macro context is absent
- KR sector lookup via `pykrx.get_index_portfolio_deposit_file()` returns valid mapping for top KOSPI tickers
- US sector lookup via `yfinance.Ticker().info['sector']` returns valid GICS sector

**Integration Tests:**
- Macro agent output -> trigger batch: sector filtering correctly applied, selected stock count adjusts with regime
- Macro agent output -> buy agent (via report): agent can reference leading/lagging sectors in its analysis
- Full data flow: orchestrator calls macro agent -> passes output to trigger batch -> passes output to report -> tracking agent passes report to buy agent
- Cache test: second stock analysis in same pipeline run reuses cached macro intelligence
- Perplexity deduplication: macro agent and market index agent make separate calls with different query focus

**E2E Tests:**
- Run full KR pipeline (`python stock_analysis_orchestrator.py --mode morning --no-telegram`) and verify:
  - Macro intelligence JSON is generated and logged
  - Trigger batch receives and uses macro context
  - Generated reports include macro context in section 4
  - Buy agent receives report with macro context
- Run full US pipeline (`python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram`) with same verification

**Observability:**
- Log macro intelligence output (full JSON) at INFO level in orchestrator
- Log regime classification and leading sectors at each trigger batch run
- Log whether sector bonus/penalty was applied for each buy decision
- Track and log: stocks screened, stocks with sector bonus, stocks entered, entry rate
- Log macro agent regime vs. simple 20-day MA regime for bias detection (Failure 4)

---

## Recommended Implementation Strategy

**Phase 1 (Prompt Enhancement) should be implemented FIRST as a standalone shipment.** It requires zero new infrastructure, zero new files, and delivers ~80% of the zero-entry fix by:
1. Replacing binary bull/bear with 5-tier regime classification in the buy agent prompt
2. Adjusting min_score per regime (the BIGGER lever)
3. Adding macro-aware scoring instructions that read existing report section 4

**After shipping Phase 1, measure entry rate for 1-2 weeks.** If entry rate improves to an acceptable level (target: 10-30% of screened stocks), Phases 2-4 become optimizations rather than critical fixes. If entry rate remains near zero, proceed with Phases 2-4 for the full macro intelligence pipeline.

```
Phase 1 (Prompt Enhancement) ──> MEASURE 1-2 WEEKS ──> Phase 2 (Macro Agent) ──> Phase 3 (Trigger Enhancement) ──┐
                                                                                                                   ├──> Phase 4 (Pipeline Integration) ──> Phase 5 (QA)
                                                                                                                   │
                                                        Phase 2 and Phase 3 can be developed in parallel ─────────┘
```

---

## Phase 1: Buy Agent Prompt Enhancement (SHIP FIRST)

### Objective
Enhance the trading scenario agent (buy agent) prompts to incorporate multi-dimensional market regime and sector-aware scoring. This phase is prompt-only -- no new files, no infrastructure changes, no pipeline modifications.

### Why Ship First
- Zero new infrastructure required -- only prompt text changes in existing files
- Delivers ~80% of the zero-entry fix via min_score reduction (the bigger lever)
- Can be measured independently before investing in the macro agent infrastructure
- Fully backward compatible -- no data format or API changes

### Files to Modify

**1. `cores/agents/trading_agents.py` (KR) -- Modify `create_trading_scenario_agent()`**

Changes to the Korean instruction (starting at line ~298):

**1a. Replace binary market regime with multi-dimensional regime (Section 0 stage)**

Current (line ~337-339):
```
**0단계: 시장 환경 판단**
kospi_kosdaq-get_index_ohlcv로 KOSPI 최근 20일 데이터 확인 후:
- 강세장: KOSPI 20일 이동평균선 위 + 최근 2주 +5% 이상 상승
- 약세장/횡보장: 위 조건 미충족
```

New:
```
**0단계: 시장 환경 판단**

A) 보고서의 '시장 분석' 섹션에서 거시경제 환경 정보를 먼저 확인:
- 시장 체제(regime) 정보가 제공되면 이를 우선 활용 (거시경제 인텔리전스 요약의 regime을 min_score 결정에 사용)
- 주도 섹터(leading sectors)와 소외 섹터(lagging sectors) 정보 확인
- 리스크 이벤트와 수혜 테마 확인

B) kospi_kosdaq-get_index_ohlcv로 KOSPI 최근 20일 데이터로 보완 검증:
- 강한 강세장(strong_bull): KOSPI 20일 이동평균선 위 + 최근 2주 +5% 이상 상승
- 보통 강세장(moderate_bull): KOSPI 20일 이동평균선 위 + 양의 추세
- 횡보장(sideways): KOSPI 20일 이동평균선 부근, 혼재 신호
- 보통 약세장(moderate_bear): KOSPI 20일 이동평균선 아래 + 음의 추세
- 강한 약세장(strong_bear): KOSPI 20일 이동평균선 아래 + 최근 2주 -5% 이상 하락

C) 최종 시장 판단은 A와 B를 종합하여 결정. 거시환경 데이터가 기술적 지표와 상충할 경우, 거시환경 정보의 근거를 더 면밀히 검토.
단, 지수가 20일 이동평균선 아래이고 2주 변화율이 -2% 미만이면 '강세장' 판단 불가 (낙관적 편향 방지).
```

**1b. Add macro-economic risk as independent checkpoint (new Section 3-6)**

Add after Section 3-5 (현재 시간 반영):
```
#### 3-6. 거시경제 및 지정학적 리스크 평가
보고서의 '4. 시장 분석' 섹션에서 '당일 시장 변동 요인 분석'을 반드시 확인하고 다음을 평가:

**매수 점수 조정:**
- 분석 대상 종목의 섹터가 현재 '주도 섹터'에 해당하면: +1점 가산
- 분석 대상 종목의 섹터가 현재 '소외 섹터'에 해당하면: -1점 감점
- 분석 대상 종목이 현재 '수혜 테마'의 직접 수혜주이면: +1점 가산 (주도 섹터 가산과 중복 불가, 최대 +1)
- 분석 대상 종목이 현재 '리스크 이벤트'의 직접 피해주이면: -1점 감점 (소외 섹터 감점과 중복 불가, 최대 -1)

**거시경제 리스크가 미진입 사유가 될 수 있는 경우:**
- 해당 종목의 섹터가 현재 리스크 이벤트의 직접 피해 섹터이고, 리스크 심각도가 "high"인 경우
- 시장 체제가 "strong_bear"이고 해당 종목의 강한 모멘텀 신호가 2개 미만인 경우
- 단, 거시경제 리스크만으로 미진입 결정 시 반드시 구체적 리스크 이벤트명과 영향 경로를 명시할 것
```

**1c. Modify no-entry justification (Section 6)**

Current (line ~520):
```
**불충분한 표현 (사용 금지):** "과열 우려", "변곡 신호", "추가 확인 필요", "리스크 통제 불가"
```

New:
```
**불충분한 표현 (사용 금지):** "과열 우려", "변곡 신호", "추가 확인 필요"

**허용되는 거시경제 기반 미진입 표현 (구체적 근거 필수):**
- "[구체적 리스크 이벤트]로 인한 [해당 섹터] 직접 피해 예상" (예: "미중 관세 전쟁 심화로 반도체 수출 직접 피해 예상")
- "시장 체제 강한 약세 + 방어적 포지션 필요" (단, 강한 모멘텀 2개 이상이면 이 사유 불가)
- "해당 섹터 소외 + 자금 이탈 추세 확인" (거시 데이터 근거 필수)
```

**1d. Add explicit instruction to read market analysis section**

Add to Section "보고서 섹션별 확인 가이드" table (line ~322-332):
```
| 4. 시장 분석 | 시장 리스크 레벨, 거시환경, 업종 동향, **주도/소외 섹터, 수혜 테마, 리스크 이벤트** |
```

Also add after the table:
```
**필수 확인**: '4. 시장 분석' 섹션의 '당일 시장 변동 요인 분석' 부분을 반드시 읽고, 해당 종목의 섹터가 현재 시장 변동 요인과 어떤 관계에 있는지 분석에 반영하세요.
주도 섹터 종목은 시장 순풍을 받고 있으므로 더 적극적으로, 소외 섹터 종목은 역풍을 받고 있으므로 더 보수적으로 판단합니다.
거시경제 인텔리전스 요약이 있으면 해당 regime을 min_score 결정에 사용하고, 없으면 B)의 기술적 판단을 사용하세요.
```

**1e. Update min_score in JSON output for multi-regime**

Current KR (line ~554):
```
"min_score": 시장 환경에 따른 최소 진입 요구 점수 (강세장: 6, 약세장: 7),
```

New KR:
```
"min_score": 시장 환경에 따른 최소 진입 요구 점수 (강한 강세장: 5, 보통 강세장: 6, 횡보장: 6, 보통 약세장: 7, 강한 약세장: 8),
```

**1f. Apply same changes to English instruction** (line ~18-296)
Mirror all Korean changes in the English instruction block.

**2. `prism-us/cores/agents/trading_agents.py` (US) -- Same pattern**

Apply identical changes to `create_us_trading_scenario_agent()` with US-specific adaptations:
- Replace KOSPI references with S&P 500
- Replace kospi_kosdaq tool references with yahoo_finance tool
- Use US sector taxonomy
- US-specific regime criteria (S&P 500 + VIX based)
- US min_score (currently `강세장: 5, 약세장: 6`) updated to:
  ```
  "min_score": 시장 환경에 따른 최소 진입 요구 점수 (강한 강세장: 4, 보통 강세장: 5, 횡보장: 5, 보통 약세장: 6, 강한 약세장: 7),
  ```

### Verification Steps (Phase 1)
1. Verify KR buy agent prompt compiles without syntax errors (instantiate agent, check instruction string)
2. Verify US buy agent prompt compiles without syntax errors
3. Manual review: read the full prompt to ensure no contradictions with existing rules
4. Verify that with NO macro context in the report, the buy agent falls back to its 5-tier regime check using index data alone (backward compatible)
5. Create a test report with mock macro context including leading sector info, and verify the prompt correctly instructs the agent to apply sector bonus
6. **MEASURE**: Run pipeline daily for 1-2 weeks and track entry rate. Target: 10-30% of screened stocks entered.

### Integration Impact
- The buy agent already receives the full report text which includes "4. 시장 분석" section
- No changes needed to how the report is passed to the buy agent
- Sell agent (`create_sell_decision_agent`): NOT modified -- sell decisions are based on price action, not macro entry logic
- Tracking agent: NOT modified -- it only passes the report to the buy agent
- `cores/main.py`: Re-exports `analyze_stock` -- compatible, no change needed since prompt changes are internal to the agent
- `examples/streamlit/app_modern.py`: Calls `analyze_stock` -- compatible, no change needed

### Rollback Strategy
- Revert `cores/agents/trading_agents.py` and `prism-us/cores/agents/trading_agents.py` to previous versions
- Since changes are prompt-only, no data format changes need reverting

---

## Phase 2: Macro Intelligence Agent (NEW)

### Objective
Create a new agent that runs BEFORE the trigger batch to produce structured macro-economic intelligence. This agent uses perplexity to gather current macro data and outputs a fixed JSON schema.

### Files to Create

**1. `cores/agents/macro_intelligence_agent.py` (NEW - KR)**

```
Purpose: Define create_macro_intelligence_agent() for Korean market
MCP servers: ["perplexity", "kospi_kosdaq"]
```

Agent prompt must instruct:
- Use `perplexity_ask` to search for current Korean macro conditions:
  - Query: "[today's date] 한국 증시 거시경제 동향, KOSPI KOSDAQ 업종별 동향, 주도 섹터, 리스크 이벤트, 지정학적 리스크 종합분석"
- Use `kospi_kosdaq-get_index_ohlcv` for KOSPI (ticker "1001") and KOSDAQ ("2001") recent 20 trading days
- Classify market regime based on index data + macro context (not just 20-day MA)
- **Anti-bias rule in prompt**: "If KOSPI is below 20-day MA AND 2-week change is < -2%, regime CANNOT be bull regardless of news narrative tone. Index data is PRIMARY evidence; perplexity narrative is SECONDARY confirmation."
- Identify leading and lagging sectors from perplexity response
- Output MUST be valid JSON matching this schema:

```json
{
  "analysis_date": "YYYYMMDD",
  "market": "KR",
  "market_regime": "strong_bull|moderate_bull|sideways|moderate_bear|strong_bear",
  "regime_confidence": 0.0-1.0,
  "regime_rationale": "Brief explanation of why this regime was determined",
  "simple_ma_regime": "bull|bear|sideways",
  "index_summary": {
    "kospi_20d_trend": "up|down|sideways",
    "kospi_vs_20d_ma": "above|below",
    "kospi_2w_change_pct": 0.0,
    "kosdaq_20d_trend": "up|down|sideways"
  },
  "leading_sectors": [
    {"sector": "반도체", "reason": "AI 수요 급증", "confidence": 0.8},
    {"sector": "방산", "reason": "지정학적 긴장 고조", "confidence": 0.7}
  ],
  "lagging_sectors": [
    {"sector": "건설", "reason": "금리 인상 영향", "confidence": 0.6}
  ],
  "risk_events": [
    {"event": "미중 무역갈등 심화", "impact": "negative", "severity": "high", "affected_sectors": ["반도체", "자동차"]}
  ],
  "beneficiary_themes": [
    {"theme": "AI 인프라 투자 확대", "beneficiary_sectors": ["반도체", "소프트웨어"], "duration": "medium_term"}
  ],
  "recommended_max_holdings": 8,
  "cash_ratio_suggestion": 20
}
```

Note: `simple_ma_regime` field is for bias detection (Pre-Mortem Failure 4). The agent computes a simple 20-day MA regime independently and reports it alongside its LLM-interpreted regime.

Sector taxonomy for KR (fixed list to map to):
```
반도체, 자동차, 배터리/2차전지, 바이오/제약, 건설, 철강, 화학, 금융,
유통/소비재, IT/소프트웨어, 엔터테인먼트, 조선, 방산, 에너지, 통신, 운송/물류, 기타
```

**2. `prism-us/cores/agents/macro_intelligence_agent.py` (NEW - US)**

```
Purpose: Define create_us_macro_intelligence_agent() for US market
MCP servers: ["perplexity", "yahoo_finance"]
```

Agent prompt differences from KR:
- Use `perplexity_ask` with English query: "[today's date] US stock market macro trends, sector rotation, leading lagging sectors, risk events, geopolitical risks comprehensive analysis"
- Use `yahoo_finance-get_historical_stock_prices` for S&P 500 (^GSPC), NASDAQ (^IXIC), VIX (^VIX) recent 20 days
- Market regime based on S&P 500 + VIX level + macro context
- Same anti-bias rule as KR version
- Regime logic:
  - `strong_bull`: S&P above 20d MA + 4-week change > +3% + VIX < 18
  - `moderate_bull`: S&P above 20d MA + positive trend
  - `sideways`: S&P near 20d MA, mixed signals
  - `moderate_bear`: S&P below 20d MA + negative trend
  - `strong_bear`: S&P below 20d MA + 4-week change < -5% + VIX > 25

Output schema (same structure, market="US", same `simple_ma_regime` field for bias detection).

Sector taxonomy for US (GICS-based):
```
Technology, Healthcare, Financials, Consumer Discretionary, Consumer Staples,
Energy, Industrials, Materials, Real Estate, Utilities, Communication Services,
Defense/Aerospace, Semiconductors, Software, Biotechnology, EV/Clean Energy
```

### Verification Steps (Phase 2)
1. Run macro intelligence agent standalone for KR -- verify JSON output matches schema
2. Run macro intelligence agent standalone for US -- verify JSON output matches schema
3. Verify graceful handling when perplexity is unavailable (agent should return a default "unknown" regime with confidence 0)
4. Verify execution time is under 60 seconds for each market
5. Verify `simple_ma_regime` field is populated and matches expected value from index data

### Integration Impact
- No existing code is modified in this phase
- New files only: `cores/agents/macro_intelligence_agent.py`, `prism-us/cores/agents/macro_intelligence_agent.py`

### Rollback Strategy
- Delete the two new files. No other code was changed.

---

## Phase 3: Trigger Batch Enhancement

### Objective
Modify the trigger batch to accept and use macro intelligence output for regime-based screening strategy and sector weighting.

### KR Sector Mapping Source (DECIDED)

**CONFIRMED**: `cap_df` from `get_market_cap_by_ticker()` only has `시가총액` column. `enhance_dataframe()` adds `stock_name` but NOT sector. A sector source must be added.

**Decision: Use `pykrx.stock.get_index_portfolio_deposit_file()` to map tickers to KOSPI sub-indices.**

Approach:
```python
from pykrx import stock as pykrx_stock

def get_kr_sector_map(trade_date: str) -> dict:
    """
    Map KR tickers to sectors using KOSPI sub-index composition.

    Uses pykrx.stock.get_index_portfolio_deposit_file() to get which tickers
    belong to which KOSPI sector index (e.g., KOSPI 반도체, KOSPI 자동차).

    Returns:
        dict: {ticker: sector_name} e.g. {"005930": "반도체", "000270": "자동차"}
    """
    KOSPI_SECTOR_INDICES = {
        "1024": "반도체",
        "1028": "자동차",
        "1033": "철강",
        "1027": "화학",
        "1026": "건설",
        "1034": "금융",
        "1025": "에너지",
        "1035": "IT/소프트웨어",
        "1029": "통신",
        "1030": "유통/소비재",
        "1031": "운송/물류",
        "1032": "바이오/제약",
    }
    sector_map = {}
    for index_code, sector_name in KOSPI_SECTOR_INDICES.items():
        try:
            tickers = pykrx_stock.get_index_portfolio_deposit_file(index_code, trade_date)
            for ticker in tickers:
                sector_map[ticker] = sector_name
        except Exception:
            continue

    # Tickers not in any sub-index get "기타"
    return sector_map
```

**Fallback**: If `get_index_portfolio_deposit_file()` fails (holiday, API issue), use LLM-based sector inference from company name as last resort -- but this should be extremely rare since pykrx is a local data source, not an API call.

**Why not hardcoded mapping**: Ticker-to-sector mapping changes over time (IPOs, delistings, sector reclassifications). Using pykrx sub-index data keeps it current automatically.

### US Sector Mapping Source (DECIDED)

**Decision: Use `yfinance.Ticker(ticker).info['sector']` per-ticker, cached for the pipeline run.**

```python
import yfinance as yf

def get_us_sector_map(tickers: list) -> dict:
    """
    Map US tickers to GICS sectors using yfinance.

    Called once per pipeline run with the list of candidate tickers from trigger batch.
    Results are cached in-memory for the run duration.

    Returns:
        dict: {ticker: sector_name} e.g. {"AAPL": "Technology", "JNJ": "Healthcare"}
    """
    sector_map = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            sector_map[ticker] = info.get("sector", "Other")
        except Exception:
            sector_map[ticker] = "Other"
    return sector_map
```

**Performance note**: yfinance per-ticker calls add ~0.5s each. For typical trigger batch output of 10-20 candidates, this adds 5-10 seconds. Acceptable since trigger batch already takes 30+ seconds. Cache the result so it is not called again during the same pipeline run.

### Files to Modify

**1. `trigger_batch.py` (KR) -- Modify `select_final_tickers()` and `run_batch()`**

Changes to `run_batch()` (line 948):
- Add optional parameter `macro_context: dict = None`
- Pass `macro_context` to `select_final_tickers()`
- No changes to individual trigger functions (volume surge, gap up, etc.) -- they remain pure OHLCV

Changes to `select_final_tickers()`:
- Add parameter `macro_context: dict = None`
- Add regime-based selection count:
  ```python
  # Current: always selects max 3
  # New: regime-based selection count
  if macro_context:
      regime = macro_context.get("market_regime", "sideways")
      if regime in ("strong_bull", "moderate_bull"):
          max_selections = 5  # More aggressive in bull
      elif regime == "sideways":
          max_selections = 3  # Current behavior
      else:  # bear
          max_selections = 2  # More conservative
  else:
      max_selections = 3  # Default (backward compatible)
  ```
- Replace hardcoded `3` in selection logic (lines 909, 925, 937) with `max_selections`
- Add sector scoring bonus when macro_context is provided:
  ```python
  # After collecting all trigger_candidates, before final selection
  if macro_context:
      leading = {s["sector"] for s in macro_context.get("leading_sectors", [])}
      lagging = {s["sector"] for s in macro_context.get("lagging_sectors", [])}
      sector_map = get_kr_sector_map(trade_date)

      for name, df in trigger_candidates.items():
          if not df.empty and score_column in df.columns:
              for ticker in df.index:
                  stock_sector = sector_map.get(ticker, "기타")
                  if stock_sector in leading:
                      df.loc[ticker, score_column] += 0.1  # +10% bonus
                  elif stock_sector in lagging:
                      df.loc[ticker, score_column] -= 0.1  # -10% penalty
  ```

**2. `prism-us/us_trigger_batch.py` (US) -- Same pattern as KR**

Same changes as KR version but adapted for US:
- `run_batch()` accepts `macro_context`
- `select_final_tickers()` uses macro_context for regime-based count and sector scoring
- US sector mapping: use `get_us_sector_map()` with yfinance per-ticker lookup, cached for the run

### Key Constraint: Must NOT break existing trigger types
- All 6 trigger functions remain unchanged
- `select_final_tickers()` only changes AFTER candidates are collected
- When `macro_context is None`, behavior is 100% identical to current code

### Verification Steps (Phase 3)
1. Run `python trigger_batch.py morning INFO` without macro_context -- verify identical output to current behavior
2. Run with mock macro_context `{"market_regime": "strong_bull", "leading_sectors": [...]}` -- verify max_selections increases to 5
3. Run with mock macro_context `{"market_regime": "strong_bear"}` -- verify max_selections decreases to 2
4. Verify sector bonus applied: create test where a leading-sector stock ranks higher than before
5. Verify `get_kr_sector_map()` returns valid mapping for at least 200 tickers
6. Verify `get_us_sector_map()` returns valid GICS sectors for top 20 US tickers
7. Same tests for US trigger batch
8. Verify all 6 KR trigger types still produce valid output
9. Verify all US trigger types still produce valid output

### Integration Impact
- `stock_analysis_orchestrator.py` will need to pass `macro_context` to `run_batch()` (done in Phase 4)
- `us_stock_analysis_orchestrator.py` same
- No DB schema changes
- No changes to: trading_agents, sell agents, tracking agents, telegram agents, PDF converter

### Rollback Strategy
- Revert changes to `trigger_batch.py` and `prism-us/us_trigger_batch.py`
- Since `macro_context=None` is the default, partial rollback is safe -- just stop passing the argument

---

## Phase 4: Pipeline Integration

### Objective
Wire the macro intelligence agent into the orchestrator pipeline so its output flows to both the trigger batch and the buy agent (via the report).

### Files to Modify

**1. `stock_analysis_orchestrator.py` (KR Orchestrator)**

Add new method `run_macro_intelligence()`:
```python
async def run_macro_intelligence(self, language: str = "ko") -> dict:
    """
    Run macro intelligence agent to get market regime and sector context.

    Returns:
        dict: Macro intelligence JSON or empty dict on failure
    """
    logger.info("Starting macro intelligence analysis")
    try:
        from cores.agents.macro_intelligence_agent import create_macro_intelligence_agent
        from mcp_agent.app import MCPApp
        from mcp_agent.agents.agent import Agent
        from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

        reference_date = datetime.now().strftime("%Y%m%d")
        agent = create_macro_intelligence_agent(reference_date, language=language)

        # Create own MCPApp context (like cores/analysis.py:40)
        app = MCPApp(name="macro_intelligence")

        async with app.run() as macro_app:
            llm = await agent.attach_llm(OpenAIAugmentedLLM)
            response = await llm.generate_str(
                message=f"Analyze current Korean market macro conditions for {reference_date}.",
                request_params=RequestParams(model="gpt-5-mini", maxTokens=4000)
            )

        # Parse JSON from response
        from cores.utils import parse_llm_json
        macro_context = parse_llm_json(response)

        if macro_context and "market_regime" in macro_context:
            # Log both regimes for bias detection (Pre-Mortem Failure 4)
            simple_regime = macro_context.get("simple_ma_regime", "unknown")
            logger.info(f"Macro intelligence: regime={macro_context['market_regime']}, "
                       f"simple_ma_regime={simple_regime}, "
                       f"leading={[s['sector'] for s in macro_context.get('leading_sectors', [])]}")
            self.macro_context = macro_context
            return macro_context
        else:
            logger.warning("Macro intelligence returned invalid format, using defaults")
            return {}

    except Exception as e:
        logger.error(f"Macro intelligence failed: {e}")
        return {}
```

**IMPORTANT**: Uses `MCPApp(name="macro_intelligence")` -- its own app context, NOT referencing an undefined `app` variable.

Modify `run_full_pipeline()` (around line 835):
```python
async def run_full_pipeline(self, mode, language: str = "ko"):
    try:
        # 0. NEW: Run macro intelligence BEFORE trigger batch
        macro_context = await self.run_macro_intelligence(language)

        # 1. Execute trigger batch - pass macro_context
        results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
        tickers = await self.run_trigger_batch(mode, macro_context=macro_context)

        # ... rest of pipeline unchanged ...
```

Modify `run_trigger_batch()` to accept and pass `macro_context`:
```python
async def run_trigger_batch(self, mode, macro_context: dict = None):
    # ...existing code...
    from trigger_batch import run_batch
    results = await loop.run_in_executor(
        None,
        lambda: run_batch(mode, "INFO", results_file, macro_context=macro_context)
    )
    # ...rest unchanged...
```

**Injecting macro context into the report for the buy agent:**

**Decision: Pass macro_context through analyze_stock() parameter (Approach B from v1.0)**

Modify `cores/analysis.py` `analyze_stock()` function signature:
```python
async def analyze_stock(company_code: str = "000660", company_name: str = "SK하이닉스",
                       reference_date: str = None, language: str = "ko",
                       macro_context: dict = None):
```

At the end where the final report is assembled, append the macro intelligence summary to the market section:
```python
# After getting market_index_analysis report
market_section = section_reports.get("market_index_analysis", "Analysis not available")

# Append structured macro intelligence summary if available
if macro_context and "market_regime" in macro_context:
    mc = macro_context
    macro_summary = f"""

#### 거시경제 인텔리전스 요약
- **시장 체제**: {mc.get('market_regime', 'unknown')} (신뢰도: {mc.get('regime_confidence', 0):.0%})
- **체제 근거**: {mc.get('regime_rationale', 'N/A')}
- **주도 섹터**: {', '.join(s['sector'] + '(' + s['reason'] + ')' for s in mc.get('leading_sectors', []))}
- **소외 섹터**: {', '.join(s['sector'] + '(' + s['reason'] + ')' for s in mc.get('lagging_sectors', []))}
- **리스크 이벤트**: {'; '.join(e['event'] + ' [' + e['severity'] + ']' for e in mc.get('risk_events', []))}
- **수혜 테마**: {', '.join(t['theme'] for t in mc.get('beneficiary_themes', []))}
- **권장 최대 보유**: {mc.get('recommended_max_holdings', 'N/A')}개
- **권장 현금 비율**: {mc.get('cash_ratio_suggestion', 'N/A')}%
"""
    market_section += macro_summary

final_report += market_section
```

Then in `stock_analysis_orchestrator.py` `generate_reports()` (line ~997), pass `macro_context`:
```python
report = await analyze_stock(
    company_code=ticker,
    company_name=company_name,
    reference_date=reference_date,
    language=language,
    macro_context=self.macro_context  # NEW
)
```

**Note on callers of `analyze_stock()`:**
- `stock_analysis_orchestrator.py` (line 993): Imports via `from cores.main import analyze_stock`. Will pass `macro_context` as new kwarg.
- `cores/main.py` (line 8): Re-exports `analyze_stock`. Compatible -- `macro_context` is optional with default `None`, no change needed to `cores/main.py`.
- `examples/streamlit/app_modern.py` (line 612): Calls `analyze_stock` without `macro_context`. Compatible -- default `None` means no macro context, existing behavior preserved.
- `report_generator.py` (line 521-526): Imports `analyze_stock` directly from `cores.analysis` and calls it in a subprocess template string. Compatible -- `macro_context` is optional with default `None`. No change needed; report_generator's subprocess calls will simply not have macro context (acceptable for on-demand single-stock reports).

Same pattern for US:

**2. `prism-us/us_stock_analysis_orchestrator.py` (US Orchestrator)**

Apply identical pattern:
- Add `run_macro_intelligence()` method using US macro agent, with its own `MCPApp(name="us_macro_intelligence")`
- Call it before `run_trigger_batch()` in `run_full_pipeline()`
- Pass `macro_context` to `run_trigger_batch()`
- Pass `macro_context` to `generate_reports()` -> `analyze_us_stock()`

**3. `prism-us/cores/us_analysis.py` (US Analysis)**

Modify `analyze_us_stock()` to accept `macro_context` parameter and append macro summary to market section.

### Data Flow Summary

```
Orchestrator
  |
  +--> [NEW] run_macro_intelligence() --> macro_context (dict)
  |         Uses own MCPApp(name="macro_intelligence")
  |
  +--> run_trigger_batch(macro_context) --> trigger_batch.run_batch(macro_context)
  |     |                                        |
  |     |                                        +--> select_final_tickers(macro_context)
  |     |                                              - Regime-based max_selections
  |     |                                              - Sector bonus/penalty on final_score
  |     |
  |     +--> selected tickers
  |
  +--> generate_reports(tickers)
  |     |
  |     +--> from cores.main import analyze_stock  (line 993)
  |     +--> analyze_stock(..., macro_context=self.macro_context)
  |           |
  |           +--> [existing] market_index_analysis agent generates section 4
  |           |    (separate perplexity call -- stock-specific market context)
  |           +--> [NEW] Append macro_context summary to section 4
  |           +--> Final report assembled with macro context embedded
  |
  +--> convert_to_pdf(reports)
  |
  +--> send_telegram(pdfs)
  |
  +--> tracking_agent.run(pdfs)
        |
        +--> read report content (pdf_to_markdown_text)
        +--> pass to trading_scenario_agent (buy agent)
              |
              +--> Agent reads section 4 with macro intelligence summary
              +--> Uses regime from macro summary for min_score (Phase 1 prompt)
              +--> Applies sector bonus/penalty per Phase 1 prompt
              +--> Makes buy/no-buy decision with macro context
```

### Verification Steps (Phase 4)
1. Run KR orchestrator with `--no-telegram` -- verify macro intelligence runs before trigger batch
2. Verify `run_macro_intelligence()` creates its own `MCPApp` context (no undefined `app` reference)
3. Verify macro_context is passed to trigger batch (add debug logging)
4. Verify generated reports contain "거시경제 인텔리전스 요약" section in section 4
5. Verify tracking agent receives report with macro context (check report content in logs)
6. Run US orchestrator with `--no-telegram` -- same verification
7. Verify caching: macro intelligence runs once per pipeline, not per stock
8. Test graceful degradation: simulate macro agent failure, verify pipeline continues with empty macro_context
9. Verify `cores/main.py`, `examples/streamlit/app_modern.py`, and `report_generator.py` all work without passing macro_context (backward compatibility)

### Integration Impact
- `cores/analysis.py`: Modified to accept `macro_context` param and append to report
- `prism-us/cores/us_analysis.py`: Same modification
- `stock_analysis_orchestrator.py`: New method + modified pipeline
- `prism-us/us_stock_analysis_orchestrator.py`: Same modifications
- `cores/main.py`: No change needed (re-exports `analyze_stock`, `macro_context` has default `None`)
- `examples/streamlit/app_modern.py`: No change needed (calls without `macro_context`, gets default `None`)
- `report_generator.py`: No change needed (calls `analyze_stock` without `macro_context`, gets default `None`)
- NOT modified: sell agents, tracking agents (they just read the report), telegram agents, PDF converter, DB schema

### Rollback Strategy
- Revert orchestrator files to remove macro intelligence call
- Revert analysis.py files to remove macro_context parameter
- Pipeline falls back to existing behavior (no macro context)

---

## Phase 5: Verification & QA

### Objective
Comprehensive verification that all phases work together correctly and existing functionality is not broken.

### 5.1 Unit-Level Verification

| Test | File | What to Verify | Pass Criteria |
|------|------|----------------|---------------|
| Macro agent JSON schema | `cores/agents/macro_intelligence_agent.py` | Output matches schema incl. `simple_ma_regime` | All required fields present, correct types |
| Macro agent KR sector taxonomy | Same | Sectors in output match fixed list | No unknown sectors |
| Macro agent US schema | `prism-us/cores/agents/macro_intelligence_agent.py` | Output matches schema | All required fields present |
| KR sector map | `trigger_batch.py` | `get_kr_sector_map()` returns valid mapping | >= 200 tickers mapped |
| US sector map | `prism-us/us_trigger_batch.py` | `get_us_sector_map()` returns valid GICS | All tickers have sector |
| Trigger batch backward compat | `trigger_batch.py` | `run_batch()` without macro_context | Output identical to current |
| Trigger batch regime selection | `trigger_batch.py` | `select_final_tickers()` with bull context | max_selections = 5 |
| Trigger batch bear selection | `trigger_batch.py` | `select_final_tickers()` with bear context | max_selections = 2 |
| Trigger batch sector bonus | `trigger_batch.py` | Leading sector stock score increases | final_score increases by 0.1 |
| US trigger batch same tests | `prism-us/us_trigger_batch.py` | All above tests for US | Same criteria |
| Buy agent prompt syntax | `cores/agents/trading_agents.py` | Agent instantiation succeeds | No errors |
| US buy agent prompt syntax | `prism-us/cores/agents/trading_agents.py` | Agent instantiation succeeds | No errors |
| Bias detection field | Macro agent | `simple_ma_regime` populated | Matches expected from index data |

### 5.2 Integration Verification

| Test | Components | What to Verify | Pass Criteria |
|------|-----------|----------------|---------------|
| Macro -> Trigger flow | Macro agent + trigger_batch | Macro output consumed by trigger | Trigger logs show regime and sector info |
| Macro -> Report flow | Macro agent + analysis.py | Report section 4 includes macro summary | "거시경제 인텔리전스 요약" present in report |
| Report -> Buy agent flow | analysis.py + tracking_agent + trading_agents | Buy agent receives macro context in report | Buy agent response references sector context |
| Cache coherence | Orchestrator | Macro intelligence generated once, reused | Only one macro agent call in logs per pipeline run |
| Perplexity dedup | Macro agent + market_index_agent | Both make separate calls | Different queries in logs, macro agent cached |
| US full integration | US pipeline | Same as above for US market | Same criteria |
| Backward compat: main.py | `cores/main.py` | Re-export works without macro_context | `analyze_stock()` callable with original signature |
| Backward compat: streamlit | `examples/streamlit/app_modern.py` | App works without macro_context | No errors |
| Backward compat: report_gen | `report_generator.py` | Subprocess works without macro_context | Reports generated normally |

### 5.3 Regression Verification

| Test | What to Verify | Pass Criteria |
|------|----------------|---------------|
| Existing trigger types fire | All 6 KR triggers still produce candidates | Non-empty DataFrames when market conditions match |
| US triggers fire | All US triggers still produce candidates | Same |
| Sell agent unaffected | `create_sell_decision_agent()` | Prompt unchanged, behavior unchanged |
| Tracking agent unaffected | `stock_tracking_agent.py` / `stock_tracking_enhanced_agent.py` | Report passed to buy agent same as before (just with more content in section 4) |
| Telegram agent unaffected | Telegram message generation | Messages generated correctly |
| PDF converter unaffected | `pdf_converter.py` | PDFs generated correctly |
| DB schema unchanged | `stock_tracking_db.sqlite` | No new tables or columns needed |

### 5.4 Impact Check: Components NOT Modified

These files/components should be verified as NOT impacted:

| Component | File(s) | Expected: No Changes |
|-----------|---------|---------------------|
| Sell decision agent | `cores/agents/trading_agents.py::create_sell_decision_agent` | Prompt unchanged |
| US sell decision agent | `prism-us/cores/agents/trading_agents.py::create_us_sell_decision_agent` | Prompt unchanged |
| Enhanced tracking agent | `stock_tracking_enhanced_agent.py` | Only reads report, no logic changes |
| US tracking agent | `prism-us/us_stock_tracking_agent.py` | Same |
| Telegram summary agent | `cores/agents/telegram_summary_agent.py` | Not involved in buy decisions |
| Report generation agents | `cores/agents/price_volume_agents.py`, etc. | Individual section agents unchanged |
| PDF converter | `pdf_converter.py` | Just converts markdown to PDF |
| Trading module | `trading/` | Executes trades, not decisions |
| DB schema | All tables | No changes needed |
| KRX data client | `krx_data_client.py` | Data layer unchanged |
| Weekly insight report | `weekly_insight_report.py` | Independent pipeline |
| cores/main.py | Re-export of analyze_stock | No change needed, macro_context defaults to None |
| examples/streamlit/app_modern.py | Streamlit dashboard | No change needed, calls without macro_context |
| report_generator.py | Subprocess report generation | No change needed, calls without macro_context |

### 5.5 E2E Test Plan

**Test 1: Phase 1 Only (Ship First)**
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

Verification checklist (Phase 1 only -- no macro agent yet):
- [ ] Buy agent uses 5-tier regime classification from index data
- [ ] min_score reflects the new regime tiers
- [ ] Entry rate tracked over 1-2 weeks
- [ ] No regression in existing pipeline behavior

**Test 2: KR Full Pipeline (After Phases 2-4)**
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

Verification checklist:
- [ ] Macro intelligence agent executed (check logs for "Starting macro intelligence analysis")
- [ ] `run_macro_intelligence()` creates own MCPApp (no undefined `app` error)
- [ ] Macro intelligence JSON logged (check for "market_regime=")
- [ ] `simple_ma_regime` logged alongside `market_regime` for bias detection
- [ ] Trigger batch received macro_context (check for regime-based selection count in logs)
- [ ] Reports generated with macro summary in section 4
- [ ] Tracking agent ran buy analysis
- [ ] Buy agent referenced sector context in its analysis (check for "주도 섹터" or "소외 섹터" in buy agent output)
- [ ] Buy agent used macro intelligence regime for min_score (not just index data)
- [ ] No errors in full pipeline execution

**Test 3: US Full Pipeline**
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

Same verification checklist adapted for US market.

**Test 4: Graceful Degradation**
- Temporarily break perplexity API key
- Run KR pipeline -- verify it completes with empty macro_context
- Verify trigger batch uses default max_selections=3
- Verify reports are generated without macro summary
- Verify buy agent falls back to 5-tier regime from index data alone

**Test 5: Backward Compatibility**
- Run `python cores/main.py` (uses original `analyze_stock` signature) -- verify no errors
- Run streamlit app -- verify dashboard works without macro_context
- Run `report_generator.py` subprocess -- verify reports generated normally

### 5.6 Observability Additions

Add the following logging at INFO level:

| Location | Log Message | Purpose |
|----------|-------------|---------|
| Orchestrator after macro agent | `Macro intelligence: regime={}, simple_ma_regime={}, leading={}, lagging={}` | Track regime + bias detection |
| Trigger batch | `Regime-based selection: max_selections={} (regime={})` | Verify regime affects selection |
| Trigger batch | `Sector adjustment for {ticker}: {adjustment} (sector={})` | Track sector scoring |
| Trigger batch | `KR sector map loaded: {} tickers mapped` | Verify sector data availability |
| Analysis.py | `Macro intelligence summary appended to report` | Verify data flow |
| Tracking agent (optional) | `Buy agent input includes macro context: {true/false}` | Verify end-to-end |

---

## Implementation Order & Dependencies

```
Phase 1 (Prompt Enhancement) ──> SHIP & MEASURE 1-2 WEEKS
                                        |
                                        v
                               Phase 2 (Macro Agent) ──────────────────┐
                               Phase 3 (Trigger Enhancement) ─────────┤  (2 & 3 can be parallel)
                                                                       v
                                                   Phase 4 (Pipeline Integration) ──> Phase 5 (QA)
```

- **Phase 1 is independent and ships first** -- prompt-only changes, zero infrastructure
- Phase 2 and Phase 3 are independent and can be developed in parallel (Phase 3 needs Phase 2's JSON schema definition but not the running agent)
- Phase 4 depends on Phases 2 and 3 (wires everything together)
- Phase 5 depends on Phase 4 (tests the full integration)

---

## Known Limitations & Future Improvements

### Sell Agent Macro Awareness (Future - Not Required for v1)

**Information Asymmetry**: After this plan is implemented, buy decisions will use macro context but sell decisions will not. The sell agent (`create_sell_decision_agent`) makes decisions based on price action (stop-loss, take-profit targets) without considering whether macro conditions have changed since entry.

**Why acceptable for v1**: Selling is primarily price-action based (stop-loss triggers, resistance levels). The macro regime at time of entry is less relevant than current price behavior relative to entry price. Adding macro context to sell would require tracking the regime at entry time and comparing to current regime -- a separate feature.

**Future improvement**: Track `entry_regime` in `stock_holdings` table. When current regime shifts significantly from entry regime (e.g., entered in `strong_bull`, now `moderate_bear`), the sell agent could tighten stop-loss or suggest early exit. This should be a separate plan.

---

## File Change Summary

### New Files (2)
| File | Purpose | Phase |
|------|---------|-------|
| `cores/agents/macro_intelligence_agent.py` | KR macro intelligence agent | Phase 2 |
| `prism-us/cores/agents/macro_intelligence_agent.py` | US macro intelligence agent | Phase 2 |

### Modified Files (8)
| File | Changes | Phase |
|------|---------|-------|
| `cores/agents/trading_agents.py` | Buy agent prompt: multi-regime, sector scoring, macro risk checkpoint | Phase 1 |
| `prism-us/cores/agents/trading_agents.py` | US buy agent prompt: same changes | Phase 1 |
| `trigger_batch.py` | `run_batch()` and `select_final_tickers()` accept macro_context; add `get_kr_sector_map()` | Phase 3 |
| `prism-us/us_trigger_batch.py` | Same changes for US; add `get_us_sector_map()` | Phase 3 |
| `stock_analysis_orchestrator.py` | New `run_macro_intelligence()` with own MCPApp, pipeline wiring | Phase 4 |
| `prism-us/us_stock_analysis_orchestrator.py` | Same changes for US | Phase 4 |
| `cores/analysis.py` | Accept `macro_context` param (default None), append to report | Phase 4 |
| `prism-us/cores/us_analysis.py` | Same changes for US | Phase 4 |

### Unchanged Files (confirmed no impact)
| File | Reason |
|------|--------|
| `cores/main.py` | Re-exports `analyze_stock` -- `macro_context` has default `None`, fully compatible |
| `examples/streamlit/app_modern.py` | Calls `analyze_stock` without `macro_context` -- default `None` works |
| `report_generator.py` | Calls `analyze_stock` in subprocess without `macro_context` -- default `None` works |
| All trading modules, sell agents, tracking agents, telegram agents, PDF converter, DB schema, weekly reports, data clients | No changes needed |

---

## ADR: Architectural Decision Record

### Decision
Implement macro-economic intelligence as a standalone pre-pipeline agent that feeds structured context to both the trigger batch (for sector-weighted screening) and the buy agent (for regime-aware scoring). Ship prompt-only changes first to measure impact before building infrastructure.

### Decision Drivers
1. Zero entry rate caused by binary bull/bear regime and missing macro context
2. Need for sector-aware scoring without breaking existing OHLCV-based triggers
3. Desire for minimal disruption -- additive changes with graceful degradation

### Alternatives Considered
- **Option B (Embed in Market Analysis Agent)**: Rejected -- market analysis runs AFTER trigger batch; reordering pipeline + mixing structured/free-text output creates fragility
- **Option C (Hybrid non-LLM pre-scan)**: Rejected -- cannot identify sector themes or geopolitical context without LLM interpretation; fails to address Decision Driver #3

### Why Chosen
Option A (Standalone Agent) is the only architecture that:
1. Provides macro context BEFORE stock screening (trigger batch needs it for sector weighting)
2. Keeps concerns cleanly separated (macro analysis vs. stock-specific market analysis)
3. Produces cacheable, testable structured output
4. Can be rolled back independently without affecting any existing component

The phased shipping strategy (prompt changes first) reduces risk by validating the core hypothesis (min_score is the bigger lever) before investing in infrastructure.

### Consequences
- **Positive**: Buy agent gains multi-regime awareness, sector context; trigger batch gains theme-aware screening; pipeline latency increase bounded by macro agent cache
- **Negative**: Two perplexity calls per pipeline (macro agent + market index agent); sell agent has information asymmetry (buy uses macro, sell does not)
- **Neutral**: `analyze_stock()` gains optional parameter but all existing callers work unchanged

### Follow-ups
1. Monitor regime bias (Failure 4) for 2+ weeks after launch
2. Evaluate sell agent macro awareness as separate feature
3. Consider caching macro agent output across pipeline runs (e.g., 4-hour TTL) to reduce API costs
4. Evaluate if sector bonus should increase to +/-2 based on measured entry rate data

---

## Estimated Complexity

- **Phase 1 (Prompt Enhancement)**: LOW-MEDIUM -- Prompt engineering only, careful not to introduce contradictions. **Ship first.**
- **Phase 2 (Macro Agent)**: MEDIUM -- New agent files with structured prompts, JSON schema validation
- **Phase 3 (Trigger Enhancement)**: MEDIUM -- Modify selection logic, add sector mapping lookup (pykrx for KR, yfinance for US)
- **Phase 4 (Pipeline Integration)**: MEDIUM -- Pipeline wiring, data flow through multiple components, own MCPApp context
- **Phase 5 (Verification & QA)**: LOW -- Testing and verification

**Total**: MEDIUM-HIGH complexity across 8 modified + 2 new files
**Estimated development time**: Phase 1: 1 session. Phases 2-5: 2-3 sessions. Plus 1-2 weeks measurement between Phase 1 and Phases 2-5.

---

## Appendix: Sector Taxonomy Reference

### KR Sectors (KOSPI-based)
```
반도체, 자동차, 배터리/2차전지, 바이오/제약, 건설, 철강, 화학, 금융,
유통/소비재, IT/소프트웨어, 엔터테인먼트, 조선, 방산, 에너지, 통신, 운송/물류, 기타
```

### US Sectors (GICS-based)
```
Technology, Healthcare, Financials, Consumer Discretionary, Consumer Staples,
Energy, Industrials, Materials, Real Estate, Utilities, Communication Services,
Defense/Aerospace, Semiconductors, Software, Biotechnology, EV/Clean Energy
```

### Market Regime Scale
```
strong_bull     -> Most aggressive: max selections, sector bonuses amplified, lowest min_score
moderate_bull   -> Moderately aggressive: above-average selections
sideways        -> Neutral: current default behavior
moderate_bear   -> Conservative: reduced selections, stricter scoring
strong_bear     -> Most conservative: minimum selections, highest entry bar
```

### KR min_score by Regime
```
strong_bull:    5  (current bull=6, reduced by 1)
moderate_bull:  6  (current bull=6, unchanged)
sideways:       6  (current bear=7, reduced by 1 -- this is the key fix)
moderate_bear:  7  (current bear=7, unchanged)
strong_bear:    8  (new tier, stricter than current)
```

### US min_score by Regime
```
strong_bull:    4  (current bull=5, reduced by 1)
moderate_bull:  5  (current bull=5, unchanged)
sideways:       5  (current bear=6, reduced by 1 -- key fix)
moderate_bear:  6  (current bear=6, unchanged)
strong_bear:    7  (new tier, stricter than current)
```
