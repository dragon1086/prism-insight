"""Pure-function checks for tools/replay_kr_stop_noise.py (no KIS, no DB)."""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from prism_core.oneil_adaptive_policy import initial_sizing

_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "replay_kr_stop_noise.py"
_SPEC = importlib.util.spec_from_file_location("replay_kr_stop_noise", _SOURCE)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


# ------------------------------------------------------------------ fixtures / helpers

def _daily(closes, start="2026-01-02", spread=2.0):
    """Build list of daily bar dicts from a close sequence."""
    day = date.fromisoformat(start)
    out = []
    for c in closes:
        out.append({"date": day.isoformat(), "high": c + spread / 2,
                    "low": c - spread / 2, "close": c})
        day += timedelta(days=1)
    return out


def _trade(buy_idx, sell_idx, bars, buy_price, sell_price, stop_loss,
           exit_kind="profit", buy_time="100000", ticker="000001"):
    """Build a trade dict as replay expects it."""
    buy_date = bars[buy_idx]["date"] + " " + buy_time[:2] + ":" + buy_time[2:4] + ":00"
    sell_date = bars[sell_idx]["date"] + " 15:20:00"
    return {
        "id": 1,
        "ticker": ticker,
        "buy_date": buy_date,
        "buy_price": float(buy_price),
        "sell_date": sell_date,
        "sell_price": float(sell_price),
        "exit_kind": exit_kind,
        "trigger_type": "momentum1",
        "stop_loss": float(stop_loss),
        "stop_loss_valid": True,
    }


def _minute_row(close, low=None, high=None):
    return {"close": close, "low": low or close - 0.5, "high": high or close + 0.5, "open": close, "vol": 1000}


def _sessions(bars):
    return sorted(b["date"] for b in bars)


def _bar_by(bars):
    return {b["date"]: b for b in bars}


# ------------------------------------------------------------------ check-time constants

def test_check_times_a_count_and_bounds():
    # 09:00 to 15:20 with 12 offsets per hour but capped at 15:20
    # Hours 9-14: 12 checks each = 72; hour 15: :00, :06, :10, :16, :20 = 5 checks; total = 77
    assert len(tool.CHECK_TIMES_A) == 77
    assert tool.CHECK_TIMES_A[0] == "090000"
    assert tool.CHECK_TIMES_A[-1] == "152000"


def test_check_times_h_exactly_6():
    assert tool.CHECK_TIMES_H == ("100000", "110000", "120000", "130000", "140000", "150000")


# ------------------------------------------------------------------ A stops at right check time

def test_a_stops_at_correct_check_time_and_price():
    """Price drops below A trigger at 10:06 check; arm A exits there."""
    bars = _daily([100.0] * 30 + [90.0] * 5)  # entry at index 30
    trade = _trade(buy_idx=30, sell_idx=34, bars=bars,
                   buy_price=100.0, sell_price=85.0, stop_loss=97.0, exit_kind="stop")
    # A trigger = max(97*0.995, 100*0.93) = max(96.515, 93) = 96.515
    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)
    sessions = _sessions(bars)
    bar_by = _bar_by(bars)

    # Entry day with a price breach at 10:06
    entry_day = trade["buy_date"][:10]
    entry_day_compact = entry_day.replace("-", "")
    minutes_data = {
        f"000001_{entry_day_compact}": {
            "090600": _minute_row(98.0),    # no breach
            "100000": _minute_row(97.0),    # no breach (97 > 96.515)
            "100600": _minute_row(96.0),    # breach! 96 < 96.515
            "101000": _minute_row(90.0),
        }
    }

    result = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "A", 1.0, a_trigger)
    assert "missing" not in result
    assert result["stopped"] is True
    assert result["exit_price"] == pytest.approx(96.0)
    assert result["exit_date"] == entry_day


