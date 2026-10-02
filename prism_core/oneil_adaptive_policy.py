"""Isolated caller-attested research policy. No orders, I/O or stop mutation."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json
from zoneinfo import ZoneInfo

VERSION = "oneil-adaptive-v2"
EVIDENCE_VERSION = "oneil-adaptive-evidence-v2"
# Frozen v1 plans and campaigns stay readable and keep their original rules.
V1_VERSION = "oneil-adaptive-v1"
V1_EVIDENCE_VERSION = "oneil-adaptive-evidence-v1"
VERSIONS = (V1_VERSION, VERSION)
# B3 for every actual entry (2026-10-02): v2 adds/ladder, initial allocation filled
# at the actual entry, no setup-review attestation, US and KR sessions. SHADOW only.
V3_AE_VERSION = "oneil-adaptive-v3-ae"
V3_AE_EVIDENCE_VERSION = "oneil-adaptive-evidence-v3-ae"
ALL_VERSIONS = VERSIONS + (V3_AE_VERSION,)
NY = ZoneInfo("America/New_York")
# Regular session per market: local timezone, open, close (both 390 minutes).
MARKETS = {"US": (NY, time(9, 30), time(16, 0)),
           "KR": (ZoneInfo("Asia/Seoul"), time(9, 0), time(15, 30))}
_SETUP_V1 = frozenset({"proper_base", "pivot", "as_of", "source_ref", "price_basis_ref",
                       "fundamental_leader", "fundamental_source_ref", "fundamental_as_of"})
_SETUP_V2 = _SETUP_V1 | {"atr14", "atr14_source_ref", "atr14_as_of", "atr14_last_trade_date"}
_SETUP_V3_AE = frozenset({"basis", "pivot", "as_of", "source_ref", "price_basis_ref",
                          "atr14", "atr14_source_ref", "atr14_as_of", "atr14_last_trade_date"})


def _num(value, positive=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("finite number required") from None
    if isinstance(value, bool) or not number.is_finite() or number < 0 or (positive and not number):
        raise ValueError("finite nonnegative number required")
    return number


def _time(value):
    if not isinstance(value, str):
        raise ValueError("aware timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("aware timestamp required")
    return parsed.astimezone(timezone.utc)


def _ref(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source reference required")
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def evidence_version(plan):
    if plan["policy_version"] == V3_AE_VERSION:
        return V3_AE_EVIDENCE_VERSION
    return V1_EVIDENCE_VERSION if plan["policy_version"] == V1_VERSION else EVIDENCE_VERSION


def market_zone(plan):
    """Local timezone of the plan's market; v1/v2 plans are always US."""
    return MARKETS[plan.get("market", "US")][0]


def _check_atr(setup, created, zone):
    # ATR14 is frozen point-in-time evidence: its 14 completed sessions end
    # strictly before both its own observation date and the plan's local date.
    _ref(setup["atr14_source_ref"])
    atr_as_of = _time(setup["atr14_as_of"])
    if not isinstance(setup["atr14_last_trade_date"], str):
        raise ValueError("ATR session date required")
    last_trade_date = date.fromisoformat(setup["atr14_last_trade_date"])
    if (atr_as_of > created or last_trade_date >= created.astimezone(zone).date()
            or last_trade_date >= atr_as_of.astimezone(zone).date()):
        raise ValueError("future or same-session ATR")
    # Bound staleness independently of the setup builder: observed within a day
    # of the plan, ending no more than 5 calendar days before the plan's local date.
    if (created - atr_as_of > timedelta(days=1)
            or (created.astimezone(zone).date() - last_trade_date).days > 5):
        raise ValueError("stale ATR")


def initial_sizing(entry_reference, atr14):
    """B3 volatility-scaled first allocation; there is no default size."""
    entry, atr = _num(entry_reference, True), _num(atr14, True)
    proxy = min(max(Decimal("1.5") * atr / entry, Decimal(".04")), Decimal(".10"))
    proxy = proxy.quantize(Decimal(".000001"), rounding=ROUND_DOWN)
    initial = min(max(Decimal(".5") * Decimal(".07") / proxy, Decimal(".30")), Decimal(".80"))
    return proxy, initial.quantize(Decimal(".0001"), rounding=ROUND_DOWN)


