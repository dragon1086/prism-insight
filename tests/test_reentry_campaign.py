"""Re-entry v3 campaign rules (prism_core/reentry_campaign.py) and the SHADOW runtime."""
import json
import sqlite3
from datetime import date, timedelta

import pytest

from prism_core import reentry_campaign as C
from prism_core.oneil_adaptive_policy import initial_sizing


def _bars(rows, start=date(2026, 1, 1)):
    out, day = [], start
    for row in rows:
        o, h, low, c = row[:4]
        v = row[4] if len(row) > 4 else 1000
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append({"date": day.isoformat(), "open": o, "high": h, "low": low, "close": c, "volume": v})
        day += timedelta(days=1)
    return out


def _flat(close, n, spread=0.004):
    return [(close, close * (1 + spread), close * (1 - spread), close)] * n


def _rally(start, n, step):
    rows, close = [], start
    for _ in range(n):
        nxt = close * (1 + step)
        rows.append((close, nxt * 1.003, close * 0.997, nxt))
        close = nxt
    return rows


def _fall(start, n, step):
    rows, close = [], start
    for _ in range(n):
        nxt = close * (1 - step)
        rows.append((close, close * 1.002, nxt * 0.997, nxt))
        close = nxt
    return rows


# --- worked example 1: 와이씨-like STOP_EXIT, reference price 11,950 ---------------------------
YC_LEVELS = {"primary_support": 11950, "secondary_support": 11400, "primary_resistance": 13200,
             "secondary_resistance": 14760}


def _yc_head():
    rows = _rally(11000, 30, 0.006)                         # run-up to a ~13,200 peak
    rows[-1] = (13100, 13200, 13050, 13150)
    rows += _flat(11700, 35)                                # base under the 11,950 level
    rows.append((11800, 12100, 11780, 12050))               # 65: original breakout entry 12,050
    rows += [(12000, 12020, 11850, 11900), (11900, 11950, 11780, 11800), (11800, 11850, 11700, 11750)]
    return rows


def _yc_rows():
    rows = _yc_head()
    rows.append((11750, 11850, 11550, 11600))               # 69: stop-out (anchor), exit 11,600 (> L*0.97)
    rows.append((12300, 12450, 12250, 12400))               # 70: gap over L*1.03 -> re-break chase
    rows.append((12350, 12380, 12000, 12150))               # 71: low touches the band, holds -> retest
    rows += _rally(12150, 25, 0.012)                        # 72..96: trend run
    rows += _fall(rows[-1][3], 8, 0.02)                     # 97..: pull back under MA20
    rows += _flat(rows[-1][3], 40)
    return rows


def _yc_shakeout_rows(day71_volume=1500):
    rows = _yc_head()
    rows.append((11750, 11850, 11350, 11500))               # 69: stop-out closes under L*0.97 -> window opens
    rows.append((11500, 11600, 11300, 11450, 900))          # 70: still under L (shakeout low 11,300)
    rows.append((11500, 12150, 11480, 12100, day71_volume))  # 71: back over L with volume -> shakeout recovery
    rows += _rally(12100, 25, 0.012)
    rows += _fall(rows[-1][3], 8, 0.02)
    rows += _flat(rows[-1][3], 40)
    return rows


def yc_bars():
    return _bars(_yc_rows())


def yc_setup(bars, exit_price=11600):
    return C.make_setup("STOP_EXIT", YC_LEVELS, 12050, exit_price, bars[69]["date"])


# --- worked example 2: 심텍-like STOP_EXIT, re-break then a -7% shakeout and a second try -------
ST_LEVELS = {"primary_support": 30000, "secondary_support": 28500, "primary_resistance": 33000,
             "secondary_resistance": 36000}


def _st_rows(cycles=1, rally=True):
    rows = _rally(27000, 30, 0.004)
    rows += _flat(29500, 32)
    rows.append((29600, 30700, 29550, 30600))               # 62: original entry 30,600
    rows.append((30500, 30550, 29600, 29700))
    rows.append((29700, 29800, 28900, 29200))               # 64: stop-out (anchor), exit 29,200 (> L*0.97)
    for _ in range(cycles):
        rows.append((29200, 30500, 29100, 30400))           # re-break at 30,400
        rows.append((30000, 30100, 28200, 29300))           # -7.2% shakeout: stop 29,100 hit, close holds 29,100
        rows.append((29300, 29600, 29200, 29400))           # idle session after the stop
    rows.append((29500, 30500, 29450, 30350))               # re-break again
    if rally:
        rows += _rally(30350, 20, 0.015)
        rows += _fall(rows[-1][3], 8, 0.02)
    rows += _flat(rows[-1][3], 40)
    return rows


def st_setup(bars):
    return C.make_setup("STOP_EXIT", ST_LEVELS, 30600, 29200, bars[64]["date"])


# ---------------------------------------------------------------- level, watch end and windows
def test_level_selection():
    assert C.select_level("STOP_EXIT", YC_LEVELS, 12050) == (11950, "primary_support")
    assert C.select_level("STOP_EXIT", YC_LEVELS, 13500) == (13200, "primary_resistance")
    assert C.select_level("STOP_EXIT", YC_LEVELS, 11000) == (13200, "primary_resistance_fallback")
    assert C.select_level("LOCATION_SKIP", YC_LEVELS, 12500) == (13200, "primary_resistance")
    assert C.select_level("ENTER_BLOCKED", {"primary_support": 100}, 105) == (None, "no_level")
    assert C.make_setup("LOCATION_SKIP", {"primary_support": 100}, 105, 105, "2026-01-02") is None


