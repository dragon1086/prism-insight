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


# --- worked example 1: 와이씨-like STOP_EXIT, L = 11,950, box-bottom retest -> big win ----------
YC_LEVELS = {"primary_support": 11950, "secondary_support": 11400, "primary_resistance": 13200,
             "secondary_resistance": 14760}


def _yc_rows():
    rows = _rally(11000, 30, 0.006)                         # run-up to a ~13,200 peak
    rows[-1] = (13100, 13200, 13050, 13150)
    rows += _flat(11700, 35)                                # base under the 11,950 level
    rows.append((11800, 12100, 11780, 12050))               # 65: original breakout entry 12,050
    rows += [(12000, 12020, 11850, 11900), (11900, 11950, 11780, 11800), (11800, 11850, 11700, 11750)]
    rows.append((11750, 11850, 11550, 11600))               # 69: stop-out (anchor), exit 11,600
    rows.append((12300, 12450, 12250, 12400))               # 70: gap over L*1.03 -> R1C chase
    rows.append((12350, 12380, 12000, 12150))               # 71: low touches L band, holds -> R2S
    rows += _rally(12150, 25, 0.012)                        # 72..96: trend run
    rows += _fall(rows[-1][3], 8, 0.02)                     # 97..: pull back under MA20
    rows += _flat(rows[-1][3], 40)
    return rows


def yc_bars():
    return _bars(_yc_rows())


def yc_setup(bars):
    return C.make_setup("STOP_EXIT", YC_LEVELS, 12050, 11600, bars[69]["date"])


# --- worked example 2: 심텍-like STOP_EXIT, re-break then a -7% shakeout and a second try -------
ST_LEVELS = {"primary_support": 30000, "secondary_support": 28500, "primary_resistance": 33000,
             "secondary_resistance": 36000}


def _st_rows(cycles=1, rally=True):
    rows = _rally(27000, 30, 0.004)
    rows += _flat(29500, 32)
    rows.append((29600, 30700, 29550, 30600))               # 62: original entry 30,600
    rows.append((30500, 30550, 29600, 29700))
    rows.append((29700, 29800, 28900, 29000))               # 64: stop-out (anchor), exit 29,000
    for _ in range(cycles):
        rows.append((29200, 30500, 29100, 30400))           # re-break: R1C at 30,400
        rows.append((30000, 30100, 28200, 29300))           # -7.2% shakeout: structural stop 29,100 hit
        rows.append((29300, 29600, 29200, 29400))           # idle session after the stop
    rows.append((29500, 30500, 29450, 30350))               # re-break again
    if rally:
        rows += _rally(30350, 20, 0.015)
        rows += _fall(rows[-1][3], 8, 0.02)
    rows += _flat(rows[-1][3], 40)
    return rows


def st_setup(bars):
    return C.make_setup("STOP_EXIT", ST_LEVELS, 30600, 29000, bars[64]["date"])


# ---------------------------------------------------------------- level and campaign end
def test_level_selection():
    assert C.select_level("STOP_EXIT", YC_LEVELS, 12050) == (11950, "primary_support")
    assert C.select_level("STOP_EXIT", YC_LEVELS, 13500) == (13200, "primary_resistance")
    assert C.select_level("STOP_EXIT", YC_LEVELS, 11000) == (13200, "primary_resistance_fallback")
    assert C.select_level("LOCATION_SKIP", YC_LEVELS, 12500) == (13200, "primary_resistance")
    assert C.select_level("ENTER_BLOCKED", {"primary_support": 100}, 105) == (None, "no_level")
    assert C.make_setup("LOCATION_SKIP", {"primary_support": 100}, 105, 105, "2026-01-02") is None


def _stop_setup(ss=96.0, source="STOP_EXIT"):
    levels = {"primary_support": 100.0, "secondary_support": ss, "primary_resistance": 110.0,
              "secondary_resistance": 120.0}
    return C.make_setup(source, levels, 103.0, 98.0, None)


def _with_anchor(closes, history=25, base=100.0):
    rows = _flat(base, history) + [(c, c * 1.002, c * 0.998, c) for c in closes]
    return _bars(rows), history


