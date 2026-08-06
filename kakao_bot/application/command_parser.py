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
import unicodedata
from dataclasses import dataclass
from enum import Enum

KR_MARKET = "kr"
US_MARKET = "us"

_MENTION = re.compile(r"^@\S+\s*")
_KR_CODE = re.compile(r"^\d{6}$")
_US_TICKER = re.compile(r"^[A-Za-z]{1,5}$")
_PRICE = re.compile(r"^₩?(\d+(?:,\d{3})*(?:\.\d+)?)원?$")
# Marks that make a token part of a sentence rather than a name on its own.
_SENTENCE_MARK = re.compile(r"[?？!！.,、。]")
_PERIOD = re.compile(r"^(\d+)\s*(?:개월|달|months?|m)?$", re.IGNORECASE)
_NATURAL_EVALUATE = re.compile(
    r"(?<!\S)평가(?:\s*해)?\s*(?:줘|주세요|줄래|주실래)?[.!?？！。]*(?=\s|$)"
)
_EVALUATE_FILLERS = frozenset(
    {
        "보유종목",
        "종목",
        "평단",
        "평단가",
        "평균",
        "평균매수가",
        "매수가",
        "보유기간",
        "보유",
        "부탁해",
        "부탁해요",
    }
)
_REPORT_REQUEST_SUFFIXES = frozenset(
    {
        "써줘",
        "작성해줘",
        "만들어줘",
        "해주세요",
        "해줘",
        "부탁해",
        "부탁해요",
    }
)


class CommandKind(Enum):
    REPORT = "report"
    EVALUATE = "evaluate"
    ASK = "ask"
    HELP = "help"
    # No keyword was used. Which command this becomes depends on whether the
    # text names a stock, and only the ticker resolver knows that — so the
    # decision belongs to the service, not here (see `ParsedCommand`).
    NATURAL = "natural"
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
    # Free text after the numbers on an evaluate: the requested feedback style.
    tone: str | None = None
    # Only meaningful for NATURAL: whether the text is shaped like a single
    # stock reference and is therefore worth handing to the resolver. A whole
    # sentence is not, so "삼성전자 어때?" never becomes a report request.
    resembles_ticker: bool = False

    @property
    def is_actionable(self) -> bool:
        return self.kind is not CommandKind.UNKNOWN


def parse_command(utterance: str) -> ParsedCommand:
    """Parse one utterance. Never raises; unparseable input is ``UNKNOWN``."""

    if not isinstance(utterance, str):
        return ParsedCommand(kind=CommandKind.UNKNOWN)

    text = _MENTION.sub("", utterance.strip())
    if not text:
        # A bare mention is someone reaching for the bot without knowing what
        # to say. Showing what it can do is the whole answer; silence used to
        # be the answer, which reads as a broken bot.
        return ParsedCommand(kind=CommandKind.HELP)

    tokens = text.split()
    kind, market, rest = _take_keyword(tokens)
    if kind is None:
        natural_evaluate = _NATURAL_EVALUATE.search(text)
        if natural_evaluate is not None:
            kind = CommandKind.EVALUATE
            rest = (
                text[: natural_evaluate.start()] + text[natural_evaluate.end() :]
            ).split()
    if kind is None:
        # Kakao only delivers group messages the bot was mentioned in, so
        # anything that reaches here was aimed at the bot on purpose. Demanding
        # a keyword on top of the mention makes the user say "this is for you"
        # twice, and answering nothing when they get the grammar wrong is the
        # worst outcome available.
        return ParsedCommand(
            kind=CommandKind.NATURAL,
            query=text,
            market=_infer_market(text),
            resembles_ticker=_resembles_ticker(text),
        )

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

    if kind is CommandKind.REPORT:
        rest = _strip_report_request_suffixes(rest)

    query = " ".join(rest).strip() or None
    return ParsedCommand(
        kind=kind,
        query=query,
        market=market or _infer_market(query),
    )


def _strip_report_request_suffixes(tokens: list[str]) -> list[str]:
    """Drop polite request words that are not part of a stock name."""

    result = list(tokens)
    while result and result[-1].strip().lower() in _REPORT_REQUEST_SUFFIXES:
        result.pop()
    return result


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
    """``평가 <종목> <평단가> [기간] [말투]``.

    The numbers are located rather than taken off the end, because anything
    after them is a requested tone — "평가 삼성전자 70000 6 취한 친구처럼".
    Reading from the end would see "친구처럼", find no number, and silently
    drop the price the user did supply.
    """

    tokens = [
        cleaned
        for token in tokens
        if (cleaned := _clean_evaluate_token(token))
        and not _is_evaluate_filler(cleaned)
    ]

    stock: list[str] = []
    numbers: list[str] = []
    tone: list[str] = []

    for index, token in enumerate(tokens):
        if numbers:
            # Past the numbers: a second number is the holding period, anything
            # else begins the tone.
            if len(numbers) < 2 and _PERIOD.match(token):
                numbers.append(token)
            else:
                tone = tokens[index:]
                break
            continue
        # A six-digit KR code is a ticker, not a price, and a price cannot come
        # before the stock it belongs to.
        if stock and _PRICE.match(token) and not _KR_CODE.match(token):
            numbers.append(token)
            continue
        stock.append(token)

    avg_price = _to_price(numbers[0]) if numbers else None
    period = _to_period(numbers[1]) if len(numbers) == 2 else None

    query = " ".join(stock).strip() or None
    return ParsedCommand(
        kind=CommandKind.EVALUATE,
        query=query,
        market=market or _infer_market(query),
        avg_price=avg_price,
        period_months=period,
        tone=" ".join(tone).strip() or None,
    )


def _to_price(token: str) -> float | None:
    match = _PRICE.match(token)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _clean_evaluate_token(token: str) -> str:
    cleaned = token.strip()
    while cleaned and unicodedata.category(cleaned[0]).startswith("P"):
        cleaned = cleaned[1:]
    while cleaned and unicodedata.category(cleaned[-1]).startswith("P"):
        cleaned = cleaned[:-1]
    return cleaned


def _is_evaluate_filler(token: str) -> bool:
    if token in _EVALUATE_FILLERS:
        return True
    for particle in ("은", "는", "이", "가", "을", "를"):
        if token.endswith(particle) and token[: -len(particle)] in _EVALUATE_FILLERS:
            return True
    return False


def _to_period(token: str) -> int | None:
    match = _PERIOD.match(token)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _resembles_ticker(text: str) -> bool:
    """Is this one stock reference, rather than something said about one?

    Deliberately strict, because the resolver matches substrings: handing it a
    sentence risks "화장품" quietly resolving to whichever single stock happens
    to contain that word, and a keyword-free utterance has no explicit intent
    to justify that guess. One token, no sentence punctuation.

    An explicit `리포트 …` keeps the forgiving path — asking for a report is
    already an unambiguous intent, so a partial match there is helpful rather
    than surprising.
    """

    if len(text.split()) != 1:
        return False
    return not _SENTENCE_MARK.search(text)


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