def create_plan(*, symbol, entry_reference, initial_stop, source_decision_ref,
                created_at, setup, entry_eligible, fee_bps=10, policy_version=VERSION,
                market="US"):
    """Freeze explicit proper-base and leader attestations, never infer them.

    v3-ae instead freezes the actual entry (pivot = entry) and ATR14 only.
    """
    entry, stop = _num(entry_reference, True), _num(initial_stop, True)
    created, fee = _time(created_at), _num(fee_bps) / 10000
    if policy_version not in ALL_VERSIONS:
        raise ValueError("registered policy version required")
    if entry_eligible is not True or stop >= entry or fee_bps not in (10, 25):
        raise ValueError("eligible entry, valid stop and registered fee required")
    _ref(symbol)
    _ref(source_decision_ref)
    if policy_version == V3_AE_VERSION:
        return _create_v3_ae_plan(symbol=symbol, entry=entry, stop=stop, created=created, fee=fee,
                                  source_decision_ref=source_decision_ref, setup=setup, market=market)
    if market != "US":
        raise ValueError("v1/v2 plans are US only")
    if setup["proper_base"] != "VERIFIED" or setup["fundamental_leader"] is not True:
        raise ValueError("explicit base and leader evidence required")
    if set(setup) != (_SETUP_V1 if policy_version == V1_VERSION else _SETUP_V2):
        raise ValueError("strict structured setup required")
    pivot = _num(setup["pivot"], True)
    for key in ("source_ref", "price_basis_ref", "fundamental_source_ref"):
        _ref(setup[key])
    if any(_time(setup[k]) > created for k in ("as_of", "fundamental_as_of")):
        raise ValueError("future setup")
    if not pivot <= entry <= pivot * Decimal("1.05"):
        raise ValueError("entry outside pivot band")
    if policy_version == V1_VERSION:
        plan = {"policy_version": V1_VERSION, "mode": "RESEARCH_ONLY", "market": "US",
                "symbol": symbol, "entry_reference": str(entry), "initial_stop": str(stop),
                "source_decision_ref": source_decision_ref, "created_at": created.isoformat(),
                "expires_at": (created + timedelta(days=5)).isoformat(),
                "setup": deepcopy(setup), "fee_rate": str(fee),
                "risk_limit": str((entry - stop) / entry),
                "authority": "CALLER_ATTESTED_NOT_AUTHENTICATED"}
        plan["plan_hash"] = _hash(plan)
        return plan
    _check_atr(setup, created, NY)
    stop_proxy, initial = initial_sizing(entry, setup["atr14"])
    plan = {"policy_version": VERSION, "mode": "RESEARCH_ONLY", "market": "US",
            "symbol": symbol, "entry_reference": str(entry), "initial_stop": str(stop),
            "source_decision_ref": source_decision_ref, "created_at": created.isoformat(),
            # B3 expiry is 10 trading sessions. The plan carries no exchange
            # calendar, so this uses the calendar-day equivalent of 14 days.
            "expires_at": (created + timedelta(days=14)).isoformat(),
            "setup": deepcopy(setup), "fee_rate": str(fee),
            "risk_limit": str((entry - stop) / entry),
            "stop_proxy": str(stop_proxy), "initial_nominal": str(initial),
            "authority": "CALLER_ATTESTED_NOT_AUTHENTICATED"}
    plan["plan_hash"] = _hash(plan)
    return plan


def _create_v3_ae_plan(*, symbol, entry, stop, created, fee, source_decision_ref, setup, market):
    if market not in MARKETS:
        raise ValueError("unsupported market")
    if set(setup) != _SETUP_V3_AE or setup["basis"] != "ACTUAL_ENTRY":
        raise ValueError("strict actual-entry setup required")
    if _num(setup["pivot"], True) != entry:
        raise ValueError("actual-entry pivot must equal the entry")
    for key in ("source_ref", "price_basis_ref"):
        _ref(setup[key])
    if _time(setup["as_of"]) > created:
        raise ValueError("future setup")
    _check_atr(setup, created, MARKETS[market][0])
    stop_proxy, initial = initial_sizing(entry, setup["atr14"])
    plan = {"policy_version": V3_AE_VERSION, "mode": "RESEARCH_ONLY", "market": market,
            "symbol": symbol, "entry_reference": str(entry), "initial_stop": str(stop),
            "source_decision_ref": source_decision_ref, "created_at": created.isoformat(),
            # Same 14 calendar-day add window as v2 (about 10 sessions).
            "expires_at": (created + timedelta(days=14)).isoformat(),
            "setup": deepcopy(setup), "fee_rate": str(fee),
            "risk_limit": str((entry - stop) / entry),
            "stop_proxy": str(stop_proxy), "initial_nominal": str(initial),
            "authority": "CALLER_ATTESTED_NOT_AUTHENTICATED"}
    plan["plan_hash"] = _hash(plan)
    return plan


