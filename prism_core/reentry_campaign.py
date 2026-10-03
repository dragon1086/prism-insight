"""Campaign re-entry v3 (KR/US): pure rules, no I/O. Design: docs/REENTRY_V3_CAMPAIGN_SHADOW_ko.md.

A stock that was stopped out (STOP_EXIT) or analysed and held off (LOCATION_SKIP /
ENTER_BLOCKED) is re-tried around one key level L for up to 60 sessions (a "campaign"),
accepting several small stops while L holds. Definitions follow the pre-registered research
(re-entry v3/v4, 2026-10-04) as approved by the user:

* L: STOP_EXIT -> the highest scenario key level strictly below the original entry (the level
  the first buy broke), else primary_resistance; held-off rows -> primary_resistance.
* Campaign: sessions anchor+1 .. anchor+60. Two independent end rules are recorded side by side:
  L97 (close < L*0.97) and SS (close < secondary_support, or L*0.95 when SS is missing or >= L).
  Held-off names that have not closed above L yet use the support-based levels of the research.
* Triggers, evaluated on completed bars plus the decision-time price (live: the lunch-time
  quote as the close proxy; backfill: the completed close):
  R1C (re-break) and R2S (box-bottom retest with an R/R floor), R1C first when both fire.
* One virtual position at a time, max 3 attempts, no entry on an exit day, one idle session
  after a stop. Stop = max(L*0.97, entry*0.90). Exit E1 (production-like) governs the campaign;
  E2 (stop or campaign end only) is recorded per attempt as a shadow value.
* Sizing: B3 initial allocation (prism_core.oneil_adaptive_policy.initial_sizing, ATR14).

Rule sets are pluggable: TRIGGERS, CAMPAIGN_RULES (end), STOP_RULES and EXIT_RULES are
registries, POLICY names the active ids, and every ledger record carries the ids it used.
A trigger may return its own "stop" (e.g. under a shakeout low) which then overrides the
stop rule for that attempt.

Bars are dicts {date, open, high, low, close, volume} sorted by date. A function looking at
"day i" reads bars[:i] as completed history plus explicitly passed day-i decision values.
"""
from __future__ import annotations

from itertools import pairwise

from cores.buy_gate import REGIME_RULES
from prism_core import pivot_reentry as P
from prism_core.oneil_adaptive_policy import initial_sizing

POLICY_VERSION = "reentry_campaign_v3"
STOP_EXIT = "STOP_EXIT"
KEY_LEVELS = ("primary_support", "secondary_support", "primary_resistance", "secondary_resistance")

HORIZON = 60                # campaign sessions after the anchor
MAX_ATTEMPTS = 3
PRIOR_HIGH_BARS = 60        # "prior 60-session high" target candidate
REBREAK_CHASE = 0.03        # R1C: price <= L*1.03
RETEST_BAND = 0.03          # R2S: day low within L*(1 +/- 3%)
RETEST_CHASE = 0.05         # R2S: price <= L*1.05
TARGET_MIN_GAP = 0.02       # next resistance must be above entry*1.02
LEVEL_BREAK = 0.97          # L97 end rule, structural stop under L
SS_FALLBACK = 0.95          # SS end rule fallback
STOP_CAP = 0.10             # structural stop never wider than -10%
CONTROL_STOP = 0.93         # research fallback when L*0.97 is not under the entry (control entries)
BREAKEVEN_AFTER = 0.10      # E1: stop to entry after a +10% close
MA_EXIT_FROM_BAR = 3        # E1: MA20 close exit from bar 3
HOLD_BARS = 40              # E1: time exit
ALLOC_FALLBACK = 0.5        # research fallback when ATR14 is unavailable (flagged)
G1_SESSIONS, G1_STOPS = 10, 2   # BUY Step 1.6: >=2 stops within ~2 weeks
# R/R floors come from the production buy gate (cores/buy_gate.REGIME_RULES). The approved
# design lists parabolic at 1.0 although the gate uses 0.7; the deterministic regime
# function never returns parabolic, so this only matters if that changes.
PARABOLIC_RR_FLOOR = 1.0