def test_l97_and_ss_end_independently_and_ignore_the_anchor_day():
    bars, a = _with_anchor([90.0, 99.0, 97.5, 96.5, 95.5, 99.0])      # anchor close 90 < L*0.97 is ignored
    setup = dict(_stop_setup(), anchor_date=bars[a]["date"])
    ledger = C.replay(setup, bars, a)
    l97, ss = ledger["campaigns"]["L97"], ledger["campaigns"]["SS"]
    assert (l97["status"], l97["end_date"], l97["end_reason"], l97["end_level"]) == \
        ("ENDED", bars[a + 3]["date"], "breakdown", 97.0)
    assert (ss["status"], ss["end_date"], ss["end_level"]) == ("ENDED", bars[a + 4]["date"], 96.0)
    assert l97["end_rule"] == "L97" and ss["end_rule"] == "SS"


def test_ss_rule_falls_back_to_l_times_095():
    assert C.CAMPAIGN_RULES["SS"](_stop_setup(ss=None), True) == pytest.approx(95.0)
    above_l = _stop_setup()
    above_l["levels"]["secondary_support"] = 100.5                   # SS >= L (L = 100)
    assert C.CAMPAIGN_RULES["SS"](above_l, True) == pytest.approx(95.0)
    assert C.CAMPAIGN_RULES["SS"](_stop_setup(ss=93.0), True) == 93.0


def test_held_off_campaign_uses_support_until_it_breaks_out():
    setup = _stop_setup(source="LOCATION_SKIP")                      # L = primary_resistance 110
    assert setup["L"] == 110.0
    assert C.CAMPAIGN_RULES["L97"](setup, False) == pytest.approx(97.0)      # PS*0.97 before a close > L
    assert C.CAMPAIGN_RULES["L97"](setup, True) == pytest.approx(106.7)
    bars, a = _with_anchor([105.0, 104.0, 112.0, 106.0])
    ledger = C.replay(dict(setup, anchor_price=105.0), bars, a)
    camp = ledger["campaigns"]["L97"]
    assert camp["status"] == "ENDED" and camp["end_date"] == bars[a + 3]["date"] and camp["end_level"] == 106.7


def test_horizon_end_closes_the_open_position():
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
    assert _eval(setup, 101.0, low=99.0)["trigger"]["trigger"] == "R1C"
    assert _eval(setup, 101.0, low=99.0)["trigger"]["label"] == "①C"
    assert _eval(setup, 101.0, armed=False)["checks"]["R1C"] == "not_armed"
    assert _eval(setup, 103.2)["checks"]["R1C"] == "chase"           # > L*1.03
    assert _eval(setup, 103.0, low=99.0)["trigger"]["trigger"] == "R1C"
    assert _eval(setup, 100.0)["checks"]["R1C"] == "below_level"
    assert _eval(setup, 101.0, prev_close=100.5)["checks"]["R1C"] == "prev_close_above"


def test_retest_band_chase_and_held_off_breakout_requirement():
    setup = _stop_setup()                                            # targets 110 / 120, stop = 97
    fired = _eval(setup, 101.0, low=98.0, prev_close=100.5, high=150)["trigger"]
    assert fired["trigger"] == "R2S" and fired["label"] == "②S"
    assert (fired["target"], fired["target_source"], fired["stop"], fired["rr"]) == (110.0, "primary_resistance", 97.0, 2.25)
    assert _eval(setup, 101.0, low=96.9, prev_close=100.5, high=150)["checks"]["R2S"] == "no_touch"
    assert _eval(setup, 105.1, low=100.0, prev_close=100.5)["checks"]["R2S"] == "chase"
    assert _eval(setup, 99.9, low=99.0, prev_close=100.5)["checks"]["R2S"] == "below_level"
    assert _eval(setup, 101.0, low=None, prev_close=100.5)["checks"]["R2S"] == "missing_low"
    held = dict(setup, source="LOCATION_SKIP", L=100.0)
    assert _eval(held, 101.0, low=99.0, prev_close=100.5, above=False)["checks"]["R2S"] == "not_broken_out"
    assert _eval(held, 101.0, low=99.0, prev_close=100.5, above=True, high=150)["trigger"]["trigger"] == "R2S"


