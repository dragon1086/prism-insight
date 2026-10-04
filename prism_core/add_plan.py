"""Scenario-based micro-split add plans (KR/US): schema, validator and evaluator. Pure, no I/O.

The BUY agent writes ``add_plan`` with the entry and the holdings review rewrites
it for the next session (docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md). The LLM
decides where, on which conditions and up to which allocation; this module only
checks a closed condition vocabulary and executes it deterministically. The code
safety rails cannot be exceeded by any scenario: one slot cap, 5%p grid, at most
25%p and no more than the previous leg per add (pyramid), one add per session
(two in a deterministic ACCELERATION session: price >= initial entry +8% and
volume pace >= 1.5x, decided by code, never by the scenario label), the risk clip
versus the current stop on the initial-entry basis, profitable positions only, a
chase limit versus the trigger price, a plan valid for one session, and no add on
a sell/stop day. There is deliberately no market-regime ban (user decision, 2026-10-02).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

CONTRACT = "micro-split-add-plan-v1"
TYPES = ("breakout", "pullback_reclaim", "new_closing_high", "acceleration")
LENSES = ("oneil", "minervini", "druckenmiller", "buffett", "quant_risk", "systems")
TRIGGER_KEYS = frozenset({
    "price_above", "zone_low", "zone_high", "reclaim_above", "new_closing_high_lookback", "gap_up_min_pct",
    "hold_above_open_minutes", "volume_pace_min", "volume_dry_up_max", "confirm", "bars", "earliest_session",
    "hold_sessions"})
CONFIRMS = ("5m_close", "daily_close")
GRID = Decimal("0.05")
MAX_STEP = Decimal("0.25")
MAX_CHASE_PCT = Decimal("2.0")
MIN_SCENARIOS, MAX_SCENARIOS = 2, 4
HISTORY_LIMIT = 30
SESSION_PLANS = 3  # stored plans per holding (today, next session, one spare)
ZONE_WINDOW = 10  # completed sessions searched for the pullback touch
DRY_UP_WINDOW = 5  # completed sessions searched for the volume dry-up
AVERAGE_WINDOW = 20
MIN_PACE_SAMPLES = 10
# Acceleration rail (2026-10-04, user-approved design 2): a confirmed runner may add twice in
# one session so it can reach one slot in about three sessions instead of one small step per
# session. Decided by code from price and volume only; every other rail still applies.
ACCELERATION = "ACCELERATION"
ACCELERATION_GAIN = Decimal("0.08")  # price versus the initial entry
ACCELERATION_PACE = Decimal("1.5")  # matched intraday volume pace, or daily volume at the close
ADDS_PER_SESSION, ACCELERATION_ADDS_PER_SESSION = 1, 2
# Local session per market: zone, open, close, and the review cutoff before which a
# review revises the same session's plan (KR morning batch; US before the open).
SESSIONS = {"KR": (ZoneInfo("Asia/Seoul"), time(9, 0), time(15, 30), time(12, 0)),
            "US": (ZoneInfo("America/New_York"), time(9, 30), time(16, 0), time(9, 30))}
_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
_LENS_ALIASES = {"quantrisk": "quant_risk", "oneill": "oneil", "o'neil": "oneil"}
TYPE_LABELS = {"breakout": ("돌파", "breakout"), "pullback_reclaim": ("눌림 회복", "pullback reclaim"),
               "new_closing_high": ("종가 신고가", "new closing high"), "acceleration": ("가속", "acceleration")}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _dec(value):
    """Positive finite Decimal or None (bools and blanks are not numbers)."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _int(value, low, high):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number != number.to_integral_value() or not low <= number <= high:
        return None
    return int(number)


def _time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("aware timestamp required")
    return parsed.astimezone(timezone.utc)


def _num(value):
    """JSON-friendly number for stored prices (int when integral)."""
    return int(value) if value == value.to_integral_value() else float(value)


# ---------------------------------------------------------------- session dates

def next_weekday(day):
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


# Exchange calendars so a plan is never dated for a holiday (e.g. KR 2026-10-05, the
# National Foundation Day substitute). Falls back to weekdays if the calendar is missing.
_CALENDARS = {"KR": "XKRX", "US": "XNYS"}
USE_EXCHANGE_CALENDAR = True


@lru_cache(maxsize=8)
def _sessions(market, year):
    import pandas_market_calendars as calendars
    schedule = calendars.get_calendar(_CALENDARS[market]).schedule(
        start_date=f"{year - 1}-12-01", end_date=f"{year + 1}-01-31")
    return frozenset(ts.date() for ts in schedule.index)


