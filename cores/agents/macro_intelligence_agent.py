from mcp_agent.agents.agent import Agent


def create_macro_intelligence_agent(reference_date, language="ko", prefetched_data: dict = None):
    """Create macro intelligence agent for KR market

    The agent receives pre-computed regime and index data from programmatic prefetch,
    and only calls perplexity for qualitative analysis (sector trends, risk events).

    Args:
        reference_date: Analysis reference date (YYYYMMDD)
        language: Language code ("ko" or "en")
        prefetched_data: Dict with computed_regime, kospi_ohlcv_md, kosdaq_ohlcv_md, sector_map

    Returns:
        Agent: Macro intelligence agent
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

The following regime was computed programmatically from KOSPI price data:
- **market_regime**: {regime}
- **regime_confidence**: {confidence}
- **simple_ma_regime**: {simple_ma}
- **KOSPI 20d trend**: {idx.get('kospi_20d_trend', 'N/A')}
- **KOSPI vs 20d MA**: {idx.get('kospi_vs_20d_ma', 'N/A')}
- **KOSPI 2-week change**: {idx.get('kospi_2w_change_pct', 'N/A')}%
- **KOSPI current**: {idx.get('kospi_current', 'N/A')}
- **KOSPI 20d MA**: {idx.get('kospi_20d_ma', 'N/A')}
- **KOSDAQ 20d trend**: {idx.get('kosdaq_20d_trend', 'N/A')}

You MUST use these pre-computed values for market_regime, regime_confidence, simple_ma_regime, and index_summary.
You may adjust regime_confidence (±0.1) based on perplexity analysis, but DO NOT change market_regime unless
perplexity data provides overwhelming contradictory evidence.
"""

        kospi_md = prefetched_data.get("kospi_ohlcv_md", "")
        kosdaq_md = prefetched_data.get("kosdaq_ohlcv_md", "")
        if kospi_md or kosdaq_md:
            index_data_context = "\n## Pre-fetched Index Data\n\n"
            if kospi_md:
                index_data_context += kospi_md + "\n"
            if kosdaq_md:
                index_data_context += kosdaq_md + "\n"

    instruction = f"""You are a Korean stock market macro intelligence analyst.
Follow the instructions below to collect data, then output ONLY valid JSON. Do not include any text outside the JSON.

Analysis date: {reference_date} (YYYYMMDD format)
{regime_context}
{index_data_context}
## Tool Call to Execute

### Perplexity macro search (1 call only)
Use the perplexity_ask tool with the following query:
"{reference_date} 한국 증시 거시경제 동향, 업종별 동향, 주도 섹터와 소외 섹터, 리스크 이벤트, 지정학적 리스크 종합분석"

---

## Your Task

Based on the perplexity search results AND the pre-computed index data above:
1. Use the pre-computed market_regime and index_summary values as-is
2. Identify leading and lagging sectors from perplexity analysis
3. Identify risk events and beneficiary themes
4. Write a `regime_rationale` explaining the regime judgment
5. Write a `report_prose` section — a well-written 3-5 paragraph narrative summary for inclusion in stock analysis reports

---

## Sector Taxonomy (KR fixed sector list)

Use ONLY these sectors for leading_sectors and lagging_sectors:
반도체, 자동차, 배터리/2차전지, 바이오/제약, 건설, 철강, 화학, 금융,
유통/소비재, IT/소프트웨어, 엔터테인먼트, 조선, 방산, 에너지, 통신, 운송/물류, 기타

---

## Output JSON Schema (output exactly this structure)

```json
{{{{
  "analysis_date": "YYYYMMDD",
  "market": "KR",
  "market_regime": "{prefetched_data.get('computed_regime', {}).get('market_regime', 'sideways') if prefetched_data else 'sideways'}",
  "regime_confidence": {prefetched_data.get('computed_regime', {}).get('regime_confidence', 0.5) if prefetched_data else 0.5},
  "regime_rationale": "판단 근거 간략 설명 (1-2 sentences)",
  "simple_ma_regime": "{prefetched_data.get('computed_regime', {}).get('simple_ma_regime', 'sideways') if prefetched_data else 'sideways'}",
  "index_summary": {_format_index_summary(prefetched_data)},
  "leading_sectors": [
    {{"sector": "반도체", "reason": "AI 수요 급증", "confidence": 0.8}}
  ],
  "lagging_sectors": [
    {{"sector": "건설", "reason": "금리 인상 영향", "confidence": 0.6}}
  ],
  "risk_events": [
    {{"event": "미중 무역갈등 심화", "impact": "negative", "severity": "high", "affected_sectors": ["반도체", "자동차"]}}
  ],
  "beneficiary_themes": [
    {{"theme": "AI 인프라 투자 확대", "beneficiary_sectors": ["반도체", "IT/소프트웨어"], "duration": "medium_term"}}
  ],
  "recommended_max_holdings": 8,
  "cash_ratio_suggestion": 20,
  "report_prose": "거시경제 분석 리포트 내러티브 (3-5 paragraphs, formal Korean 합쇼체)"
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
        name="macro_intelligence_agent",
        instruction=instruction,
        server_names=["perplexity"]
    )


def _format_index_summary(prefetched_data: dict) -> str:
    """Format index_summary for JSON schema example."""
    if not prefetched_data:
        return '{"kospi_20d_trend": "sideways", "kospi_vs_20d_ma": "above", "kospi_2w_change_pct": 0.0, "kosdaq_20d_trend": "sideways"}'

    computed = prefetched_data.get("computed_regime", {})
    idx = computed.get("index_summary", {})
    if not idx:
        return '{"kospi_20d_trend": "sideways", "kospi_vs_20d_ma": "above", "kospi_2w_change_pct": 0.0, "kosdaq_20d_trend": "sideways"}'

    import json
    # Only include the standard fields (not current/ma which are internal)
    output = {
        "kospi_20d_trend": idx.get("kospi_20d_trend", "sideways"),
        "kospi_vs_20d_ma": idx.get("kospi_vs_20d_ma", "above"),
        "kospi_2w_change_pct": idx.get("kospi_2w_change_pct", 0.0),
        "kosdaq_20d_trend": idx.get("kosdaq_20d_trend", "sideways"),
    }
    return json.dumps(output, ensure_ascii=False)
