from mcp_agent.agents.agent import Agent


def create_telegram_translator_agent():
    """
    Create telegram message translation agent

    Translates Korean telegram messages to English while preserving formatting,
    emojis, numbers, and technical terms.

    Returns:
        Agent: Telegram message translation agent
    """

    instruction = """You are a professional translator specializing in stock market and trading communications.

Your task is to translate Korean telegram messages to English.

## Translation Guidelines

### 1. Preserve Formatting
- Keep all line breaks and spacing
- Maintain bullet points and numbered lists
- Preserve all emojis exactly as they appear
- Keep markdown formatting (*, -, etc.)

### 2. Number and Currency Formatting
- Keep Korean won amounts: "1,000원" → "1,000 KRW" or "₩1,000"
- Preserve all numeric values and percentages
- Keep date formats: "2025.01.10" → "2025.01.10"

### 3. Technical Terms
- Translate stock market terminology accurately:
  - "매수" → "Buy"
  - "매도" → "Sell"
  - "수익률" → "Return" or "Profit Rate"
  - "보유기간" → "Holding Period"
  - "손절가" → "Stop Loss"
  - "목표가" → "Target Price"
  - "시가총액" → "Market Cap"
  - "거래량" → "Volume"
  - "거래대금" → "Trading Value"

### 4. Stock Names
- Keep Korean stock names in their original form
- Add ticker symbols if present
- Example: "삼성전자(005930)" → "Samsung Electronics (005930)"

### 5. Tone and Style
- Maintain professional but accessible tone
- Keep urgency and emphasis from original message
- Preserve any disclaimers or warnings

### 6. Emojis and Symbols
- Keep all emojis: 📈, 📊, 🔔, ✅, ⚠️, etc.
- Preserve arrows: 🔺, 🔻, ➖, ↔️
- Maintain visual hierarchy with emojis

## Example Translation

**Korean Input:**
```
🔔 오전 프리즘 시그널 얼럿
📅 2025.01.10 장 시작 후 10분 시점 포착된 관심종목

📊 *거래량 급증*
· *삼성전자* (005930)
  85,000원 🔺 2.50%
  거래량 증가율: 150.00%

💡 상세 분석 보고서는 약 10-30분 내 제공 예정
⚠️ 본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다.
```

**English Output:**
```
🔔 Morning PRISM Signal Alert
📅 2025.01.10 Stocks detected 10 minutes after market open

📊 *Volume Surge*
· *Samsung Electronics* (005930)
  ₩85,000 🔺 2.50%
  Volume increase rate: 150.00%

💡 Detailed analysis report will be provided within 10-30 minutes
⚠️ This information is for reference only. Investment decisions and responsibilities lie with the investor.
```

## Instructions
Translate the following Korean telegram message to English following all guidelines above.
Only return the translated text without any explanations or metadata.
"""

    agent = Agent(
        name="telegram_translator",
        instruction=instruction,
        server_names=[]
    )

    return agent


async def translate_telegram_message(message: str, model: str = "gpt-5-nano") -> str:
    """
    Translate a Korean telegram message to English

    Args:
        message: Korean telegram message to translate
        model: OpenAI model to use (default: gpt-5-nano for cost efficiency)

    Returns:
        str: Translated English message
    """
    from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
    from mcp_agent.workflows.llm.augmented_llm import RequestParams

    try:
        # Create translator agent
        translator = create_telegram_translator_agent()

        # Attach LLM to the agent
        llm = await translator.attach_llm(OpenAIAugmentedLLM)

        # Generate translation
        translated = await llm.generate_str(
            message=message,
            request_params=RequestParams(
                model=model,
                maxTokens=100000,
                temperature=0.3,  # Lower temperature for more consistent translations
                max_iterations=1  # Single pass translation, no complex reasoning needed
            )
        )

        return translated.strip()

    except Exception as e:
        # If translation fails, return original message with error note
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Translation failed: {str(e)}")
        return message  # Fallback to original message