def is_session(market, day):
    """True when ``day`` is a trading session of the market (weekday fallback)."""
    market = str(market).upper()
    if USE_EXCHANGE_CALENDAR and market in _CALENDARS:
        try:
            return day in _sessions(market, day.year)
        except Exception:  # noqa: BLE001 - calendar unavailable: weekday rule
            return day.weekday() < 5
    return day.weekday() < 5


def next_session(market, day):
    """The first trading session after ``day``."""
    day = next_weekday(day)
    for _ in range(15):
        if is_session(market, day):
            return day
        day = next_weekday(day)
    return day


def _local(market, at):
    return _time(at).astimezone(SESSIONS[str(market).upper()][0])


def entry_session_date(market, entered_at):
    """The session an entry belongs to: today before the close, else the next weekday (US reserved fill)."""
    local = _local(market, entered_at)
    if is_session(market, local.date()) and local.time() < SESSIONS[str(market).upper()][2]:
        return local.date()
    return next_session(market, local.date())


def buy_valid_for(market, entered_at):
    """A BUY plan is for the session after the entry session (no add on the entry day)."""
    return next_session(market, entry_session_date(market, entered_at)).isoformat()


def review_valid_for(market, now):
    """KR morning / US pre-open reviews revise today's plan; later reviews plan the next session.

    Sessions follow the exchange calendar, so a plan is never dated for a holiday.
    """
    local = _local(market, now)
    cutoff = SESSIONS[str(market).upper()][3]
    if is_session(market, local.date()) and local.time() < cutoff:
        return local.date().isoformat()
    return next_session(market, local.date()).isoformat()


def session_index(entry_session, session_date):
    """1 for the entry session, counting weekdays (holidays make it at most one larger)."""
    start, end = date.fromisoformat(str(entry_session)), date.fromisoformat(str(session_date))
    count, day = 1, start
    while day < end:
        day = next_weekday(day)
        count += 1
    return count


# ---------------------------------------------------------------- validation

def _drop(reason, sid=None):
    return None, {"id": sid, "reason": reason}