def _validate(plan):
    content = deepcopy(plan)
    digest = content.pop("plan_hash", None)
    if content.get("policy_version") not in ALL_VERSIONS or digest != _hash(content):
        raise ValueError("plan version or hash mismatch")
    rebuilt = create_plan(symbol=plan["symbol"], entry_reference=plan["entry_reference"],
                          initial_stop=plan["initial_stop"],
                          source_decision_ref=plan["source_decision_ref"],
                          created_at=plan["created_at"], setup=plan["setup"],
                          entry_eligible=True,
                          fee_bps=int(_num(plan["fee_rate"]) * 10000),
                          policy_version=plan["policy_version"],
                          market=plan.get("market", "US"))
    if rebuilt != plan:
        raise ValueError("noncanonical plan")


def _trend_above_sma20(trend, today, opened, calendar_ref, zone=NY):
    """Latest completed daily close versus SMA20 of the last 20 completed sessions.

    Every session ends strictly before the current trade date and comes from the
    same verified calendar as the market window. The flag is recomputed here.
    """
    _ref(trend["source_ref"])
    if trend["basis"] != "COMPLETED_DAILY_CLOSE_SMA20" or trend["calendar_ref"] != calendar_ref:
        raise ValueError("trend basis or calendar")
    days, closes = trend["trade_dates"], trend["closes"]
    if (not isinstance(days, list) or not isinstance(closes, list) or len(days) != 20
            or len(closes) != 20 or len(set(days)) != 20 or days != sorted(days)):
        raise ValueError("trend population")
    if any(date.fromisoformat(day) >= today or date.fromisoformat(day).weekday() >= 5
           for day in days):
        raise ValueError("future trend session")
    as_of = _time(trend["as_of"])
    if (as_of.astimezone(zone).date() != date.fromisoformat(days[-1]) or as_of > opened
            or opened - as_of > timedelta(days=5)):
        raise ValueError("stale or future trend")
    values = [_num(close, True) for close in closes]
    return values[-1] > sum(values) / 20


