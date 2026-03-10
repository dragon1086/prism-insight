from mcp_agent.agents.agent import Agent


def create_us_macro_intelligence_agent(reference_date, language="ko", prefetched_data: dict = None):
    """Create macro intelligence agent for US market

    The agent receives pre-computed regime and index data from programmatic prefetch,
    and only calls perplexity for qualitative analysis (sector trends, risk events).

    Args:
        reference_date: Analysis reference date (YYYYMMDD)
        language: Language code ("ko" or "en")
        prefetched_data: Dict with computed_regime, sp500_ohlcv_md, nasdaq_ohlcv_md, vix_ohlcv_md

    Returns:
        Agent: US macro intelligence agent
    """

    # Build context from prefetched data
    regime_context = ""
    index_data_context = ""

    if prefetched_data:
        computed = prefetched_data.get("computed_regime", {})
        if computed:
            regime = computed.get("market_regime", "sideways")
            confidence = computed.get("regime_confidence", 0.5)
            simple_ma = computed.get("simple_ma_regime", "sideways")
            idx = computed.get("index_summary", {})

            regime_context = f"""
## Pre-Computed Market Regime (from actual index data)

The following regime was computed programmatically from S&P 500 / VIX price data:
- **market_regime**: {regime}
- **regime_confidence**: {confidence}
- **simple_ma_regime**: {simple_ma}
- **S&P 500 20d trend**: {idx.get('sp500_20d_trend', 'N/A')}
- **S&P 500 vs 20d MA**: {idx.get('sp500_vs_20d_ma', 'N/A')}
- **S&P 500 4-week change**: {idx.get('sp500_4w_change_pct', 'N/A')}%
- **S&P 500 current**: {idx.get('sp500_current', 'N/A')}
- **S&P 500 20d MA**: {idx.get('sp500_20d_ma', 'N/A')}
- **NASDAQ 20d trend**: {idx.get('nasdaq_20d_trend', 'N/A')}
- **VIX current**: {idx.get('vix_current', 'N/A')}
- **VIX level**: {idx.get('vix_level', 'N/A')}

You MUST use these pre-computed values for market_regime, regime_confidence, simple_ma_regime, and index_summary.
You may adjust regime_confidence (±0.1) based on perplexity analysis, but DO NOT change market_regime unless
perplexity data provides overwhelming contradictory evidence.
"""

        sp500_md = prefetched_data.get("sp500_ohlcv_md", "")
        nasdaq_md = prefetched_data.get("nasdaq_ohlcv_md", "")
        vix_md = prefetched_data.get("vix_ohlcv_md", "")
        if sp500_md or nasdaq_md or vix_md:
            index_data_context = "\n## Pre-fetched Index Data\n\n"
            if sp500_md:
                index_data_context += sp500_md + "\n"
            if nasdaq_md:
                index_data_context += nasdaq_md + "\n"
            if vix_md:
                index_data_context += vix_md + "\n"

    instruction = f"""You are a US stock market macro intelligence analyst.
Follow the instructions below to collect data, then output ONLY valid JSON. Do not include any text outside the JSON.

Analysis date: {reference_date} (YYYYMMDD format)
{regime_context}
{index_data_context}
## Tool Call to Execute

### Perplexity macro search (1 call only)
Use the perplexity_ask tool with the following query:
"{reference_date} US stock market macro trends, sector rotation, leading lagging sectors, risk events, geopolitical risks comprehensive analysis"

---

## Your Task

Based on the perplexity search results AND the pre-computed index data above:
1. Use the pre-computed market_regime and index_summary values as-is
2. Identify leading and lagging sectors from perplexity analysis
3. Identify risk events and beneficiary themes
4. Write a `regime_rationale` explaining the regime judgment
5. Write a `report_prose` section — a well-written 3-5 paragraph narrative summary for inclusion in stock analysis reports

---

## Sector Taxonomy (US GICS-based fixed list)

Use ONLY these sectors for leading_sectors and lagging_sectors:
Technology, Healthcare, Financials, Consumer Discretionary, Consumer Staples,
Energy, Industrials, Materials, Real Estate, Utilities, Communication Services,
Defense/Aerospace, Semiconductors, Software, Biotechnology, EV/Clean Energy

---

## Output JSON Schema (output exactly this structure)

```json
{{{{
  "analysis_date": "YYYYMMDD",
  "market": "US",
  "market_regime": "{prefetched_data.get('computed_regime', {}).get('market_regime', 'sideways') if prefetched_data else 'sideways'}",
  "regime_confidence": {prefetched_data.get('computed_regime', {}).get('regime_confidence', 0.5) if prefetched_data else 0.5},
  "regime_rationale": "Brief explanation of regime judgment (1-2 sentences)",
  "simple_ma_regime": "{prefetched_data.get('computed_regime', {}).get('simple_ma_regime', 'sideways') if prefetched_data else 'sideways'}",
  "index_summary": {_format_us_index_summary(prefetched_data)},
  "leading_sectors": [
    {{"sector": "Semiconductors", "reason": "AI demand surge", "confidence": 0.8}}
  ],
  "lagging_sectors": [
    {{"sector": "Real Estate", "reason": "Rate hike pressure", "confidence": 0.6}}
  ],
  "risk_events": [
    {{"event": "US-China trade tensions", "impact": "negative", "severity": "high", "affected_sectors": ["Semiconductors", "Technology"]}}
  ],
  "beneficiary_themes": [
    {{"theme": "AI infrastructure buildout", "beneficiary_sectors": ["Semiconductors", "Software"], "duration": "medium_term"}}
  ],
  "recommended_max_holdings": 8,
  "cash_ratio_suggestion": 20,
  "report_prose": "Macro analysis report narrative (3-5 paragraphs)"
}}}}
```

## report_prose Guidelines

Write a professional 3-5 paragraph narrative in {"formal Korean (합쇼체)" if language == "ko" else "English"} covering:
1. Current market regime and its rationale
2. Leading sectors and why they are outperforming
3. Key risk events and their potential market impact
4. Recommended investment posture given the current regime

This prose will be directly inserted into stock analysis reports. Make it informative but concise.
{"Use formal polite style (합쇼체): ~습니다, ~있습니다, ~됩니다" if language == "ko" else ""}

## Field Values Guide

- `recommended_max_holdings`: 6~10 based on regime
  - strong_bull: 9~10, moderate_bull: 8~9, sideways: 7~8, moderate_bear: 6~7, strong_bear: 5~6
- `cash_ratio_suggestion`: integer %
  - strong_bull: 10%, moderate_bull: 15~20%, sideways: 20~25%, moderate_bear: 30%, strong_bear: 40%+

## Important Notes

- Execute perplexity tool call before generating JSON
- Output MUST be pure JSON only. No markdown code fences, no explanatory text
- leading_sectors: max 5, descending confidence
- lagging_sectors: max 5
- Anti-hallucination: only include content confirmed from actual data
"""

    return Agent(
        name="us_macro_intelligence_agent",
        instruction=instruction,
        server_names=["perplexity"]
    )