POLICY = {
    "policy_version": POLICY_VERSION,
    "triggers": ("R1C", "R2S"),           # priority order
    "campaign_rules": ("L97", "SS"),
    "primary_rule": "L97",                # dedupe, controls and the LLM recheck follow this one
    "stop_rule": "STRUCT",
    "exit_rule": "E1",
    "shadow_exit_rules": ("E2",),
    "max_attempts": MAX_ATTEMPTS,
    "horizon": HORIZON,
}


def _pos(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def levels_of(key_levels):
    return {k: _pos((key_levels or {}).get(k)) for k in KEY_LEVELS}


def select_level(source, key_levels, entry_price):
    """(L, basis). STOP_EXIT: highest key level strictly below the original entry, else
    primary_resistance; held-off rows: primary_resistance. (None, "no_level") when absent."""
    levels = levels_of(key_levels)
    pr = levels["primary_resistance"]
    if source == STOP_EXIT:
        entry = _pos(entry_price)
        below = [(v, k) for k, v in levels.items() if v is not None and entry is not None and v < entry]
        if below:
            value, name = max(below)
            return value, name
        return (pr, "primary_resistance_fallback") if pr else (None, "no_level")
    return (pr, "primary_resistance") if pr else (None, "no_level")


def make_setup(source, key_levels, entry_price, anchor_price, anchor_date):
    """Campaign setup (JSON-friendly) or None when no level L exists."""
    level, basis = select_level(source, key_levels, entry_price)
    if level is None:
        return None
    return {"policy_version": POLICY_VERSION, "source": source, "L": level, "level_basis": basis,
            "levels": levels_of(key_levels), "entry_price0": _pos(entry_price),
            "anchor_price": _pos(anchor_price), "anchor_date": anchor_date}


def rr_floor(regime):
    if regime == "parabolic":
        return PARABOLIC_RR_FLOOR
    return float((REGIME_RULES.get(regime) or REGIME_RULES["sideways"])["rr_floor"])


def prior_high(bars, i, n=PRIOR_HIGH_BARS):
    window = bars[max(0, i - n):i]
    return max(b["high"] for b in window) if window else None


def target_for(setup, entry, high60):
    """Nearest of {primary_resistance, secondary_resistance, prior 60-session high} above entry*1.02."""
    named = [("primary_resistance", setup["levels"].get("primary_resistance")),
             ("secondary_resistance", setup["levels"].get("secondary_resistance")),
             ("prior_60_high", high60)]
    above = [(v, k) for k, v in named if v and v > entry * (1 + TARGET_MIN_GAP)]
    return min(above) if above else (None, None)


def atr14(history):
    """Mean true range of the last 14 completed sessions (needs 15 bars), else None."""
    rows = history[-15:]
    if len(rows) < 15:
        return None
    return sum(max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
               for p, c in pairwise(rows)) / 14


def b3_allocation(entry, atr):
    """(allocation, fallback_used): B3 initial allocation of one slot (30-80%)."""
    if not atr or atr <= 0:
        return ALLOC_FALLBACK, True
    return float(initial_sizing(entry, round(atr, 6))[1]), False


def g1_blocked(stop_dates, bars, i):
    """BUY Step 1.6 counterfactual: >= 2 stop exits within the 10 sessions before day i."""
    window = {b["date"] for b in bars[max(0, i - G1_SESSIONS):i]}
    return len(set(stop_dates) & window) >= G1_STOPS


# ---------------------------------------------------------------- registries
TRIGGERS = {}


def register_trigger(trigger_id, label):
    """Add a trigger fn(ctx) -> {"fired": bool, "reason": str, ...}; may set "stop"/"stop_rule"."""
    def wrap(fn):
        TRIGGERS[trigger_id] = {"label": label, "fn": fn}
        return fn
    return wrap


def _stop_struct(setup, entry):
    level = setup["L"]
    if level * LEVEL_BREAK >= entry * 0.99:
        return entry * CONTROL_STOP
    return max(level * LEVEL_BREAK, entry * (1 - STOP_CAP))


def _end_l97(setup, broke):
    levels = setup["levels"]
    if setup["source"] != STOP_EXIT and not broke:
        base = levels.get("primary_support") or levels.get("secondary_support")
        return base * LEVEL_BREAK if base else None
    return setup["L"] * LEVEL_BREAK


def _end_ss(setup, broke):
    levels, level = setup["levels"], setup["L"]
    ss = levels.get("secondary_support")
    if setup["source"] != STOP_EXIT and not broke:
        base = levels.get("primary_support") or ss
        if not base:
            return None
        return ss if ss else base * SS_FALLBACK
    return ss if (ss and ss < level) else level * SS_FALLBACK


def _stop_hit(bar, pos, k):
    if bar["low"] <= pos["stop"]:
        price = pos["stop"] if (k == 1 and pos.get("intraday")) or bar["open"] > pos["stop"] else bar["open"]
        return price, ("stop" if pos["stop"] < pos["entry"] else "be_stop")
    return None


def _exit_e1(bars, j, pos):
    """Production-like: stop, breakeven after a +10% close, MA20 close exit from bar 3, 40 bars."""
    k = j - pos["_start"] + 1
    hit = _stop_hit(bars[j], pos, k)
    if hit:
        return hit
    close = bars[j]["close"]
    if k >= MA_EXIT_FROM_BAR and j >= 19 and close < sum(b["close"] for b in bars[j - 19:j + 1]) / 20:
        return close, "ma20"
    if close >= pos["entry"] * (1 + BREAKEVEN_AFTER):
        pos["stop"] = max(pos["stop"], pos["entry"])
    if k >= HOLD_BARS:
        return close, "hold40"
    return None


def _exit_e2(bars, j, pos):
    """Trend-follow shadow: only the stop (campaign end closes it)."""
    return _stop_hit(bars[j], pos, j - pos["_start"] + 1)


CAMPAIGN_RULES = {"L97": _end_l97, "SS": _end_ss}
STOP_RULES = {"STRUCT": _stop_struct}
EXIT_RULES = {"E1": _exit_e1, "E2": _exit_e2}


@register_trigger("R1C", "①C")
def _trigger_rebreak(ctx):
    level, price = ctx["L"], ctx["price"]
    if not ctx["flags"]["armed"]:
        return {"fired": False, "reason": "not_armed"}
    if ctx["prev_close"] > level:
        return {"fired": False, "reason": "prev_close_above"}
    if price <= level:
        return {"fired": False, "reason": "below_level"}
    if price > level * (1 + REBREAK_CHASE):
        return {"fired": False, "reason": "chase"}
    return {"fired": True, "reason": "fired", "entry": price}


@register_trigger("R2S", "②S")
def _trigger_retest(ctx):
    setup, level, price, low = ctx["setup"], ctx["L"], ctx["price"], ctx["day_low"]
    if setup["source"] != STOP_EXIT and not ctx["flags"]["above"]:
        return {"fired": False, "reason": "not_broken_out"}
    if ctx["prev_close"] < level:
        return {"fired": False, "reason": "prev_close_below"}
    if low is None:
        return {"fired": False, "reason": "missing_low"}
    if not level * (1 - RETEST_BAND) <= low <= level * (1 + RETEST_BAND):
        return {"fired": False, "reason": "no_touch"}
    if price < level:
        return {"fired": False, "reason": "below_level"}
    if price > level * (1 + RETEST_CHASE):
        return {"fired": False, "reason": "chase"}
    out = {"fired": True, "reason": "fired", "entry": price}
    out.update(_price_levels(ctx, price))
    if out["rr"] is not None and out["rr"] < out["rr_floor"]:
        return {"fired": False, "reason": "rr_below_floor", "rr": out["rr"], "rr_floor": out["rr_floor"]}
    return out


def _price_levels(ctx, entry, stop=None, stop_rule=None):
    rule = stop_rule or POLICY["stop_rule"]
    stop = stop if stop is not None else STOP_RULES[rule](ctx["setup"], entry)
    target, source = target_for(ctx["setup"], entry, ctx["prior_high"])
    return {"stop": round(stop, 6), "stop_rule": rule, "target": target, "target_source": source,
            "no_overhead": target is None, "rr_floor": rr_floor(ctx["regime"]),
            "rr": round((target - entry) / (entry - stop), 4) if target and entry > stop else None}


def evaluate(setup, *, bars, i, flags, price, day_low, regime, policy=None):
    """Trigger check for day i: bars[:i] is completed history; price/day_low are day-i decision values.

    Returns {"trigger": fired dict or None, "checks": {trigger_id: reason}, ...context}.
    """
    policy = policy or POLICY
    ctx = {"setup": setup, "L": setup["L"], "bars": bars, "i": i, "flags": dict(flags),
           "prev_close": bars[i - 1]["close"], "price": price, "day_low": day_low,
           "regime": regime or "sideways", "prior_high": prior_high(bars, i)}
    checks, fired = {}, None
    for trigger_id in policy["triggers"]:
        result = TRIGGERS[trigger_id]["fn"](ctx)
        checks[trigger_id] = result["reason"]
        if result["fired"] and fired is None:
            fired = dict(result, trigger=trigger_id, label=TRIGGERS[trigger_id]["label"])
    if fired is not None:
        if "rr_floor" not in fired:
            fired.update(_price_levels(ctx, fired["entry"], fired.get("stop"), fired.get("stop_rule")))
        fired.pop("fired", None)
        fired.pop("reason", None)
    return {"trigger": fired, "checks": checks, "prev_close": ctx["prev_close"], "prior_high": ctx["prior_high"],
            "regime": ctx["regime"], "regime_missing": regime is None, "flags": dict(flags)}


# ---------------------------------------------------------------- campaign replay
def _new_campaign(rule):
    return {"rule": rule, "status": "ACTIVE", "attempts": [], "_pos": None, "_last_exit": None,
            "_last_stop": False, "virtual_stop_dates": [], "pnl_slot": 0.0, "mark_slot": 0.0,
            "_peak": 0.0, "mdd_slot": 0.0}


def eligible(campaign, i, anchor, policy=None):
    """(bool, reason): may this campaign open a position on day i?"""
    policy = policy or POLICY
    if campaign["status"] != "ACTIVE":
        return False, "ended"
    if campaign["_pos"] is not None:
        return False, "position_open"
    if len(campaign["attempts"]) >= policy["max_attempts"]:
        return False, "max_attempts"
    if i >= anchor + policy["horizon"]:
        return False, "horizon"
    if campaign["_last_exit"] == i:
        return False, "exit_day"
    if campaign["_last_stop"] and campaign["_last_exit"] == i - 1:
        return False, "stop_cooldown"
    return True, "ok"


def _close(pos, bars, j, price, reason, rule):
    ret = price / pos["entry"] - 1
    pos.update(status="CLOSED", exit_date=bars[j]["date"], exit_price=round(price, 6), exit_reason=reason,
               exit_rule=rule, ret=round(ret, 6), bars_held=j - pos["_start"] + 1)
    if "alloc" in pos:
        pos["slot"] = round(pos["alloc"] * ret, 6)


def _close_attempt(campaign, bars, j, price, reason, rule):
    pos = campaign["attempts"][campaign["_pos"]]
    _close(pos, bars, j, price, reason, rule)
    campaign["pnl_slot"] += pos["slot"]
    campaign["_pos"], campaign["_last_exit"], campaign["_last_stop"] = None, j, reason == "stop"
    if reason == "stop" and pos["ret"] < 0:
        campaign["virtual_stop_dates"].append(bars[j]["date"])


def _step(campaign, bars, j, policy):
    for att in campaign["attempts"]:
        for rule, shadow in att["shadow_exits"].items():
            if shadow["status"] == "OPEN" and j >= shadow["_start"]:
                hit = EXIT_RULES[rule](bars, j, shadow)
                if hit:
                    _close(shadow, bars, j, hit[0], hit[1], rule)
    if campaign["_pos"] is not None:
        pos = campaign["attempts"][campaign["_pos"]]
        if j >= pos["_start"]:
            hit = EXIT_RULES[pos["exit_rule"]](bars, j, pos)
            if hit:
                _close_attempt(campaign, bars, j, hit[0], hit[1], pos["exit_rule"])


def _open(campaign, setup, bars, i, trigger, *, mode, decision, regime, pulse, stop_dates, policy):
    entry = trigger["entry"]
    atr = atr14(bars[:i])
    alloc, fallback = b3_allocation(entry, atr)
    number = len(campaign["attempts"]) + 1
    stops = set(stop_dates) | set(campaign["virtual_stop_dates"])
    if setup["source"] == STOP_EXIT and setup.get("anchor_date"):
        stops.add(setup["anchor_date"])
    record = {
        "rule": campaign["rule"], "attempt": number, "trigger": trigger["trigger"], "label": trigger["label"],
        "mode": mode, "date": bars[i]["date"], "entry": entry, "stop0": trigger["stop"], "stop": trigger["stop"],
        "stop_rule": trigger["stop_rule"], "exit_rule": policy["exit_rule"], "target": trigger["target"],
        "target_source": trigger["target_source"], "no_overhead": trigger["no_overhead"], "rr": trigger["rr"],
        "rr_floor": trigger["rr_floor"], "regime": regime, "market_pulse": pulse,
        "atr14": round(atr, 6) if atr else None, "alloc": alloc, "alloc_fallback": fallback,
        "gates": {"G1_step16": g1_blocked(stops, bars, i), "G2_v2_one_retry": number >= 2},
        "status": "OPEN", "_start": i + 1, "intraday": False,
        "shadow_exits": {r: {"entry": entry, "stop": trigger["stop"], "_start": i + 1, "intraday": False,
                             "status": "OPEN"} for r in policy["shadow_exit_rules"]},
    }
    if decision:
        record.update(decision_price=decision.get("decision_price"), decision_time=decision.get("decision_time"),
                      decision_low=decision.get("day_low"))
    campaign["attempts"].append(record)
    campaign["_pos"] = len(campaign["attempts"]) - 1
    bar = bars[i]
    record["close_on_entry_day"] = bar["close"]
    record["close_vs_decision_pct"] = round((bar["close"] / entry - 1) * 100, 4)
    # A lunch-time entry still lives through the afternoon: a new low after the decision that
    # reaches the stop exits on the entry day (the low so far was already above the stop).
    low_so_far = record.get("decision_low")
    if mode == "INTRADAY" and low_so_far and bar["low"] < low_so_far:
        for shadow_rule, shadow in record["shadow_exits"].items():
            if bar["low"] <= shadow["stop"]:
                shadow["_start"] = i
                _close(shadow, bars, i, shadow["stop"], "stop", shadow_rule)
        if bar["low"] <= record["stop"]:
            record["_start"] = i
            _close_attempt(campaign, bars, i, record["stop"], "stop", record["exit_rule"])
            record["entry_day_stop"] = True


def _end(campaign, bars, i, reason, level):
    for att in campaign["attempts"]:
        for shadow in att["shadow_exits"].values():
            if shadow["status"] == "OPEN":
                _close(shadow, bars, i, bars[i]["close"], "campaign_end", campaign["rule"])
    if campaign["_pos"] is not None:
        _close_attempt(campaign, bars, i, bars[i]["close"], "campaign_end", campaign["rule"])
    campaign.update(status="ENDED", end_date=bars[i]["date"], end_reason=reason,
                    end_level=round(level, 6) if level else None, end_rule=campaign["rule"], _end_index=i)


def replay(setup, bars, anchor, *, decisions=None, regime_at=None, pulse_at=None, stop_dates=(), policy=None,
           market=None):
    """Campaign ledger from the anchor through the last completed bar.

    decisions: {date: intraday decision} recorded live; such a day uses that decision (entry at the
    decision price) instead of re-evaluating the close. Other days are evaluated on the close
    (BACKFILL). regime_at(day)/pulse_at(day) give the state as of the session before `day`.
    market: also records the pivot control (P.evaluate_day, no market ban).
    """
    policy = policy or POLICY
    decisions = decisions or {}
    regime_at = regime_at or (lambda day: None)
    pulse_at = pulse_at or (lambda day: None)
    level, a, n = setup["L"], anchor, len(bars)
    flags = {"armed": bars[a]["close"] < level,
             "above": (setup.get("anchor_price") or 0) >= level or bars[a]["close"] > level}
    camps = {rule: _new_campaign(rule) for rule in policy["campaign_rules"]}
    stats = {"fired": {}, "checks": {}, "conflicts": []}
    last = min(n - 1, a + policy["horizon"])
    for i in range(a + 1, last + 1):
        bar, broke = bars[i], flags["above"]
        ending = {}
        for rule, camp in camps.items():
            if camp["status"] != "ACTIVE":
                continue
            _step(camp, bars, i, policy)
            lvl = CAMPAIGN_RULES[rule](setup, broke)
            camp["level_now"] = round(lvl, 6) if lvl else None
            if lvl is not None and bar["close"] < lvl:
                ending[rule] = ("breakdown", lvl)
            elif i == a + policy["horizon"]:
                ending[rule] = ("horizon", lvl)
        open_ok = {rule: camp for rule, camp in camps.items() if eligible(camp, i, a, policy)[0]}
        decision = decisions.get(bar["date"])
        if open_ok:
            regime = regime_at(bar["date"])
            if decision is not None:
                mode, trigger = "INTRADAY", decision.get("trigger")
            else:
                mode = "BACKFILL"
                open_ok = {r: c for r, c in open_ok.items() if r not in ending}    # research: entry only before end
                result = evaluate(setup, bars=bars, i=i, flags=flags, price=bar["close"], day_low=bar["low"],
                                  regime=regime, policy=policy) if open_ok else {"trigger": None, "checks": {}}
                trigger = result["trigger"]
                for tid, reason in result["checks"].items():
                    stats["checks"].setdefault(tid, {}).setdefault(reason, 0)
                    stats["checks"][tid][reason] += 1
            if trigger and open_ok:
                stats["fired"][trigger["trigger"]] = stats["fired"].get(trigger["trigger"], 0) + 1
                for camp in open_ok.values():
                    _open(camp, setup, bars, i, trigger, mode=mode, decision=decision, regime=regime or "sideways",
                          pulse=pulse_at(bar["date"]), stop_dates=stop_dates, policy=policy)
        elif decision is not None and decision.get("trigger"):
            stats["conflicts"].append({"date": bar["date"], "reason": "not_eligible_on_replay"})
        for rule, (reason, lvl) in ending.items():
            _end(camps[rule], bars, i, reason, lvl)
        if bar["close"] < level:
            flags["armed"] = True
        if bar["close"] > level:
            flags["above"] = True
        for camp in camps.values():
            if camp["status"] == "ACTIVE" or camp.get("_end_index") == i:
                pos = camp["attempts"][camp["_pos"]] if camp["_pos"] is not None else None
                camp["mark_slot"] = round(pos["alloc"] * (bar["close"] / pos["entry"] - 1), 6) if pos else 0.0
                equity = camp["pnl_slot"] + camp["mark_slot"]
                camp["_peak"] = max(camp["_peak"], equity)
                camp["mdd_slot"] = round(min(camp["mdd_slot"], equity - camp["_peak"]), 6)
    complete = a + policy["horizon"] <= n - 1
    upcoming = {}
    if last == n - 1 and not complete:
        for rule, camp in camps.items():
            ok, why = eligible(camp, n, a, policy)
            upcoming[rule] = {"eligible": ok, "reason": why, "attempt": len(camp["attempts"]) + 1}
    ledger = {
        "policy_version": policy["policy_version"],
        "rule_ids": {k: policy[k] for k in ("triggers", "campaign_rules", "primary_rule", "stop_rule", "exit_rule",
                                            "shadow_exit_rules")},
        "L": level, "anchor_date": bars[a]["date"], "asof": bars[last]["date"], "elapsed": last - a,
        "horizon_complete": complete, "flags": flags, "next": upcoming,
        "trigger_stats": {"fired": stats["fired"], "checks": stats["checks"]}, "conflicts": stats["conflicts"],
        "campaigns": {rule: _public(camp) for rule, camp in camps.items()},
    }
    if market:
        end_index = camps[policy["primary_rule"]].get("_end_index")
        ledger["controls"] = {"PIVOT": pivot_control(setup, bars, a, end_index, market, policy)}
    return ledger


def _public(value):
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_public(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def campaign_summary(campaign):
    attempts = campaign.get("attempts") or []
    return {"status": campaign["status"], "end_date": campaign.get("end_date"), "end_reason": campaign.get("end_reason"),
            "attempts": len(attempts), "wins": sum(1 for t in attempts if (t.get("ret") or 0) > 0),
            "pnl_slot": campaign["pnl_slot"], "mark_slot": campaign["mark_slot"], "mdd_slot": campaign["mdd_slot"]}


# ---------------------------------------------------------------- control
def pivot_control(setup, bars, anchor, end_index, market, policy=None):
    """Existing pivot trigger per day (P.evaluate_day, no CORRECTION ban, deterministic), first trigger
    before the primary campaign end, managed like an attempt (structural stop, E1, campaign end)."""
    policy = policy or POLICY
    last = min(len(bars) - 1, anchor + policy["horizon"])
    stop_at = end_index if end_index is not None else last + 1
    counts, first = {}, None
    for i in range(anchor + 1, min(last, stop_at - 1) + 1):
        if i < 56:
            continue
        day = P.evaluate_day(bars, i, market)
        counts[day["status"]] = counts.get(day["status"], 0) + 1
        if day["status"] == "TRIGGERED" and first is None:
            first = (i, day)
    out = {"status_counts": counts, "first_trigger": None, "trade": None}
    if first is None:
        return out
    i, day = first
    intraday = day["trigger"] != "FOLLOW_THROUGH"
    entry = day["entry"]
    stop = STOP_RULES[policy["stop_rule"]](setup, entry)
    pos = {"entry": entry, "stop": stop, "_start": i, "intraday": intraday, "status": "OPEN"}
    out["first_trigger"] = {"date": bars[i]["date"], "trigger": day["trigger"], "entry": entry,
                            "pivot": day.get("pivot"), "stop": round(stop, 6)}
    for j in range(i, last + 1):
        if end_index is not None and j > end_index:
            break
        hit = EXIT_RULES[policy["exit_rule"]](bars, j, pos)
        if hit:
            _close(pos, bars, j, hit[0], hit[1], policy["exit_rule"])
            break
        if j == end_index:
            _close(pos, bars, j, bars[j]["close"], "campaign_end", policy["primary_rule"])
            break
    alloc, _ = b3_allocation(entry, atr14(bars[:i]))
    if pos["status"] == "CLOSED":
        pos["slot"] = round(alloc * pos["ret"], 6)
    else:
        pos["mark_ret"] = round(bars[last]["close"] / entry - 1, 6)
    pos["alloc"] = alloc
    out["trade"] = _public(pos)
    return out
