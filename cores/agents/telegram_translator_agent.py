from mcp_agent.agents.agent import Agent


def create_telegram_translator_agent(from_lang: str = "ko", to_lang: str = "en"):
    """
    Create telegram message translation agent

    Translates telegram messages from source language to target language while preserving formatting,
    emojis, numbers, and technical terms.

    Args:
        from_lang: Source language code (default: "ko" for Korean)
        to_lang: Target language code (default: "en" for English)

    Returns:
        Agent: Telegram message translation agent
    """

    # Language name mapping
    lang_names = {
        "ko": "Korean",
        "en": "English",
        "ja": "Japanese",
        "zh": "Chinese",
        "es": "Spanish",
        "fr": "French",
        "de": "German"
    }

    from_lang_name = lang_names.get(from_lang, from_lang.upper())
    to_lang_name = lang_names.get(to_lang, to_lang.upper())

    instruction = f"""You are a professional translator specializing in stock market and trading communications.

Your task is to translate {from_lang_name} telegram messages to {to_lang_name}.

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

### 4. Stock Names - CRITICAL
- **ALWAYS translate company names to {to_lang_name}**
- **DO NOT keep the original language company names**
- Always include ticker symbols if present
- Example (Korean to English): "삼성전자(005930)" → "Samsung Electronics (005930)"
- Example (Korean to English): "현대자동차" → "Hyundai Motor Company"
- Example (Korean to English): "SK하이닉스" → "SK Hynix"
- For well-known companies, use their official {to_lang_name} names
- For lesser-known companies, provide a descriptive translation

### 5. Tone and Style
- Maintain professional but accessible tone
- Keep urgency and emphasis from original message
- Preserve any disclaimers or warnings

### 6. Emojis and Symbols
- Keep all emojis: 📈, 📊, 🔔, ✅, ⚠️, etc.
- Preserve arrows: ⬆️, ⬇️, ➖, ↔️
- Maintain visual hierarchy with emojis

### 7. Placeholders - CRITICAL
- NEVER modify, translate, or remove placeholder tokens such as <<<__BASE64_IMAGE_0__>>>, <<<__BASE64_IMAGE_1__>>>, etc.
- These tokens MUST appear in the translated output EXACTLY as they appear in the input, character-for-character.
- Do not add spaces, change angle brackets, or wrap them in markdown formatting.

## Instructions
Translate the following {from_lang_name} telegram message to {to_lang_name} following all guidelines above.
**CRITICAL**: Make sure to translate ALL company names to {to_lang_name}. Do not leave them in {from_lang_name}.
**CRITICAL**: Preserve all <<<__BASE64_IMAGE_N__>>> placeholders exactly as-is. Do not translate or modify them.
Only return the translated text without any explanations or metadata.
"""

    agent = Agent(
        name="telegram_translator",
        instruction=instruction,
        server_names=[]
    )

    return agent


async def translate_telegram_message(
    message: str,
    model: str = "gpt-5-nano",
    from_lang: str = "ko",
    to_lang: str = "en"
) -> str:
    """
    Translate a telegram message from source language to target language

    Args:
        message: Telegram message to translate
        model: OpenAI model to use (default: gpt-5-nano for cost efficiency)
        from_lang: Source language code (default: "ko" for Korean)
        to_lang: Target language code (default: "en" for English)

    Returns:
        str: Translated message
    """
    from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
    from mcp_agent.workflows.llm.augmented_llm import RequestParams

    try:
        # Sanitize: strip control characters that break JSON serialization
        # (NUL bytes and other ASCII control chars except \t \n \r are invalid in JSON strings)
        import re as _re
        message = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', message)

        # Create translator agent
        translator = create_telegram_translator_agent(from_lang=from_lang, to_lang=to_lang)

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