def _stop_setup(ss=96.0, source="STOP_EXIT", pr=110.0, sr=120.0):
    levels = {"primary_support": 100.0, "secondary_support": ss, "primary_resistance": pr,
              "secondary_resistance": sr}
    return C.make_setup(source, levels, 103.0, 98.0, None)


def _with_anchor(closes, history=25, base=100.0):
    rows = _flat(base, history)
    for c in closes:
        rows.append(c if isinstance(c, tuple) else (c, c * 1.002, c * 0.998, c))
    return _bars(rows), history


def test_breakdown_opens_a_five_session_window_per_rule_then_expires():
    bars, a = _with_anchor([99.0, 97.5, 98.0, 96.5, 95.5, 95.0, 95.0, 95.0, 95.0, 95.0])
    setup = dict(_stop_setup(), anchor_date=bars[a]["date"])
    ledger = C.replay(setup, bars, a)
    l97, ss = ledger["campaigns"]["L97"], ledger["campaigns"]["SS"]
    assert l97["windows"] == [{"start_date": bars[a + 3]["date"], "R": 100.0, "line": 97.0}]
    assert ss["windows"] == [{"start_date": bars[a + 4]["date"], "R": 100.0, "line": 96.0}]
    assert (l97["status"], l97["end_reason"], l97["end_date"], l97["end_rule"]) == \
        ("ENDED", "expire", bars[a + 8]["date"], "L97")
    assert (ss["status"], ss["end_reason"], ss["end_date"], ss["end_rule"]) == ("ENDED", "expire", bars[a + 9]["date"], "SS")


def test_the_original_stop_day_opens_the_window_and_a_deep_close_ends_the_watch():
    bars, a = _with_anchor([96.0, 98.0, 89.5])
    ledger = C.replay(dict(_stop_setup(), anchor_date=bars[a]["date"]), bars, a)
    camp = ledger["campaigns"]["L97"]
    assert camp["windows"][0]["start_date"] == bars[a]["date"]
    assert (camp["end_reason"], camp["end_date"], camp["end_level"]) == ("deep", bars[a + 2]["date"], 90.0)
    bars, a = _with_anchor([89.0, 100.0])
    camp = C.replay(_stop_setup(), bars, a)["campaigns"]["L97"]
    assert (camp["status"], camp["end_reason"], camp["end_date"]) == ("ENDED", "deep", bars[a]["date"])


def test_a_close_back_above_the_reference_price_returns_the_watch_to_normal():
    bars, a = _with_anchor([96.0, (99.0, 101.5, 98.5, 101.0, 500)])        # reclaim without volume
    ledger = C.replay(_stop_setup(), bars, a)
    camp = ledger["campaigns"]["L97"]
    assert camp["status"] == "ACTIVE" and camp["window"] is None and camp["attempts"] == []
    assert ledger["trigger_stats"]["checks"]["SHAKEOUT_RECLAIM"] == {"volume_below_avg20": 1}
    assert ledger["next"]["L97"]["mode"] == "NORMAL"
    bars, a = _with_anchor([96.0, 98.0])
    nxt = C.replay(_stop_setup(), bars, a)["next"]["L97"]
    assert nxt["mode"] == "SHAKEOUT" and nxt["window"]["sessions_left"] == 4 and nxt["window"]["low"] < 96.0
    assert C.replay(_stop_setup(), bars[:a + 1], a)["next"]["L97"]["window"]["sessions_left"] == 5


def test_ss_rule_falls_back_to_l_times_095():
    assert C.CAMPAIGN_RULES["SS"](_stop_setup(ss=None), True) == pytest.approx(95.0)
    above_l = _stop_setup()
    above_l["levels"]["secondary_support"] = 100.5                   # SS >= L (L = 100)
    assert C.CAMPAIGN_RULES["SS"](above_l, True) == pytest.approx(95.0)
    assert C.CAMPAIGN_RULES["SS"](_stop_setup(ss=93.0), True) == 93.0


def test_held_off_watch_uses_support_until_it_breaks_out():
    setup = _stop_setup(source="LOCATION_SKIP")                      # reference price = 1st resistance 110
    assert setup["L"] == 110.0 and C.reclaim_level(setup, False) == 100.0 and C.reclaim_level(setup, True) == 110.0
    assert C.CAMPAIGN_RULES["L97"](setup, False) == pytest.approx(97.0)      # PS*0.97 before a close > L
    assert C.CAMPAIGN_RULES["L97"](setup, True) == pytest.approx(106.7)
    bars, a = _with_anchor([105.0, 104.0, 112.0, 106.0])
    camp = C.replay(dict(setup, anchor_price=105.0), bars, a)["campaigns"]["L97"]
    assert camp["attempts"][0]["trigger"] == "R1C" and camp["attempts"][0]["exit_reason"] == "stop"
    assert camp["status"] == "ACTIVE" and camp["windows"][0] == {"start_date": bars[a + 3]["date"], "R": 110.0,
                                                                 "line": pytest.approx(106.7)}
    bars, a = _with_anchor([105.0, 96.0])
    camp = C.replay(dict(setup, anchor_price=105.0), bars, a)["campaigns"]["L97"]
    assert camp["windows"][0]["R"] == 100.0 and camp["window"]["R"] == 100.0


def test_horizon_end_closes_the_watch():
    flat = _bars(_yc_rows()[:72] + _rally(12150, 58, 0.002))
    led = C.replay(yc_setup(flat), flat, 69)
    camp = led["campaigns"]["L97"]
    assert camp["end_reason"] == "horizon" and camp["end_date"] == flat[129]["date"]
    assert camp["attempts"][-1]["exit_reason"] in {"campaign_end", "hold40"}
    assert led["horizon_complete"] is True and led["next"] == {}


