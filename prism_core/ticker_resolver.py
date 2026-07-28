"""Pure ticker-resolution helper, shared between the Telegram and Kakao bots.

Extracted from telegram_ai_bot.TelegramAIBot.get_stock_code so the Kakao bot can
reuse the same stock name/code resolution logic without needing a Telegram bot
instance. Logic-preserving extraction: instance state (self.stock_map,
self.stock_name_map) becomes explicit keyword arguments; error message
wording, matching order, and logging are otherwise unchanged.
"""
import logging
import re
from typing import Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


def resolve_ticker(
    stock_input: str,
    *,
    code_to_name: Optional[Mapping[str, str]],
    name_to_code: Optional[Mapping[str, str]],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Convert stock name or code input to a stock code.

    Extracted from telegram_ai_bot.TelegramAIBot.get_stock_code.

    Args:
        stock_input: Stock code or name.
        code_to_name: Mapping of stock code -> stock name (was self.stock_map).
        name_to_code: Mapping of stock name -> stock code (was self.stock_name_map).

    Returns:
        tuple: (stock_code, stock_name, error_message)
    """
    # Input value defense code
    if not stock_input:
        logger.warning("Empty input value passed")
        return None, None, "종목명이나 코드를 입력해주세요."

    if not isinstance(stock_input, str):
        logger.warning(f"Invalid input type: {type(stock_input)}")
        stock_input = str(stock_input)

    original_input = stock_input
    stock_input = stock_input.strip()

    logger.info(f"Stock search started - Input: '{original_input}' -> Cleaned input: '{stock_input}'")

    # Check name_to_code status
    if name_to_code is None:
        logger.error("stock_name_map is not initialized")
        return None, None, "시스템 오류: 주식 데이터가 로드되지 않았습니다."

    if not isinstance(name_to_code, dict):
        logger.error(f"stock_name_map type error: {type(name_to_code)}")
        return None, None, "시스템 오류: 주식 데이터 형식이 잘못되었습니다."

    logger.info(f"stock_name_map status - Size: {len(name_to_code)}")

    # Check code_to_name status
    if code_to_name is None:
        logger.warning("stock_map is not initialized")
        code_to_name = {}

    # If already a stock code (6-digit number)
    if re.match(r'^\d{6}$', stock_input):
        logger.info(f"Recognized as 6-digit numeric code: {stock_input}")
        stock_code = stock_input
        stock_name = code_to_name.get(stock_code)

        if stock_name:
            logger.info(f"Stock code match successful: {stock_code} -> {stock_name}")
            return stock_code, stock_name, None
        else:
            logger.warning(f"No name information for stock code {stock_code}")
            return stock_code, f"종목_{stock_code}", "해당 종목 코드에 대한 정보가 없습니다. 코드가 정확한지 확인해주세요."

    # If entered as stock name - check for exact match
    logger.info(f"Starting exact name match search: '{stock_input}'")

    # Log key samples for debugging
    sample_keys = list(name_to_code.keys())[:5]
    logger.debug(f"stock_name_map key samples: {sample_keys}")

    # Exact match check
    if stock_input in name_to_code:
        stock_code = name_to_code[stock_input]
        logger.info(f"Exact match successful: '{stock_input}' -> {stock_code}")
        return stock_code, stock_input, None
    else:
        logger.info(f"Exact match failed: '{stock_input}'")

        # Log input value details
        logger.debug(f"Input details - Length: {len(stock_input)}, "
                     f"Bytes: {stock_input.encode('utf-8')}, "
                     f"Unicode: {[ord(c) for c in stock_input]}")

    # Partial stock name match search
    logger.info("Starting partial match search")
    possible_matches = []

    try:
        for name, code in name_to_code.items():
            if not isinstance(name, str) or not isinstance(code, str):
                logger.warning(f"Invalid data type: name={type(name)}, code={type(code)}")
                continue

            if stock_input.lower() in name.lower():
                possible_matches.append((name, code))
                logger.debug(f"Partial match found: '{name}' ({code})")

    except Exception as e:
        logger.error(f"Error during partial match search: {e}")
        return None, None, "검색 중 오류가 발생했습니다."

    logger.info(f"Partial match results: {len(possible_matches)} found")

    if len(possible_matches) == 1:
        # Use if single match found
        stock_name, stock_code = possible_matches[0]
        logger.info(f"Single partial match successful: '{stock_name}' ({stock_code})")
        return stock_code, stock_name, None
    elif len(possible_matches) > 1:
        # Return error message if multiple matches
        logger.info(f"Multiple matches: {[f'{name}({code})' for name, code in possible_matches]}")
        match_info = "\n".join([f"{name} ({code})" for name, code in possible_matches[:5]])
        if len(possible_matches) > 5:
            match_info += f"\n... 외 {len(possible_matches)-5}개"

        return None, None, f"'{stock_input}'에 해당하는 종목이 여러 개 있습니다. 정확한 종목명이나 코드를 입력해주세요:\n{match_info}"
    else:
        # Return error message if no matches
        logger.warning(f"No matching stock: '{stock_input}'")
        return None, None, f"'{stock_input}'에 해당하는 종목을 찾을 수 없습니다. 정확한 종목명이나 코드를 입력해주세요."