def _validate_scenario(raw, allocation, last_step):
    if not isinstance(raw, dict):
        return _drop("SCENARIO_NOT_OBJECT")
    sid = raw.get("id")
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        return _drop("INVALID_ID", sid if isinstance(sid, str) else None)
    kind = raw.get("type")
    if kind not in TYPES:
        return _drop("UNKNOWN_TYPE", sid)
    lens = raw.get("lens")
    lens = [lens] if isinstance(lens, str) else lens
    if not isinstance(lens, list):
        lens = []
    # Lens is an explanation label, never a condition: normalize spelling ("druck enmiller",
    # "Quant-Risk") and drop unknown labels instead of the executable scenario (2026-10-02 e2e).
    lens = list(dict.fromkeys(
        _LENS_ALIASES.get(key, key) for key in (re.sub(r"[\s_\-]", "", str(item)).lower() for item in lens)
        if _LENS_ALIASES.get(key, key) in LENSES))
    trigger = raw.get("trigger")
    if not isinstance(trigger, dict) or not trigger:
        return _drop("TRIGGER_MISSING", sid)
    unknown = sorted(set(trigger) - TRIGGER_KEYS)
    if unknown:
        return _drop(f"UNKNOWN_CONDITION:{unknown[0]}", sid)
    out = {}
    for key in ("price_above", "zone_low", "zone_high", "reclaim_above"):
        if key in trigger:
            value = _dec(trigger[key])
            if value is None:
                return _drop(f"INVALID_VALUE:{key}", sid)
            out[key] = _num(value)
    zone = [key in out for key in ("zone_low", "zone_high", "reclaim_above")]
    if any(zone) and not all(zone):
        return _drop("ZONE_INCOMPLETE", sid)
    if all(zone) and not out["zone_low"] < out["zone_high"] <= out["reclaim_above"]:
        return _drop("ZONE_ORDER", sid)
    ranges = {"gap_up_min_pct": (Decimal("0.5"), Decimal(15)), "volume_pace_min": (Decimal("0.5"), Decimal(10)),
              "volume_dry_up_max": (Decimal("0.1"), Decimal(1))}
    for key, (low, high) in ranges.items():
        if key in trigger:
            value = _dec(trigger[key])
            if value is None or not low <= value <= high:
                return _drop(f"INVALID_VALUE:{key}", sid)
            out[key] = float(value)
    integers = {"new_closing_high_lookback": (3, AVERAGE_WINDOW), "hold_above_open_minutes": (5, 390),
                "bars": (1, 6), "earliest_session": (2, 20), "hold_sessions": (1, 10)}
    for key, (low, high) in integers.items():
        if key in trigger:
            value = _int(trigger[key], low, high)
            if value is None:
                return _drop(f"INVALID_VALUE:{key}", sid)
            out[key] = value
    if out.get("hold_above_open_minutes", 5) % 5:
        return _drop("INVALID_VALUE:hold_above_open_minutes", sid)
    confirm = trigger.get("confirm", "5m_close")
    if confirm not in CONFIRMS:
        return _drop("INVALID_VALUE:confirm", sid)
    out["confirm"] = confirm
    if confirm == "5m_close":
        out.setdefault("bars", 2)  # one spiking bar never qualifies by default
    elif "bars" in out or "hold_above_open_minutes" in out:
        return _drop("CONFIRM_MISMATCH", sid)
    if not any(key in out for key in ("price_above", "reclaim_above", "new_closing_high_lookback",
                                       "gap_up_min_pct")):
        return _drop("NO_TRIGGER_LEVEL", sid)
    if "hold_sessions" in out and not ("price_above" in out or "reclaim_above" in out):
        return _drop("HOLD_SESSIONS_WITHOUT_LEVEL", sid)
    target = _dec(raw.get("target_allocation"))
    if target is None or target > 1:
        return _drop("INVALID_TARGET", sid)
    if target % GRID:
        return _drop("TARGET_NOT_ON_5PCT_GRID", sid)
    step = target - allocation
    if step <= 0:
        return _drop("TARGET_NOT_ABOVE_ALLOCATION", sid)
    if step > MAX_STEP:
        return _drop("STEP_TOO_LARGE", sid)
    if step > last_step:
        return _drop("PYRAMID_STEP", sid)
    chase = _dec(raw.get("max_chase_pct", MAX_CHASE_PCT))
    if chase is None:
        return _drop("INVALID_VALUE:max_chase_pct", sid)
    rationale = raw.get("rationale")
    scenario = {"id": sid, "type": kind, "lens": list(dict.fromkeys(lens)), "trigger": out,
                "target_allocation": str(target.quantize(Decimal("0.01"))),
                "max_chase_pct": float(min(chase, MAX_CHASE_PCT)),
                "rationale": rationale.strip()[:300] if isinstance(rationale, str) else ""}
    return scenario, ({"id": sid, "reason": "CHASE_CLAMPED"} if chase > MAX_CHASE_PCT else None)


def validate_plan(raw, *, market, source, created_at, valid_for, allocation, last_step):
    """(plan or None, issues). Invalid scenarios are dropped with a reason code, never executed.

    ``allocation`` is the current slot allocation, ``last_step`` the previous leg's
    allocation (the initial allocation before any add). A review may cancel with
    ``{"cancel": true, "reason": ...}``.
    """
    allocation, last_step = Decimal(str(allocation)), Decimal(str(last_step))
    base = {"contract": CONTRACT, "market": str(market).upper(), "source": source, "created_at": created_at,
            "valid_for": valid_for, "allocation_at_plan": str(allocation)}
    if not isinstance(raw, dict):
        return None, [{"id": None, "reason": "PLAN_NOT_OBJECT"}]
    if raw.get("cancel") is True:
        reason = raw.get("reason")
        plan = dict(base, status="CANCELLED", scenarios=[], dropped=[], thesis_check="",
                    invalidation={}, cancel_reason=reason.strip()[:300] if isinstance(reason, str) else "")
        plan["plan_hash"] = _hash(plan)
        return plan, []
    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, list) or not MIN_SCENARIOS <= len(scenarios) <= MAX_SCENARIOS:
        return None, [{"id": None, "reason": "PLAN_SCENARIO_COUNT"}]
    kept, issues, seen = [], [], set()
    for item in scenarios:
        scenario, issue = _validate_scenario(item, allocation, last_step)
        if scenario is not None and scenario["id"] in seen:
            scenario, issue = None, {"id": scenario["id"], "reason": "DUPLICATE_ID"}
        if issue:
            issues.append(issue)
        if scenario is not None:
            seen.add(scenario["id"])
            kept.append(scenario)
    invalidation, raw_invalidation = {}, raw.get("invalidation")
    if isinstance(raw_invalidation, dict):
        close_below = _dec(raw_invalidation.get("close_below"))
        if close_below is not None:
            invalidation["close_below"] = _num(close_below)
        stall = _int(raw_invalidation.get("stall_sessions"), 2, 20)
        if stall is not None:
            invalidation["stall_sessions"] = stall
        note = raw_invalidation.get("note")
        if isinstance(note, str) and note.strip():
            invalidation["note"] = note.strip()[:300]
    thesis = raw.get("thesis_check")
    plan = dict(base, status="ACTIVE" if kept else "EMPTY", scenarios=kept,
                dropped=[i for i in issues if i["reason"] != "CHASE_CLAMPED"], invalidation=invalidation,
                thesis_check=thesis.strip()[:300] if isinstance(thesis, str) else "")
    plan["plan_hash"] = _hash(plan)
    return plan, issues