def evaluate_target(plan, evidence, *, now, cumulative_allocation, remaining_allocation,
                    normalized_units, remaining_entry_cost, current_stop,
                    last_add_bar_end=None, add_permission="AVAILABLE"):
    """Return a sizing intent only; persisted last-add clock is caller-owned."""
    _validate(plan)
    v3_ae = plan["policy_version"] == V3_AE_VERSION
    v2 = plan["policy_version"] == VERSION or v3_ae  # v3-ae keeps the B3 ladder
    zone, session_open, session_close = MARKETS[plan.get("market", "US")]
    current = _time(now)
    deployed, remaining, units, cost, stop = map(_num, (
        cumulative_allocation, remaining_allocation, normalized_units, remaining_entry_cost, current_stop))
    if remaining > deployed or deployed > 1 or stop < _num(plan["initial_stop"]):
        raise ValueError("invalid ledger allocation or lowered protection")
    if (not deployed and (remaining or units or cost)) or (not remaining and (units or cost)):
        raise ValueError("inconsistent ledger state")
    facts = evidence if isinstance(evidence, dict) else {}
    digest, price, bar_end, nominal = _hash(facts), None, None, deployed

    def result(reason, action="WAIT", target=None, evidence_status="NOT_ASSESSED"):
        target = deployed if target is None else target
        return {"action": action, "reason": reason, "nominal_target": str(nominal),
                "target_allocation": str(target), "delta_allocation": str(target - deployed),
                "bar_end": bar_end, "price": None if price is None else str(price),
                "plan_hash": plan["plan_hash"], "evidence_hash": digest,
                "risk_clipped": target < nominal, "evidence_status": evidence_status}

    def fresh(value, seconds):
        stamp = _time(value)
        if not 0 <= (current - stamp).total_seconds() <= seconds:
            raise ValueError("stale or future evidence")
        return stamp

    # Identity and fresh quote are required even for protection. Add-only gates,
    # expiry and pending intents cannot suppress a valid protective signal.
    try:
        if (facts["contract_version"] != evidence_version(plan) or facts["symbol"] != plan["symbol"]
                or facts["price_basis_ref"] != plan["setup"]["price_basis_ref"]):
            raise ValueError("identity mismatch")
        _ref(facts["source_ref"])
        quote = facts["quote"]
        _ref(quote["source_ref"])
        quote_at = fresh(quote["observed_at"], 120)
        price = _num(quote["price"], True)
    except KeyError:
        return result("MISSING_QUOTE_OR_IDENTITY", evidence_status="MISSING")
    except (TypeError, ValueError, AttributeError):
        return result("INVALID_QUOTE_OR_IDENTITY")
    if units and price <= stop:
        return result("PROTECTIVE_STOP", "PROTECTIVE_EXIT_REQUIRED")
    if deployed and not units:
        return result("STRATEGY_CLOSED")
    if remaining != deployed:
        return result("REDUCED_POSITION")
    if add_permission != "AVAILABLE":
        return result("ADD_NOT_AVAILABLE")
    if not _time(plan["created_at"]) <= current < _time(plan["expires_at"]):
        return result("PLAN_NOT_ACTIVE")
    if v3_ae and not deployed:
        # The initial allocation is filled only at the actual entry, never later.
        return result("INITIAL_FILLED_AT_ENTRY_ONLY")
    try:
        if facts["source"] not in ("regular", "mechanical"):
            raise ValueError("source")
        session = facts["market_window"]
        _ref(session["source_ref"])
        opened, closed = _time(session["open_at"]), _time(session["close_at"])
        today = date.fromisoformat(session["trade_date"])
        local_open, local_close = (stamp.astimezone(zone) for stamp in (opened, closed))
        if session["verified"] is not True or not opened <= current < closed:
            raise ValueError("regular session")
        if (local_open.date() != today or local_close.date() != today or today.weekday() >= 5
                or local_open.timetz().replace(tzinfo=None) != session_open
                or local_close.timetz().replace(tzinfo=None) > session_close
                or not timedelta(0) < closed - opened <= timedelta(hours=6, minutes=30)):
            raise ValueError("session period")
        bars = facts["bars"]
        if not 1 <= len(bars) <= 2:
            raise ValueError("one or two completed bars required")
        previous_end = None
        for bar in bars:
            _ref(bar["source_ref"])
            start, end = _time(bar["start_at"]), _time(bar["end_at"])
            if (bar["complete"] is not True or bar["regular"] is not True
                    or end - start != timedelta(minutes=5) or not opened <= start < end <= closed
                    or end > current or (previous_end is not None and start != previous_end)
                    or (start - opened).total_seconds() % 300):
                raise ValueError("invalid completed bars")
            _num(bar["close"], True)
            previous_end = end
        bar_end = bars[-1]["end_at"]
        ended = fresh(bar_end, 600)
        if ((deployed and ended <= _time(plan["created_at"])) or quote_at < ended
                or (last_add_bar_end is not None and ended <= _time(last_add_bar_end))):
            raise ValueError("repeated bar or older quote")
        gates = facts["gates"]
        _ref(gates["source_ref"])
        fresh(gates["observed_at"], 120)
        if (any(type(gates[k]) is not bool for k in ("admission", "risk", "RR", "sector", "slot"))
                or gates["market_pulse"] not in ("UPTREND", "UNDER_PRESSURE", "CORRECTION")
                or gates["regime"] not in ("moderate_bull", "strong_bull", "parabolic", "sideways",
                                           "moderate_bear", "strong_bear")):
            return result("MISSING_GATE_EVIDENCE", evidence_status="MISSING")
        if (any(gates[k] is not True for k in ("admission", "risk", "RR", "sector", "slot"))
                or gates["market_pulse"] != "UPTREND"
                or gates["regime"] not in ("moderate_bull", "strong_bull", "parabolic")):
            return result("ADD_GATE_NOT_MET", evidence_status="CONDITION_NOT_MET")
        if v2:
            # B3: volume evidence may be recorded (it is in evidence_hash) but it
            # never gates. Adds after the first entry need a completed-daily trend.
            if deployed:
                trend = facts.get("trend")
                if trend is None:
                    return result("MISSING_TREND_EVIDENCE", evidence_status="MISSING")
                if not _trend_above_sma20(trend, today, opened, session["source_ref"], zone):
                    return result("TREND_NOT_CONFIRMED", evidence_status="CONDITION_NOT_MET")
        else:
            volume = facts["volume"]
            _ref(volume["calendar_ref"])
            _ref(volume["source_ref"])
            elapsed = (ended - opened).total_seconds() / 60
            if (_time(volume["as_of"]) != ended or volume["elapsed_minutes"] != elapsed
                    or volume["complete"] is not True or volume["regular"] is not True
                    or volume["basis"] != "MATCHED_REGULAR_CUMULATIVE"):
                raise ValueError("volume period")
            expected = volume["expected_prior_trade_dates"]
            if len(expected) != 20 or len(set(expected)) != 20 or expected != sorted(expected):
                raise ValueError("comparison dates")
            if any(date.fromisoformat(day) >= today or date.fromisoformat(day).weekday() >= 5
                   for day in expected):
                raise ValueError("future volume")
            samples = volume["samples"]
            if len(samples) != 20 or sorted(x["trade_date"] for x in samples) != expected:
                raise ValueError("comparison population")
            total = Decimal(0)
            for sample in samples:
                _ref(sample["source_ref"])
                if (sample["elapsed_minutes"] != elapsed or sample["complete"] is not True
                        or sample["regular"] is not True):
                    raise ValueError("unmatched volume")
                total += _num(sample["cumulative_volume"])
            if not total:
                return result("MISSING_VOLUME_DENOMINATOR", evidence_status="MISSING")
            if _num(volume["cumulative_volume"]) / (total / 20) < Decimal("1.5"):
                return result("VOLUME_NOT_CONFIRMED", evidence_status="CONDITION_NOT_MET")
    except KeyError:
        return result("MISSING_ADD_EVIDENCE", evidence_status="MISSING")
    except (TypeError, ValueError, AttributeError):
        return result("ADD_EVIDENCE_REJECTED")
    pivot = _num(plan["setup"]["pivot"])
    entry = _num(plan["entry_reference"])
    closing = _num(bars[-1]["close"])
    persistent = len(bars) == 2 and all(_num(b["close"]) > pivot for b in bars)
    if v2 and deployed:
        # B3 ladder is relative to the frozen entry and adds stop above +10%.
        if not all(pivot <= p <= entry * Decimal("1.10") for p in (closing, price)):
            return result("OUTSIDE_ADD_BAND")
        initial = _num(plan["initial_nominal"], True)
        nominal = initial
        if persistent:
            if min(closing, price) >= entry * Decimal("1.04"):
                nominal = Decimal(1)
            elif min(closing, price) >= entry * Decimal("1.02") and initial < Decimal(".8"):
                nominal = Decimal(".8")
    else:
        if not all(pivot <= p <= pivot * Decimal("1.05") for p in (closing, price)):
            return result("OUTSIDE_BUY_BAND")
        nominal = _num(plan["initial_nominal"], True) if v2 else Decimal(".5")
        if deployed and persistent:
            if min(closing, price) >= pivot * Decimal("1.04"):
                nominal = Decimal(1)
            elif min(closing, price) >= pivot * Decimal("1.02"):
                nominal = Decimal(".8")
    if deployed and (price <= entry or price * units <= remaining + cost):
        return result("NOT_PROFITABLE")
    if nominal <= deployed:
        return result("TARGET_ALREADY_REACHED")
    fee, limit = _num(plan["fee_rate"]), _num(plan["risk_limit"])
    if price <= stop:
        return result("STOP_NOT_BELOW_PRICE")
    base_loss = remaining + cost - units * stop * (1 - fee)
    marginal = 1 + fee - stop / price * (1 - fee)
    if base_loss >= limit:
        return result("RISK_LIMIT")
    allowed = (limit - base_loss) / marginal
    target = min(nominal, deployed + allowed).quantize(Decimal(".0001"), rounding=ROUND_DOWN)
    if target <= deployed:
        return result("RISK_LIMIT")
    return result("QUALIFIED", "ADD", target, evidence_status="INPUT_CONTRACT_PASSED")