def test_a_skips_check_times_before_buy_time():
    """On entry day, checks at or before buy time are skipped."""
    bars = _daily([100.0] * 30 + [90.0] * 5)
    # Buy at 10:30; check at 10:06 (before) should be skipped
    trade = _trade(buy_idx=30, sell_idx=34, bars=bars,
                   buy_price=100.0, sell_price=85.0, stop_loss=97.0, exit_kind="stop",
                   buy_time="103000")
    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)
    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    entry_day = trade["buy_date"][:10]
    entry_day_compact = entry_day.replace("-", "")
    minutes_data = {
        f"000001_{entry_day_compact}": {
            "100600": _minute_row(95.0),  # before buy_time 103000; should be skipped
            "103600": _minute_row(96.0),  # after buy_time; breach!
        }
    }
    result = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "A", 1.0, a_trigger)
    # 10:36 is the first valid check after 10:30; price 96 < 96.515 triggers
    assert result["stopped"] is True
    assert result["exit_date"] == entry_day


# ------------------------------------------------------------------ H: misses brief dip, catches sustained breach

def test_h_misses_brief_dip_between_hourly_checks():
    """H only checks at :00 each hour; a brief dip at 10:30 is missed but sustained breach at 11:00 is caught."""
    bars = _daily([100.0] * 30 + [90.0] * 5)
    trade = _trade(buy_idx=30, sell_idx=34, bars=bars,
                   buy_price=100.0, sell_price=85.0, stop_loss=97.0, exit_kind="stop")
    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)
    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    entry_day = trade["buy_date"][:10]
    entry_day_compact = entry_day.replace("-", "")
    minutes_data = {
        f"000001_{entry_day_compact}": {
            "103000": _minute_row(95.0),   # A would stop here; H does NOT check at :30
            "110000": _minute_row(95.5),   # H checks here; 95.5 < 96.515 -> H stops
        }
    }
    res_a = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "A", 1.0, a_trigger)
    res_h = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "H", 1.0, a_trigger)

    # A stops at 10:30
    assert res_a["stopped"] is True
    assert res_a["exit_price"] == pytest.approx(95.0)

    # H stops at 11:00
    assert res_h["stopped"] is True
    assert res_h["exit_price"] == pytest.approx(95.5)


def test_h_misses_brief_recovery_dip():
    """H misses a dip entirely if price recovers before the next hourly check.

    After the dip day, price recovers so daily lows exceed the trigger — no further
    minute data is needed.  H doesn't stop, so with exit_kind='stop' it exits at
    the 20th session close.
    """
    # Entry at index 30; only day 30 has a low dip, days 31+ recover to 100 (low 99 > 96.515).
    # Need >= 51 bars so session_20 = sessions[50] is a real bar.
    bars = _daily([100.0] * 30 + [90.0] + [100.0] * 25)
    trade = _trade(buy_idx=30, sell_idx=34, bars=bars,
                   buy_price=100.0, sell_price=85.0, stop_loss=97.0, exit_kind="stop")
    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)
    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    entry_day = trade["buy_date"][:10]
    dc = entry_day.replace("-", "")
    # Brief dip at 10:36 (A would catch via CHECK_TIMES_A), but 11:00 has recovered.
    # Days 31+ have low=99 > 96.515 so no minute data is required for them.
    minutes_data = {
        f"000001_{dc}": {
            "103600": _minute_row(95.0),   # A stops here (in CHECK_TIMES_A)
            "110000": _minute_row(97.5),   # H checks at 11:00; 97.5 > 96.515 → no H stop
            "150000": _minute_row(99.0),   # H last check on entry day
        }
    }
    res_a = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "A", 1.0, a_trigger)
    res_h = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "H", 1.0, a_trigger)
    assert res_a["stopped"] is True
    # H never triggers; actual exit was 'stop' and H didn't stop -> session_20 close
    assert res_h["stopped"] is False
    # session_20 = sessions[30 + 20] = sessions[50]; bars[50]["close"] = 100.0
    expected_close = bars[50]["close"]
    assert res_h["exit_price"] == pytest.approx(expected_close)


# ------------------------------------------------------------------ W: wider stop, weight from initial_sizing