def plan_intact(plan):
    if not isinstance(plan, dict) or plan.get("contract") != CONTRACT:
        return False
    content = {k: v for k, v in plan.items() if k != "plan_hash"}
    return plan.get("plan_hash") == _hash(content)


def history_entry(plan, issues, *, raw_hash):
    """Compact record kept on the holding row (raw JSON is summarized by its hash)."""
    entry = {"raw_hash": raw_hash, "issues": issues[:8]}
    if plan is not None:
        entry.update({k: plan.get(k) for k in ("plan_hash", "source", "created_at", "valid_for", "status")})
        entry["scenario_ids"] = [s["id"] for s in plan["scenarios"]]
    return entry


def store(block, plan, issues, *, raw, source, created_at):
    """Attach a validated plan and its history to a micro_split block in place.

    A failed validation keeps the previous plan; it is valid for its own session only.
    """
    history = list(block.get("add_plan_history") or [])
    entry = history_entry(plan, issues, raw_hash=_hash(raw) if raw is not None else None)
    entry.setdefault("source", source)
    entry.setdefault("created_at", created_at)
    history.append(entry)
    block["add_plan_history"] = history[-HISTORY_LIMIT:]
    if plan is not None:
        # Plans are kept per session: a review issuing tomorrow's plan during today's session
        # (US 10:15/14:30 ET, KR 14:46) must not replace the plan valid today (2026-10-02).
        plans = dict(block.get("add_plans") or {})
        if plan.get("status") == "CANCELLED":
            plans = {session: plan for session in plans}  # a sell decision cancels every pending plan
        plans[plan["valid_for"]] = plan
        block["add_plans"] = dict(sorted(plans.items())[-SESSION_PLANS:])
        block["add_plan"] = plan  # latest plan (messages, dashboard)
    return block


def plan_for_session(block, session_date):
    """The stored plan valid for ``session_date`` (latest per session), or None."""
    plan = (block.get("add_plans") or {}).get(session_date)
    if plan is None:
        latest = block.get("add_plan") or {}
        plan = latest if latest.get("valid_for") == session_date else None
    return plan


# ---------------------------------------------------------------- evaluation

def weighted_entry(legs):
    total = sum((Decimal(str(leg["allocation"])) for leg in legs), Decimal(0))
    units = sum((Decimal(str(leg["allocation"])) / Decimal(str(leg["price"])) for leg in legs), Decimal(0))
    return total / units


def risk_clip(*, legs, target, price, initial_entry, initial_stop, current_stop, fee_rate):
    """Largest target (5%p grid, <= ``target``) whose loss at the current stop stays within
    the initial-entry risk of one slot: (entry - stop) / entry.

    Same ledger math as ``oneil_adaptive_policy.evaluate_target`` (B3 risk clip).
    """
    fee, entry = Decimal(str(fee_rate)), Decimal(str(initial_entry))
    stop = max(Decimal(str(initial_stop)), Decimal(str(current_stop)))
    price = Decimal(str(price))
    limit = (entry - Decimal(str(initial_stop))) / entry
    deployed = sum((Decimal(str(leg["allocation"])) for leg in legs), Decimal(0))
    units = sum((Decimal(str(leg["allocation"])) / Decimal(str(leg["price"])) for leg in legs), Decimal(0))
    base_loss = deployed + deployed * fee - units * stop * (1 - fee)
    marginal = 1 + fee - stop / price * (1 - fee)
    if base_loss >= limit or marginal <= 0 or price <= stop:
        return deployed
    allowed = deployed + (limit - base_loss) / marginal
    capped = min(Decimal(str(target)), allowed)
    return max((capped / GRID).to_integral_value(rounding=ROUND_DOWN) * GRID, Decimal(0))


