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

    # JSON schema values (language-agnostic)
    schema_market_regime = prefetched_data.get('computed_regime', {}).get('market_regime', 'sideways') if prefetched_data else 'sideways'
    schema_regime_confidence = prefetched_data.get('computed_regime', {}).get('regime_confidence', 0.5) if prefetched_data else 0.5
    schema_simple_ma_regime = prefetched_data.get('computed_regime', {}).get('simple_ma_regime', 'sideways') if prefetched_data else 'sideways'
    schema_index_summary = _format_index_summary(prefetched_data)

    if language == "en":
        instruction = f"""You are a Korean stock market macro intelligence analyst.
Follow the instructions below to collect data, then output ONLY valid JSON. Do not include any text outside the JSON.

Analysis date: {reference_date} (YYYYMMDD format)
{regime_context}
{index_data_context}
## Tool Call to Execute

### Perplexity macro search (1 call only)
Use the perplexity_ask tool with the following query:
"{reference_date} Korean stock market macro trends, sector performance, leading and lagging sectors, risk events, geopolitical risk comprehensive analysis"

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
  "market_regime": "{schema_market_regime}",
  "regime_confidence": {schema_regime_confidence},
  "regime_rationale": "Brief rationale for the regime judgment (1-2 sentences)",
  "simple_ma_regime": "{schema_simple_ma_regime}",
  "index_summary": {schema_index_summary},
  "leading_sectors": [
    {{"sector": "반도체", "reason": "Surging AI demand", "confidence": 0.8}}
  ],
  "lagging_sectors": [
    {{"sector": "건설", "reason": "Impact of rate hikes", "confidence": 0.6}}
  ],
  "risk_events": [
    {{"event": "Escalating US-China trade tensions", "impact": "negative", "severity": "high", "affected_sectors": ["반도체", "자동차"]}}
  ],
  "beneficiary_themes": [
    {{"theme": "Expanding AI infrastructure investment", "beneficiary_sectors": ["반도체", "IT/소프트웨어"], "duration": "medium_term"}}
  ],
  "recommended_max_holdings": 8,
  "cash_ratio_suggestion": 20,
  "report_prose": "Macro intelligence report narrative (3-5 paragraphs, formal English)"
}}}}
```

## report_prose Guidelines

Write a professional 3-5 paragraph narrative in formal English covering:
1. Current market regime and its rationale
2. Leading sectors and why they are outperforming
3. Key risk events and their potential market impact
4. Recommended investment posture given the current regime

This prose will be directly inserted into stock analysis reports. Make it informative but concise.

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
        instruction = f"""당신은 한국 주식시장 거시경제 인텔리전스 애널리스트입니다.
아래 지시에 따라 데이터를 수집한 후, 유효한 JSON만 출력하십시오. JSON 외부에 어떠한 텍스트도 포함하지 마십시오.

분석 기준일: {reference_date} (YYYYMMDD 형식)
{regime_context}
{index_data_context}
## 실행할 도구 호출

### Perplexity 거시경제 검색 (1회만)
perplexity_ask 도구를 사용하여 다음 쿼리를 실행하십시오:
"{reference_date} 한국 증시 거시경제 동향, 업종별 동향, 주도 섹터와 소외 섹터, 리스크 이벤트, 지정학적 리스크 종합분석"

---

## 수행 과제

Perplexity 검색 결과와 위의 사전 계산된 지수 데이터를 바탕으로:
1. 사전 계산된 market_regime 및 index_summary 값을 그대로 사용하십시오
2. Perplexity 분석에서 주도 섹터와 소외 섹터를 파악하십시오
3. 리스크 이벤트와 수혜 테마를 파악하십시오
4. 체제 판단 근거를 설명하는 `regime_rationale`을 작성하십시오
5. 주식 분석 보고서에 삽입될 잘 작성된 3-5단락 내러티브 요약인 `report_prose` 섹션을 작성하십시오

---

## 섹터 분류 체계 (KR 고정 섹터 목록)

leading_sectors와 lagging_sectors에는 반드시 다음 섹터만 사용하십시오:
반도체, 자동차, 배터리/2차전지, 바이오/제약, 건설, 철강, 화학, 금융,
유통/소비재, IT/소프트웨어, 엔터테인먼트, 조선, 방산, 에너지, 통신, 운송/물류, 기타

---

## 출력 JSON 스키마 (정확히 이 구조로 출력하십시오)

```json
{{{{
  "analysis_date": "YYYYMMDD",
  "market": "KR",
  "market_regime": "{schema_market_regime}",
  "regime_confidence": {schema_regime_confidence},
  "regime_rationale": "판단 근거 간략 설명 (1-2 sentences)",
  "simple_ma_regime": "{schema_simple_ma_regime}",
  "index_summary": {schema_index_summary},
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

## report_prose 작성 지침

다음 내용을 포함하는 전문적인 3-5단락 내러티브를 한국어 합쇼체로 작성하십시오:
1. 현재 시장 체제와 그 근거
2. 주도 섹터와 초과 성과 이유
3. 주요 리스크 이벤트와 잠재적 시장 영향
4. 현재 체제를 고려한 권장 투자 포지션

이 내러티브는 주식 분석 보고서에 직접 삽입됩니다. 유익하되 간결하게 작성하십시오.
합쇼체를 사용하십시오: ~습니다, ~있습니다, ~됩니다

## 필드값 가이드

- `recommended_max_holdings`: 체제에 따라 6~10
  - strong_bull: 9~10, moderate_bull: 8~9, sideways: 7~8, moderate_bear: 6~7, strong_bear: 5~6
- `cash_ratio_suggestion`: 정수 %
  - strong_bull: 10%, moderate_bull: 15~20%, sideways: 20~25%, moderate_bear: 30%, strong_bear: 40%+

## 중요 사항

- JSON 생성 전에 Perplexity 도구 호출을 실행하십시오
- 출력은 순수 JSON만이어야 합니다. 마크다운 코드 펜스나 설명 텍스트 없이 출력하십시오
- leading_sectors: 최대 5개, 신뢰도 내림차순
- lagging_sectors: 최대 5개
- 반환금지: 실제 데이터에서 확인된 내용만 포함하십시오
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
