"""Fail-closed, free US individual-share directory and metadata screening.

Directory membership is only a preliminary filter: callers MUST additionally use
eligibility_reason before admitting a symbol. Corporate REIT common shares and
enterprise ADRs are allowed; pooled funds and preferred depositary shares are not.
No market-cap policy is implied: the caller must supply its approved USD minimum.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
import csv
import io
import math
import re

import requests


DIRECTORY_URLS = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
)
_EXCLUDED_NAME = re.compile(
    r"\b(?:ETF|ETN|ETP|funds?|portfolio|warrants?|rights?|"
    r"units?|debentures?|notes?|bonds?|closed[ -]end|exchange[ -]traded|"
    r"beneficial interest)\b", re.IGNORECASE,
)
_PREFERRED_SECURITY = re.compile(
    r"\b(?:preferred|preference)\s+(?:(?:ordinary|common|depositary)\s+)?"
    r"(?:stock|shares|securities)\b|\bdepositary shares\b.*\b(?:preferred|preference)\b",
    re.IGNORECASE,
)
_COMMON_NAME = re.compile(
    r"\b(?:common (?:stock|shares)|ordinary shares|american depositary (?:shares|receipts)|"
    r"american depository (?:shares|receipts)|ADR|ADS)\b", re.IGNORECASE,
)
_FUND_INDUSTRY = re.compile(
    r"\b(?:shell companies|closed[ -]end|exchange[ -]traded|mutual fund|"
    r"investment funds?|investment trusts?|blank[ -]check|SPAC)\b", re.IGNORECASE,
)


@dataclass(frozen=True)
class UniverseRecord:
    symbol: str
    name: str
    exchange: str


@dataclass
class UniverseResult:
    records: list[UniverseRecord]
    counts: dict[str, int]


def _excluded_security_name(name: str) -> bool:
    # Preferred Bank is an issuer, not preferred stock. A Nasdaq-style instrument
    # suffix can still explicitly identify preferred securities without "stock".
    suffix = name.partition(" - ")[2]
    return bool(
        _EXCLUDED_NAME.search(name)
        or _PREFERRED_SECURITY.search(name)
        or re.search(r"\b(?:preferred|preference)\b", suffix, re.IGNORECASE)
    )


def _rows(text: str, nasdaq: bool) -> tuple[list[dict[str, str]], date]:
    lines = text.strip().splitlines()
    if len(lines) < 3:
        raise ValueError("Directory missing data or creation-time footer")
    footer = re.fullmatch(r"File Creation Time: (\d{10}:\d{2})\|*", lines[-1])
    if not footer:
        raise ValueError("Invalid directory creation-time footer")
    try:
        created_date = datetime.strptime(footer.group(1), "%m%d%Y%H:%M").date()
    except ValueError as exc:
        raise ValueError("Invalid directory creation timestamp") from exc
    if any(line.startswith("File Creation Time:") for line in lines[:-1]):
        raise ValueError("Directory has an embedded footer")
    reader = csv.DictReader(io.StringIO("\n".join(lines[:-1])), delimiter="|")
    required = {"Security Name", "Test Issue", "ETF"}
    required |= {"Symbol", "Financial Status", "Market Category"} if nasdaq else {
        "ACT Symbol", "Exchange", "CQS Symbol", "NASDAQ Symbol",
    }
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not required.issubset(headers):
        raise ValueError("Directory headers missing or ambiguous")
    rows = list(reader)
    if not rows:
        raise ValueError("Directory contains no securities")
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Malformed directory row")
        if not row["Security Name"].strip():
            raise ValueError("Missing security name")
        if row["Test Issue"] not in {"Y", "N"} or row["ETF"] not in {"Y", "N"}:
            raise ValueError("Unknown directory test/ETF flag")
    return rows, created_date


def parse_directories(nasdaq_text: str, other_text: str) -> UniverseResult:
    """Parse both complete sources; malformed sources never silently degrade.

    Counts include source_rows, eligible_directory and excluded_<reason>. Symbols
    ending in W/R/U are NOT guessed to be derivatives. Only explicit names and
    flags establish security type; unknown names are excluded conservatively.
    """
    records = []
    counts = Counter(source_rows=0, eligible_directory=0)
    seen = set()
    nasdaq_rows, nasdaq_date = _rows(nasdaq_text, True)
    other_rows, other_date = _rows(other_text, False)
    if nasdaq_date != other_date:
        raise ValueError("Directory creation dates do not match")
    for nasdaq, rows in ((True, nasdaq_rows), (False, other_rows)):
        for row in rows:
            counts["source_rows"] += 1
            raw = row["Symbol" if nasdaq else "ACT Symbol"].strip()
            if not raw or len(raw) > 32 or not re.fullmatch(r"[A-Z][A-Z0-9.$^/+=-]*", raw):
                raise ValueError("Malformed directory symbol")
            symbol = raw.replace(".", "-")
            if symbol in seen:
                raise ValueError(f"Duplicate or ambiguous mapped symbol: {symbol}")
            seen.add(symbol)
            reason = None
            name = row["Security Name"].strip()
            if row["Test Issue"] == "Y":
                reason = "test_issue"
            elif row["ETF"] == "Y":
                reason = "etf"
            elif nasdaq and row["Market Category"] not in {"Q", "G", "S"}:
                reason = "exchange"
            elif not nasdaq and row["Exchange"] not in {"N", "A"}:
                reason = "exchange"
            elif nasdaq and row["Financial Status"] != "N":
                reason = "financial_status"
            elif _excluded_security_name(name):
                reason = "security_type"
            elif not _COMMON_NAME.search(name):
                reason = "unknown_security_type"
            elif not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z])?", raw):
                reason = "unsupported_symbol"
            if reason:
                counts[f"excluded_{reason}"] += 1
                continue
            exchange = "NASDAQ" if nasdaq else {"N": "NYSE", "A": "AMEX"}[row["Exchange"]]
            records.append(UniverseRecord(symbol, name, exchange))
            counts["eligible_directory"] += 1
    return UniverseResult(records, dict(counts))


def fetch_universe() -> UniverseResult:
    """Synchronous bounded download; async callers must offload to a thread.

    Both public sources are required. No credentials, retry loop, cached fallback,
    or index fallback is used; request/status/parser errors propagate to callers.
    """
    texts = []
    for url in DIRECTORY_URLS:
        response = requests.get(url, timeout=(5, 20))
        response.raise_for_status()
        texts.append(response.text)
    return parse_directories(texts[0], texts[1])


def _positive_finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def eligibility_reason(info: Mapping, min_market_cap_usd: float) -> str | None:
    """Return exclusion reason, or None only for complete individual-equity info.

    Must follow the directory filter. Yahoo quoteType alone is insufficient (some
    closed-end funds also say EQUITY). Missing sector/industry is unknown, not a
    healthy-company judgment. No earnings/debt thresholds are invented here.
    """
    if not _positive_finite(min_market_cap_usd):
        raise ValueError("An explicit finite positive USD market-cap minimum is required")
    if not isinstance(info, Mapping) or not info:
        return "missing_metadata"
    if info.get("quoteType") != "EQUITY":
        return "quote_type"
    if info.get("exchange") not in {"NMS", "NGM", "NCM", "NYQ", "ASE"}:
        return "exchange"
    if info.get("currency") != "USD":
        return "currency"
    name = info.get("longName") or info.get("shortName")
    sector, industry = info.get("sector"), info.get("industry")
    if not all(isinstance(value, str) and value.strip() for value in (name, sector, industry)):
        return "unknown_company_classification"
    if (
        _excluded_security_name(name)
        or _FUND_INDUSTRY.search(f"{sector} {industry}")
        or re.search(r"\b(?:shell company|blank[ -]check|SPAC)\b", name, re.IGNORECASE)
    ):
        return "non_operating_equity"
    if info.get("fundFamily") or info.get("legalType") or info.get("category"):
        return "fund_metadata"
    cap = info.get("marketCap")
    if not _positive_finite(cap):
        return "missing_market_cap"
    if cap < min_market_cap_usd:
        return "market_cap"
    return None