def _bars(evidence, now):
    rows = [b for b in evidence.get("today_bars") or [] if _time(b["end_at"]) <= now]
    return sorted(rows, key=lambda b: b["end_at"])


def _daily(evidence):
    return sorted(evidence.get("daily") or [], key=lambda b: b["date"])


def _average_volume(daily):
    window = [Decimal(str(b["volume"])) for b in daily[-AVERAGE_WINDOW:]]
    if len(window) < AVERAGE_WINDOW or not sum(window):
        return None
    return sum(window) / len(window)


def _volume_pace(evidence, bars, phase, daily):
    if phase == "CLOSE":
        average, today = _average_volume(daily), evidence.get("today_daily")
        return None if average is None or not today else Decimal(str(today["volume"])) / average
    prior = evidence.get("prior_cumulative") or {}
    if not bars or prior.get("end_at") != bars[-1]["end_at"]:
        return None
    samples = [Decimal(str(v)) for v in prior.get("samples") or []]
    if len(samples) < MIN_PACE_SAMPLES or not sum(samples):
        return None
    return sum((Decimal(str(b["volume"])) for b in bars), Decimal(0)) / (sum(samples) / len(samples))


def _confirm_above(level, trigger, bars, price, phase, today):
    """Close confirmation above ``level``: n completed 5m closes plus the quote, or the daily close."""
    level = Decimal(str(level))
    if trigger["confirm"] == "daily_close":
        return Decimal(str(today["close"])) > level
    recent = bars[-trigger["bars"]:]
    return (len(recent) == trigger["bars"] and all(Decimal(str(b["close"])) > level for b in recent)
            and price > level)


def _conditions(trigger, evidence, *, bars, daily, price, phase, now):
    """(trigger_price, None) when every condition holds, else (None, reason)."""
    today = evidence.get("today_daily") if phase == "CLOSE" else None
    if phase == "CLOSE" and not today:
        return None, "CLOSE_DATA_MISSING"
    if phase == "INTRADAY" and len(bars) < trigger.get("bars", 1):
        return None, "BARS_MISSING"
    levels = []
    if "price_above" in trigger:
        if not _confirm_above(trigger["price_above"], trigger, bars, price, phase, today):
            return None, "PRICE_NOT_ABOVE"
        levels.append(Decimal(str(trigger["price_above"])))
    if "reclaim_above" in trigger:
        window = daily[-ZONE_WINDOW:]
        lows = [Decimal(str(b["low"])) for b in window]
        if phase == "CLOSE":
            lows.append(Decimal(str(today["low"])))
        elif bars:
            lows.append(min(Decimal(str(b["low"])) for b in bars))
        if not lows or min(lows) > Decimal(str(trigger["zone_high"])):
            return None, "ZONE_NOT_TOUCHED"
        if any(Decimal(str(b["close"])) < Decimal(str(trigger["zone_low"])) for b in window):
            return None, "ZONE_UNDERCUT"
        if not _confirm_above(trigger["reclaim_above"], trigger, bars, price, phase, today):
            return None, "NOT_RECLAIMED"
        levels.append(Decimal(str(trigger["reclaim_above"])))
    if "new_closing_high_lookback" in trigger:
        lookback = trigger["new_closing_high_lookback"]
        if len(daily) < lookback:
            return None, "DAILY_HISTORY_SHORT"
        reference = max(Decimal(str(b["close"])) for b in daily[-lookback:])
        if not _confirm_above(reference, trigger, bars, price, phase, today):
            return None, "NO_NEW_CLOSING_HIGH"
        levels.append(reference)
    session_open = None
    if "gap_up_min_pct" in trigger or "hold_above_open_minutes" in trigger:
        if phase == "CLOSE":
            session_open = Decimal(str(today["open"]))
        elif bars and bars[0]["start_at"] and _time(bars[0]["start_at"]) == _time(evidence["open_at"]):
            session_open = Decimal(str(bars[0]["open"]))
        if session_open is None:
            return None, "SESSION_OPEN_MISSING"
    if "gap_up_min_pct" in trigger:
        if not daily:
            return None, "PREV_CLOSE_MISSING"
        previous = Decimal(str(daily[-1]["close"]))
        if session_open < previous * (1 + Decimal(str(trigger["gap_up_min_pct"])) / 100):
            return None, "GAP_NOT_MET"
        levels.append(session_open)
    if "hold_above_open_minutes" in trigger:
        minutes = trigger["hold_above_open_minutes"]
        if (now - _time(evidence["open_at"])) < timedelta(minutes=minutes) or len(bars) < minutes // 5:
            return None, "HOLD_ABOVE_OPEN_PENDING"
        if price < session_open or any(Decimal(str(b["close"])) < session_open for b in bars):
            return None, "LOST_SESSION_OPEN"
    if "volume_pace_min" in trigger:
        pace = _volume_pace(evidence, bars, phase, daily)
        if pace is None:
            return None, "VOLUME_PACE_UNAVAILABLE"
        if pace < Decimal(str(trigger["volume_pace_min"])):
            return None, "VOLUME_PACE_LOW"
    if "volume_dry_up_max" in trigger:
        average = _average_volume(daily)
        if average is None:
            return None, "VOLUME_HISTORY_SHORT"
        quietest = min(Decimal(str(b["volume"])) for b in daily[-DRY_UP_WINDOW:]) / average
        if quietest > Decimal(str(trigger["volume_dry_up_max"])):
            return None, "NO_VOLUME_DRY_UP"
    if "hold_sessions" in trigger:
        level = Decimal(str(trigger.get("price_above", trigger.get("reclaim_above"))))
        closes = [Decimal(str(b["close"])) for b in daily]
        if phase == "CLOSE":
            closes.append(Decimal(str(today["close"])))
        recent = closes[-trigger["hold_sessions"]:]
        if len(recent) < trigger["hold_sessions"] or any(close <= level for close in recent):
            return None, "HOLD_SESSIONS_NOT_MET"
    return max(levels), None


