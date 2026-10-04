"""Campaign re-entry v3 (KR/US): pure rules, no I/O. Design: docs/REENTRY_V3_LIVE_ko.md.

A stock that was stopped out (STOP_EXIT) or analysed and held off (LOCATION_SKIP /
ENTER_BLOCKED) is re-tried around one key level L (the "reference price") for up to 60
sessions (the "re-entry watch window", called a campaign in code), accepting several small
stops while L holds. Definitions follow the pre-registered research (re-entry v3/v4/v5,
2026-10-04) and the user decisions of 2026-10-04:

* L: STOP_EXIT -> the highest scenario key level strictly below the original entry (the level
  the first buy broke), else primary_resistance; held-off rows -> primary_resistance.
* Watch: sessions anchor+1 .. anchor+60, two campaign rules recorded side by side: L97
  (breakdown line L*0.97) and SS (secondary_support, or L*0.95 when SS is missing or >= L);
  held-off names that have not closed above L yet use the research's support-based lines.
  A completed close under the breakdown line (the original stop day included) opens a
  5-session shakeout window: a close back above the reclaim level returns to normal, a close
  under reclaim*0.90 or an expired window ends the watch.
* Triggers on completed bars plus the decision-time price (live: the 14:00 KST / 13:50 ET
  quote as the close proxy; backfill: the completed close): R1C re-break and R2S retest
  (R/R >= regime floor with the live BUY target rule) outside a window, SHAKEOUT_RECLAIM inside
  one (other triggers are off there).
* Target (live BUY rule): 80% of the distance to the nearest confirmed major resistance above
  the entry among {primary_resistance, secondary_resistance, prior 60-session high}; in bull
  regimes a resistance within +3% is an add condition and the next one is used; with no
  resistance the BUY 2a rule (entry*1.20) or no target at all.
* Stop: never wider than the regime maximum stop of the BUY table (-7/-6/-5%).
* One virtual position at a time, max 3 attempts, no entry on an exit day, one idle session
  after a stop (not for SHAKEOUT_RECLAIM). Exit E1 (production-like) governs the ledger; E2
  (stop or level breakdown) is recorded per attempt as a shadow value.
* Sizing: B3 initial allocation (prism_core.oneil_adaptive_policy.initial_sizing, ATR14).

Rule sets are pluggable: TRIGGERS, CAMPAIGN_RULES (breakdown line), STOP_RULES and EXIT_RULES
are registries, POLICY names the active ids, and every ledger record carries the ids it used.
A trigger may return its own "stop"/"stop_rule" which then replaces the stop rule for that attempt.

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
BULL_REGIMES = ("parabolic", "strong_bull", "moderate_bull")

HORIZON = 60                # watch sessions after the anchor
MAX_ATTEMPTS = 3
PRIOR_HIGH_BARS = 60        # "prior 60-session high" resistance candidate (completed bars only)
REBREAK_CHASE = 0.03        # R1C: price <= L*1.03
RETEST_BAND = 0.03          # R2S: day low within L*(1 +/- 3%)
RETEST_CHASE = 0.05         # R2S: price <= L*1.05
LEVEL_BREAK = 0.97          # L97 breakdown line and the structural stop under L
SS_FALLBACK = 0.95          # SS breakdown line fallback
STRUCT_FLOOR = 0.90         # structural stop never below entry*0.90 (the regime cap is tighter anyway)
CONTROL_STOP = 0.93         # research fallback when L*0.97 is not under the entry (control entries)
# Live BUY target rule (cores/agents/trading_agents.py "진입가 / 목표가 / 손절가 산정").
TARGET_FRACTION = 0.80
ADD_CONDITION_GAP = 0.03    # bull regimes: a resistance within +3% is an add condition, not the target
ONEIL_TARGET = 1.20         # 2a: overhead-free breakout target
ONEIL_NEAR_HIGH = 0.95      # 2a (a): price >= 95% of the confirmed 52-week high
ONEIL_CHASE = 1.05          # 2a (c): price <= breakout high * 1.05
H52_BARS, H52_MIN_BARS = 252, 240
# Shakeout recovery (research v5 A2, user decision 2026-10-04).
SHAKEOUT_WINDOW = 5
SHAKEOUT_DEEP = 0.90        # close < reclaim*0.90 ends the watch
SHAKEOUT_CHASE = 0.05       # entry <= reclaim*1.05
SHAKEOUT_LOW_STOP = 0.99    # stop under the shakeout low
# Share of a normal day's volume traded by the decision time (14:00 KST / 13:50 ET) when no
# ticker-specific intraday profile is supplied (quote["volume_share"]).
VOLUME_SHARE_DEFAULT = {"KR": 0.80, "US": 0.70}
BREAKEVEN_AFTER = 0.10      # E1: stop to entry after a +10% close
MA_EXIT_FROM_BAR = 3        # E1: MA20 close exit from bar 3
HOLD_BARS = 40              # E1: time exit
ALLOC_FALLBACK = 0.5        # research fallback when ATR14 is unavailable (flagged)
G1_SESSIONS, G1_STOPS = 10, 2   # BUY Step 1.6: >=2 stops within ~2 weeks (counterfactual flag only)

POLICY = {
    "policy_version": POLICY_VERSION,
    "triggers": ("R1C", "R2S"),           # outside a shakeout window, priority order
    "window_triggers": ("SHAKEOUT_RECLAIM",),
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


def _rule(regime):
    return REGIME_RULES.get(regime) or REGIME_RULES["sideways"]


def rr_floor(regime):
    """R/R floor of the BUY table (cores/buy_gate.REGIME_RULES; parabolic 0.7)."""
    return float(_rule(regime)["rr_floor"])


def max_stop(regime):
    """Regime maximum stop of the BUY table as a fraction (0.07 / 0.06 / 0.05)."""
    return float(_rule(regime)["max_loss_pct"]) / 100


def reclaim_level(setup, broke):
    """Price a shakeout must close back above: L, or the support for held-off names before a close > L."""
    levels = setup["levels"]
    if setup["source"] == STOP_EXIT or broke:
        return setup["L"]
    return levels.get("primary_support") or levels.get("secondary_support")


def prior_high(bars, i, n=PRIOR_HIGH_BARS):
    window = bars[max(0, i - n):i]
    return max(b["high"] for b in window) if window else None


def oneil_2a(entry, resistances, bars, i):
    """BUY 2a: (a) price >= 95% of the confirmed 52-week high, (b) no other major resistance within
    entry*1.20, (c) price <= that high*1.05, (d) not trend-gated (Step 1.5 T1/T2). -> (ok, reason)."""
    window = bars[max(0, i - H52_BARS):i]
    if len(window) < H52_MIN_BARS:
        return False, "h52_history_short"
    high = max(b["high"] for b in window)
    if entry < high * ONEIL_NEAR_HIGH:
        return False, "below_95pct_52w_high"
    if any(v <= entry * ONEIL_TARGET and abs(v / high - 1) > 0.001 for v, _ in resistances):
        return False, "resistance_within_20pct"
    if entry > high * ONEIL_CHASE:
        return False, "above_breakout_high_5pct"
    if P.trend_ok(bars, i) is not True:
        return False, "trend_gated"
    return True, "ok"


def target_for(setup, entry, regime, bars, i):
    """Live BUY target: 80% of the distance to the nearest confirmed major resistance above the entry
    (bull regimes: a resistance within +3% is an add condition and the next one is used); none -> 2a
    (entry*1.20) or unsupported. Today's in-progress high is never a candidate (completed bars only)."""
    named = [("primary_resistance", setup["levels"].get("primary_resistance")),
             ("secondary_resistance", setup["levels"].get("secondary_resistance")),
             ("prior_60_high", prior_high(bars, i))]
    above = sorted((v, k) for k, v in named if v and v > entry)
    out = {"target": None, "target_source": None, "resistance": None, "add_condition": None,
           "target_rule": None, "target_unsupported": None}
    chosen = above[0] if above else None
    if chosen and regime in BULL_REGIMES and chosen[0] <= entry * (1 + ADD_CONDITION_GAP):
        out["add_condition"] = {"price": chosen[0], "source": chosen[1]}
        chosen = next(((v, k) for v, k in above if v > chosen[0]), None)
        out["target_rule"] = "next_after_add_condition"
    else:
        out["target_rule"] = "nearest"
    if chosen:
        out.update(target=round(entry + TARGET_FRACTION * (chosen[0] - entry), 6), target_source=chosen[1],
                   resistance=chosen[0])
        return out
    ok, reason = oneil_2a(entry, above, bars, i)
    if ok:
        out.update(target=round(entry * ONEIL_TARGET, 6), target_source="oneil_breakout_2a", target_rule="oneil_2a")
    else:
        out.update(target_rule="unsupported", target_unsupported=reason)
    return out


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


