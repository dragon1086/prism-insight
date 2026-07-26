"""Closed, deterministic parsing for read-only Telegram queries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class CommandName(str, Enum):
    HELP = "help"
    STATUS = "status"
    DAILY = "daily"
    WEEKLY = "weekly"
    SYMBOL = "symbol"
    PORTFOLIO = "portfolio"
    PAPER = "paper"
    HEALTH = "health"
    SEARCH = "search"


ALLOWED_COMMANDS = frozenset(
    {
        CommandName.HELP,
        CommandName.STATUS,
        CommandName.DAILY,
        CommandName.WEEKLY,
        CommandName.SYMBOL,
        CommandName.PORTFOLIO,
        CommandName.PAPER,
        CommandName.HEALTH,
    }
)

_DENIED_SLASH_COMMANDS = frozenset({"buy", "sell", "cancel", "live"})
_MARKETS = frozenset({"KR", "US"})
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
_COMMAND = re.compile(r"^/([a-z]+)(?:@[A-Za-z0-9_]{5,32})?(?:\s+(.*))?$", re.DOTALL)
_DENIED_NATURAL_LANGUAGE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:^|\W)/(?:buy|sell|cancel|live)\b",
        r"\b(?:buy|sell)\b.{0,40}\b(?:position|stock|shares?|all)\b",
        r"\bcancel\b.{0,40}\border\b",
        r"\b(?:switch|enable|turn)\b.{0,30}\blive(?:\s+trading)?\b",
        r"\b(?:increase|raise|expand)\b.{0,30}\b(?:risk|exposure|limit)\b",
        r"\b(?:disable|bypass|turn\s+off)\b.{0,30}\bkill[ -]?switch\b",
        r"\b(?:credentials?|bot\s+token|api\s+keys?|passwords?)\b",
        r"\b(?:change|modify|mutate|update|reveal|show)\b.{0,40}\b(?:system\s+prompt|prompt|policy|configuration|config)\b",
        r"\b(?:approve|create|place|submit)\b.{0,40}\border\b",
        r"\borderintent\b",
        r"\bkis\s+demo\b.{0,30}\border\b",
        r"\bexternal\s+paper\b.{0,30}\bbroker\b",
    )
)


class CommandValidationError(ValueError):
    """Inbound text cannot be represented as a bounded read-only query."""


class DeniedCommandError(CommandValidationError):
    """Inbound text requests behavior outside the read-only boundary."""


@dataclass(frozen=True)
class ParsedTelegramQuery:
    command: CommandName
    arguments: tuple[str, ...] = ()
    natural_language: bool = False


def parse_telegram_query(text: object) -> ParsedTelegramQuery:
    """Map inbound text to a fixed query enum; never return an arbitrary tool name."""

    if not isinstance(text, str) or not text.strip():
        raise CommandValidationError("Telegram query must be non-empty text")
    normalized = text.strip()
    if normalized.startswith("/"):
        return _parse_slash_command(normalized)
    return _parse_natural_language(normalized)


def _parse_slash_command(text: str) -> ParsedTelegramQuery:
    match = _COMMAND.fullmatch(text)
    if match is None:
        raise CommandValidationError("Telegram command syntax is invalid")
    name = match.group(1).lower()
    if name in _DENIED_SLASH_COMMANDS:
        raise DeniedCommandError("Telegram interaction is read-only")
    try:
        command = CommandName(name)
    except ValueError:
        raise CommandValidationError("Telegram command is not allowlisted") from None
    if command not in ALLOWED_COMMANDS:
        raise CommandValidationError("Telegram command is not allowlisted")
    arguments = tuple((match.group(2) or "").split())
    return _validated(command, arguments, natural_language=False)


def _parse_natural_language(text: str) -> ParsedTelegramQuery:
    if any(pattern.search(text) for pattern in _DENIED_NATURAL_LANGUAGE):
        raise DeniedCommandError("Telegram interaction is read-only")
    lowered = text.casefold()
    market_match = re.search(r"\b(kr|us)\b", lowered)
    market = market_match.group(1).upper() if market_match else None

    if re.fullmatch(r"(?:show me (?:the )?)?daily(?: report)?\s+(?:kr|us)", lowered):
        return _validated(CommandName.DAILY, (market or "",), natural_language=True)
    if re.fullmatch(
        r"(?:show me (?:the )?)?weekly(?: (?:kr|us))?(?: report)?(?: (?:kr|us))?",
        lowered,
    ) and market is not None:
        return _validated(CommandName.WEEKLY, (market,), natural_language=True)
    symbol_match = re.fullmatch(r"(?:show me )?symbol\s+([A-Za-z0-9.-]+)", text, re.I)
    if symbol_match is not None:
        return _validated(
            CommandName.SYMBOL,
            (symbol_match.group(1),),
            natural_language=True,
        )
    if re.search(r"\bportfolio\b", lowered):
        return ParsedTelegramQuery(CommandName.PORTFOLIO, natural_language=True)
    if re.search(r"\bpaper\b", lowered):
        return ParsedTelegramQuery(CommandName.PAPER, natural_language=True)
    if re.search(r"\b(?:system\s+)?health\b", lowered):
        return ParsedTelegramQuery(CommandName.HEALTH, natural_language=True)
    if re.fullmatch(r"(?:help|what can you do)\??", lowered):
        return ParsedTelegramQuery(CommandName.HELP, natural_language=True)
    if re.search(r"\bstatus\b", lowered):
        return ParsedTelegramQuery(CommandName.STATUS, natural_language=True)
    return ParsedTelegramQuery(CommandName.SEARCH, (text,), natural_language=True)


def _validated(
    command: CommandName,
    arguments: tuple[str, ...],
    *,
    natural_language: bool,
) -> ParsedTelegramQuery:
    if command in {CommandName.DAILY, CommandName.WEEKLY}:
        if len(arguments) != 1 or arguments[0].upper() not in _MARKETS:
            raise CommandValidationError(f"/{command.value} requires exactly KR or US")
        arguments = (arguments[0].upper(),)
    elif command is CommandName.SYMBOL:
        if len(arguments) != 1:
            raise CommandValidationError("/symbol requires exactly one ticker")
        symbol = arguments[0].upper()
        if _SYMBOL.fullmatch(symbol) is None:
            raise CommandValidationError("/symbol contains an invalid ticker")
        arguments = (symbol,)
    elif arguments:
        raise CommandValidationError(f"/{command.value} does not accept arguments")
    return ParsedTelegramQuery(command, arguments, natural_language=natural_language)