def test_w_survives_dip_that_stops_a_and_uses_initial_sizing_weight():
    """With a high ATR, W trigger is lower than A trigger; W survives A's stop price."""
    # entry=100, ATR14 ~5 -> stop_proxy ~0.075 -> W_trigger ~92
    # stop_loss=97 -> A trigger = max(96.515, 93) = 96.515
    # A dip to 95 stops A but not W
    prices = [100.0] * 15 + [105.0, 95.0, 100.0, 102.0, 95.0, 93.0, 98.0, 100.0, 100.0, 100.0,
              100.0, 100.0, 100.0, 100.0, 100.0] + [100.0] * 20
    bars = _daily(prices, spread=10.0)  # wide spread so ATR14 is large

    trade = _trade(buy_idx=30, sell_idx=49, bars=bars,
                   buy_price=100.0, sell_price=105.0, stop_loss=97.0, exit_kind="profit")

    prior = [b for b in bars if b["date"] < trade["buy_date"][:10]]
    a14 = tool.atr14(prior)
    assert a14 is not None and a14 > 3.0, f"ATR14 should be large with spread=10, got {a14}"
    stop_proxy, initial = initial_sizing(100.0, a14)
    w_trigger = 100.0 * (1 - float(stop_proxy)) * 0.995
    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)

    # W trigger should be lower than A trigger for this setup (wider stop)
    assert w_trigger < a_trigger, f"W trigger {w_trigger:.3f} should be < A trigger {a_trigger:.3f}"

    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    entry_day = trade["buy_date"][:10]
    dc = entry_day.replace("-", "")

    # Price dips to 95 at check time: A stops, W doesn't
    minutes_data = {f"000001_{dc}": {"103600": _minute_row(95.0)}}

    res_a = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "A", 1.0, a_trigger)
    res_w = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "W", float(initial), w_trigger)

    assert res_a["stopped"] is True   # A stops at 95
    assert res_w["stopped"] is False  # W survives (95 > w_trigger which is ~92)
    # W uses actual sell_price since exit_kind != 'stop'
    assert res_w["exit_price"] == pytest.approx(105.0)
    assert res_w["weight"] == pytest.approx(float(initial))
    # Weight is between 0.30 and 0.80 per initial_sizing
    assert 0.30 <= float(initial) <= 0.80


# ------------------------------------------------------------------ non-stop actual exit preserved

def test_non_stop_actual_exit_used_when_arm_does_not_stop():
    """If exit_kind != 'stop' and arm never triggers, actual sell price is used."""
    bars = _daily([100.0] * 55)  # flat price; no dip
    trade = _trade(buy_idx=30, sell_idx=50, bars=bars,
                   buy_price=100.0, sell_price=115.0, stop_loss=97.0, exit_kind="profit")
    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)
    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    # daily lows are all 99 (spread=2), never <= 96.515 -> no minute lookup needed
    minutes_data = {}

    for arm in ("A", "H", "B"):
        res = tool._simulate_arm(trade, sessions, bar_by, minutes_data, arm, 1.0, a_trigger)
        assert "missing" not in res
        assert res["stopped"] is False
        assert res["exit_price"] == pytest.approx(115.0)
        assert res["exit_date"] == trade["sell_date"][:10]


# ------------------------------------------------------------------ avoided stop -> 20th session close

def test_avoided_stop_uses_session_20_close():
    """Actual exit was a stop, but arm doesn't trigger -> use 20th session close after entry."""
    closes = [100.0] * 55
    closes[35] = 120.0  # session_20 = entry_idx(30) + 20 = index 50, close = 100
    bars = _daily(closes)
    # Actual stop on sell day 40, but arm H never triggers
    trade = _trade(buy_idx=30, sell_idx=40, bars=bars,
                   buy_price=100.0, sell_price=94.0, stop_loss=97.0, exit_kind="stop")

    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    session_20_date = sessions[30 + 20]  # entry is index 30, 20th session after

    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)
    # daily lows are all 99 (spread=2); never <= 96.515 -> no minute fetching needed
    minutes_data = {}

    res_h = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "H", 1.0, a_trigger)
    assert "missing" not in res_h
    assert res_h["stopped"] is False
    assert res_h["exit_date"] == session_20_date
    assert res_h["exit_price"] == pytest.approx(bar_by[session_20_date]["close"])


# ------------------------------------------------------------------ missing minutes -> MISSING

def test_missing_minutes_marks_trade_as_missing():
    """If daily low <= trigger but minute data is absent, arm is MISSING."""
    bars = _daily([100.0] * 30 + [93.0] * 5)  # low = 93 - 1 = 92 for stop days
    trade = _trade(buy_idx=30, sell_idx=34, bars=bars,
                   buy_price=100.0, sell_price=85.0, stop_loss=97.0, exit_kind="stop")
    a_trigger = max(97.0 * 0.995, 100.0 * 0.93)  # ~96.515
    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    # daily low on entry day = 93 - 1 = 92 < 96.515, so we need minutes, but they're absent
    minutes_data = {}  # no minute data

    res = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "A", 1.0, a_trigger)
    assert "missing" in res