def _invalidated(plan, evidence, daily, phase, entry_session):
    rule = plan.get("invalidation") or {}
    closes = [(b["date"], Decimal(str(b["close"]))) for b in daily]
    if phase == "CLOSE" and evidence.get("today_daily"):
        closes.append((evidence["session_date"], Decimal(str(evidence["today_daily"]["close"]))))
    if "close_below" in rule and closes and closes[-1][1] < Decimal(str(rule["close_below"])):
        return "CLOSE_BELOW"
    if "stall_sessions" in rule:
        since = [close for day, close in closes if day >= str(entry_session)]
        if since:
            best = max(range(len(since)), key=lambda i: (since[i], i))
            if len(since) - 1 - best >= rule["stall_sessions"]:
                return "STALL"
    return None


def needs_intraday_pace(plan):
    """True when an active scenario confirms on 5m closes with a volume pace (providers fetch prior curves)."""
    return any("volume_pace_min" in s["trigger"] and s["trigger"]["confirm"] == "5m_close"
               for s in (plan or {}).get("scenarios") or [])


def acceleration_price(initial_entry):
    """Price from which the acceleration rail can apply (providers then fetch the volume pace)."""
    return float(Decimal(str(initial_entry)) * (1 + ACCELERATION_GAIN))


def acceleration(state, evidence, *, bars, daily, price, phase):
    """Deterministic ACCELERATION check: price >= initial entry +8% AND volume pace >= 1.5x.

    The pace is the cumulative volume versus the 20-session average at the same elapsed
    time (intraday), or the daily volume versus its 20-session average (CLOSE).
    """
    gain = Decimal(str(price)) / Decimal(str(state["initial_entry"])) - 1
    out = {"gain_pct": float((gain * 100).quantize(Decimal("0.01")))}
    if gain < ACCELERATION_GAIN:
        return dict(out, active=False, reason="GAIN_BELOW_THRESHOLD")
    pace = _volume_pace(evidence, bars, phase, daily)
    if pace is None:
        return dict(out, active=False, reason="VOLUME_PACE_UNAVAILABLE")
    out["volume_pace"] = float(pace.quantize(Decimal("0.01")))
    if pace < ACCELERATION_PACE:
        return dict(out, active=False, reason="VOLUME_PACE_LOW")
    return dict(out, active=True, reason=ACCELERATION)


def add_key(session_date, scenario_id):
    """Idempotency key of one scenario in one session (stored as the leg's bar_end)."""
    return f"add-plan:{session_date}:{scenario_id}"


def acceleration_key(session_date, scenario_id):
    """Key of the deterministic second add that follows ``scenario_id`` in an ACCELERATION session."""
    return f"{add_key(session_date, scenario_id)}:acceleration"