def projected_volume(volume_so_far, market, share=None):
    """Full-day volume projected from the cumulative volume at the decision time."""
    share = share or VOLUME_SHARE_DEFAULT.get(market)
    if volume_so_far is None or not share:
        return None
    return volume_so_far / share


# ---------------------------------------------------------------- registries
TRIGGERS = {}


def register_trigger(trigger_id, label):
    """Add a trigger fn(ctx) -> {"fired": bool, "reason": str, ...}; may set "stop"/"stop_rule"."""
    def wrap(fn):
        TRIGGERS[trigger_id] = {"label": label, "fn": fn}
        return fn
    return wrap


def _stop_struct(setup, entry, regime):
    """max(structural stop under L, entry*(1 - regime max stop)): never wider than the BUY table."""
    level = setup["L"]
    struct = entry * CONTROL_STOP if level * LEVEL_BREAK >= entry * 0.99 else \
        max(level * LEVEL_BREAK, entry * STRUCT_FLOOR)
    return max(struct, entry * (1 - max_stop(regime)))


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
    """Trend-follow shadow: only the stop (the level breakdown / watch end closes it)."""
    return _stop_hit(bars[j], pos, j - pos["_start"] + 1)


CAMPAIGN_RULES = {"L97": _end_l97, "SS": _end_ss}
STOP_RULES = {"STRUCT": _stop_struct}
EXIT_RULES = {"E1": _exit_e1, "E2": _exit_e2}


