"""Disabled, pure no-order fast-lane comparison; not a scheduler or fill model.

Source cron matching is not an exchange calendar. The caller must supply frozen
session evidence and timestamped quotes/bars; this module retrieves nothing.
Only deterministic ordinary TIER1 stops differ. Agent/fallback exits are outside
this component and must never be vetoed by its WAIT/UNKNOWN observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from cores.oneil_fallback import SellInputs, evaluate_tier1_hardstop

POLICY = "stop-fastlane-5m-v1"
ENABLED = False
_ZONES = {"KR": "Asia/Seoul", "US": "America/New_York"}


@dataclass(frozen=True)
class Holding:
    holding_id: str
    symbol: str
    buy_price: float
    stop_loss: float
    entered_at: datetime


@dataclass(frozen=True)
class Quote:
    price: float
    observed_at: datetime
    available_at: datetime
    source_id: str


@dataclass(frozen=True)
class Bar:
    start: datetime
    end: datetime
    close: float
    available_at: datetime
    source_id: str


@dataclass(frozen=True)
class Session:
    opens_at: datetime
    closes_at: datetime
    source_id: str


@dataclass(frozen=True)
class Observation:
    holding_id: str
    at: datetime
    baseline: str
    candidate: str
    catastrophic: bool | None
    reason: str
    quote_source_id: str | None
    bar_source_id: str | None
    session_source_id: str | None
    source_cadence_match: bool


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _finite(value: object, *, zero: bool = False) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (value >= 0 if zero else value > 0))


def source_cadence_matches(market: str, at: datetime) -> bool:
    """Frozen source cron union, including pre/post-session cron matches.

    KR: 0-50/10 and 6-56/10, hours 9-15 weekdays, Seoul.
    US: 4-54/10 and 8-58/10, hours 9-16 weekdays, New York (DST aware).
    This does not assert that production has an exchange-holiday guard.
    """
    if market not in _ZONES or not _aware(at):
        raise ValueError("invalid market or timestamp")
    local = at.astimezone(ZoneInfo(_ZONES[market]))
    residues, last_hour = ((0, 6), 15) if market == "KR" else ((4, 8), 16)
    return (local.weekday() < 5 and 9 <= local.hour <= last_hour
            and local.minute % 10 in residues)


def observe(holdings: Sequence[Holding], quotes: Mapping[str, Quote],
            bars: Mapping[str, Bar], *, at: datetime, market: str,
            session: Session | None, max_quote_age_seconds: float) -> tuple[Observation, ...]:
    """Evaluate every supplied holding once, with one shared source quote.

    Quote-age tolerance is explicit caller policy, never a hidden global tuning.
    The bar must be the latest completed aligned five-minute regular-session bar
    available by this observation. No continuous-breach inference or execution
    price is produced. Missing observations cannot be backfilled as successes.
    """
    cadence = source_cadence_matches(market, at)
    if not _finite(max_quote_age_seconds, zero=True):
        raise ValueError("invalid quote age limit")
    ids = [h.holding_id for h in holdings]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid or duplicate holding identity")
    valid_session = (isinstance(session, Session) and bool(session.source_id)
                     and _aware(session.opens_at) and _aware(session.closes_at)
                     and session.opens_at <= at <= session.closes_at)
    output = []
    for holding in holdings:
        quote = quotes.get(holding.symbol)
        bar = bars.get(holding.symbol)

        def result(baseline, candidate, catastrophic, reason):
            return Observation(holding.holding_id, at, baseline, candidate,
                               catastrophic, reason,
                               quote.source_id if isinstance(quote, Quote) else None,
                               bar.source_id if isinstance(bar, Bar) else None,
                               session.source_id if isinstance(session, Session) else None,
                               cadence)

        if (not _finite(holding.buy_price) or not _finite(holding.stop_loss, zero=True)
                or not _aware(holding.entered_at) or holding.entered_at > at):
            output.append(result("UNKNOWN", "UNKNOWN", None, "HOLDING_INVALID"))
            continue
        if (not isinstance(quote, Quote) or not quote.source_id or not _finite(quote.price)
                or not _aware(quote.observed_at) or not _aware(quote.available_at)
                or not holding.entered_at <= quote.observed_at <= quote.available_at <= at
                or (at - quote.observed_at).total_seconds() > max_quote_age_seconds):
            output.append(result("UNKNOWN", "UNKNOWN", None, "QUOTE_UNAVAILABLE_OR_STALE"))
            continue
        baseline, _ = evaluate_tier1_hardstop(
            SellInputs(holding.buy_price, quote.price, holding.stop_loss))
        # Disable only scenario threshold to query the canonical absolute rule.
        catastrophic, _ = evaluate_tier1_hardstop(SellInputs(holding.buy_price, quote.price))
        if catastrophic:
            output.append(result("EXIT", "EXIT", True, "CATASTROPHIC"))
        elif not baseline:
            output.append(result("HOLD", "HOLD", False, "NO_CURRENT_BREACH"))
        elif not valid_session:
            # Production hardstop has no calendar guard: do not suppress its
            # baseline or catastrophic evaluation when session data is absent.
            output.append(result("EXIT", "UNKNOWN", False, "SESSION_UNAVAILABLE_OR_CLOSED"))
        else:
            local = at.astimezone(ZoneInfo(_ZONES[market]))
            latest_end = local.replace(minute=local.minute // 5 * 5, second=0, microsecond=0)
            valid_bar = (isinstance(bar, Bar) and bool(bar.source_id) and _finite(bar.close)
                         and all(_aware(t) for t in (bar.start, bar.end, bar.available_at))
                         and bar.end == latest_end and bar.end - bar.start == timedelta(minutes=5)
                         and max(session.opens_at, holding.entered_at) <= bar.start
                         and bar.end <= session.closes_at and bar.end <= bar.available_at <= at)
            if not valid_bar:
                output.append(result("EXIT", "UNKNOWN", False, "COMPLETED_BAR_UNAVAILABLE"))
            else:
                confirmed, _ = evaluate_tier1_hardstop(
                    SellInputs(holding.buy_price, bar.close, holding.stop_loss))
                output.append(result("EXIT", "EXIT" if confirmed else "WAIT", False,
                                     "ORDINARY_CONFIRMED" if confirmed else "ORDINARY_UNCONFIRMED"))
    return tuple(output)