def _continuation(plan, state, *, added, allocation, last_step, price, session_date, used, accel):
    """The deterministic second add of an ACCELERATION session, or (None, reason).

    It repeats today's executed scenario at its own trigger: one pyramid step (<= 0.25 and
    <= the previous leg, 5%p grid), the same chase limit versus that trigger, the risk clip.
    """
    leg = added[-1]
    sid = leg.get("scenario_id")
    if not sid or _dec(leg.get("trigger_price")) is None:
        return None, "ACCELERATION_NO_TRIGGER"
    key = acceleration_key(session_date, sid)
    if key in used:
        return None, "DONE_THIS_SESSION"
    target = min(allocation + min(MAX_STEP, last_step), Decimal(1))
    target = (target / GRID).to_integral_value(rounding=ROUND_DOWN) * GRID
    if target <= allocation:
        return None, "TARGET_REACHED"
    scenario = next((s for s in plan["scenarios"] if s["id"] == sid), None)
    chase = Decimal(str(scenario["max_chase_pct"])) if scenario else MAX_CHASE_PCT
    trigger_price = _dec(leg["trigger_price"])
    cap = trigger_price * (1 + chase / 100)
    if price > cap:
        return None, "CHASE_LIMIT"
    clipped = risk_clip(legs=state["legs"], target=target, price=price, initial_entry=state["initial_entry"],
                        initial_stop=state["initial_stop"], current_stop=state["current_stop"],
                        fee_rate=state.get("fee_rate", 0))
    if clipped <= allocation:
        return None, "RISK_LIMIT"
    lens = scenario["lens"] if scenario else list(leg.get("lens") or [])
    return {"action": "ADD", "reason": "QUALIFIED", "rail": ACCELERATION, "acceleration": accel,
            "plan_hash": plan["plan_hash"], "session_date": session_date, "scenario_id": sid,
            "scenario_type": leg.get("scenario_type") or (scenario or {}).get("type"), "lens": lens,
            "rationale": (scenario or {}).get("rationale") or "", "target_allocation": str(clipped),
            "planned_target": str(target), "delta": str(clipped - allocation), "risk_clipped": clipped < target,
            "price": _num(price), "limit_price": _num(min(price, cap)), "trigger_price": _num(trigger_price),
            "key": key}, None