def _priced(ctx, entry, stop=None, stop_rule=None):
    rule = stop_rule or ctx["policy"]["stop_rule"]
    stop = stop if stop is not None else STOP_RULES[rule](ctx["setup"], entry, ctx["regime"])
    out = target_for(ctx["setup"], entry, ctx["regime"], ctx["bars"], ctx["i"])
    out.update(stop=round(stop, 6), stop_rule=rule, max_stop=max_stop(ctx["regime"]), rr_floor=rr_floor(ctx["regime"]),
               rr=round((out["target"] - entry) / (entry - stop), 4) if out["target"] and entry > stop else None)
    return out


@register_trigger("R1C", "기준 가격 재돌파 매수")
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


@register_trigger("R2S", "기준 가격 눌림 지지 매수")
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
    out.update(_priced(ctx, price))
    if out["target"] is None:
        return {"fired": False, "reason": "target_unsupported", "target_unsupported": out["target_unsupported"]}
    if out["rr"] is None or out["rr"] < out["rr_floor"]:
        return {"fired": False, "reason": "rr_below_floor", "rr": out["rr"], "rr_floor": out["rr_floor"]}
    return out


@register_trigger("SHAKEOUT_RECLAIM", "흔들기 후 회복 매수")
def _trigger_shakeout(ctx):
    window, price = ctx["window"], ctx["price"]
    reclaim = window["R"]
    if not reclaim < price <= reclaim * (1 + SHAKEOUT_CHASE):
        return {"fired": False, "reason": "below_level" if price <= reclaim else "chase"}
    avg20 = P.avg_volume(ctx["bars"], ctx["i"])
    projected = ctx.get("volume_projected")
    if projected is None or not avg20:
        return {"fired": False, "reason": "missing_volume"}
    if projected < avg20:
        return {"fired": False, "reason": "volume_below_avg20", "volume_ratio": round(projected / avg20, 4)}
    low = min(v for v in (window["low"], ctx["day_low"]) if v is not None)
    stop = max(low * SHAKEOUT_LOW_STOP, price * (1 - max_stop(ctx["regime"])))
    out = {"fired": True, "reason": "fired", "entry": price, "shakeout_low": low,
           "window_start": window["start_date"], "volume_ratio": round(projected / avg20, 4)}
    out.update(_priced(ctx, price, stop=stop, stop_rule="SHAKEOUT_LOW"))
    return out