def test_missing_minutes_error_marker():
    """Minute data with error marker is treated as missing."""
    bars = _daily([100.0] * 30 + [92.0] * 5, spread=2.0)  # low ~91
    trade = _trade(buy_idx=30, sell_idx=34, bars=bars,
                   buy_price=100.0, sell_price=85.0, stop_loss=97.0, exit_kind="stop")
    a_trigger = 96.515
    sessions = _sessions(bars)
    bar_by = _bar_by(bars)
    entry_day = trade["buy_date"][:10]
    dc = entry_day.replace("-", "")
    minutes_data = {f"000001_{dc}": {"error": "KisError"}}
    res = tool._simulate_arm(trade, sessions, bar_by, minutes_data, "A", 1.0, a_trigger)
    assert "missing" in res


# ------------------------------------------------------------------ full replay integration

def _make_replay_fixtures():
    """Build synthetic trades, daily bars, minute data for replay() tests.

    Layout:
      bars[0:21]  close=100 spread=10 → ATR14 ≈ 10; stop_proxy=0.10; W_trigger≈89.55
      bars[21]    close=99  spread=10 → entry day, low=94 <= A_trigger=96.515
      bars[22:57] close=102 spread=10 → low=97 > 96.515; no minutes needed after entry day

    Arm outcomes for trade (entry=100, stop_loss=97, exit_kind='stop'):
      A: stops at 10:06 (95.5 < 96.515)          → stopped=True,  exit=95.5
      B: same trigger as A, initial_sizing weight → stopped=True,  exit=95.5
      H: entry-day 11:00 check = 97 > trigger;
         post-entry lows all 97 > trigger         → stopped=False, exit=session_20 close=102
      W: W_trigger≈89.55; entry low=94 > 89.55    → no minute check, stopped=False, exit=102
    """
    # bars[0:21] for ATR history, bars[21] = entry, bars[22:57] = post-entry (low > trigger)
    all_closes = [100.0] * 21 + [99.0] + [102.0] * 35
    bars = _daily(all_closes, spread=10.0)
    daily = {"000001": bars}

    entry_day = bars[21]["date"]
    prior = [b for b in bars if b["date"] < entry_day]
    a14 = tool.atr14(prior)
    assert a14 is not None and a14 > 0
    stop_proxy, initial = initial_sizing(100.0, a14)

    sessions = sorted(b["date"] for b in bars)
    ei = sessions.index(entry_day)
    session_20 = sessions[ei + 20]  # bars[41], close=102.0

    trade = {
        "id": 1, "ticker": "000001",
        "buy_date": entry_day + " 10:00:00", "buy_price": 100.0,
        "sell_date": bars[22]["date"] + " 15:20:00", "sell_price": 95.0,
        "exit_kind": "stop", "trigger_type": "momentum1",
        "stop_loss": 97.0, "stop_loss_valid": True,
    }

    entry_dc = entry_day.replace("-", "")
    # A/B check 10:06 → 95.5 < 96.515 → stop.  H check 11:00 → 97 > 96.515 → no stop.
    # W trigger ≈89.55; entry low=94 > 89.55 → W skips minute lookup entirely.
    min_store = {
        f"000001_{entry_dc}": {
            "100600": _minute_row(95.5),
            "110000": _minute_row(97.0),
        }
    }

    return [trade], daily, min_store, a14, float(initial), float(stop_proxy)