def test_retest_rr_floor_by_regime_and_no_overhead():
    setup = _stop_setup()
    # target = prior high 106 -> rr = (106-101)/(101-97) = 1.25: strong_bull 1.0 passes, moderate_bear 1.5 fails
    assert _eval(setup, 101.0, low=99.0, prev_close=100.5, high=106)["trigger"]["rr"] == 1.25
    out = _eval(setup, 101.0, low=99.0, prev_close=100.5, high=106, regime="moderate_bear")
    assert out["trigger"] is None and out["checks"]["R2S"] == "rr_below_floor"
    assert C.rr_floor("sideways") == 1.3 and C.rr_floor("strong_bear") == 1.8 and C.rr_floor("parabolic") == 1.0
    # levels within +2% are not a target; nothing above -> NO_OVERHEAD passes and is flagged
    bare = C.make_setup("STOP_EXIT", {"primary_support": 100.0, "primary_resistance": 102.0}, 101.5, 98.0, None)
    assert bare["L"] == 100.0
    fired = _eval(bare, 101.0, low=99.0, prev_close=100.5, high=101.5, regime="strong_bear")["trigger"]
    assert fired["no_overhead"] is True and fired["target"] is None and fired["rr"] is None


def test_structural_stop():
    setup = _stop_setup()                                            # L = 100
    assert C.STOP_RULES["STRUCT"](setup, 101.0) == pytest.approx(97.0)
    assert C.STOP_RULES["STRUCT"](setup, 120.0) == pytest.approx(108.0)      # capped at -10%
    assert C.STOP_RULES["STRUCT"](setup, 97.5) == pytest.approx(97.5 * 0.93)  # control fallback (L*0.97 >= entry*0.99)


# ---------------------------------------------------------------- campaign mechanics
def test_eligibility_attempt_limit_cooldown_and_no_same_day_entry():
    camp = C._new_campaign("L97")
    assert C.eligible(camp, 5, 0) == (True, "ok")
    camp.update(_last_exit=10, _last_stop=True)
    assert C.eligible(camp, 10, 0) == (False, "exit_day")
    assert C.eligible(camp, 11, 0) == (False, "stop_cooldown")
    assert C.eligible(camp, 12, 0) == (True, "ok")
    camp["_last_stop"] = False
    assert C.eligible(camp, 11, 0) == (True, "ok")
    assert C.eligible(camp, 60, 0) == (False, "horizon")
    camp["_pos"] = 0
    assert C.eligible(camp, 12, 0) == (False, "position_open")
    camp.update(_pos=None, attempts=[{}, {}, {}])
    assert C.eligible(camp, 12, 0) == (False, "max_attempts")


def test_max_three_attempts_per_campaign():
    bars = _bars(_st_rows(cycles=4, rally=False))
    ledger = C.replay(st_setup(bars), bars, 64)
    camp = ledger["campaigns"]["L97"]
    assert [t["exit_reason"] for t in camp["attempts"]] == ["stop", "stop", "stop"]
    dates = [t["date"] for t in camp["attempts"]]
    assert dates == [bars[65]["date"], bars[68]["date"], bars[71]["date"]]
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
        ("R2S", "②S", bars[71]["date"], 12150, "BACKFILL")
    assert first["stop0"] == pytest.approx(11950 * 0.97) and first["target"] == 13200
    assert first["rr"] >= 1.3 and first["stop_rule"] == "STRUCT" and first["exit_rule"] == "E1"
    assert first["exit_reason"] == "ma20" and first["ret"] > 0.2
    atr = C.atr14(bars[:71])
    assert first["alloc"] == float(initial_sizing(12150, round(atr, 6))[1]) and first["alloc_fallback"] is False
    assert first["slot"] == pytest.approx(first["alloc"] * first["ret"], abs=1e-6)
    assert first["gates"] == {"G1_step16": False, "G2_v2_one_retry": False}
    e2 = first["shadow_exits"]["E2"]                                  # E2 recorded beside E1
    assert e2["status"] == "CLOSED" and e2["exit_reason"] in {"stop", "be_stop", "campaign_end"}
    assert ledger["trigger_stats"]["checks"]["R1C"]["chase"] >= 1
    assert ledger["rule_ids"]["campaign_rules"] == ("L97", "SS")


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