def evaluate(setup, *, bars, i, flags, price, day_low, regime, window=None, volume_projected=None, policy=None):
    """Trigger check for day i: bars[:i] is completed history; price/day_low/volume_projected are day-i
    decision values. Inside a shakeout window only the window triggers are evaluated.

    Returns {"trigger": fired dict or None, "checks": {trigger_id: reason}, ...context}.
    """
    policy = policy or POLICY
    ctx = {"setup": setup, "L": setup["L"], "bars": bars, "i": i, "flags": dict(flags), "window": window,
           "policy": policy,
           "prev_close": bars[i - 1]["close"], "price": price, "day_low": day_low,
           "volume_projected": volume_projected, "regime": regime or "sideways"}
    checks, fired = {}, None
    for trigger_id in (policy["window_triggers"] if window else policy["triggers"]):
        result = TRIGGERS[trigger_id]["fn"](ctx)
        checks[trigger_id] = result["reason"]
        if result["fired"] and fired is None:
            fired = dict(result, trigger=trigger_id, label=TRIGGERS[trigger_id]["label"])
    if fired is not None:
        if "rr_floor" not in fired:
            fired.update(_priced(ctx, fired["entry"], fired.get("stop"), fired.get("stop_rule")))
        fired.pop("fired", None)
        fired.pop("reason", None)
    return {"trigger": fired, "checks": checks, "prev_close": ctx["prev_close"], "prior_high": prior_high(bars, i),
            "regime": ctx["regime"], "regime_missing": regime is None, "flags": dict(flags),
            "window": dict(window) if window else None}


# ---------------------------------------------------------------- campaign replay
def _new_campaign(rule):
    return {"rule": rule, "status": "ACTIVE", "attempts": [], "window": None, "windows": [], "_pos": None,
            "_last_exit": None, "_last_stop": False, "virtual_stop_dates": [], "pnl_slot": 0.0, "mark_slot": 0.0,
            "_peak": 0.0, "mdd_slot": 0.0}


def eligible(campaign, i, anchor, policy=None):
    """(bool, reason): may this campaign open a position on day i? A shakeout window lifts the
    one-session cooldown after a stop (the exit day itself stays blocked)."""
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
    if campaign["_last_stop"] and campaign["_last_exit"] == i - 1 and not campaign["window"]:
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


def _close_all(campaign, bars, j, reason):
    for att in campaign["attempts"]:
        for shadow in att["shadow_exits"].values():
            if shadow["status"] == "OPEN":
                _close(shadow, bars, j, bars[j]["close"], reason, campaign["rule"])
    if campaign["_pos"] is not None and not campaign["attempts"][campaign["_pos"]].get("real"):
        _close_attempt(campaign, bars, j, bars[j]["close"], reason, campaign["rule"])


def _exit_index(bars, exit_date):
    """Session index of a real sale: the first bar on/after its date, else the next session (len(bars))."""
    return next((k for k, b in enumerate(bars) if b["date"] >= exit_date), len(bars))


def _close_real(campaign, exit_info, j):
    """Close the LIVE position's attempt with the real sale from trading_history (the sell logic decided).

    j is the session index of the sale (len(bars) when it happened after the last completed bar, e.g.
    earlier today), so the exit-day block and the stop cooldown apply to real sells as well.
    """
    pos = campaign["attempts"][campaign["_pos"]]
    price = float(exit_info["price"])
    ret = price / pos["entry"] - 1
    reason = "stop" if exit_info.get("stop") else "real_exit"
    pos.update(status="CLOSED", exit_date=exit_info["date"], exit_price=round(price, 6), exit_reason=reason,
               exit_rule="REAL", real_exit_kind=exit_info.get("exit_kind"), ret=round(ret, 6),
               slot=round(pos["alloc"] * ret, 6))
    campaign["pnl_slot"] += pos["slot"]
    campaign["_pos"], campaign["_last_exit"], campaign["_last_stop"] = None, j, reason == "stop"
    if reason == "stop" and ret < 0:
        campaign["virtual_stop_dates"].append(exit_info["date"])