def test_replay_full_pipeline(tmp_path):
    """End-to-end replay with synthetic data; verify all arm outcomes.

    Fixture layout (see _make_replay_fixtures):
      A/B stop at entry-day 10:06 (95.5 < 96.515).
      H exits at session_20 close (no trigger found).
      W exits at session_20 close (W trigger ~89.55 is never reached).
    """
    trades, daily, min_store, a14, initial, stop_proxy = _make_replay_fixtures()

    trades_f = tmp_path / "trades.json"
    daily_f = tmp_path / "daily.json"
    minutes_f = tmp_path / "minutes.json"
    out_f = tmp_path / "result.json"

    trades_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "rows": trades}))
    daily_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "bars": daily}))
    minutes_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "minutes": min_store}))

    tool.replay(str(trades_f), str(daily_f), str(minutes_f), str(out_f),
                sizing=initial_sizing)

    result = json.loads(out_f.read_text())
    rows = result["rows"]
    assert len(rows) == 1
    r = rows[0]

    # A stops at 10:06 (95.5 < 96.515)
    assert r["stopped_A"] is True
    assert r["exit_price_A"] == pytest.approx(95.5)
    # B uses A trigger with initial_sizing weight
    assert r["stopped_B"] is True
    assert r["exit_price_B"] == pytest.approx(95.5)
    # H: 11:00 check = 97 > 96.515; post-entry lows > trigger → no stop; exits at session_20
    assert r["stopped_H"] is False
    # W: W_trigger ≈89.55; entry-day low=94 > 89.55 → never triggers; exits at session_20
    assert r["stopped_W"] is False

    summary = result["summary"]
    assert summary["rows_used"] == 1
    for arm in ("A", "H", "W", "B"):
        assert r[f"slot_{arm}"] is not None

    # A slot return: 1.0 * (95.5/100 - 1) = -0.045
    assert r["slot_A"] == pytest.approx(1.0 * (95.5 / 100.0 - 1.0))
    # H/W exit at session_20 close = bars[41]["close"] = 102.0
    sessions = sorted(b["date"] for b in daily["000001"])
    ei = sessions.index(trades[0]["buy_date"][:10])
    s20_close = daily["000001"][ei + 20]["close"]  # = 102.0
    assert r["slot_H"] == pytest.approx(1.0 * (s20_close / 100.0 - 1.0))
    assert r["slot_W"] == pytest.approx(initial * (s20_close / 100.0 - 1.0))


# ------------------------------------------------------------------ verdict boundaries

def test_verdict_h_candidate():
    hold = {
        "n": 35, "a_reproduction": 0.85,
        "diff_A_H_mean": 0.0025,           # >= +0.002
        "diff_A_H_ci90": [0.001, 0.005],   # lower > 0
        "mdd_A": -0.05, "mdd_H": -0.04,   # H mdd >= A mdd
        "loss_avg_A": -0.05, "loss_avg_H": -0.053,  # -0.053 >= -0.055 (10% worse threshold)
        "diff_A_W_mean": -0.001, "diff_A_W_ci90": [-0.003, 0.002],
        "mdd_W": -0.04, "loss_avg_W": -0.03, "whipsaw_A": 0.4, "whipsaw_W": 0.3,
    }
    v = tool._verdicts(hold)
    assert v["H"] == "H_CANDIDATE"


def test_verdict_h_retire_ci_below_zero():
    hold = {
        "n": 35, "a_reproduction": 0.90,
        "diff_A_H_mean": 0.0025,
        "diff_A_H_ci90": [-0.001, 0.005],  # lower <= 0 -> RETIRE
        "mdd_A": -0.05, "mdd_H": -0.04,
        "loss_avg_A": -0.05, "loss_avg_H": -0.053,
        "diff_A_W_mean": 0.0, "diff_A_W_ci90": [None, None],
        "mdd_W": -0.05, "loss_avg_W": None, "whipsaw_A": None, "whipsaw_W": None,
    }
    v = tool._verdicts(hold)
    assert v["H"] == "RETIRE"


def test_verdict_w_limited_live_review():
    hold = {
        "n": 35, "a_reproduction": 0.85,
        "diff_A_W_mean": -0.001,           # >= -0.002
        "diff_A_W_ci90": [-0.003, 0.001],
        "mdd_A": -0.05, "mdd_W": -0.03,   # W mdd > A mdd (less negative)
        "loss_avg_A": -0.06, "loss_avg_W": -0.042,  # -0.042 >= -0.06*0.75=-0.045 -> 30% reduction
        "whipsaw_A": 0.4, "whipsaw_W": 0.25,
        "diff_A_H_mean": 0.0, "diff_A_H_ci90": [None, None],
        "mdd_H": -0.05, "loss_avg_H": None,
    }
    v = tool._verdicts(hold)
    assert v["W"] == "LIMITED_LIVE_REVIEW"


