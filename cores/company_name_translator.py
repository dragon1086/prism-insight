"""
Company Name Translator Utility

Translates Korean company names to English for filename generation.
Supports caching to avoid duplicate API calls.
"""

import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

# In-memory cache: {korean_name: english_name}
_translation_cache: Dict[str, str] = {}


def _sanitize_for_filename(name: str, ascii_only: bool = False) -> str:
    """
    Convert name to filename-safe format.

    - Replace spaces with underscores
    - Remove special characters except underscores and hyphens
    - Strip leading/trailing whitespace

    Args:
        name: Company name to sanitize
        ascii_only: If True, only keep ASCII alphanumeric characters (removes Korean, etc.)

    Returns:
        Filename-safe string
    """
    # Replace spaces with underscores
    sanitized = name.strip().replace(" ", "_")

    if ascii_only:
        # For English filenames: only keep ASCII alphanumeric, underscore, hyphen
        sanitized = re.sub(r'[^a-zA-Z0-9_\-]', '', sanitized)
    else:
        # Keep Unicode word characters (allows Korean, etc.)
        sanitized = re.sub(r'[^\w\-]', '', sanitized, flags=re.UNICODE)

    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)

    # Strip leading/trailing underscores
    sanitized = sanitized.strip('_')

    return sanitized


async def translate_company_name(korean_name: str) -> str:
    """
    Translate Korean company name to English.

    Uses GPT-5-nano for cost-efficient translation with caching
    to prevent duplicate API calls.

    Args:
        korean_name: Korean company name to translate

    Returns:
        English company name (filename-safe format)
    """
    global _translation_cache

    # Check cache first
    if korean_name in _translation_cache:
        logger.debug(f"Cache hit for company name: {korean_name}")
        return _translation_cache[korean_name]

    try:
        from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
        from mcp_agent.workflows.llm.augmented_llm import RequestParams
        from mcp_agent.agents.agent import Agent

        # Create a simple translation agent
        instruction = """You are a Korean-to-English translator for company names.

Your task is to translate Korean company names to their official English names.

## Guidelines
1. Use the official English name if the company has one (e.g., "삼성전자" → "Samsung Electronics")
2. For Korean conglomerates, keep the Korean name romanized (e.g., "SK하이닉스" → "SK Hynix")
3. For lesser-known companies, provide a natural English translation
4. Keep the translation concise and suitable for filenames
5. Do NOT include suffixes like "Co., Ltd.", "Inc.", "Corp." unless essential

## Output Format
Return ONLY the English company name, nothing else. No quotes, no explanation.

## Examples
- 삼성전자 → Samsung Electronics
- 현대자동차 → Hyundai Motor
- SK하이닉스 → SK Hynix
- LG에너지솔루션 → LG Energy Solution
- 카카오 → Kakao
- 네이버 → Naver
- 셀트리온 → Celltrion
- 포스코홀딩스 → POSCO Holdings
- 삼성SDI → Samsung SDI
- 기아 → Kia
"""

        agent = Agent(
            name="company_name_translator",
            instruction=instruction,
            server_names=[]
        )

        # Attach LLM
        llm = await agent.attach_llm(OpenAIAugmentedLLM)

        # Generate translation
        # Using gpt-4o-mini: non-reasoning model, 350x cheaper & 14x faster than gpt-5-mini
        english_name = await llm.generate_str(
            message=f"Translate this Korean company name to English: {korean_name}",
            request_params=RequestParams(
                model="gpt-4o-mini",
                maxTokens=100,
                temperature=0.1,
                max_iterations=1
            )
        )

        # Clean and sanitize the result
        # Use ascii_only=True to ensure English-only filename (no Korean characters)
        english_name = english_name.strip().strip('"\'')
        sanitized_name = _sanitize_for_filename(english_name, ascii_only=True)

        # Fallback to ticker-based name if translation is empty (don't use Korean in English filename)
        if not sanitized_name:
            logger.warning(f"Translation returned empty for '{korean_name}', using 'Company' as fallback")
            sanitized_name = "Company"

        # Cache the result
        _translation_cache[korean_name] = sanitized_name
        logger.info(f"Translated company name: {korean_name} → {sanitized_name}")

        return sanitized_name

    except Exception as e:
        logger.error(f"Failed to translate company name '{korean_name}': {str(e)}")
        # Fallback: return generic name (don't use Korean characters in English filename)
        # Korean name would fail the ascii_only sanitization anyway
        fallback = "Company"
        _translation_cache[korean_name] = fallback
        return fallback


def clear_cache():
    """Clear the translation cache."""
    global _translation_cache
    _translation_cache.clear()
    logger.info("Company name translation cache cleared")


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    return {
        "cache_size": len(_translation_cache),
        "cached_names": list(_translation_cache.keys())
    }