def _step(campaign, bars, j):
    for att in campaign["attempts"]:
        for rule, shadow in att["shadow_exits"].items():
            if shadow["status"] == "OPEN" and j >= shadow["_start"]:
                hit = EXIT_RULES[rule](bars, j, shadow)
                if hit:
                    _close(shadow, bars, j, hit[0], hit[1], rule)
    if campaign["_pos"] is not None:
        pos = campaign["attempts"][campaign["_pos"]]
        if pos.get("real"):                    # a real position: only its recorded sale closes it
            exit_info = (pos.get("live") or {}).get("exit")
            if exit_info and bars[j]["date"] >= exit_info["date"]:
                _close_real(campaign, exit_info, j)
        elif j >= pos["_start"]:
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
        "stop_rule": trigger["stop_rule"], "max_stop": trigger["max_stop"], "exit_rule": policy["exit_rule"],
        "target": trigger["target"], "target_source": trigger["target_source"], "target_rule": trigger["target_rule"],
        "add_condition": trigger["add_condition"], "rr": trigger["rr"], "rr_floor": trigger["rr_floor"],
        "regime": regime, "market_pulse": pulse, "atr14": round(atr, 6) if atr else None, "alloc": alloc,
        "alloc_fallback": fallback,
        "gates": {"G1_step16": g1_blocked(stops, bars, i), "G2_v2_one_retry": number >= 2},
        "status": "OPEN", "_start": i + 1, "intraday": False,
        "shadow_exits": {r: {"entry": entry, "stop": trigger["stop"], "_start": i + 1, "intraday": False,
                             "status": "OPEN"} for r in policy["shadow_exit_rules"]},
    }
    for key in ("shakeout_low", "window_start", "volume_ratio"):
        if key in trigger:
            record[key] = trigger[key]
    if decision:
        record.update(decision_price=decision.get("decision_price"), decision_time=decision.get("decision_time"),
                      decision_low=decision.get("day_low"))
        live = decision.get("live") or {}
        if live.get("status") == "BOUGHT" and campaign["rule"] == policy["primary_rule"]:
            # LIVE order (primary rule only; SS stays a record): the existing sell logic manages it.
            record["real"] = True
            record["live"] = {k: live.get(k) for k in ("key", "holding_ids", "entry_price", "exit")}
            record["exit_rule"] = "REAL"
    campaign["attempts"].append(record)
    campaign["_pos"] = len(campaign["attempts"]) - 1
    bar = bars[i]
    record["close_on_entry_day"] = bar["close"]
    record["close_vs_decision_pct"] = round((bar["close"] / entry - 1) * 100, 4)
    exit_info = (record.get("live") or {}).get("exit")
    if record.get("real") and exit_info and exit_info["date"] <= bar["date"]:
        _close_real(campaign, exit_info, i)        # sold on the entry day: closes on the entry bar
        return
    # An entry at the decision time still lives through the rest of the session: a new low after
    # the decision that reaches the stop exits on the entry day (the low so far was above the stop).
    low_so_far = record.get("decision_low")
    if mode == "INTRADAY" and low_so_far and bar["low"] < low_so_far and not record.get("real"):
        for shadow_rule, shadow in record["shadow_exits"].items():
            if bar["low"] <= shadow["stop"]:
                shadow["_start"] = i
                _close(shadow, bars, i, shadow["stop"], "stop", shadow_rule)
        if bar["low"] <= record["stop"]:
            record["_start"] = i
            _close_attempt(campaign, bars, i, record["stop"], "stop", record["exit_rule"])
            record["entry_day_stop"] = True


def _transition(campaign, setup, bars, i, broke, anchor, policy):
    """Close-of-day state change: ("end", reason, level) | ("open_window", R, level) | ("reclaim",) | None."""
    close, window, change = bars[i]["close"], campaign["window"], None
    if window:
        if close > window["R"]:
            change = ("reclaim",)
        elif close < window["R"] * SHAKEOUT_DEEP:
            return ("end", "deep", window["R"] * SHAKEOUT_DEEP)
        elif i - window["_start"] >= SHAKEOUT_WINDOW:
            return ("end", "expire", window["R"])
    else:
        line = CAMPAIGN_RULES[campaign["rule"]](setup, broke)
        if line is not None and close < line:
            reclaim = reclaim_level(setup, broke)
            if reclaim and close < reclaim * SHAKEOUT_DEEP:
                return ("end", "deep", reclaim * SHAKEOUT_DEEP)
            change = ("open_window", reclaim, line)
    if i == anchor + policy["horizon"]:
        return ("end", "horizon", None)
    return change


def _open_window(campaign, bars, i, reclaim, line):
    campaign["window"] = {"R": reclaim, "line": round(line, 6), "start_date": bars[i]["date"], "_start": i,
                          "low": bars[i]["low"]}
    campaign["windows"].append({"start_date": bars[i]["date"], "R": reclaim, "line": round(line, 6)})