# ---------------------------------------------------------------- triggers
def _ctx_bars(prev_close=99.0, history=70, high=104.0):
    rows = _flat(100.0, history - 1) + [(prev_close, prev_close + 0.5, prev_close - 0.5, prev_close)]
    rows[10] = (100, high, 99, 100)
    return _bars(rows)


def _eval(setup, price, low=None, prev_close=99.0, armed=True, above=False, regime="strong_bull", high=104.0):
    bars = _ctx_bars(prev_close, high=high)
    return C.evaluate(setup, bars=bars, i=len(bars), flags={"armed": armed, "above": above}, price=price,
                      day_low=low, regime=regime)


def test_rebreak_needs_armed_prev_close_at_or_below_l_and_a_chase_limit():
    setup = _stop_setup()                                            # L = 100
    fired = _eval(setup, 101.0, low=99.0)["trigger"]
    assert fired["trigger"] == "R1C" and fired["label"] == "기준 가격 재돌파 매수"
    assert _eval(setup, 101.0, armed=False)["checks"]["R1C"] == "not_armed"
    assert _eval(setup, 103.2)["checks"]["R1C"] == "chase"           # > L*1.03
    assert _eval(setup, 103.0, low=99.0)["trigger"]["trigger"] == "R1C"
    assert _eval(setup, 100.0)["checks"]["R1C"] == "below_level"
    assert _eval(setup, 101.0, prev_close=100.5)["checks"]["R1C"] == "prev_close_above"


def test_retest_band_chase_and_held_off_breakout_requirement():
    setup = _stop_setup()                                            # L = 100, resistances 110 / 120
    fired = _eval(setup, 101.0, low=98.0, prev_close=100.5, high=150)["trigger"]
    assert fired["trigger"] == "R2S" and fired["label"] == "기준 가격 눌림 지지 매수"
    assert _eval(setup, 101.0, low=96.9, prev_close=100.5, high=150)["checks"]["R2S"] == "no_touch"
    assert _eval(setup, 105.1, low=100.0, prev_close=100.5)["checks"]["R2S"] == "chase"
    assert _eval(setup, 99.9, low=99.0, prev_close=100.5)["checks"]["R2S"] == "below_level"
    assert _eval(setup, 101.0, low=None, prev_close=100.5)["checks"]["R2S"] == "missing_low"
    held = dict(setup, source="LOCATION_SKIP", L=100.0)
    assert _eval(held, 101.0, low=99.0, prev_close=100.5, above=False)["checks"]["R2S"] == "not_broken_out"
    assert _eval(held, 101.0, low=99.0, prev_close=100.5, above=True, high=150)["trigger"]["trigger"] == "R2S"


# --- item 2: the live BUY target rule ----------------------------------------------------------
def test_retest_target_is_80pct_of_the_nearest_confirmed_resistance():
    fired = _eval(_stop_setup(), 101.0, low=98.0, prev_close=100.5, high=150)["trigger"]
    assert (fired["target"], fired["target_source"], fired["resistance"], fired["target_rule"]) == \
        (pytest.approx(108.2), "primary_resistance", 110.0, "nearest")
    assert fired["stop"] == 97.0 and fired["rr"] == pytest.approx(1.8)
    # the prior 60-session high counts when it is nearer; today's high never does (completed bars only)
    fired = _eval(_stop_setup(), 101.0, low=98.0, prev_close=100.5, high=106)["trigger"]
    assert (fired["target"], fired["target_source"]) == (pytest.approx(105.0), "prior_60_high")
    assert fired["rr"] == pytest.approx(1.0)
    out = _eval(_stop_setup(), 101.0, low=98.0, prev_close=100.5, high=106, regime="sideways")
    assert out["trigger"] is None and out["checks"]["R2S"] == "rr_below_floor"          # 1.0 < 1.3


def test_bull_regime_turns_a_resistance_within_3pct_into_an_add_condition():
    near = _stop_setup(pr=103.0)                                      # 103 is within +3% of 101
    fired = _eval(near, 101.0, low=98.0, prev_close=100.5, high=99, regime="strong_bull")["trigger"]
    assert fired["add_condition"] == {"price": 103.0, "source": "primary_resistance"}
    assert (fired["target"], fired["resistance"], fired["target_rule"]) == \
        (pytest.approx(116.2), 120.0, "next_after_add_condition")
    side = _eval(near, 101.0, low=98.0, prev_close=100.5, high=99, regime="sideways")
    assert side["trigger"] is None and side["checks"]["R2S"] == "rr_below_floor"         # target 102.6 kept
    target = C.target_for(near, 101.0, "sideways", _ctx_bars(100.5, high=99), 70)
    assert target["target"] == pytest.approx(102.6) and target["add_condition"] is None


# --- item 3: no overhead resistance -> BUY 2a or no entry ---------------------------------------
def _long_trend(spike=None):
    rows = _rally(60, 256, 0.002)                                     # ~100 after a steady uptrend
    if spike:
        rows[100] = (rows[100][0], spike, rows[100][2], rows[100][3])
    return _bars(rows)