def _format_us_index_summary(prefetched_data: dict) -> str:
    """Format index_summary for JSON schema example."""
    if not prefetched_data:
        return '{"sp500_20d_trend": "sideways", "sp500_vs_20d_ma": "above", "sp500_4w_change_pct": 0.0, "nasdaq_20d_trend": "sideways", "vix_current": 0.0, "vix_level": "moderate"}'

    computed = prefetched_data.get("computed_regime", {})
    idx = computed.get("index_summary", {})
    if not idx:
        return '{"sp500_20d_trend": "sideways", "sp500_vs_20d_ma": "above", "sp500_4w_change_pct": 0.0, "nasdaq_20d_trend": "sideways", "vix_current": 0.0, "vix_level": "moderate"}'

    import json
    output = {
        "sp500_20d_trend": idx.get("sp500_20d_trend", "sideways"),
        "sp500_vs_20d_ma": idx.get("sp500_vs_20d_ma", "above"),
        "sp500_4w_change_pct": idx.get("sp500_4w_change_pct", 0.0),
        "nasdaq_20d_trend": idx.get("nasdaq_20d_trend", "sideways"),
        "vix_current": idx.get("vix_current", 0.0),
        "vix_level": idx.get("vix_level", "moderate"),
    }
    return json.dumps(output, ensure_ascii=False)