def _end(campaign, bars, i, reason, level):
    _close_all(campaign, bars, i, "campaign_end")
    campaign.update(status="ENDED", end_date=bars[i]["date"], end_reason=reason,
                    end_level=round(level, 6) if level else None, end_rule=campaign["rule"], _end_index=i, window=None)


def _window_public(window, i):
    if not window:
        return None
    return {"R": window["R"], "line": window["line"], "start_date": window["start_date"], "low": window["low"],
            "sessions_left": SHAKEOUT_WINDOW - (i - window["_start"]) + 1}     # including session i


def replay(setup, bars, anchor, *, decisions=None, regime_at=None, pulse_at=None, stop_dates=(), policy=None,
           market=None):
    """Ledger of the watch from the anchor through the last completed bar.

    decisions: {date: intraday decision} recorded live; such a day uses that decision's per-rule
    triggers (entry at the decision price) instead of re-evaluating the close. Other days are
    evaluated on the close (BACKFILL, completed volume). regime_at(day)/pulse_at(day) give the
    state as of the session before `day`. market: also records the pivot control.
    """
    policy = policy or POLICY
    decisions = decisions or {}
    regime_at = regime_at or (lambda day: None)
    pulse_at = pulse_at or (lambda day: None)
    level, a, n = setup["L"], anchor, len(bars)
    flags = {"armed": bars[a]["close"] < level,
             "above": (setup.get("anchor_price") or 0) >= level or bars[a]["close"] > level}
    camps = {rule: _new_campaign(rule) for rule in policy["campaign_rules"]}
    for camp in camps.values():         # the original stop day can already be a shakeout (or a deep failure)
        line = CAMPAIGN_RULES[camp["rule"]](setup, flags["above"])
        reclaim = reclaim_level(setup, flags["above"])
        if line is not None and bars[a]["close"] < line:
            if reclaim and bars[a]["close"] < reclaim * SHAKEOUT_DEEP:
                _end(camp, bars, a, "deep", reclaim * SHAKEOUT_DEEP)
            else:
                _open_window(camp, bars, a, reclaim, line)
    stats = {"fired": {}, "checks": {}, "conflicts": []}
    last = min(n - 1, a + policy["horizon"])
    for i in range(a + 1, last + 1):
        bar, broke = bars[i], flags["above"]
        changes = {}
        for rule, camp in camps.items():
            if camp["status"] != "ACTIVE":
                continue
            _step(camp, bars, i)
            changes[rule] = _transition(camp, setup, bars, i, broke, a, policy)
        decision = decisions.get(bar["date"])
        regime = regime_at(bar["date"])
        real_trigger = _real_buy_trigger(decision, policy)
        if real_trigger is not None:
            primary = camps[policy["primary_rule"]]
            if not eligible(primary, i, a, policy)[0]:
                # The real order exists, so the ledger follows it even when the replay disagrees
                # (data revision, ended watch): close any virtual position and open the real attempt.
                if primary["_pos"] is not None and not primary["attempts"][primary["_pos"]].get("real"):
                    _close_attempt(primary, bars, i, real_trigger["entry"], "superseded_by_real", "REAL")
                stats["conflicts"].append({"date": bar["date"], "reason": "real_buy_forced"})
                _fire(stats, primary, setup, bars, i, real_trigger, "INTRADAY", decision, regime, pulse_at,
                      stop_dates, policy)
        open_ok = {r: c for r, c in camps.items() if eligible(c, i, a, policy)[0]}
        if open_ok and decision is not None:
            by_rule = decision.get("by_rule") or {}
            for rule, camp in open_ok.items():
                trigger = by_rule.get(rule)
                if trigger:
                    _fire(stats, camp, setup, bars, i, trigger, "INTRADAY", decision, regime, pulse_at, stop_dates,
                          policy)
        elif open_ok:
            groups = {}
            for rule, camp in open_ok.items():
                if (changes.get(rule) or ("",))[0] == "end":
                    continue                                  # research: no close entry on the end day
                window = camp["window"]
                key = (window["R"], window["_start"]) if window else None
                groups.setdefault(key, []).append(camp)
            for members in groups.values():
                window = members[0]["window"]
                result = evaluate(setup, bars=bars, i=i, flags=flags, price=bar["close"], day_low=bar["low"],
                                  regime=regime, window=window, volume_projected=bar["volume"], policy=policy)
                for tid, reason in result["checks"].items():
                    stats["checks"].setdefault(tid, {}).setdefault(reason, 0)
                    stats["checks"][tid][reason] += 1
                if result["trigger"]:
                    for camp in members:
                        _fire(stats, camp, setup, bars, i, result["trigger"], "BACKFILL", None, regime, pulse_at,
                              stop_dates, policy)
        elif decision is not None and any((decision.get("by_rule") or {}).values()):
            stats["conflicts"].append({"date": bar["date"], "reason": "not_eligible_on_replay"})
        for rule, change in changes.items():
            camp = camps[rule]
            if change is None:
                if camp["window"]:
                    camp["window"]["low"] = min(camp["window"]["low"], bar["low"])
            elif change[0] == "end":
                _end(camp, bars, i, change[1], change[2])
            elif change[0] == "open_window":
                _close_all(camp, bars, i, "breakdown")
                _open_window(camp, bars, i, change[1], change[2])
            elif change[0] == "reclaim":
                camp["window"] = None
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
    for camp in camps.values():      # a real sale after the last processed bar (or after the watch ended)
        if camp["_pos"] is not None and camp["attempts"][camp["_pos"]].get("real"):
            exit_info = (camp["attempts"][camp["_pos"]].get("live") or {}).get("exit")
            if exit_info:
                _close_real(camp, exit_info, _exit_index(bars, exit_info["date"]))
    complete = a + policy["horizon"] <= n - 1
    upcoming = {}
    if last == n - 1 and not complete:
        for rule, camp in camps.items():
            ok, why = eligible(camp, n, a, policy)
            upcoming[rule] = {"eligible": ok, "reason": why, "attempt": len(camp["attempts"]) + 1,
                              "mode": "SHAKEOUT" if camp["window"] else "NORMAL",
                              "window": _window_public(camp["window"], n)}
    ledger = {
        "policy_version": policy["policy_version"],
        "rule_ids": {k: policy[k] for k in ("triggers", "window_triggers", "campaign_rules", "primary_rule",
                                            "stop_rule", "exit_rule", "shadow_exit_rules")},
        "L": level, "anchor_date": bars[a]["date"], "asof": bars[last]["date"], "elapsed": last - a,
        "horizon_complete": complete, "flags": flags, "next": upcoming,
        "trigger_stats": {"fired": stats["fired"], "checks": stats["checks"]}, "conflicts": stats["conflicts"],
        "campaigns": {rule: _public(dict(camp, window=_window_public(camp["window"], last + 1)))
                      for rule, camp in camps.items()},
    }
    if market:
        end_index = camps[policy["primary_rule"]].get("_end_index")
        ledger["controls"] = {"PIVOT": pivot_control(setup, bars, a, end_index, market, policy, regime_at)}
    return ledger


