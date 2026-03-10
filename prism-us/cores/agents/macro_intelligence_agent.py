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

    # Build context from prefetched data (language-agnostic)
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

    # JSON schema values (computed once, used in both prompts)
    _default_regime = prefetched_data.get('computed_regime', {}).get('market_regime', 'sideways') if prefetched_data else 'sideways'
    _default_confidence = prefetched_data.get('computed_regime', {}).get('regime_confidence', 0.5) if prefetched_data else 0.5
    _default_simple_ma = prefetched_data.get('computed_regime', {}).get('simple_ma_regime', 'sideways') if prefetched_data else 'sideways'
    _index_summary_json = _format_us_index_summary(prefetched_data)

    if language == "en":
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
  "market_regime": "{_default_regime}",
  "regime_confidence": {_default_confidence},
  "regime_rationale": "Brief explanation of regime judgment (1-2 sentences)",
  "simple_ma_regime": "{_default_simple_ma}",
  "index_summary": {_index_summary_json},
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

Write a professional 3-5 paragraph narrative in formal English covering:
1. Current market regime and its rationale
2. Leading sectors and why they are outperforming
3. Key risk events and their potential market impact
4. Recommended investment posture given the current regime

This prose will be directly inserted into stock analysis reports. Make it informative but concise.
Use formal professional English throughout.

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
    else:
        instruction = f"""당신은 미국 주식시장 거시경제 인텔리전스 분석가입니다.
아래 지시에 따라 데이터를 수집한 후, 유효한 JSON만 출력하십시오. JSON 외에 어떠한 텍스트도 포함하지 마십시오.

분석 기준일: {reference_date} (YYYYMMDD 형식)
{regime_context}
{index_data_context}
## 실행할 도구 호출

### Perplexity 거시경제 검색 (1회만)
perplexity_ask 도구를 사용하여 다음 쿼리를 실행하십시오:
"{reference_date} 미국 주식시장 거시경제 트렌드, 섹터 로테이션, 주도/소외 섹터, 리스크 이벤트, 지정학적 리스크 종합 분석"

---

## 작업 지시

Perplexity 검색 결과와 위의 사전 계산된 지수 데이터를 기반으로:
1. 사전 계산된 market_regime 및 index_summary 값을 그대로 사용하십시오
2. Perplexity 분석에서 주도 섹터와 소외 섹터를 파악하십시오
3. 리스크 이벤트와 수혜 테마를 식별하십시오
4. 시장 체제 판단 근거를 설명하는 `regime_rationale`을 작성하십시오
5. 주식 분석 보고서에 직접 삽입될 `report_prose` — 잘 작성된 3~5문단의 서술형 요약을 작성하십시오

---

## 섹터 분류 체계 (미국 GICS 기반 고정 목록)

leading_sectors 및 lagging_sectors에는 반드시 아래 섹터만 사용하십시오:
Technology, Healthcare, Financials, Consumer Discretionary, Consumer Staples,
Energy, Industrials, Materials, Real Estate, Utilities, Communication Services,
Defense/Aerospace, Semiconductors, Software, Biotechnology, EV/Clean Energy

---

## 출력 JSON 스키마 (정확히 이 구조로 출력)

```json
{{{{
  "analysis_date": "YYYYMMDD",
  "market": "US",
  "market_regime": "{_default_regime}",
  "regime_confidence": {_default_confidence},
  "regime_rationale": "시장 체제 판단 근거 (1~2문장)",
  "simple_ma_regime": "{_default_simple_ma}",
  "index_summary": {_index_summary_json},
  "leading_sectors": [
    {{"sector": "Semiconductors", "reason": "AI 수요 급증", "confidence": 0.8}}
  ],
  "lagging_sectors": [
    {{"sector": "Real Estate", "reason": "금리 인상 압박", "confidence": 0.6}}
  ],
  "risk_events": [
    {{"event": "미중 무역 갈등", "impact": "negative", "severity": "high", "affected_sectors": ["Semiconductors", "Technology"]}}
  ],
  "beneficiary_themes": [
    {{"theme": "AI 인프라 확장", "beneficiary_sectors": ["Semiconductors", "Software"], "duration": "medium_term"}}
  ],
  "recommended_max_holdings": 8,
  "cash_ratio_suggestion": 20,
  "report_prose": "거시경제 분석 보고서 서술 (3~5문단)"
}}}}
```

## report_prose 작성 지침

다음 내용을 포함하여 정중한 한국어(합쇼체)로 전문적인 3~5문단 서술을 작성하십시오:
1. 현재 시장 체제와 그 판단 근거
2. 주도 섹터와 해당 섹터가 강세를 보이는 이유
3. 주요 리스크 이벤트와 시장에 미칠 잠재적 영향
4. 현재 시장 체제를 감안한 권장 투자 포지셔닝

이 서술은 주식 분석 보고서에 직접 삽입됩니다. 정보를 충실히 담되 간결하게 작성하십시오.
반드시 합쇼체 문체를 사용하십시오: ~습니다, ~있습니다, ~됩니다

## 필드 값 가이드

- `recommended_max_holdings`: 시장 체제에 따라 6~10
  - strong_bull: 9~10, moderate_bull: 8~9, sideways: 7~8, moderate_bear: 6~7, strong_bear: 5~6
- `cash_ratio_suggestion`: 정수 %
  - strong_bull: 10%, moderate_bull: 15~20%, sideways: 20~25%, moderate_bear: 30%, strong_bear: 40%+

## 중요 사항

- JSON 생성 전에 반드시 Perplexity 도구를 호출하십시오
- 출력은 순수 JSON만 가능합니다. 마크다운 코드 펜스나 설명 텍스트를 포함하지 마십시오
- leading_sectors: 최대 5개, 신뢰도 내림차순
- lagging_sectors: 최대 5개
- 반(反)환각: 실제 데이터에서 확인된 내용만 포함하십시오
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