def test_no_overhead_uses_the_2a_rule_or_has_no_target():
    bare = C.make_setup("STOP_EXIT", {"primary_support": 99.0}, 99.5, 98.0, None)
    bars = _long_trend()
    i, price = len(bars), 100.5
    assert price > C.prior_high(bars, i)                              # nothing overhead
    out = C.target_for(bare, price, "sideways", bars, i)
    assert (out["target"], out["target_source"], out["target_rule"]) == (pytest.approx(120.6), "oneil_breakout_2a",
                                                                         "oneil_2a")
    fired = C.evaluate(bare, bars=bars, i=i, flags={"armed": True, "above": True}, price=price, day_low=98.5,
                       regime="sideways")["trigger"]
    assert fired["trigger"] == "R2S" and fired["target_source"] == "oneil_breakout_2a"
    # (a) fails: a 52-week high far above -> unsupported -> the retest does not fire
    spiked = _long_trend(spike=130.0)
    assert C.target_for(bare, price, "sideways", spiked, i)["target_unsupported"] == "below_95pct_52w_high"
    out = C.evaluate(bare, bars=spiked, i=i, flags={"armed": True, "above": True}, price=price, day_low=98.5,
                     regime="sideways")
    assert out["trigger"] is None and out["checks"]["R2S"] == "target_unsupported"
    # short history, other resistance within +20%, chase over the high, trend gate
    assert C.oneil_2a(price, [], _ctx_bars(), 70) == (False, "h52_history_short")
    assert C.oneil_2a(price, [(115.0, "secondary_resistance")], bars, i) == (False, "resistance_within_20pct")
    assert C.oneil_2a(110.0, [], bars, i) == (False, "above_breakout_high_5pct")
    falling = _bars(_rally(60, 230, 0.002) + _fall(90, 26, 0.004))
    assert C.oneil_2a(falling[-1]["close"] * 1.01, [], falling, len(falling))[1] in {"trend_gated",
                                                                                    "below_95pct_52w_high"}


# --- items 6 and 7: stop cap by regime, parabolic floor -----------------------------------------
def test_every_stop_is_capped_at_the_regime_maximum_stop():
    setup = _stop_setup()                                             # L = 100 -> structural 97
    stop = C.STOP_RULES["STRUCT"]
    assert stop(setup, 103.0, "strong_bull") == pytest.approx(97.0)       # -5.8%, inside -7%
    assert stop(setup, 103.0, "sideways") == pytest.approx(97.0)          # inside -6%
    assert stop(setup, 103.0, "moderate_bear") == pytest.approx(97.85)    # capped at -5%
    assert stop(setup, 108.0, "parabolic") == pytest.approx(100.44)       # capped at -7%
    assert stop(setup, 97.5, "sideways") == pytest.approx(97.5 * 0.94)    # control fallback, then the cap
    assert [C.max_stop(r) for r in ("parabolic", "strong_bull", "moderate_bull", "sideways", "moderate_bear",
                                    "strong_bear")] == [0.07, 0.07, 0.07, 0.06, 0.05, 0.05]
    fired = _eval(setup, 103.0, low=99.0, regime="strong_bear")["trigger"]
    assert fired["trigger"] == "R1C" and fired["stop"] == pytest.approx(97.85) and fired["max_stop"] == 0.05


def test_rr_floors_follow_the_buy_table():
    assert [C.rr_floor(r) for r in ("parabolic", "strong_bull", "moderate_bull", "sideways", "moderate_bear",
                                    "strong_bear", None)] == [0.7, 1.0, 1.2, 1.3, 1.5, 1.8, 1.3]


# ---------------------------------------------------------------- campaign mechanics
def test_eligibility_attempt_limit_cooldown_and_no_same_day_entry():
    camp = C._new_campaign("L97")
    assert C.eligible(camp, 5, 0) == (True, "ok")
    camp.update(_last_exit=10, _last_stop=True)
    assert C.eligible(camp, 10, 0) == (False, "exit_day")
    assert C.eligible(camp, 11, 0) == (False, "stop_cooldown")
    camp["window"] = {"R": 100.0}                                     # shakeout window: no cooldown
    assert C.eligible(camp, 11, 0) == (True, "ok")
    camp["window"] = None
    assert C.eligible(camp, 12, 0) == (True, "ok")
    camp["_last_stop"] = False
    assert C.eligible(camp, 11, 0) == (True, "ok")
    assert C.eligible(camp, 60, 0) == (False, "horizon")
    camp["_pos"] = 0
    assert C.eligible(camp, 12, 0) == (False, "position_open")
    camp.update(_pos=None, attempts=[{}, {}, {}])
    assert C.eligible(camp, 12, 0) == (False, "max_attempts")


def test_max_three_attempts_per_watch():
    bars = _bars(_st_rows(cycles=4, rally=False))
    ledger = C.replay(st_setup(bars), bars, 64)
    camp = ledger["campaigns"]["L97"]
    assert [t["exit_reason"] for t in camp["attempts"]] == ["stop", "stop", "stop"]
    assert [t["date"] for t in camp["attempts"]] == [bars[65]["date"], bars[68]["date"], bars[71]["date"]]
    assert ledger["campaigns"]["SS"]["attempts"][2]["attempt"] == 3


def test_e1_breakeven_ma20_and_time_exits():
    flat = _bars(_flat(100.0, 20) + [(100, 112, 99, 111), (105, 106, 99.5, 104)])
    pos = {"entry": 100.0, "stop": 90.0, "_start": 20, "intraday": False}
    assert C.EXIT_RULES["E1"](flat, 20, pos) is None and pos["stop"] == 100.0
    assert C.EXIT_RULES["E1"](flat, 21, pos) == (100.0, "be_stop")
    dip = _bars(_rally(100, 22, 0.01) + [(120, 121, 110, 111), (111, 112, 110, 111)])
    pos = {"entry": 120.0, "stop": 100.0, "_start": 21, "intraday": False}
    assert C.EXIT_RULES["E1"](dip, 22, pos) is None                  # bar 2: MA20 exit not yet
    assert C.EXIT_RULES["E1"](dip, 23, pos) == (111, "ma20")
    up = _bars(_rally(100, 70, 0.003))
    pos = {"entry": up[20]["close"], "stop": 1.0, "_start": 21, "intraday": False}
    exits = [C.EXIT_RULES["E1"](up, j, pos) for j in range(21, 61)]
    assert exits[:-1] == [None] * 39 and exits[-1][1] == "hold40"
    assert all(C.EXIT_RULES["E2"](up, j, dict(pos, stop=1.0)) is None for j in range(21, 70))


