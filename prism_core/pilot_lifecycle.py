"""Pure SHADOW-only pilot policy. No broker, clock, network or scheduler I/O.

Calendar provenance is a caller attestation, NOT external verification here.
An integration must authenticate its source and supply consecutive exchange
sessions, including holiday omissions and early closes; never infer weekdays.
"""
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from zoneinfo import ZoneInfo

from prism_core.entry_score_policy import min_score_floor

OWNER = "split-pilot-v1"
TERMINAL = frozenset({"FULL_100", "ADD_EXPIRED", "ADD_CANCELLED", "EXITED"})
GATES = ("risk", "regime", "pulse", "risk_reward", "sector", "slot")
MAX_EVIDENCE_AGE_SECONDS = 120


def _clock(value):
    if not isinstance(value, str):
        raise ValueError("aware evidence clock required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("aware evidence clock required")
    return result.astimezone(timezone.utc)


def _number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("finite nonnegative number required") from None
    if isinstance(value, bool) or not number.is_finite() or number < 0:
        raise ValueError("finite nonnegative number required")
    return number


def _positive(value):
    number = _number(value)
    if not number:
        raise ValueError("positive price required")
    return number


def _calendar(calendar, market):
    expected_zone = {"KR": "Asia/Seoul", "US": "America/New_York"}.get(market)
    if not expected_zone or not isinstance(calendar, dict) or calendar.get("market") != market or calendar.get("timezone") != expected_zone:
        raise ValueError("calendar market/timezone mismatch")
    if calendar.get("verification_status") != "VERIFIED" or calendar.get("source_kind") not in {"exchange_official_snapshot", "exchange_calendar", "test_fixture"}:
        raise ValueError("verified calendar provenance required")
    if not isinstance(calendar.get("source_ref"), str) or not calendar["source_ref"].strip():
        raise ValueError("calendar source reference required")
    sessions = calendar.get("sessions")
    if not isinstance(sessions, list) or not 4 <= len(sessions) <= 128:
        raise ValueError("bounded consecutive calendar sessions required")
    zone = ZoneInfo(expected_zone)
    result, previous_close = [], None
    for session in sessions:
        if not isinstance(session, dict):
            raise ValueError("invalid calendar session")
        opened, closed = _clock(session.get("open_at")), _clock(session.get("close_at"))
        local_open, local_close = opened.astimezone(zone), closed.astimezone(zone)
        expected_open = time(9) if market == "KR" else time(9, 30)
        latest_close = time(15, 30) if market == "KR" else time(16)
        if (local_open.date().isoformat() != session.get("date") or local_close.date() != local_open.date()
                or local_open.time() != expected_open or local_close.time() > latest_close or closed <= opened
                or (previous_close is not None and opened <= previous_close)):
            raise ValueError("invalid regular session bounds/order")
        if result and result[-1]["date"] == session["date"]:
            raise ValueError("duplicate calendar session")
        result.append({"date": session["date"], "open_at": opened.isoformat(), "close_at": closed.isoformat()})
        previous_close = closed
    return result


def _session_at(sessions, moment):
    return next((s for s in sessions if _clock(s["open_at"]) <= moment < _clock(s["close_at"])), None)


def _bar_session(bar, sessions, now):
    if not isinstance(bar, dict) or bar.get("completed") is not True:
        raise ValueError("completed regular bar required")
    opened, closed = _clock(bar.get("open_at")), _clock(bar.get("close_at"))
    observed = _clock(bar.get("observed_at"))
    session = _session_at(sessions, opened)
    if not session or not opened < closed <= _clock(session["close_at"]) or not closed <= observed <= now:
        raise ValueError("invalid completed regular bar clock")
    high, close = _positive(bar.get("high")), _positive(bar.get("close"))
    if high < close:
        raise ValueError("inconsistent bar high/close")
    if "open" in bar and _positive(bar["open"]) > high:
        raise ValueError("inconsistent bar open/high")
    if "low" in bar:
        low = _positive(bar["low"])
        if low > close or low > high or ("open" in bar and low > _positive(bar["open"])):
            raise ValueError("inconsistent bar low")
    return session


def create_pilot(*, market, mode, owner, entry_at, entry_price, signal_bar, calendar, entry_eligible):
    if mode != "SHADOW" or owner != OWNER or entry_eligible is not True:
        raise ValueError("explicit eligible SHADOW split-pilot-v1 campaign required")
    entered, price = _clock(entry_at), _positive(entry_price)
    sessions = _calendar(calendar, market)
    session = _session_at(sessions, entered)
    if session is None:
        raise ValueError("pilot entry must be within a regular session")
    index = sessions.index(session)
    if index + 3 >= len(sessions):
        raise ValueError("three following sessions required for fixed expiry")
    high = None
    try:
        signal_session = _bar_session(signal_bar, sessions, entered)
        if signal_session == session:
            high = str(_positive(signal_bar.get("high")))
    except (ValueError, TypeError, AttributeError):
        pass  # Missing signal provenance can never authorize a later add.
    canonical = json.dumps(calendar, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "owner": OWNER, "policy_version": OWNER, "mode": mode, "market": market,
        "state": "PILOT_50", "revision": 0, "reason": "INITIAL_ELIGIBLE_50",
        "entry_at": entered.isoformat(), "entry_price": str(price), "entry_session": session["date"],
        "signal_bar_high": high, "expires_at": sessions[index + 3]["close_at"],
        "calendar_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "calendar_source_kind": calendar["source_kind"], "calendar_source_ref": calendar["source_ref"],
        "calendar_verification": "CALLER_ATTESTED_NOT_VERIFIED_BY_POLICY",
        "sessions": sessions, "last_evaluated_at": entered.isoformat(),
    }


def evaluate_pilot(pilot, evidence, *, now, cumulative_allocation, remaining_allocation, normalized_units):
    """Return an intent only. Durable owner/CAS enforcement belongs to ledger."""
    if pilot.get("mode") != "SHADOW" or pilot.get("owner") != OWNER or pilot.get("policy_version") != OWNER:
        raise ValueError("unowned or non-SHADOW campaign cannot advance")
    current = _clock(now)
    if current < _clock(pilot["last_evaluated_at"]):
        raise ValueError("out-of-order pilot evaluation")
    deployed, remaining, units = map(_number, (cumulative_allocation, remaining_allocation, normalized_units))

    def result(state, reason, delta="0"):
        return {"state": state, "reason": reason, "delta_allocation": delta,
                "evaluated_at": current.isoformat()}

    if not units:
        return result("EXITED", "STRATEGY_EXITED")
    if remaining < deployed:
        return result("ADD_CANCELLED", "STRATEGY_REDUCED")
    if pilot["state"] in TERMINAL:
        return result(pilot["state"], "TERMINAL_NO_RESUBMISSION")
    if deployed != Decimal(".5") or remaining != Decimal(".5"):
        return result("ADD_CANCELLED", "INVALID_PILOT_ALLOCATION")
    if current >= _clock(pilot["expires_at"]):
        return result("ADD_EXPIRED", "THIRD_FOLLOWING_SESSION_ENDED")
    try:
        if not isinstance(evidence, dict):
            raise ValueError("missing evidence")

        def fresh(value):
            observed = _clock(value)
            return 0 <= (current - observed).total_seconds() <= MAX_EVIDENCE_AGE_SECONDS

        if not fresh(evidence.get("observed_at")):
            return result("WAIT", "STALE_OR_FUTURE_EVIDENCE")
        gates = evidence.get("gates")
        gates = gates if isinstance(gates, dict) else {}
        if (evidence.get("thesis_valid") is False or evidence.get("prohibited_regime") is True
                or evidence.get("risk_failure_confirmed") is True or gates.get("risk") is False or gates.get("regime") is False):
            return result("ADD_CANCELLED", "CONFIRMED_THESIS_OR_RISK_FAILURE")
        current_session = _session_at(pilot["sessions"], current)
        if not current_session or current_session["date"] <= pilot["entry_session"]:
            return result("WAIT", "NOT_LATER_REGULAR_SESSION")
        if pilot["signal_bar_high"] is None:
            return result("WAIT", "MISSING_SIGNAL_BAR_PROVENANCE")
        bar = evidence.get("bar")
        bar_session = _bar_session(bar, pilot["sessions"], current)
        if bar_session["date"] <= pilot["entry_session"] or _clock(bar["open_at"]) <= _clock(pilot["entry_at"]):
            return result("WAIT", "BAR_NOT_AFTER_ENTRY")
        quote, ai = evidence.get("quote"), evidence.get("ai")
        if not isinstance(quote, dict) or not isinstance(ai, dict) or not fresh(quote.get("observed_at")) or not fresh(ai.get("observed_at")):
            return result("WAIT", "STALE_OR_MISSING_QUOTE_OR_AI")
        price = _positive(pilot["entry_price"])
        if _positive(bar.get("close")) <= max(price, _positive(pilot["signal_bar_high"])) or _positive(quote.get("price")) <= price:
            return result("WAIT", "NO_WINNING_BREAKOUT")
        regime, pulse = evidence.get("market_regime"), evidence.get("pulse")
        if (not isinstance(regime, str) or regime not in {"sideways", "moderate_bull", "strong_bull", "parabolic", "moderate_bear", "strong_bear"}
                or pulse not in {"UPTREND", "UNDER_PRESSURE", "CORRECTION"}):
            return result("WAIT", "MISSING_REGIME_OR_PULSE")
        score, requested_floor = _number(ai.get("score")), _number(ai.get("ordinary_min_score"))
        ordinary_floor = max(Decimal(7), Decimal(min_score_floor(regime, pulse)), requested_floor)
        if ai.get("decision") != "Enter" or score > 10 or requested_floor > 10 or score < ordinary_floor:
            return result("WAIT", "ORDINARY_ENTRY_SCORE_NOT_MET")
        if evidence.get("thesis_valid") is not True or any(gates.get(name) is not True for name in GATES):
            return result("WAIT", "INDEPENDENT_GATES_NOT_CONFIRMED")
        return result("FULL_100", "LATER_WINNING_BREAKOUT_CONFIRMED", str(1 - deployed))
    except (ValueError, TypeError, KeyError, AttributeError):
        return result("WAIT", "MISSING_OR_INVALID_EVIDENCE")
