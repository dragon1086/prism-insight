"""Parse a Kakao utterance into a command, with no Kakao types involved.

Two things about the platform shape this parser (see
`docs/superpowers/specs/2026-07-27-kakao-live-contract-findings.md`):

Tapping a ListCard item sends the item's *title* as the utterance, so the text
a user reads is also the text we parse. Card titles therefore read naturally
("삼성전자 리포트") while typed commands tend to lead with the verb
("리포트 삼성전자"). Both must mean the same thing, so keyword position is
free.

Kakao strips the bot mention before delivery, but a leading mention is
tolerated anyway so that hand-typed input behaves the same as a tap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

KR_MARKET = "kr"
US_MARKET = "us"

_MENTION = re.compile(r"^@\S+\s*")
_KR_CODE = re.compile(r"^\d{6}$")
_US_TICKER = re.compile(r"^[A-Za-z]{1,5}$")
_NUMERIC = re.compile(r"^\d+(?:[.,]\d+)?$")
_PERIOD = re.compile(r"^(\d+)\s*(?:개월|달|months?|m)?$", re.IGNORECASE)


class CommandKind(Enum):
    REPORT = "report"
    EVALUATE = "evaluate"
    ASK = "ask"
    HELP = "help"
    UNKNOWN = "unknown"


# Longest-first within each kind so that "미국리포트" is not shadowed by "리포트".
_KEYWORDS: tuple[tuple[str, CommandKind, str | None], ...] = (
    ("미국리포트", CommandKind.REPORT, US_MARKET),
    ("해외리포트", CommandKind.REPORT, US_MARKET),
    ("us리포트", CommandKind.REPORT, US_MARKET),
    ("리포트", CommandKind.REPORT, None),
    ("레포트", CommandKind.REPORT, None),
    ("보고서", CommandKind.REPORT, None),
    ("report", CommandKind.REPORT, None),
    ("미국평가", CommandKind.EVALUATE, US_MARKET),
    ("us평가", CommandKind.EVALUATE, US_MARKET),
    ("평가", CommandKind.EVALUATE, None),
    ("evaluate", CommandKind.EVALUATE, None),
    ("질문", CommandKind.ASK, None),
    ("물어봐", CommandKind.ASK, None),
    ("ask", CommandKind.ASK, None),
    ("도움말", CommandKind.HELP, None),
    ("help", CommandKind.HELP, None),
)

_MARKET_HINTS: tuple[tuple[str, str], ...] = (
    ("미국", US_MARKET),
    ("해외", US_MARKET),
    ("국내", KR_MARKET),
    ("한국", KR_MARKET),
)


@dataclass(frozen=True)
class ParsedCommand:
    """A command with its arguments; tickers are *not* resolved here."""

    kind: CommandKind
    query: str | None = None
    market: str | None = None
    avg_price: float | None = None
    period_months: int | None = None

    @property
    def is_actionable(self) -> bool:
        return self.kind is not CommandKind.UNKNOWN


def parse_command(utterance: str) -> ParsedCommand:
    """Parse one utterance. Never raises; unparseable input is ``UNKNOWN``."""

    if not isinstance(utterance, str):
        return ParsedCommand(kind=CommandKind.UNKNOWN)

    text = _MENTION.sub("", utterance.strip())
    if not text:
        return ParsedCommand(kind=CommandKind.UNKNOWN)

    tokens = text.split()
    kind, market, rest = _take_keyword(tokens)
    if kind is None:
        return ParsedCommand(kind=CommandKind.UNKNOWN)

    if kind is CommandKind.ASK:
        # A free-form question is prose, not arguments. "질문 미국 금리 언제
        # 내려?" is about US rates, not a US ticker lookup — stripping "미국" as
        # a market hint would silently change what the user asked. Retrieval for
        # ask is market-agnostic anyway (no ticker to resolve), so nothing here
        # is consumed.
        return ParsedCommand(kind=kind, query=" ".join(rest).strip() or None)

    market_hint, rest = _take_market_hint(rest)
    market = market or market_hint

    if kind is CommandKind.EVALUATE:
        return _parse_evaluate(market, rest)

    query = " ".join(rest).strip() or None
    return ParsedCommand(
        kind=kind,
        query=query,
        market=market or _infer_market(query),
    )


def _take_keyword(
    tokens: list[str],
) -> tuple[CommandKind | None, str | None, list[str]]:
    """Find the command keyword anywhere in the utterance and remove it.

    Position is free because a tapped card title reads "삼성전자 리포트" while a
    typed command reads "리포트 삼성전자".
    """

    for index, token in enumerate(tokens):
        normalized = token.strip().lower()
        for keyword, kind, market in _KEYWORDS:
            if normalized == keyword.lower():
                return kind, market, tokens[:index] + tokens[index + 1 :]
    return None, None, tokens


def _take_market_hint(tokens: list[str]) -> tuple[str | None, list[str]]:
    for index, token in enumerate(tokens):
        for hint, market in _MARKET_HINTS:
            if token == hint:
                return market, tokens[:index] + tokens[index + 1 :]
    return None, tokens


def _parse_evaluate(market: str | None, tokens: list[str]) -> ParsedCommand:
    """``평가 <종목> <평단가> [기간]`` — trailing numbers are the arguments."""

    numbers: list[str] = []
    while tokens and (_NUMERIC.match(tokens[-1]) or _PERIOD.match(tokens[-1])):
        # A six-digit KR code is a ticker, not a price.
        if _KR_CODE.match(tokens[-1]) and len(numbers) >= 1:
            break
        numbers.insert(0, tokens.pop())
        if len(numbers) == 2:
            break

    avg_price: float | None = None
    period: int | None = None
    if numbers:
        avg_price = _to_price(numbers[0])
    if len(numbers) == 2:
        period = _to_period(numbers[1])

    query = " ".join(tokens).strip() or None
    return ParsedCommand(
        kind=CommandKind.EVALUATE,
        query=query,
        market=market or _infer_market(query),
        avg_price=avg_price,
        period_months=period,
    )


def _to_price(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _to_period(token: str) -> int | None:
    match = _PERIOD.match(token)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _infer_market(query: str | None) -> str | None:
    """Infer the market from the ticker's shape only.

    A plain Korean company name yields ``None`` — resolution belongs to the
    ticker resolver, not here.
    """

    if not query:
        return None
    if _KR_CODE.match(query):
        return KR_MARKET
    if _US_TICKER.match(query):
        return US_MARKET
    return None
