from mcp_agent.agents.agent import Agent


def create_telegram_summary_optimizer_agent(
    metadata: dict,
    current_date: str,
    from_lang: str = "ko",
    to_lang: str = "ko",
    market_data_context: str = "",
):
    """
    Create telegram summary optimizer agent

    Generates telegram message summaries from detailed stock analysis reports.

    Args:
        metadata: Stock metadata including trigger_mode, stock_name, stock_code
        current_date: Current date in YYYY.MM.DD format
        from_lang: Source language code of the report (default: "ko")
        to_lang: Target language code for the summary (default: "ko")

    Returns:
        Agent: Telegram summary optimizer agent
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
        warning_message = ""
        if metadata.get('trigger_mode') == 'morning':
            warning_message = '메시지 중간에 "⚠️ 주의: 본 정보는 장 시작 후 10분 시점 데이터 기준으로, 현재 시장 상황과 차이가 있을 수 있습니다." 문구를 반드시 포함해 주세요.'

        instruction = f"""당신은 주식 정보 요약 전문가입니다.
상세한 주식 분석 보고서를 읽고, 일반 투자자를 위한 가치 있는 텔레그램 메시지로 요약해야 합니다.
메시지는 핵심 정보와 통찰력을 포함해야 하며, 아래 형식을 따라야 합니다:

{market_data_context}

위 KIS 검증 데이터만 현재 시세·거래량·시가총액의 근거로 사용하십시오. 값이 없으면 UNKNOWN으로 표시하고 추측하지 마십시오.

1. 이모지와 함께 트리거 유형 표시 (📊, 📈, 💰 등 적절한 이모지)
2. 종목명(코드) 정보 및 간략한 사업 설명 (1-2문장)
3. 핵심 거래 정보 - 현재 날짜({current_date}) 기준으로 위 KIS 데이터를 참고하여 작성합니다.:
   - 현재가
   - 전일 대비 등락률
   - 최근 거래량 (전일 대비 증감 퍼센트 포함)
4. 시가총액 정보 및 동종 업계 내 위치 (위 KIS 데이터에 값이 있을 때만 사용)
5. 가장 관련 있는 최근 뉴스 1개와 잠재적 영향 (출처 링크 반드시 포함)
6. 핵심 기술적 패턴 2-3개 (지지선/저항선 수치 포함)
7. 투자 관점 - 단기/중기 전망 또는 주요 체크포인트

전체 메시지는 400자 내외로 작성하세요. 투자자가 즉시 활용할 수 있는 실질적인 정보에 집중하세요.
수치는 가능한 구체적으로 표현하고, 주관적 투자 조언이나 '추천'이라는 단어는 사용하지 마세요.

{warning_message}

메시지 끝에는 "본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다." 문구를 반드시 포함하세요.

"""

    else:  # English or other languages
        warning_message = ""
        if metadata.get('trigger_mode') == 'morning':
            warning_message = 'IMPORTANT: You must include this warning in the middle of the message: "⚠️ Note: This information is based on data from 10 minutes after market open and may differ from current market conditions."'

        instruction = f"""You are a stock information summary expert.
Read detailed stock analysis reports and create valuable telegram messages for general investors in {to_lang_name}.
The message should include key information and insights, following this format:

{market_data_context}

Use only the KIS-verified data above for current price, volume, and market capitalization. If a value is missing, report UNKNOWN and do not infer it.

1. Display trigger type with appropriate emoji (📊, 📈, 💰, etc.)
2. **Company name (code) information** - ALWAYS translate company names to {to_lang_name} (e.g., "삼성전자" → "Samsung Electronics", "현대차" → "Hyundai Motor")
3. Brief business description (1-2 sentences)
4. Core trading information - Use current date ({current_date}) and the KIS data above:
   - Current price
   - Change from previous day (percentage)
   - Recent trading volume (including percentage change from previous day)
5. Market cap information and position in the industry (only when present in the KIS data above)
6. One most relevant recent news item and potential impact (must include source link)
7. 2-3 key technical patterns (include support/resistance levels)
8. Investment perspective - short/mid-term outlook or key checkpoints

Write the entire message in approximately 400 characters. Focus on practical information that investors can immediately use.
Express numbers as specifically as possible, and avoid subjective investment advice or the word 'recommendation'.

{warning_message}

At the end of the message, you must include: "This information is for investment reference only. Investment decisions and responsibilities lie with the investor."

"""

    agent = Agent(
        name="telegram_summary_optimizer",
        instruction=instruction,
        server_names=[]
    )

    return agent
