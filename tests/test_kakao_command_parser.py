"""Command parsing rules for Kakao utterances.

The grammar has to serve two shapes of input that the platform forces on us:
a tapped ListCard item sends its *title* ("삼성전자 리포트"), while a typed
command leads with the verb ("리포트 삼성전자"). Both must parse identically.
"""

from __future__ import annotations

import pytest

from kakao_bot.application.command_parser import (
    CommandKind,
    parse_command,
)


@pytest.mark.parametrize(
    "utterance",
    [
        "삼성전자 리포트",  # tapped card title
        "리포트 삼성전자",  # typed command
        "@prism-test 리포트 삼성전자",  # mention tolerated
        "  리포트   삼성전자  ",  # whitespace
    ],
)
def test_report_parses_regardless_of_word_order_or_mention(utterance):
    command = parse_command(utterance)

    assert command.kind is CommandKind.REPORT
    assert command.query == "삼성전자"
    assert command.market is None, "한글 종목명의 시장은 resolver가 정한다"


def test_six_digit_code_is_recognized_as_kr():
    command = parse_command("리포트 005930")

    assert command.kind is CommandKind.REPORT
    assert command.query == "005930"
    assert command.market == "kr"


def test_alphabetic_ticker_is_recognized_as_us():
    command = parse_command("리포트 AAPL")

    assert command.kind is CommandKind.REPORT
    assert command.query == "AAPL"
    assert command.market == "us"


@pytest.mark.parametrize("utterance", ["미국리포트 TSLA", "us리포트 TSLA"])
def test_us_prefixed_keyword_forces_us_market(utterance):
    command = parse_command(utterance)

    assert command.kind is CommandKind.REPORT
    assert command.query == "TSLA"
    assert command.market == "us"


def test_standalone_market_hint_is_consumed_not_treated_as_ticker():
    command = parse_command("리포트 미국 애플")

    assert command.kind is CommandKind.REPORT
    assert command.query == "애플"
    assert command.market == "us"


def test_keyword_matching_is_case_insensitive():
    command = parse_command("REPORT AAPL")

    assert command.kind is CommandKind.REPORT
    assert command.query == "AAPL"


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("순위", CommandKind.LEADERBOARD),
        ("리더보드", CommandKind.LEADERBOARD),
        ("랭킹", CommandKind.LEADERBOARD),
        ("예측", CommandKind.PREDICTION),
        ("도움말", CommandKind.HELP),
        ("help", CommandKind.HELP),
    ],
)
def test_argumentless_commands(utterance, expected):
    command = parse_command(utterance)

    assert command.kind is expected
    assert command.query is None


class TestEvaluate:
    def test_full_form(self):
        command = parse_command("평가 삼성전자 70000 6")

        assert command.kind is CommandKind.EVALUATE
        assert command.query == "삼성전자"
        assert command.avg_price == 70000.0
        assert command.period_months == 6

    def test_period_suffix_is_accepted(self):
        command = parse_command("평가 삼성전자 70000 6개월")

        assert command.avg_price == 70000.0
        assert command.period_months == 6

    def test_thousands_separator_in_price(self):
        command = parse_command("평가 삼성전자 70,000 6")

        assert command.avg_price == 70000.0

    def test_decimal_price_for_us(self):
        command = parse_command("us평가 AAPL 180.5 3")

        assert command.market == "us"
        assert command.query == "AAPL"
        assert command.avg_price == 180.5
        assert command.period_months == 3

    def test_period_is_optional(self):
        command = parse_command("평가 삼성전자 70000")

        assert command.avg_price == 70000.0
        assert command.period_months is None

    def test_six_digit_code_is_kept_as_ticker_not_eaten_as_price(self):
        command = parse_command("평가 005930 70000 6")

        assert command.query == "005930"
        assert command.market == "kr"
        assert command.avg_price == 70000.0
        assert command.period_months == 6

    def test_six_digit_code_survives_when_period_is_omitted(self):
        command = parse_command("평가 005930 70000")

        assert command.query == "005930"
        assert command.avg_price == 70000.0
        assert command.period_months is None

    def test_missing_numbers_still_parses_as_evaluate(self):
        """The caller decides how to prompt for what is missing."""

        command = parse_command("평가 삼성전자")

        assert command.kind is CommandKind.EVALUATE
        assert command.query == "삼성전자"
        assert command.avg_price is None


@pytest.mark.parametrize(
    "utterance",
    ["", "   ", "안녕하세요", "오늘 날씨 어때", "@prism-test", None, 123],
)
def test_unrecognized_input_is_unknown_and_never_raises(utterance):
    command = parse_command(utterance)

    assert command.kind is CommandKind.UNKNOWN
    assert command.is_actionable is False


def test_actionable_flag():
    assert parse_command("리포트 삼성전자").is_actionable is True