def test_wyc_like_retest_is_a_big_win_with_b3_sizing():
    bars = yc_bars()
    ledger = C.replay(yc_setup(bars), bars, 69)
    camp = ledger["campaigns"]["L97"]
    first = camp["attempts"][0]
    assert (first["trigger"], first["label"], first["date"], first["entry"], first["mode"]) == \
        ("R2S", "기준 가격 눌림 지지 매수", bars[71]["date"], 12150, "BACKFILL")
    assert first["stop0"] == pytest.approx(11950 * 0.97)
    assert first["target"] == pytest.approx(12150 + 0.8 * (13200 - 12150)) and first["target_rule"] == "nearest"
    assert first["rr"] >= 1.3 and first["stop_rule"] == "STRUCT" and first["exit_rule"] == "E1"
    assert first["exit_reason"] == "ma20" and first["ret"] > 0.2
    atr = C.atr14(bars[:71])
    assert first["alloc"] == float(initial_sizing(12150, round(atr, 6))[1]) and first["alloc_fallback"] is False
    assert first["slot"] == pytest.approx(first["alloc"] * first["ret"], abs=1e-6)
    assert first["gates"] == {"G1_step16": False, "G2_v2_one_retry": False}
    e2 = first["shadow_exits"]["E2"]                                  # E2 recorded beside E1
    assert e2["status"] == "CLOSED" and e2["exit_reason"] in {"stop", "be_stop", "campaign_end", "breakdown"}
    assert ledger["trigger_stats"]["checks"]["R1C"]["chase"] >= 1
    assert ledger["rule_ids"]["campaign_rules"] == ("L97", "SS")
    assert ledger["rule_ids"]["window_triggers"] == ("SHAKEOUT_RECLAIM",)


def test_simtech_like_shakeout_then_second_rebreak_wins_and_gates_would_block_it():
    bars = _bars(_st_rows())
    camp = C.replay(st_setup(bars), bars, 64, stop_dates=[bars[40]["date"]])["campaigns"]["L97"]
    one, two = camp["attempts"][:2]
    assert (one["trigger"], one["entry"], one["stop0"]) == ("R1C", 30400, pytest.approx(29100))
    assert one["exit_reason"] == "stop" and one["exit_date"] == bars[66]["date"]
    assert one["ret"] == pytest.approx(29100 / 30400 - 1, abs=1e-6)
    assert bars[66]["low"] / 30400 - 1 < -0.07                      # the -7% shakeout
    assert two["date"] == bars[68]["date"] and two["trigger"] == "R1C" and two["ret"] > 0.1
    assert two["gates"] == {"G1_step16": True, "G2_v2_one_retry": True}
    assert one["gates"]["G1_step16"] is False
    assert camp["pnl_slot"] > 0 and camp["mdd_slot"] < 0


# --- item 8: shakeout recovery (흔들기 후 회복 매수) -----------------------------------------------
def test_wyc_like_shakeout_recovery_with_volume_is_a_big_win():
    bars = _bars(_yc_shakeout_rows())
    setup = yc_setup(bars, exit_price=11500)
    ledger = C.replay(setup, bars, 69)
    camp = ledger["campaigns"]["L97"]
    assert camp["windows"][0]["start_date"] == bars[69]["date"]          # the stop day itself opened it
    first = camp["attempts"][0]
    assert (first["trigger"], first["label"], first["date"], first["entry"]) == \
        ("SHAKEOUT_RECLAIM", "흔들기 후 회복 매수", bars[71]["date"], 12100)
    assert first["shakeout_low"] == 11300 and first["window_start"] == bars[69]["date"]
    assert first["stop_rule"] == "SHAKEOUT_LOW"
    assert first["stop0"] == pytest.approx(max(11300 * 0.99, 12100 * 0.94))       # regime cap binds (sideways)
    assert first["volume_ratio"] >= 1.0 and first["ret"] > 0.2 and first["exit_reason"] == "ma20"
    assert camp["window"] is None and camp["status"] == "ENDED"
    # the SS line (11,400) was not broken, so that rule stayed normal and took the re-break the same day
    ss_first = ledger["campaigns"]["SS"]["attempts"][0]
    assert ledger["campaigns"]["SS"]["windows"] == [] and (ss_first["trigger"], ss_first["date"]) == \
        ("R1C", bars[71]["date"])


def test_shakeout_recovery_needs_projected_volume_and_otherwise_returns_to_normal():
    bars = _bars(_yc_shakeout_rows(day71_volume=800))
    camp = C.replay(yc_setup(bars, 11500), bars, 69)["campaigns"]["L97"]
    assert all(t["trigger"] != "SHAKEOUT_RECLAIM" for t in camp["attempts"])
    assert all(t["date"] != bars[71]["date"] for t in camp["attempts"])
    # live decision: cumulative volume / time-of-day share (KR default 0.80 at 14:00) vs the 20-day average
    bars = _bars(_yc_shakeout_rows())
    setup = yc_setup(bars, 11500)
    before = C.replay(setup, bars[:71], 69)
    nxt = before["next"]["L97"]
    assert nxt["mode"] == "SHAKEOUT" and nxt["window"]["low"] == 11300
    assert C.projected_volume(1250, "KR") == pytest.approx(1562.5)
    assert C.projected_volume(700, "US") == pytest.approx(1000.0)
    assert C.projected_volume(700, "US", share=0.5) == pytest.approx(1400.0)
    kw = {"bars": bars[:71], "i": 71, "flags": before["flags"], "price": 12100.0, "day_low": 11480.0,
          "regime": "sideways", "window": nxt["window"]}
    assert C.evaluate(setup, volume_projected=C.projected_volume(1250, "KR"), **kw)["trigger"]["trigger"] == \
        "SHAKEOUT_RECLAIM"
    out = C.evaluate(setup, volume_projected=C.projected_volume(700, "KR"), **kw)
    assert out["trigger"] is None and out["checks"] == {"SHAKEOUT_RECLAIM": "volume_below_avg20"}
    assert C.evaluate(setup, volume_projected=2000, **dict(kw, price=12600.0))["checks"]["SHAKEOUT_RECLAIM"] == "chase"