def test_intraday_decision_replaces_the_close_and_keeps_the_afternoon_risk():
    bars = yc_bars()
    setup = yc_setup(bars)
    before = C.replay(setup, bars[:71], 69)
    assert before["next"]["L97"] == {"eligible": True, "reason": "ok", "attempt": 1}
    decision = C.evaluate(setup, bars=bars[:71], i=71, flags=before["flags"], price=12180.0, day_low=12000.0,
                          regime=None)
    assert decision["trigger"]["trigger"] == "R2S"
    record = {"trigger": decision["trigger"], "decision_price": 12180.0, "decision_time": "2026-04-09T03:10:00+00:00",
              "day_low": 12000.0}
    first = C.replay(setup, bars, 69, decisions={bars[71]["date"]: record})["campaigns"]["L97"]["attempts"][0]
    assert (first["mode"], first["entry"], first["decision_price"]) == ("INTRADAY", 12180.0, 12180.0)
    assert first["close_vs_decision_pct"] == pytest.approx((12150 / 12180 - 1) * 100, abs=1e-4)
    # a new afternoon low through the stop exits on the entry day
    shaken = [dict(b) for b in bars]
    shaken[71].update(low=11500)
    first = C.replay(setup, shaken, 69, decisions={bars[71]["date"]: record})["campaigns"]["L97"]["attempts"][0]
    assert first["entry_day_stop"] is True and first["exit_date"] == bars[71]["date"]
    # a live "no trigger" decision is final for that day (the close is not re-evaluated)
    nothing = {"trigger": None, "decision_price": 12600.0, "day_low": 12550.0}
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
        out = _eval(_stop_setup(), 101.0, low=99.0)
        assert out["trigger"]["trigger"] == "R1C"
        out = C.evaluate(_stop_setup(), bars=_ctx_bars(), i=70, flags={"armed": True, "above": True}, price=101.0,
                         day_low=99.0, regime=None, policy=policy)
        assert out["trigger"]["stop"] == pytest.approx(95.95) and out["trigger"]["stop_rule"] == "TEST_STOP"
    finally:
        C.TRIGGERS.pop("TEST_ALWAYS")


# ---------------------------------------------------------------- runtime (SHADOW)
from observability import reentry_v3_recheck as RC3  # noqa: E402
from observability import reentry_v3_shadow as V3  # noqa: E402


def _db(path, bars):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trading_history (account_key TEXT, ticker TEXT, company_name TEXT, buy_date TEXT, "
                 "buy_price REAL, sell_date TEXT, sell_price REAL, profit_rate REAL, trigger_type TEXT, "
                 "exit_kind TEXT, scenario TEXT)")
    conn.execute("CREATE TABLE watchlist_history (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "analyzed_date TEXT, current_price REAL, buy_score INTEGER, min_score INTEGER, decision TEXT, "
                 "skip_reason TEXT, trigger_type TEXT, scenario TEXT, was_traded INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO trading_history VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("acct", "000001", "YC", bars[65]["date"] + " 10:00:00", 12050, bars[69]["date"] + " 14:00:00",
                  11600, -3.73, "t", "stop",
                  json.dumps({"trading_scenarios": {"key_levels": {k: str(v) for k, v in YC_LEVELS.items()}}})))
    conn.commit()
    conn.close()