def evaluate_plan(plan, state, evidence, *, now):
    """At most one qualified add for this evaluation, or WAIT/INVALIDATED with reason codes.

    One add per session; in an ACCELERATION session (``acceleration``) a second add comes
    from another qualifying scenario or, failing that, the deterministic continuation of
    today's executed scenario. Such a second add carries ``rail="ACCELERATION"``.

    ``state``: allocation, legs (micro_split legs), initial_entry, initial_stop,
    current_stop, fee_rate, entry_session (date), blocked (reason or None).
    ``evidence``: phase INTRADAY|CLOSE, session_date, open_at, price (quote, or the
    official close at CLOSE), today_bars (5m OHLCV), daily (completed sessions
    before today), today_daily (CLOSE only), prior_cumulative (matched volume).
    """
    current = _time(now)

    def wait(reason, action="WAIT", **extra):
        return dict(action=action, reason=reason, plan_hash=(plan or {}).get("plan_hash"), **extra)

    if not plan:
        return wait("NO_PLAN")
    if not plan_intact(plan):
        return wait("PLAN_HASH_MISMATCH")
    if plan.get("status") != "ACTIVE":
        return wait(f"PLAN_{plan.get('status')}")
    if not isinstance(evidence, dict) or evidence.get("status") != "OK":
        return wait(f"EVIDENCE_{(evidence or {}).get('reason') or 'MISSING'}")
    phase, session_date = evidence.get("phase"), evidence.get("session_date")
    if phase not in ("INTRADAY", "CLOSE"):
        return wait("EVIDENCE_PHASE")
    if session_date != plan.get("valid_for"):
        return wait("PLAN_NOT_VALID_FOR_SESSION")
    if state.get("blocked"):
        return wait("SELL_DAY_BLOCK", detail=state["blocked"])
    price = _dec(evidence.get("price"))
    if price is None:
        return wait("PRICE_MISSING")
    daily = _daily(evidence)
    reason = _invalidated(plan, evidence, daily, phase, state["entry_session"])
    if reason:
        return wait(reason, action="INVALIDATED", session_date=session_date)
    legs = state["legs"]
    allocation = Decimal(str(state["allocation"]))
    if allocation >= 1:
        return wait("FULL_SLOT")
    if price <= weighted_entry(legs):
        return wait("NOT_PROFITABLE")
    bars = _bars(evidence, current)
    added = [leg for leg in legs if leg.get("kind") == "ADD" and leg.get("session") == session_date]
    used = {leg.get("bar_end") for leg in legs}
    position = session_index(state["entry_session"], session_date)
    last_step = Decimal(str(legs[-1]["allocation"]))
    accel = acceleration(state, evidence, bars=bars, daily=daily, price=price, phase=phase) if added else None
    limit = ACCELERATION_ADDS_PER_SESSION if accel and accel["active"] else ADDS_PER_SESSION
    rail = ACCELERATION if added else None
    reasons = {}
    for scenario in plan["scenarios"]:
        sid, trigger = scenario["id"], scenario["trigger"]
        if add_key(session_date, sid) in used:
            reasons[sid] = "DONE_THIS_SESSION"
            continue
        if len(added) >= limit:
            reasons[sid] = "ONE_ADD_PER_SESSION" if limit == ADDS_PER_SESSION else "SESSION_ADD_LIMIT"
            continue
        if position < trigger.get("earliest_session", 1):
            reasons[sid] = "TOO_EARLY"
            continue
        if (trigger["confirm"] == "daily_close") != (phase == "CLOSE"):
            reasons[sid] = "WAITING_FOR_" + ("CLOSE" if phase == "INTRADAY" else "INTRADAY")
            continue
        trigger_price, why = _conditions(trigger, evidence, bars=bars, daily=daily, price=price, phase=phase,
                                         now=current)
        if why:
            reasons[sid] = why
            continue
        target = Decimal(scenario["target_allocation"])
        step = target - allocation
        if step <= 0:
            reasons[sid] = "TARGET_REACHED"
            continue
        if step > MAX_STEP or step > last_step:
            reasons[sid] = "STEP_TOO_LARGE" if step > MAX_STEP else "PYRAMID_STEP"
            continue
        cap = trigger_price * (1 + Decimal(str(scenario["max_chase_pct"])) / 100)
        if price > cap:
            reasons[sid] = "CHASE_LIMIT"
            continue
        clipped = risk_clip(legs=legs, target=target, price=price, initial_entry=state["initial_entry"],
                            initial_stop=state["initial_stop"], current_stop=state["current_stop"],
                            fee_rate=state.get("fee_rate", 0))
        if clipped <= allocation:
            reasons[sid] = "RISK_LIMIT"
            continue
        return {"action": "ADD", "reason": "QUALIFIED", "rail": rail, "acceleration": accel,
                "plan_hash": plan["plan_hash"],
                "session_date": session_date, "phase": phase, "scenario_id": sid, "scenario_type": scenario["type"],
                "lens": scenario["lens"], "rationale": scenario["rationale"], "target_allocation": str(clipped),
                "planned_target": str(target), "delta": str(clipped - allocation), "risk_clipped": clipped < target,
                "price": _num(price), "limit_price": _num(min(price, cap)), "trigger_price": _num(trigger_price),
                "key": add_key(session_date, sid), "reasons": reasons}
    if added and len(added) < limit:
        decision, why = _continuation(plan, state, added=added, allocation=allocation, last_step=last_step,
                                      price=price, session_date=session_date, used=used, accel=accel)
        if decision is not None:
            return dict(decision, phase=phase, reasons=reasons)
        reasons[ACCELERATION] = why
    return wait("NO_SCENARIO_QUALIFIED", reasons=reasons, session_date=session_date, acceleration=accel)


# ---------------------------------------------------------------- display

def scenario_label(scenario, market, language):
    """Short label, e.g. '돌파 53,200원 → 60%' / 'breakout $53.20 → 60%'."""
    ko = language == "ko"
    trigger, us = scenario["trigger"], str(market).upper() == "US"
    name = TYPE_LABELS[scenario["type"]][0 if ko else 1]

    def money(value):
        return f"${value:,.2f}" if us else (f"{value:,.0f}원" if ko else f"{value:,.0f} KRW")

    if "price_above" in trigger or "reclaim_above" in trigger:
        level = money(trigger.get("price_above", trigger.get("reclaim_above")))
    elif "new_closing_high_lookback" in trigger:
        n = trigger["new_closing_high_lookback"]
        level = f"{n}일 종가 고점 위" if ko else f"above the {n}-session closing high"
    else:
        level = f"갭 {trigger['gap_up_min_pct']:g}%+" if ko else f"gap {trigger['gap_up_min_pct']:g}%+"
    return f"{name} {level} → {round(float(scenario['target_allocation']) * 100)}%"