def test_shakeout_recovery_skips_the_stop_cooldown_and_counts_as_an_attempt():
    bars, a = _with_anchor([99.0, (99.0, 102.0, 98.8, 101.5), (100.0, 100.2, 96.0, 96.5),
                            (97.0, 101.5, 96.8, 101.0, 1500)])
    camp = C.replay(_stop_setup(), bars, a)["campaigns"]["L97"]
    one, two = camp["attempts"]
    assert (one["trigger"], one["exit_reason"], one["exit_date"]) == ("R1C", "stop", bars[a + 2]["date"])
    assert (two["trigger"], two["date"], two["attempt"]) == ("SHAKEOUT_RECLAIM", bars[a + 3]["date"], 2)
    assert two["stop0"] == pytest.approx(max(96.0 * 0.99, 101.0 * 0.94))
    assert two["gates"]["G2_v2_one_retry"] is True


def test_a_close_under_the_line_closes_a_position_above_its_stop_and_opens_a_new_window():
    bars, a = _with_anchor([96.0, (97.0, 101.5, 96.5, 101.0, 1500), (99.0, 99.5, 96.0, 96.5)])
    camp = C.replay(_stop_setup(), bars, a)["campaigns"]["L97"]
    first = camp["attempts"][0]
    assert first["trigger"] == "SHAKEOUT_RECLAIM" and first["stop0"] < 96.0
    assert (first["exit_reason"], first["exit_rule"], first["exit_date"]) == ("breakdown", "L97", bars[a + 2]["date"])
    assert [w["start_date"] for w in camp["windows"]] == [bars[a]["date"], bars[a + 2]["date"]]


def test_intraday_decision_replaces_the_close_and_keeps_the_afternoon_risk():
    bars = yc_bars()
    setup = yc_setup(bars)
    before = C.replay(setup, bars[:71], 69)
    assert before["next"]["L97"] == {"eligible": True, "reason": "ok", "attempt": 1, "mode": "NORMAL",
                                     "window": None}
    decision = C.evaluate(setup, bars=bars[:71], i=71, flags=before["flags"], price=12180.0, day_low=12000.0,
                          regime=None)
    assert decision["trigger"]["trigger"] == "R2S"
    record = {"by_rule": {"L97": decision["trigger"], "SS": decision["trigger"]}, "decision_price": 12180.0,
              "decision_time": "2026-04-10T05:00:00+00:00", "day_low": 12000.0}
    first = C.replay(setup, bars, 69, decisions={bars[71]["date"]: record})["campaigns"]["L97"]["attempts"][0]
    assert (first["mode"], first["entry"], first["decision_price"]) == ("INTRADAY", 12180.0, 12180.0)
    assert first["close_vs_decision_pct"] == pytest.approx((12150 / 12180 - 1) * 100, abs=1e-4)
    shaken = [dict(b) for b in bars]
    shaken[71].update(low=11500)
    first = C.replay(setup, shaken, 69, decisions={bars[71]["date"]: record})["campaigns"]["L97"]["attempts"][0]
    assert first["entry_day_stop"] is True and first["exit_date"] == bars[71]["date"]
    nothing = {"by_rule": {"L97": None, "SS": None}, "decision_price": 12600.0, "day_low": 12550.0}
    attempts = C.replay(setup, bars, 69, decisions={bars[71]["date"]: nothing})["campaigns"]["L97"]["attempts"]
    assert all(t["date"] != bars[71]["date"] for t in attempts)


def test_pivot_control_is_recorded_without_a_market_ban():
    bars = yc_bars()
    control = C.replay(yc_setup(bars), bars, 69, market="KR")["controls"]["PIVOT"]
    assert set(control) == {"status_counts", "first_trigger", "trade"} and sum(control["status_counts"].values()) > 0


def test_trigger_registry_is_pluggable():
    @C.register_trigger("TEST_ALWAYS", "T")
    def always(ctx):
        return {"fired": True, "reason": "fired", "entry": ctx["price"], "stop": ctx["price"] * 0.95,
                "stop_rule": "TEST_STOP"}
    try:
        policy = dict(C.POLICY, triggers=("TEST_ALWAYS",))
        assert _eval(_stop_setup(), 101.0, low=99.0)["trigger"]["trigger"] == "R1C"
        out = C.evaluate(_stop_setup(), bars=_ctx_bars(), i=70, flags={"armed": True, "above": True}, price=101.0,
                         day_low=99.0, regime=None, policy=policy)
        assert out["trigger"]["stop"] == pytest.approx(95.95) and out["trigger"]["stop_rule"] == "TEST_STOP"
    finally:
        C.TRIGGERS.pop("TEST_ALWAYS")


# ---------------------------------------------------------------- runtime (SHADOW)
from observability import reentry_v3_recheck as RC3
from observability import reentry_v3_shadow as V3


