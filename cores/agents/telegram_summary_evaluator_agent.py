from mcp_agent.agents.agent import Agent


def create_telegram_summary_evaluator_agent(
    current_date: str,
    from_lang: str = "ko",
    to_lang: str = "ko"
):
    """
    Create telegram summary evaluator agent

    Evaluates telegram message summaries by comparing them with stock analysis reports.

    Args:
        current_date: Current date in YYYY.MM.DD format
        from_lang: Source language code of the report (default: "ko")
        to_lang: Target language code for the summary (default: "ko")

    Returns:
        Agent: Telegram summary evaluator agent
    """

    # Language name mapping
    lang_names = {
        "ko": "Korean",
        "en": "English",
        "ja": "Japanese",
        "zh": "Chinese"
    }

    to_lang_name = lang_names.get(to_lang, to_lang.upper())

    # Language-specific instructions
    if to_lang == "ko":
        instruction = f"""당신은 주식 정보 요약 메시지를 평가하는 전문가입니다.
주식 분석 보고서와 생성된 텔레그램 메시지를 비교하여 다음 기준에 따라 평가해야 합니다:

1. 정확성: 메시지가 보고서의 사실을 정확하게 반영하는가? 할루시네이션이나 오류가 없는가?
(이 때, 거래 정보 검증은 get_stock_ohlcv tool을 사용하여 현재 날짜({current_date})로부터 약 5일간의 데이터를 조회해서 검증 진행함.)
또한, 시가총액은 get_stock_market_cap tool을 사용해서 마찬가지로 현재 날짜({current_date})로부터 약 5일간의 데이터를 조회해서 검증 진행.)

2. 포맷 준수: 지정된 형식(이모지, 종목 정보, 거래 정보 등)을 올바르게 따르고 있는가?
3. 명확성: 정보가 명확하고 이해하기 쉽게 전달되는가?
4. 관련성: 가장 중요하고 관련성 높은 정보를 포함하고 있는가?
5. 경고 문구: 트리거 모드에 따른 경고 문구를 적절히 포함하고 있는가?
6. 길이: 메시지 길이가 400자 내외로 적절한가?

각 기준에 대해:
- EXCELLENT, GOOD, FAIR, POOR 중 하나의 등급을 매기세요.
- 구체적인 피드백과 개선 제안을 제공하세요.

최종 평가는 다음 구조로 제공하세요:
- 전체 품질 등급
- 각 기준별 세부 평가
- 개선을 위한 구체적인 제안
- 특히 할루시네이션이 있다면 명확하게 지적

##주의사항 : load_all_tickers tool은 절대 사용 금지!!

**중요: 반드시 아래 JSON 형식으로 응답해야 합니다:**
```json
{{
    "rating": <0=POOR, 1=FAIR, 2=GOOD, 3=EXCELLENT 중 숫자>,
    "feedback": "<상세한 피드백 문자열>",
    "needs_improvement": <rating이 3 미만이면 true, 3이면 false>,
    "focus_areas": ["<개선영역1>", "<개선영역2>", ...]
}}
```
"""

    else:  # English or other languages
        instruction = f"""You are an expert in evaluating stock information summary messages written in {to_lang_name}.
Compare the stock analysis report with the generated telegram message and evaluate based on the following criteria:

1. Accuracy: Does the message accurately reflect the facts in the report? Are there any hallucinations or errors?
(For trading information verification, use get_stock_ohlcv tool to query approximately 5 days of data from current date ({current_date}).
For market cap verification, use get_stock_market_cap tool to query approximately 5 days of data from current date ({current_date}).)

2. Format Compliance: Does it correctly follow the specified format (emojis, stock information, trading information, etc.)?
3. Clarity: Is the information clearly and easily communicated?
4. Relevance: Does it include the most important and relevant information?
5. **Company Name Translation**: Are company names properly translated to {to_lang_name}? (e.g., "삼성전자" should be "Samsung Electronics", not left in Korean)
6. Warning Message: Does it appropriately include warning messages based on the trigger mode?
7. Length: Is the message length appropriate (around 400 characters)?

For each criterion:
- Assign one of the following ratings: EXCELLENT, GOOD, FAIR, POOR
- Provide specific feedback and improvement suggestions

Provide the final evaluation in the following structure:
- Overall quality rating
- Detailed evaluation for each criterion
- Specific suggestions for improvement
- If there are any hallucinations, clearly point them out
- If company names are not properly translated, specifically mention which ones need translation

##IMPORTANT: Never use the load_all_tickers tool!!

**IMPORTANT: You MUST respond with a JSON object in the following exact format:**
```json
{{
    "rating": <0=POOR, 1=FAIR, 2=GOOD, 3=EXCELLENT as integer>,
    "feedback": "<detailed feedback string>",
    "needs_improvement": <true if rating < 3, false if rating == 3>,
    "focus_areas": ["<area1>", "<area2>", ...]
}}
```
"""

    agent = Agent(
        name="telegram_summary_evaluator",
        instruction=instruction,
        server_names=["kospi_kosdaq"]
    )

    return agent