def test_verdict_w_retire_insufficient_loss_reduction():
    hold = {
        "n": 35, "a_reproduction": 0.85,
        "diff_A_W_mean": -0.001,
        "diff_A_W_ci90": [-0.003, 0.001],
        "mdd_A": -0.05, "mdd_W": -0.03,
        "loss_avg_A": -0.06, "loss_avg_W": -0.05,  # only 16.7% reduction < 25% required
        "whipsaw_A": 0.4, "whipsaw_W": 0.25,
        "diff_A_H_mean": 0.0, "diff_A_H_ci90": [None, None],
        "mdd_H": -0.05, "loss_avg_H": None,
    }
    v = tool._verdicts(hold)
    assert v["W"] == "RETIRE"


def test_verdict_continue_capture_small_holdout():
    hold = {"n": 25, "a_reproduction": 0.90}
    v = tool._verdicts(hold)
    assert v["H"] == "CONTINUE_CAPTURE"
    assert v["W"] == "CONTINUE_CAPTURE"


def test_verdict_inconclusive_low_a_reproduction():
    hold = {"n": 40, "a_reproduction": 0.75, "diff_A_H_mean": 0.005,
            "diff_A_H_ci90": [0.001, 0.01], "mdd_A": -0.05, "mdd_H": -0.04,
            "loss_avg_A": -0.05, "loss_avg_H": -0.053}
    v = tool._verdicts(hold)
    assert v["H"] == "INCONCLUSIVE"
    assert v["W"] == "INCONCLUSIVE"


def test_verdict_b_always_reference():
    for n in (10, 35):
        hold = {"n": n, "a_reproduction": 0.85}
        v = tool._verdicts(hold)
        assert v["B"] == "REFERENCE"


# ------------------------------------------------------------------ drop_pyramid_adds is reused

def test_pyramid_adds_excluded_from_replay_input():
    rows = [
        {"id": 1, "ticker": "A", "buy_date": "2026-01-02 10:00", "sell_date": "2026-01-20 10:00"},
        {"id": 2, "ticker": "A", "buy_date": "2026-01-10 10:00", "sell_date": "2026-01-20 10:00"},
        {"id": 3, "ticker": "A", "buy_date": "2026-01-21 10:00", "sell_date": "2026-01-25 10:00"},
    ]
    kept = tool.drop_pyramid_adds(rows)
    assert [r["id"] for r in kept] == [1, 3]


# ------------------------------------------------------------------ extract_trades smoke test (no DB)

def test_extract_trades_excludes_invalid_stop(tmp_path):
    """Rows without valid stop_loss are flagged as excluded."""
    db = tmp_path / "test.db"
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE trading_history (
        id INTEGER, ticker TEXT, buy_date TEXT, buy_price REAL, sell_date TEXT,
        sell_price REAL, exit_kind TEXT, trigger_type TEXT, scenario TEXT)""")
    # Valid: stop_loss = 97 (< buy_price=100)
    conn.execute("INSERT INTO trading_history VALUES (1,'000001','2026-01-02 10:00',100,'2026-01-10 15:00',105,'profit','m1',?)",
                 (json.dumps({"stop_loss": 97.0}),))
    # Invalid: no stop_loss
    conn.execute("INSERT INTO trading_history VALUES (2,'000002','2026-01-03 10:00',100,'2026-01-11 15:00',95,'stop','m2',?)",
                 (json.dumps({}),))
    # Invalid: stop_loss > buy_price
    conn.execute("INSERT INTO trading_history VALUES (3,'000003','2026-01-04 10:00',100,'2026-01-12 15:00',95,'stop','m3',?)",
                 (json.dumps({"stop_loss": 105.0}),))
    conn.commit()
    conn.close()

    out = tmp_path / "trades.json"
    tool.extract_trades(str(db), str(out))
    data = json.loads(out.read_text())
    assert len(data["rows"]) == 3
    assert data["excluded_no_valid_stop"] == 2
    assert data["rows"][0]["stop_loss_valid"] is True
    assert data["rows"][1]["stop_loss_valid"] is False
    assert data["rows"][2]["stop_loss_valid"] is False