def _db(path, bars, exit_price=11600):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trading_history (account_key TEXT, ticker TEXT, company_name TEXT, buy_date TEXT, "
                 "buy_price REAL, sell_date TEXT, sell_price REAL, profit_rate REAL, trigger_type TEXT, "
                 "exit_kind TEXT, scenario TEXT)")
    conn.execute("CREATE TABLE watchlist_history (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "analyzed_date TEXT, current_price REAL, buy_score INTEGER, min_score INTEGER, decision TEXT, "
                 "skip_reason TEXT, trigger_type TEXT, scenario TEXT, was_traded INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO trading_history VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("acct", "000001", "YC", bars[65]["date"] + " 10:00:00", 12050, bars[69]["date"] + " 14:00:00",
                  exit_price, round((exit_price / 12050 - 1) * 100, 2), "t", "stop",
                  json.dumps({"trading_scenarios": {"key_levels": {k: str(v) for k, v in YC_LEVELS.items()}}})))
    conn.commit()
    conn.close()


def _runtime(tmp_path, monkeypatch, rows=None, exit_price=11600, quote_price=12150.0, quote_low=12000.0,
             quote_volume=400.0):
    bars = _bars(rows or _yc_rows())
    db = tmp_path / "t.sqlite"
    _db(db, bars, exit_price)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / f"000001_YC_{bars[60]['date'].replace('-', '')}_morning_x.md").write_text("REPORT")
    monkeypatch.setattr(V3, "LOOKBACK_DAYS", 400)
    monkeypatch.setattr(RC3, "instruction", lambda market: "SYS")
    sent = []
    monkeypatch.setattr(V3, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1})
    frames = {"000001": bars, "__benchmark_rows": {"000001": bars}, "__regime_rows": bars}
    root = tmp_path / "rt"

    def run(completed_index, **kw):
        return V3.run("KR", bars[completed_index]["date"], collector=lambda t, c: frames, db_path=db, root=root,
                      reports_root=tmp_path, archive_db=None, **kw)

    def quote(ticker):                                   # 05:00 UTC = 14:00 KST decision time
        return {"price": quote_price, "low": quote_low, "open": 12350.0, "high": 12380.0, "volume": quote_volume,
                "observed_at": bars[71]["date"] + "T05:00:00+00:00", "source": "test"}
    return bars, root, sent, run, quote


def _fake_llm(calls, reply='{"decision": "진입", "buy_score": 7, "min_score": 5, "add_plan": {"scenarios": []}}'):
    async def llm(system, user):
        calls.append((system, user))
        return reply, {"model": "m", "reasoning_effort": "xhigh", "latency_s": 1.0}
    return llm


def test_runtime_dry_runs_write_nothing_and_never_call_the_llm(tmp_path, monkeypatch):
    bars, root, sent, run, quote = _runtime(tmp_path, monkeypatch)
    calls = []
    summary = run(70, dry_run=True, llm_recheck=True, llm=_fake_llm(calls))
    assert summary["newly_enrolled"] == 1 and summary["status_counts"] == {"ACTIVE": 1}
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, dry_run=True,
                  llm_recheck=True, llm=_fake_llm(calls))
    assert summary["new_triggers"] == 1 and summary["llm_calls"] == 0 and calls == []
    assert not root.exists() and sent == []          # no state, no lock file, no cache under runtime/


def test_runtime_intraday_decision_recheck_and_close_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    bars, root, sent, run, quote = _runtime(tmp_path, monkeypatch)
    calls = []
    assert run(70, llm_recheck=True, llm=_fake_llm(calls))["llm_calls"] == 0          # enrol at the close
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                  llm=_fake_llm(calls))
    assert summary["intraday"]["decisions"] == 1 and summary["new_triggers"] == 1 and summary["llm_calls"] == 1
    item = json.loads((root / "reentry_v3_recheck_inputs_kr.jsonl").read_text())
    assert item["contract"] == V3.INPUT_CONTRACT and item["trigger"] == "R2S" and item["level"]["L"] == 11950
    assert item["decision_price"] == 12150.0 and item["decision_time_local"].endswith("14:00 KST")
    assert item["attempts"]["L97"]["attempt"] == 1 and item["campaign"]["rules"] == ["L97", "SS"]
    assert item["stop"] == pytest.approx(11591.5) and item["target"] == pytest.approx(12990.0)
    assert item["target_rule"] == "nearest" and item["gates"]["G2_v2_one_retry"] is False
    assert item["campaign"]["deep_level"] == pytest.approx(10755.0) and item["volume_share_source"] == "market_default"
    assert item["appendix_sha256"] and "초분할 진입 프레임" in item["appendix_text"]
    system, user = calls[0]
    assert system == "SYS"
    assert "초분할 진입 프레임" in user and "초분할 증액 시나리오 (add_plan)" in user and "REPORT" in user
    assert "기준 가격 눌림 지지 매수" in user and "기준 가격: 11,950.00 (첫 매수 때 돌파했던 가격대" in user
    assert "이번 시도: 1/3번째" in user and "14:00 KST" in user and "시장 상황별 진입 기준표(매트릭스)" in user
    for jargon in ("캠페인", "①C", "②S", "기준 레벨 L", "shakeout"):
        assert jargon not in user.split("### Report Content:")[0], jargon
    result = json.loads((root / "reentry_v3_recheck_results_kr.jsonl").read_text())
    assert result["contract"] == RC3.RESULT_CONTRACT and result["approved"] is True and result["add_plan"] == {
        "scenarios": []}
    names = [n for n, _ in sent]
    assert "reentry_v3.shadow_trigger" in names and "reentry_v3.shadow_recheck" in names
    from observability.events import build_event
    for name, kw in sent:
        attrs = build_event("x", service="s", attributes=kw["attributes"])["attributes"]
        assert "[REDACTED]" not in json.dumps(attrs), name
    again = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                llm=_fake_llm(calls))
    assert again["intraday"]["candidates"] == 0 and len(calls) == 1
    run(len(bars) - 1, llm_recheck=True, llm=_fake_llm(calls))
    state = json.loads((root / "reentry_v3_state_kr.json").read_text())
    first = state["watches"][0]["ledger"]["campaigns"]["L97"]["attempts"][0]
    assert first["mode"] == "INTRADAY" and first["decision_price"] == 12150.0 and first["status"] == "CLOSED"
    assert state["watches"][0]["status"] == "CLOSED" and len(calls) == 1
    assert "reentry_v3.shadow_exit" in [n for n, _ in sent] and "reentry_v3.shadow_campaign_end" in [n for n, _ in sent]


