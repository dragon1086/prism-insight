"""Behavior-fixing contract test for TelegramAIBot.get_stock_code.

Locks the exact current behavior (return values, error message wording, logging
defense checks) of telegram_ai_bot.TelegramAIBot.get_stock_code BEFORE it is
extracted into prism_core.ticker_resolver.resolve_ticker so the kakao bot can
reuse the same ticker-resolution logic. These assertions must keep passing,
unmodified, once the extraction lands — only the import/binding under test
changes.
"""
from types import SimpleNamespace

import pytest

from telegram_ai_bot import TelegramAIBot


def make_stub(stock_map=None, stock_name_map=None):
    """Minimal stand-in for a TelegramAIBot instance carrying only the two
    attributes get_stock_code reads (self.stock_map, self.stock_name_map)."""
    return SimpleNamespace(
        stock_map=stock_map if stock_map is not None else {},
        stock_name_map=stock_name_map if stock_name_map is not None else {},
    )


async def call(stub, stock_input):
    return await TelegramAIBot.get_stock_code(stub, stock_input)


# --- Empty / None / non-string input ---

@pytest.mark.asyncio
async def test_empty_string_input():
    stub = make_stub()
    code, name, err = await call(stub, "")
    assert code is None
    assert name is None
    assert err == "종목명이나 코드를 입력해주세요."


@pytest.mark.asyncio
async def test_none_input():
    stub = make_stub()
    code, name, err = await call(stub, None)
    assert code is None
    assert name is None
    assert err == "종목명이나 코드를 입력해주세요."


@pytest.mark.asyncio
async def test_non_string_input_is_coerced_and_processed():
    # A non-string, non-empty input (e.g. an int) is coerced via str() and
    # then processed normally (falls through to the search paths below).
    stub = make_stub(stock_name_map={"삼성전자": "005930"})
    code, name, err = await call(stub, 12345)
    # "12345" is not a 6-digit code (only 5 digits) and doesn't match any name.
    assert code is None
    assert name is None
    assert err == "'12345'에 해당하는 종목을 찾을 수 없습니다. 정확한 종목명이나 코드를 입력해주세요."


# --- stock_name_map defense code ---

@pytest.mark.asyncio
async def test_stock_name_map_is_none():
    stub = make_stub()
    stub.stock_name_map = None
    code, name, err = await call(stub, "삼성전자")
    assert code is None
    assert name is None
    assert err == "시스템 오류: 주식 데이터가 로드되지 않았습니다."


@pytest.mark.asyncio
async def test_stock_name_map_wrong_type():
    stub = make_stub()
    stub.stock_name_map = ["not", "a", "dict"]
    code, name, err = await call(stub, "삼성전자")
    assert code is None
    assert name is None
    assert err == "시스템 오류: 주식 데이터 형식이 잘못되었습니다."


# --- 6-digit numeric code path ---

@pytest.mark.asyncio
async def test_six_digit_code_found_in_stock_map():
    stub = make_stub(stock_map={"005930": "삼성전자"}, stock_name_map={"삼성전자": "005930"})
    code, name, err = await call(stub, "005930")
    assert code == "005930"
    assert name == "삼성전자"
    assert err is None


@pytest.mark.asyncio
async def test_six_digit_code_not_found_in_stock_map():
    stub = make_stub(stock_map={}, stock_name_map={"삼성전자": "005930"})
    code, name, err = await call(stub, "999999")
    assert code == "999999"
    assert name == "종목_999999"
    assert err == "해당 종목 코드에 대한 정보가 없습니다. 코드가 정확한지 확인해주세요."


@pytest.mark.asyncio
async def test_input_with_whitespace_is_stripped_before_code_check():
    stub = make_stub(stock_map={"005930": "삼성전자"}, stock_name_map={"삼성전자": "005930"})
    code, name, err = await call(stub, "  005930  ")
    assert code == "005930"
    assert name == "삼성전자"
    assert err is None


# --- Exact name match ---

@pytest.mark.asyncio
async def test_exact_name_match():
    stub = make_stub(stock_name_map={"삼성전자": "005930", "삼성전자우": "005935"})
    code, name, err = await call(stub, "삼성전자")
    assert code == "005930"
    assert name == "삼성전자"
    assert err is None


# --- Partial match: single result ---

@pytest.mark.asyncio
async def test_partial_match_single_result():
    stub = make_stub(stock_name_map={"삼성전자우": "005935", "SK하이닉스": "000660"})
    code, name, err = await call(stub, "삼성전자")
    assert code == "005935"
    assert name == "삼성전자우"
    assert err is None


@pytest.mark.asyncio
async def test_partial_match_is_case_insensitive():
    stub = make_stub(stock_name_map={"NAVER": "035420"})
    code, name, err = await call(stub, "naver")
    assert code == "035420"
    assert name == "NAVER"
    assert err is None


# --- Partial match: multiple results ---

@pytest.mark.asyncio
async def test_partial_match_multiple_results_error_message_lists_up_to_five():
    stub = make_stub(
        stock_name_map={
            "삼성전자": "005930",
            "삼성전자우": "005935",
            "삼성SDI": "006400",
        }
    )
    code, name, err = await call(stub, "삼성")
    assert code is None
    assert name is None
    expected = (
        "'삼성'에 해당하는 종목이 여러 개 있습니다. 정확한 종목명이나 코드를 입력해주세요:\n"
        "삼성전자 (005930)\n삼성전자우 (005935)\n삼성SDI (006400)"
    )
    assert err == expected


@pytest.mark.asyncio
async def test_partial_match_more_than_five_results_truncates_with_count():
    stock_name_map = {f"삼성{i}": f"00{i:04d}" for i in range(7)}
    stub = make_stub(stock_name_map=stock_name_map)
    code, name, err = await call(stub, "삼성")
    assert code is None
    assert name is None
    assert err.startswith("'삼성'에 해당하는 종목이 여러 개 있습니다. 정확한 종목명이나 코드를 입력해주세요:\n")
    assert err.endswith("... 외 2개")
    # Exactly 5 "name (code)" lines shown before the truncation marker.
    body = err.split(":\n", 1)[1]
    lines = body.split("\n")
    assert len(lines) == 6  # 5 match lines + the "... 외 2개" line


# --- No match found ---

@pytest.mark.asyncio
async def test_no_match_found():
    stub = make_stub(stock_name_map={"삼성전자": "005930"})
    code, name, err = await call(stub, "존재하지않는종목")
    assert code is None
    assert name is None
    assert err == "'존재하지않는종목'에 해당하는 종목을 찾을 수 없습니다. 정확한 종목명이나 코드를 입력해주세요."


# --- Malformed entries inside stock_name_map are skipped during partial search ---

@pytest.mark.asyncio
async def test_partial_match_skips_non_string_entries():
    stub = make_stub(
        stock_name_map={
            "삼성전자우": "005935",
            123: "999999",  # non-string key, must be skipped without raising
            "다른종목": 456,  # non-string value, must be skipped without raising
        }
    )
    code, name, err = await call(stub, "삼성전자")
    assert code == "005935"
    assert name == "삼성전자우"
    assert err is None