def _real_buy_trigger(decision, policy):
    """The primary-rule trigger of a decision that led to a LIVE buy, else None."""
    if not decision or (decision.get("live") or {}).get("status") != "BOUGHT":
        return None
    return (decision.get("by_rule") or {}).get(policy["primary_rule"])


def _fire(stats, camp, setup, bars, i, trigger, mode, decision, regime, pulse_at, stop_dates, policy):
    stats["fired"][trigger["trigger"]] = stats["fired"].get(trigger["trigger"], 0) + 1
    _open(camp, setup, bars, i, trigger, mode=mode, decision=decision, regime=regime or "sideways",
          pulse=pulse_at(bars[i]["date"]), stop_dates=stop_dates, policy=policy)


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
    return {"status": campaign["status"], "end_date": campaign.get("end_date"),
            "end_reason": campaign.get("end_reason"), "attempts": len(attempts),
            "wins": sum(1 for t in attempts if (t.get("ret") or 0) > 0), "pnl_slot": campaign["pnl_slot"],
            "mark_slot": campaign["mark_slot"], "mdd_slot": campaign["mdd_slot"]}


# ---------------------------------------------------------------- control
def pivot_control(setup, bars, anchor, end_index, market, policy=None, regime_at=None):
    """Existing pivot trigger per day (P.evaluate_day, no CORRECTION ban, deterministic), first trigger
    before the primary watch end, managed like an attempt (capped structural stop, E1, watch end)."""
    policy = policy or POLICY
    regime_at = regime_at or (lambda day: None)
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
    stop = STOP_RULES[policy["stop_rule"]](setup, entry, regime_at(bars[i]["date"]) or "sideways")
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