def test_runtime_shakeout_recovery_is_decided_per_rule(tmp_path, monkeypatch):
    bars, root, _sent, run, quote = _runtime(tmp_path, monkeypatch, rows=_yc_shakeout_rows(), exit_price=11500,
                                            quote_price=12100.0, quote_low=11480.0, quote_volume=1250.0)
    calls = []
    run(70, llm_recheck=False)
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                  llm=_fake_llm(calls))
    assert summary["new_triggers"] == 1
    state = json.loads((root / "reentry_v3_state_kr.json").read_text())
    decision = state["watches"][0]["decisions"][bars[71]["date"]]
    assert decision["by_rule"]["L97"]["trigger"] == "SHAKEOUT_RECLAIM"   # L97 is inside its window
    assert decision["by_rule"]["SS"]["trigger"] == "R1C"                 # SS line 11,400 was not broken
    assert decision["volume_projected"] == pytest.approx(1562.5) and decision["volume_share"] == 0.8
    item = json.loads((root / "reentry_v3_recheck_inputs_kr.jsonl").read_text())
    assert item["trigger"] == "SHAKEOUT_RECLAIM" and item["shakeout"]["shakeout_low"] == 11300
    assert item["stop_rule"] == "SHAKEOUT_LOW" and set(item["attempts"]) == {"L97", "SS"}
    assert item["campaign"]["reclaim_basis"] == "L"                      # a stopped name reclaims its level
    user = calls[0][1]
    assert "흔들기 후 회복 매수" in user and "흔들기 저점 11,300.00" in user and "20일 평균의" in user


def test_micro_split_appendix_follows_the_live_flag(monkeypatch):
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ENABLED", raising=False)
    assert RC3.micro_split_appendix("KR", 0.45) == ""
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    from prism_core import add_plan_prompts, micro_split_live
    assert RC3.micro_split_appendix("KR", 0.45) == micro_split_live.buy_prompt_block("KR", "ko") + \
        add_plan_prompts.buy_block("KR", "ko", expected_initial=0.45)
    assert "약 45%" in RC3.micro_split_appendix("KR", 0.45)
    assert RC3.micro_split_appendix("US", 0.3).endswith(add_plan_prompts.buy_block("US", "en", expected_initial=0.3))


def test_recheck_section_states_the_user_decisions(monkeypatch):
    seen = {}
    monkeypatch.setattr(RC3, "recheck_instruction", lambda market, section: seen.update(m=market, s=section) or "X")
    assert RC3.instruction("US") == "X" and seen["m"] == "US" and seen["s"] is RC3.RECHECK_V3_KO
    section = RC3.RECHECK_V3_KO
    assert "1.6단계(상습 손절 종목 게이트)는 이번 판단에 적용하지 않습니다" in section
    assert "재진입 감시 기간" in section and "기준 가격(첫 매수 때 돌파했던 가격대" in section
    assert "흔들기 후 회복 매수" in section and "시장 상황별 진입 기준표(매트릭스)" in section
    assert "거리의 80%" in section and "2a 규칙" in section and "도구가 제공되지 않습니다" in section
    for jargon in ("캠페인", "①C", "②S", "shakeout"):
        assert jargon not in section, jargon


def test_policy_switches(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    monkeypatch.setattr(V3, "POLICY_PATH", path)
    assert not V3.enabled("KR") and not V3.llm_recheck_enabled()
    path.write_text(json.dumps(V3.POLICY))
    assert V3.enabled("KR") and V3.enabled("US")
    monkeypatch.delenv("REENTRY_V3_LLM_RECHECK", raising=False)
    assert not V3.llm_recheck_enabled()                              # default off for open-source users
    monkeypatch.setenv("REENTRY_V3_LLM_RECHECK", "true")
    assert V3.llm_recheck_enabled()
    monkeypatch.setenv("REENTRY_V3_SHADOW_ENABLED", "false")
    assert not V3.enabled("KR")
    from pathlib import Path
    shipped = json.loads((Path(V3.ROOT) / "trading/config/reentry_v3_shadow.json").read_text())
    assert shipped == V3.POLICY


def test_intraday_runs_only_inside_an_open_session():
    from datetime import datetime, timezone

    from tools.run_reentry_v3_shadow import decision_day

    def utc(*a):
        return datetime(*a, tzinfo=timezone.utc)
    assert decision_day("KR", utc(2026, 10, 7, 5, 0)) == "2026-10-07"            # 14:00 KST
    assert decision_day("KR", utc(2026, 10, 7, 7, 0)) is None                     # 16:00 KST, closed
    assert decision_day("KR", utc(2026, 10, 10, 5, 0)) is None                    # Saturday
    assert decision_day("US", utc(2026, 10, 7, 17, 50)) == "2026-10-07"          # 13:50 New York (EDT)
    assert decision_day("US", utc(2026, 11, 26, 18, 50)) is None                  # Thanksgiving