def _runtime(tmp_path, monkeypatch):
    bars = yc_bars()
    db = tmp_path / "t.sqlite"
    _db(db, bars)
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

    def quote(ticker):
        return {"price": 12150.0, "low": 12000.0, "open": 12350.0, "high": 12380.0, "volume": 400.0,
                "observed_at": bars[71]["date"] + "T03:10:00+00:00", "source": "test"}
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
    assert [f.name for f in root.iterdir()] == ["reentry_v3_state_kr.lock"] and sent == []


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
    assert item["decision_price"] == 12150.0 and item["decision_time_local"].endswith("12:10 KST")
    assert item["attempts"]["L97"]["attempt"] == 1 and item["campaign"]["rules"] == ["L97", "SS"]
    assert item["stop"] == pytest.approx(11591.5) and item["target"] == 13200 and item["gates"]["G2_v2_one_retry"] is False
    assert item["appendix_sha256"] and "초분할 진입 프레임" in item["appendix_text"]
    system, user = calls[0]
    assert system == "SYS"
    assert "초분할 진입 프레임" in user and "초분할 증액 시나리오 (add_plan)" in user and "REPORT" in user
    assert "②S 박스 바닥 재시험" in user and "기준 레벨 L: 11,950.00" in user and "이번 시도: 1/3" in user
    assert "SHADOW 가상 포지션 수치" in user and "12:10 KST" in user
    result = json.loads((root / "reentry_v3_recheck_results_kr.jsonl").read_text())
    assert result["contract"] == RC3.RESULT_CONTRACT and result["approved"] is True and result["add_plan"] == {
        "scenarios": []}
    names = [n for n, _ in sent]
    assert "reentry_v3.shadow_trigger" in names and "reentry_v3.shadow_recheck" in names
    from observability.events import build_event
    for name, kw in sent:
        assert "[REDACTED]" not in json.dumps(build_event("x", service="s", attributes=kw["attributes"])["attributes"]), name
    # rerun of the same intraday slot: no second quote, decision or call
    again = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                llm=_fake_llm(calls))
    assert again["intraday"]["candidates"] == 0 and len(calls) == 1
    # close phase: the recorded decision becomes the attempt (entry at the lunch-time price)
    run(len(bars) - 1, llm_recheck=True, llm=_fake_llm(calls))
    state = json.loads((root / "reentry_v3_state_kr.json").read_text())
    first = state["watches"][0]["ledger"]["campaigns"]["L97"]["attempts"][0]
    assert first["mode"] == "INTRADAY" and first["decision_price"] == 12150.0 and first["status"] == "CLOSED"
    assert state["watches"][0]["status"] == "CLOSED" and len(calls) == 1
    assert "reentry_v3.shadow_exit" in [n for n, _ in sent] and "reentry_v3.shadow_campaign_end" in [n for n, _ in sent]


def test_micro_split_appendix_follows_the_live_flag(monkeypatch):
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ENABLED", raising=False)
    assert RC3.micro_split_appendix("KR", 0.45) == ""
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    from prism_core import add_plan_prompts, micro_split_live
    assert RC3.micro_split_appendix("KR", 0.45) == micro_split_live.buy_prompt_block("KR", "ko") + \
        add_plan_prompts.buy_block("KR", "ko", expected_initial=0.45)
    assert "약 45%" in RC3.micro_split_appendix("KR", 0.45)
    assert RC3.micro_split_appendix("US", 0.3).endswith(add_plan_prompts.buy_block("US", "en", expected_initial=0.3))


def test_recheck_instruction_appends_the_v3_section(monkeypatch):
    seen = {}
    monkeypatch.setattr(RC3, "recheck_instruction", lambda market, section: seen.update(m=market, s=section) or "X")
    assert RC3.instruction("US") == "X" and seen["m"] == "US" and seen["s"] is RC3.RECHECK_V3_KO
    assert "도구가 제공되지 않습니다" in RC3.RECHECK_V3_KO and "SHADOW 가상 기록" in RC3.RECHECK_V3_KO


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
    assert decision_day("KR", utc(2026, 10, 7, 3, 10)) == "2026-10-07"           # 12:10 KST
    assert decision_day("KR", utc(2026, 10, 7, 7, 0)) is None                     # 16:00 KST, closed
    assert decision_day("KR", utc(2026, 10, 10, 3, 10)) is None                   # Saturday
    assert decision_day("US", utc(2026, 10, 7, 16, 30)) == "2026-10-07"          # 12:30 New York
    assert decision_day("US", utc(2026, 11, 26, 17, 30)) is None                  # Thanksgiving
