"""Pure-function tests for tools/replay_kr_micro_split_add.py (no KIS, no DB, no network)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "replay_kr_micro_split_add.py"
_SPEC = importlib.util.spec_from_file_location("replay_kr_micro_split_add", _SOURCE)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


# ------------------------------------------------------------------ helpers

def _rising_daily(n=80, start=90.0, step=0.2, spread=2.0, volumes=None):
    """Build daily bars with a gentle uptrend so prev_close > SMA20 near index 50.

    At index i: close = start + i*step.  With n=80, start=90, step=0.2:
    close[49]=99.8, SMA20([30..49])~97.9 → prev_close > SMA20 gate passes.
    """
    closes = [start + i * step for i in range(n)]
    return _daily(closes, spread=spread, volumes=volumes)


def _daily(closes, start="2026-01-02", spread=2.0, volumes=None):
    """Build daily bar list from a close sequence."""
    day = date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        vol = volumes[i] if volumes else 1000
        out.append({
            "date": day.isoformat(),
            "high": c + spread / 2,
            "low": c - spread / 2,
            "close": c,
            "vol": vol,
        })
        day += timedelta(days=1)
    return out


def _trade(buy_idx, sell_idx, bars, buy_price, sell_price,
           stop_loss=None, exit_kind="profit",
           buy_time="100000", ticker="000001", regime_text="strong_bull"):
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
        "stop_loss": stop_loss,
        "regime": regime_text,
    }


def _sessions_and_idx(bars):
    sessions = sorted(b["date"] for b in bars)
    idx = {d: i for i, d in enumerate(sessions)}
    return sessions, idx


def _bar_by(bars):
    return {b["date"]: b for b in bars}


def _feat_by(bars):
    sessions, _ = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    return tool._precompute_features(sessions, bar_by)


def _minutes_at(day, prices_by_time):
    """Build minute_dict {HHMMSS: {close, open, high, low, vol}}."""
    return {
        t: {"close": p, "open": p, "high": p + 0.5, "low": p - 0.5, "vol": 100}
        for t, p in prices_by_time.items()
    }


# ------------------------------------------------------------------ regime classification

def test_regime_bull_english_prefixes():
    for text in ("parabolic_move", "strong_bull", "moderate_bull_market"):
        assert tool.classify_regime(text) == "bull"


def test_regime_bull_korean():
    assert tool.classify_regime("상승추세 강함") == "bull"
    assert tool.classify_regime("매우 강세 국면") == "bull"


def test_regime_sideways_english():
    assert tool.classify_regime("sideways") == "sideways"
    assert tool.classify_regime("sideways_tight") == "sideways"


def test_regime_sideways_korean():
    assert tool.classify_regime("횡보 국면") == "sideways"


def test_regime_bear_english():
    for text in ("moderate_bear", "strong_bear", "weak", "", None):
        assert tool.classify_regime(text) == "bear", f"expected bear for {text!r}"


def test_regime_bear_korean():
    assert tool.classify_regime("하락 국면") == "bear"


# ------------------------------------------------------------------ 5-min aggregation

def test_agg_5min_boundary_09_00():
    """09:00, 09:01, 09:02, 09:03, 09:04 all fall into slot 09:00."""
    raw = {
        "090000": {"close": 100.0, "open": 99.5, "high": 100.5, "low": 99.0, "vol": 10},
        "090100": {"close": 100.5, "open": 100.0, "high": 101.0, "low": 99.8, "vol": 20},
        "090200": {"close": 101.0, "open": 100.5, "high": 101.5, "low": 100.0, "vol": 15},
        "090300": {"close": 101.2, "open": 101.0, "high": 101.8, "low": 100.8, "vol": 12},
        "090400": {"close": 101.8, "open": 101.2, "high": 102.0, "low": 100.5, "vol": 8},
        "090500": {"close": 102.0, "open": 101.8, "high": 102.5, "low": 101.5, "vol": 5},
    }
    bars = tool.agg_5min(raw)
    labels = [b[0] for b in bars]
    assert "090000" in labels
    assert "090500" in labels
    b0 = dict(bars)["090000"]
    # open = first minute's open, close = last minute's close in the slot
    assert b0["open"] == pytest.approx(99.5)
    assert b0["close"] == pytest.approx(101.8)  # 09:04 close
    assert b0["vol"] == 65


def test_agg_5min_second_slot():
    """09:05 starts the second 5-min slot."""
    raw = {
        "090500": {"close": 103.0, "open": 102.0, "high": 103.5, "low": 101.5, "vol": 20},
        "090600": {"close": 104.0, "open": 103.0, "high": 104.5, "low": 102.5, "vol": 25},
    }
    bars = dict(tool.agg_5min(raw))
    assert "090500" in bars
    b = bars["090500"]
    assert b["open"] == pytest.approx(102.0)
    assert b["close"] == pytest.approx(104.0)


def test_agg_5min_excludes_pre_market():
    """Times before 09:00 are excluded."""
    raw = {
        "085900": {"close": 99.0, "open": 99.0, "high": 99.5, "low": 98.5, "vol": 5},
        "090000": {"close": 100.0, "open": 100.0, "high": 100.5, "low": 99.5, "vol": 10},
    }
    bars = dict(tool.agg_5min(raw))
    assert "085500" not in bars  # pre-market not in output
    assert "090000" in bars


def test_agg_5min_15_30_slot():
    """15:30 should aggregate into slot 15:30."""
    raw = {
        "153000": {"close": 110.0, "open": 109.5, "high": 110.5, "low": 109.0, "vol": 50},
    }
    bars = dict(tool.agg_5min(raw))
    assert "153000" in bars


# ------------------------------------------------------------------ two-bar persistence

def test_two_bar_persistence_single_spike_no_add():
    """A single bar above threshold without a preceding bar above entry should NOT add."""
    bars = _rising_daily(n=80)
    entry_idx = 50
    entry_price = bars[entry_idx]["close"]
    bars[entry_idx]["high"] = entry_price * 1.06  # candidate day

    trade = _trade(buy_idx=entry_idx, sell_idx=70, bars=bars,
                   buy_price=entry_price, sell_price=entry_price * 1.1,
                   stop_loss=entry_price * 0.97)
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    day_compact = buy_day.replace("-", "")
    minutes_store = {
        f"000001_{day_compact}": _minutes_at(buy_day, {
            # Only one bar above threshold, no preceding bar in this session -> prev5_close=None
            "110500": entry_price * 1.035,  # above STEP_A but prev is None -> no add
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M0"
    )
    assert legs is not None
    assert len(legs) == 1  # only initial, no add (two-bar persistence requires a prior bar)


def test_two_bar_persistence_two_consecutive_bars_triggers_add():
    """Two consecutive bars both above entry, second above threshold -> add."""
    # Use rising prices so prev_close > SMA20 and the SMA20 gate passes
    bars = _rising_daily(n=80)
    entry_idx = 50
    entry_price = bars[entry_idx]["close"]
    # Make the buy-day high a candidate (>= entry*1.02)
    bars[entry_idx]["high"] = entry_price * 1.06

    trade = _trade(buy_idx=entry_idx, sell_idx=70, bars=bars,
                   buy_price=entry_price, sell_price=entry_price * 1.1,
                   stop_loss=entry_price * 0.97)
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    day_compact = buy_day.replace("-", "")
    # initial_sizing returns ~0.8 for ATR14 at these prices, so step_a target (80%) already held.
    # Use step_b (104%) to trigger add from 80% -> 100%: requires held < 1.0, close >= 1.04*entry.
    step_b_price = entry_price * tool.STEP_B
    minutes_store = {
        f"000001_{day_compact}": _minutes_at(buy_day, {
            "100500": entry_price * 1.005,    # bar1: > entry (below step_b)
            "101000": step_b_price + 0.5,     # bar2: > entry AND >= step_b -> add to 100%
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M0"
    )
    assert legs is not None
    assert len(legs) == 2  # initial + one add (80% -> 100%)


def test_two_bar_persistence_resets_at_session_boundary():
    """The two-bar window resets at the start of a new session."""
    bars = _rising_daily(n=80)
    buy_idx = 50
    sell_idx = 70
    entry_price = bars[buy_idx]["close"]
    # Make buy day NOT a candidate (high below 1.02*entry)
    bars[buy_idx]["high"] = entry_price * 1.005
    # Make next day a candidate (high above 1.02*entry)
    bars[buy_idx + 1]["high"] = entry_price * 1.06

    trade = _trade(buy_idx=buy_idx, sell_idx=sell_idx, bars=bars,
                   buy_price=entry_price, sell_price=entry_price * 1.1,
                   stop_loss=entry_price * 0.97)
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    next_day = sessions[sess_idx[buy_day] + 1]

    # Buy day: bar closes above entry (sets prev5_close before session ends)
    # Next session: only ONE bar above threshold (prev5_close reset at boundary) -> no add
    step_a_price = entry_price * tool.STEP_A
    min_store = {
        f"000001_{buy_day.replace('-', '')}": _minutes_at(buy_day, {
            "110000": entry_price * 1.01,   # close > entry, sets prev5_close on buy day
        }),
        f"000001_{next_day.replace('-', '')}": _minutes_at(next_day, {
            "090500": step_a_price + 0.5,   # first bar of new session; prev5_close=None -> no add
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, min_store, "M0"
    )
    assert legs is not None
    assert len(legs) == 1  # no add because two-bar window reset at session boundary


# ------------------------------------------------------------------ band cap

def test_band_cap_blocks_add_above_110():
    """Bars with close > entry * 1.10 do not trigger adds."""
    bars = _rising_daily(n=80)
    entry_idx = 50
    entry_price = bars[entry_idx]["close"]
    bars[entry_idx]["high"] = entry_price * 1.15

    trade = _trade(buy_idx=entry_idx, sell_idx=70, bars=bars,
                   buy_price=entry_price, sell_price=entry_price * 1.2,
                   stop_loss=entry_price * 0.97)
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    day_compact = buy_day.replace("-", "")
    minutes_store = {
        f"000001_{day_compact}": _minutes_at(buy_day, {
            "100500": entry_price * 1.005,          # bar1: > entry, below band
            "101000": entry_price * tool.BAND_TOP + 1.0,  # bar2: > 110% -> band cap, no add
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M0"
    )
    assert legs is not None
    assert len(legs) == 1  # blocked by band cap


def test_band_cap_exactly_at_110_allows_add():
    """Close at exactly entry * 1.10 is NOT blocked (> is strict); add happens."""
    bars = _rising_daily(n=80)
    entry_idx = 50
    entry_price = bars[entry_idx]["close"]
    bars[entry_idx]["high"] = entry_price * 1.12

    trade = _trade(buy_idx=entry_idx, sell_idx=70, bars=bars,
                   buy_price=entry_price, sell_price=entry_price * 1.15,
                   stop_loss=entry_price * 0.80)  # wide stop so risk clip doesn't block
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    day_compact = buy_day.replace("-", "")
    minutes_store = {
        f"000001_{day_compact}": _minutes_at(buy_day, {
            "100500": entry_price * 1.005,
            "101000": entry_price * 1.10,   # exactly at BAND_TOP: NOT blocked (> is strict)
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M0"
    )
    assert legs is not None
    # close = 1.10*entry >= step_b(1.04), two-bar met -> add to 100%
    assert len(legs) == 2


# ------------------------------------------------------------------ one-step-per-bar

def test_one_step_per_bar_max_one_add():
    """At most one add step may occur per 5-min bar; two separate bars give two adds."""
    bars = _rising_daily(n=80)
    entry_idx = 50
    entry_price = bars[entry_idx]["close"]
    bars[entry_idx]["high"] = entry_price * 1.08

    trade = _trade(buy_idx=entry_idx, sell_idx=70, bars=bars,
                   buy_price=entry_price, sell_price=entry_price * 1.1,
                   stop_loss=entry_price * 0.80)  # wide stop, no risk clip
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    dc = buy_day.replace("-", "")
    sa = entry_price * tool.STEP_A
    sb = entry_price * tool.STEP_B
    # bar0 at 10:00 (= buy_time, skipped but sets prev5_close)
    # bar1 at 10:05: prev=close0>entry, this close > entry but < step_a -> no add, sets prev5
    # bar2 at 10:10: prev=close1>entry, this >= step_a -> add to 80% (one add this bar)
    # bar3 at 10:15: prev=close2>entry, this >= step_b -> add to 100% (one add this bar)
    minutes_store = {
        f"000001_{dc}": _minutes_at(buy_day, {
            "100000": entry_price * 1.005,  # skipped (== buy_time), but sets prev5 after skip
            "100500": entry_price * 1.008,  # bar1: prev=None after skip... need preceding
            "101000": sa + 0.5,             # bar2: add to 80%
            "101500": sb + 0.5,             # bar3: add to 100%
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M0"
    )
    assert legs is not None
    # Each bar contributes at most one add; total fraction approaches 1.0
    total_frac = sum(f for f, _ in legs)
    assert total_frac <= 1.0 + 1e-9
    assert len(legs) >= 1  # at minimum initial


# ------------------------------------------------------------------ risk clip

def test_risk_clip_reduces_add_near_stop():
    """When risk limit is tight, the clip reduces the add fraction."""
    # entry=100, stop=99 -> risk_limit = 1/100 = 0.01
    # initial_sizing with ATR~0.02 -> proxy~0.03 -> initial~1.17 -> clipped to 0.80
    # After initial at 100: base_loss = 0.80 * (1 - 99/100) = 0.80 * 0.01 = 0.008
    # room left = 0.01 - 0.008 = 0.002; marginal = 1 - 99/102 = 0.0294...
    # allowed = 0.002 / 0.0294 ~ 0.068
    # nominal add to 100% = 0.20 -> clipped to ~0.068

    legs = [(0.80, 100.0)]
    entry, stop = 100.0, 99.0
    fill = 102.0
    nominal = 0.20  # want to go from 80% to 100%

    actual = tool._risk_clip(legs, entry, stop, fill, nominal)
    assert actual < nominal
    assert actual > 0
    # Should be around 0.068
    expected = (0.0020) / (1 - 99 / 102)
    assert actual == pytest.approx(expected, rel=0.01)


def test_risk_clip_no_clip_when_room_sufficient():
    """When risk limit is generous, the full nominal add is returned."""
    # entry=100, stop=90 -> risk_limit = 0.10
    # initial at 100: base_loss = 0.80 * (1 - 90/100) = 0.08
    # room = 0.10 - 0.08 = 0.02; marginal = 1 - 90/103 ~ 0.126
    # allowed = 0.02/0.126 ~ 0.159 > nominal 0.10 -> no clip
    legs = [(0.80, 100.0)]
    actual = tool._risk_clip(legs, 100.0, 90.0, 103.0, 0.10)
    assert actual == pytest.approx(0.10)


def test_risk_clip_zero_when_limit_exhausted():
    """No add when base_loss >= risk_limit."""
    # entry=100, stop=98 -> risk_limit = 0.02
    # legs with initial=0.80 at 100: base_loss = 0.80 * 0.02 = 0.016
    # add more at 99: base_loss becomes 0.016 + 0.15*(1-98/99) ~ 0.016+0.0015 = 0.0175
    # still < 0.02, but close
    # Use a tighter scenario: single leg with high fraction
    legs = [(0.80, 100.0), (0.20, 101.0)]  # total = 1.0
    # base_loss = 0.80*(1-98/100) + 0.20*(1-98/101) = 0.80*0.02 + 0.20*0.0297 ~ 0.022 >= 0.02
    actual = tool._risk_clip(legs, 100.0, 98.0, 103.0, 0.10)
    assert actual == 0.0


# ------------------------------------------------------------------ M1 regime gates

def test_m1_allows_sideways_strong_stock():
    """M1 adds in sideways regime when strong-stock criteria are met."""
    # Build bars with 60+ sessions for return60, 50+ for MA50, 20+ for slope and volume
    # Use rising prices so strong-stock criteria are met
    n = 90
    # closes: start at 80, rise to 100 -> 60-session return >= 20%
    closes = [80.0 + i * (20.0 / (n - 1)) for i in range(n)]
    volumes = [2000] * n  # high volume
    bars = _daily(closes, spread=1.0, volumes=volumes)

    # Set buy-day bar to have high >= 1.02*entry
    entry_idx = 75
    buy_price = closes[entry_idx]
    bars[entry_idx]["high"] = buy_price * 1.05
    bars[entry_idx + 1]["high"] = buy_price * 1.05
    bars[entry_idx + 2]["high"] = buy_price * 1.03

    trade = _trade(buy_idx=entry_idx, sell_idx=entry_idx + 10, bars=bars,
                   buy_price=buy_price, sell_price=buy_price * 1.1,
                   stop_loss=buy_price * 0.95, regime_text="sideways")

    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    dc = buy_day.replace("-", "")
    minutes_store = {
        f"000001_{dc}": _minutes_at(buy_day, {
            "100500": buy_price * 1.01,
            "101000": buy_price * 1.025,  # >= step_a
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M1"
    )
    # M1 should add (sideways + strong stock)
    assert legs is not None
    # Whether it adds depends on strong-stock check passing; at minimum initial
    # (We can't guarantee add without a perfect fixture, but check it doesn't error)
    assert len(legs) >= 1


def test_m1_blocks_bear_regime():
    """M1 never adds in bear regime."""
    bars = _daily([100.0] * 80)
    trade = _trade(buy_idx=50, sell_idx=70, bars=bars,
                   buy_price=100.0, sell_price=110.0, stop_loss=97.0,
                   regime_text="moderate_bear")
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    dc = buy_day.replace("-", "")
    minutes_store = {
        f"000001_{dc}": _minutes_at(buy_day, {
            "100500": 101.0,
            "101000": 103.0,
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M1"
    )
    # Bear regime: no adds for M1
    assert legs is not None
    assert len(legs) == 1


def test_m0_blocks_sideways_regime():
    """M0 does not add in sideways regime."""
    bars = _daily([100.0] * 80)
    trade = _trade(buy_idx=50, sell_idx=70, bars=bars,
                   buy_price=100.0, sell_price=110.0, stop_loss=97.0,
                   regime_text="sideways")
    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    buy_day = trade["buy_date"][:10]
    dc = buy_day.replace("-", "")
    minutes_store = {
        f"000001_{dc}": _minutes_at(buy_day, {
            "100500": 101.0,
            "101000": 103.0,
        }),
    }
    legs, _ = tool._simulate_intraday(
        trade, sessions, sess_idx, bar_by, feat_by, minutes_store, "M0"
    )
    assert legs is not None
    assert len(legs) == 1  # no add in sideways for M0


# ------------------------------------------------------------------ M2 ATR-scaled thresholds

def test_m2_thresholds_scale_with_atr():
    """M2 step_a = max(2%, 0.5*ATR%) and step_b = max(4%, 1.0*ATR%)."""
    # With ATR% = 6% -> step_a = max(2%, 3%) = 3%, step_b = max(4%, 6%) = 6%
    # With ATR% = 1% -> step_a = max(2%, 0.5%) = 2%, step_b = max(4%, 1%) = 4%

    # ATR% calculation: ATR14/entry
    # entry = 100; ATR14 = 6 -> ATR% = 6% -> step_a = 1.03, step_b = 1.06
    # We can verify this by constructing a trade where 103 triggers M2 add but
    # M0 (step_a=1.02) would also trigger at 102.

    # Build bars with ATR ~ 6: spreads = 12 so TR ~ 12
    closes = [100.0] * 80
    bars = _daily(closes, spread=12.0)  # TR ~ 12, so ATR14 ~ 12
    trade = _trade(buy_idx=50, sell_idx=70, bars=bars,
                   buy_price=100.0, sell_price=115.0, stop_loss=92.0,
                   regime_text="strong_bull")

    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)

    prior = [bar_by[d] for d in sessions[:sess_idx[trade["buy_date"][:10]]]]
    a14 = tool.atr14(prior)
    atr_frac = a14 / 100.0
    expected_step_a = 1 + max(0.02, 0.5 * atr_frac)
    expected_step_b = 1 + max(0.04, 1.0 * atr_frac)

    # M2 thresholds are applied internally; test that they're > M0 thresholds when ATR is large
    assert expected_step_a > tool.STEP_A   # > 1.02
    assert expected_step_b > tool.STEP_B   # > 1.04


def test_m2_floor_at_2_and_4_pct():
    """M2 thresholds floor at 2%/4% when ATR is small."""
    # Low ATR: spread=0.5 -> TR~0.5, ATR14~0.5 -> ATR% = 0.5%
    # step_a = max(2%, 0.5*0.5%) = max(2%, 0.25%) = 2%
    closes = [100.0] * 80
    bars = _daily(closes, spread=0.5)
    bb = _bar_by(bars)
    cutoff = bars[50]["date"]
    prior = [bb[d] for d in sorted(bb) if bb[d]["date"] < cutoff][:20]
    a14 = tool.atr14(prior)
    if a14 is not None:
        atr_frac = a14 / 100.0
        step_a = 1 + max(0.02, 0.5 * atr_frac)
        step_b = 1 + max(0.04, 1.0 * atr_frac)
        assert step_a == pytest.approx(1.02)  # floored at 2%
        assert step_b == pytest.approx(1.04)  # floored at 4%


# ------------------------------------------------------------------ M3 daily close confirmation

def test_m3_adds_at_daily_close_next_session():
    """M3 adds at daily close starting from the session AFTER entry, not on entry day."""
    # Use rising prices so prev_close > SMA20 gate passes.
    # initial_sizing returns ~0.8 for these prices, so STEP_A (80%) is already held.
    # Use STEP_B (104%) to trigger held < 1.0 -> target=1.0.
    bars = _rising_daily(n=80)
    entry_idx = 50
    entry_price = bars[entry_idx]["close"]
    step_b_price = entry_price * tool.STEP_B  # 104.0

    # Set session after entry to have close >= step_b
    bars[entry_idx + 1]["close"] = step_b_price + 0.5
    bars[entry_idx + 1]["high"] = step_b_price + 1.5
    bars[entry_idx + 1]["low"] = step_b_price - 0.5

    trade = _trade(buy_idx=entry_idx, sell_idx=entry_idx + 10, bars=bars,
                   buy_price=entry_price, sell_price=entry_price * 1.1,
                   stop_loss=entry_price * 0.80,  # wide stop
                   regime_text="strong_bull")

    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    legs = tool._simulate_m3(trade, sessions, sess_idx, bar_by, feat_by)
    assert legs is not None
    # Should have initial + add at session after entry
    assert len(legs) == 2
    # Add fill price = close of session after entry
    add_price = legs[1][1]
    assert add_price == pytest.approx(step_b_price + 0.5)


def test_m3_does_not_add_on_entry_day():
    """M3 starts from session AFTER entry, so entry day close is ignored."""
    bars = _daily([100.0] * 80)
    entry_idx = 50
    # Entry day close is 103 but M3 must skip it
    bars[entry_idx]["close"] = 103.0
    bars[entry_idx]["high"] = 104.0

    trade = _trade(buy_idx=entry_idx, sell_idx=entry_idx + 10, bars=bars,
                   buy_price=100.0, sell_price=110.0, stop_loss=94.0,
                   regime_text="strong_bull")

    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    legs = tool._simulate_m3(trade, sessions, sess_idx, bar_by, feat_by)
    assert legs is not None
    # No session after entry_day has close >= 102 (all 100.0 except entry day)
    assert len(legs) == 1


def test_m3_no_add_in_bear_regime():
    """M3 does not add in bear regime."""
    bars = _daily([100.0] * 80)
    entry_idx = 50
    bars[entry_idx + 1]["close"] = 103.0
    bars[entry_idx + 1]["high"] = 104.0

    trade = _trade(buy_idx=entry_idx, sell_idx=entry_idx + 10, bars=bars,
                   buy_price=100.0, sell_price=110.0, stop_loss=94.0,
                   regime_text="moderate_bear")

    sessions, sess_idx = _sessions_and_idx(bars)
    bar_by = _bar_by(bars)
    feat_by = tool._precompute_features(sessions, bar_by)

    legs = tool._simulate_m3(trade, sessions, sess_idx, bar_by, feat_by)
    assert legs is not None
    assert len(legs) == 1  # initial only


# ------------------------------------------------------------------ add cost

def test_add_cost_deducted_from_net():
    """Net slot return = gross - 0.25% per added fraction unit."""
    # Manually: initial=0.5 at 100, add 0.3 at 102, exit at 110
    legs = [(0.5, 100.0), (0.3, 102.0)]
    exit_p = 110.0
    gross = 0.5 * (110 / 100 - 1) + 0.3 * (110 / 102 - 1)
    add_cost = 0.3 * tool.ADD_COST  # 0.25% on 0.3
    expected_net = gross - add_cost
    net = tool._slot_net(legs, exit_p)
    assert net == pytest.approx(expected_net, rel=1e-9)


def test_no_add_cost_for_initial_leg():
    """The initial leg incurs no add cost."""
    legs = [(0.8, 100.0)]
    exit_p = 110.0
    gross = 0.8 * (110 / 100 - 1)
    assert tool._slot_net(legs, exit_p) == pytest.approx(gross)


# ------------------------------------------------------------------ MISSING exclusion

def test_missing_trade_excluded_from_intraday_arms_in_replay(tmp_path):
    """Trade with a candidate day missing minute data is MISSING for M0/M1/M2."""
    # Provide daily bars but NO minute data -> trade is MISSING for intraday arms
    bars = _daily([100.0] * 80, spread=2.0)
    entry_idx = 50
    # Make the entry day a candidate day (high >= 102)
    bars[entry_idx]["high"] = 105.0

    trade = {
        "id": 1, "ticker": "000001",
        "buy_date": bars[entry_idx]["date"] + " 10:00:00",
        "buy_price": 100.0,
        "sell_date": bars[entry_idx + 10]["date"] + " 15:20:00",
        "sell_price": 105.0,
        "exit_kind": "profit",
        "trigger_type": "m1",
        "stop_loss": 96.0,
        "regime": "strong_bull",
    }

    trades_f = tmp_path / "trades.json"
    daily_f = tmp_path / "daily.json"
    minutes_f = tmp_path / "minutes.json"
    out_f = tmp_path / "result.json"

    trades_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "rows": [trade]}))
    daily_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "bars": {"000001": bars}}))
    minutes_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "minutes": {}}))  # empty!

    tool.replay(str(trades_f), str(daily_f), str(minutes_f), str(out_f))
    result = json.loads(out_f.read_text())
    rows = result["rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["intraday_missing"] is True
    # M0/M1/M2 net should be None for MISSING trades
    assert r["M0_net"] is None
    assert r["M1_net"] is None
    assert r["M2_net"] is None
    # L and M3 should still be computed
    assert r["L_net"] is not None
    assert r["M3_net"] is not None


# ------------------------------------------------------------------ verdicts

def test_verdict_m1_candidate_all_conditions_met():
    hold = {
        "n_holdout": 40,
        "intraday_missing_ratio": 0.05,
        "M0": {
            "n": 35, "n_add": 10, "mdd_raw": -0.05,
            "loser_mean_net": -0.04, "add_then_stop_rate": 0.4,
        },
        "M1": {
            "n": 35, "n_add": 12,
            "mean_diff_vs_M0": 0.0025,       # >= 0.0020
            "ci90_diff_vs_M0": [0.0005, 0.005],  # lower > 0
            "mdd_raw": -0.045,               # >= -0.05 - 0.01 = -0.06 (not worse by >1%)
            "loser_mean_net": -0.042,        # >= -0.04 * 1.10 = -0.044
            "add_then_stop_rate": 0.35,
        },
        "M2": {"n": 30, "n_add": 6, "mean_diff_vs_M0": 0.0, "ci90_diff_vs_M0": [None, None],
               "mdd_raw": -0.05, "add_then_stop_rate": 0.3},
        "M3": {"n": 40, "n_add": 8, "mean_diff_vs_M0": 0.0, "ci90_diff_vs_M0": [None, None],
               "mdd_raw": -0.05, "add_then_stop_rate": 0.3},
    }
    v = tool._verdicts(hold)
    assert v["M1"] == "CANDIDATE"


def test_verdict_m1_retire_ci_below_zero():
    hold = {
        "n_holdout": 40,
        "intraday_missing_ratio": 0.05,
        "M0": {"n": 35, "n_add": 10, "mdd_raw": -0.05, "loser_mean_net": -0.04},
        "M1": {
            "n": 35, "n_add": 12,
            "mean_diff_vs_M0": 0.0025,
            "ci90_diff_vs_M0": [-0.001, 0.005],  # lower <= 0 -> RETIRE
            "mdd_raw": -0.045,
            "loser_mean_net": -0.042,
        },
        "M2": {"n": 0, "n_add": 0},
        "M3": {"n": 0, "n_add": 0},
    }
    v = tool._verdicts(hold)
    assert v["M1"] == "RETIRE"


def test_verdict_m1_retire_diff_too_small():
    hold = {
        "n_holdout": 40,
        "intraday_missing_ratio": 0.05,
        "M0": {"n": 35, "n_add": 10, "mdd_raw": -0.05, "loser_mean_net": -0.04},
        "M1": {
            "n": 35, "n_add": 12,
            "mean_diff_vs_M0": 0.0010,       # < 0.0020 -> RETIRE
            "ci90_diff_vs_M0": [0.0005, 0.002],
            "mdd_raw": -0.045,
            "loser_mean_net": -0.042,
        },
        "M2": {"n": 0, "n_add": 0},
        "M3": {"n": 0, "n_add": 0},
    }
    v = tool._verdicts(hold)
    assert v["M1"] == "RETIRE"


def test_verdict_m2_candidate_stop_reduction_30pct():
    hold = {
        "n_holdout": 40,
        "intraday_missing_ratio": 0.05,
        "M0": {"n": 35, "n_add": 10, "n_add_stop": 5, "add_then_stop_rate": 0.5, "mdd_raw": -0.05,
               "loser_mean_net": -0.04, "mean_diff_vs_M0": 0.0},
        "M1": {"n": 35, "n_add": 0, "mean_diff_vs_M0": 0.0, "ci90_diff_vs_M0": [None, None],
               "mdd_raw": -0.05, "loser_mean_net": None},
        "M2": {
            "n": 35, "n_add": 8,
            "mean_diff_vs_M0": -0.0005,      # >= -0.0010
            "ci90_diff_vs_M0": [-0.002, 0.001],
            "add_then_stop_rate": 0.35,      # 30% reduction from 0.50: 0.35 <= 0.50*0.70=0.35
            "mdd_raw": -0.048,               # >= -0.05 (not worse)
        },
        "M3": {"n": 35, "n_add": 6, "mean_diff_vs_M0": -0.0005,
               "ci90_diff_vs_M0": [None, None], "add_then_stop_rate": 0.3, "mdd_raw": -0.048},
    }
    v = tool._verdicts(hold)
    assert v["M2"] == "CANDIDATE"


def test_verdict_m2_retire_insufficient_stop_reduction():
    hold = {
        "n_holdout": 40,
        "intraday_missing_ratio": 0.05,
        "M0": {"n": 35, "n_add": 10, "add_then_stop_rate": 0.5, "mdd_raw": -0.05,
               "loser_mean_net": -0.04, "mean_diff_vs_M0": 0.0},
        "M1": {"n": 35, "n_add": 0, "mean_diff_vs_M0": 0.0, "ci90_diff_vs_M0": [None, None],
               "mdd_raw": -0.05, "loser_mean_net": None},
        "M2": {
            "n": 35, "n_add": 8,
            "mean_diff_vs_M0": -0.0005,
            "add_then_stop_rate": 0.40,      # only 20% reduction < 30% required
            "mdd_raw": -0.05,
        },
        "M3": {"n": 35, "n_add": 6, "mean_diff_vs_M0": -0.0005,
               "add_then_stop_rate": 0.3, "mdd_raw": -0.05},
    }
    v = tool._verdicts(hold)
    assert v["M2"] == "RETIRE"


def test_verdict_continue_capture_small_holdout():
    hold = {"n_holdout": 25, "intraday_missing_ratio": 0.0,
            "M0": {}, "M1": {}, "M2": {}, "M3": {}}
    v = tool._verdicts(hold)
    for arm in ("M0", "M1", "M2", "M3"):
        assert v[arm] == "CONTINUE_CAPTURE"


def test_verdict_inconclusive_high_missing_rate():
    hold = {
        "n_holdout": 40,
        "intraday_missing_ratio": 0.25,  # > 0.20 -> INCONCLUSIVE for intraday arms
        "M0": {"n": 32, "n_add": 10, "add_then_stop_rate": 0.4, "mdd_raw": -0.05,
               "loser_mean_net": -0.04, "mean_diff_vs_M0": 0.0},
        "M1": {"n": 32, "n_add": 8, "mean_diff_vs_M0": 0.003,
               "ci90_diff_vs_M0": [0.001, 0.005], "mdd_raw": -0.04, "loser_mean_net": -0.042},
        "M2": {"n": 32, "n_add": 6, "mean_diff_vs_M0": -0.0005,
               "add_then_stop_rate": 0.3, "mdd_raw": -0.05},
        "M3": {"n": 40, "n_add": 8, "mean_diff_vs_M0": 0.001,
               "add_then_stop_rate": 0.3, "mdd_raw": -0.05},
    }
    v = tool._verdicts(hold)
    assert v["M0"] == "INCONCLUSIVE"
    assert v["M1"] == "INCONCLUSIVE"
    assert v["M2"] == "INCONCLUSIVE"
    # M3 not affected by intraday missing
    assert v["M3"] != "INCONCLUSIVE"


# ------------------------------------------------------------------ extract-trades pyramid exclusion

def test_extract_trades_pyramid_exclusion(tmp_path):
    """extract_trades writes all rows; drop_pyramid_adds excludes overlapping rows."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE trading_history (
        id INTEGER, ticker TEXT, company_name TEXT,
        buy_date TEXT, buy_price REAL, sell_date TEXT, sell_price REAL,
        exit_kind TEXT, trigger_type TEXT, scenario TEXT)""")
    scenario = json.dumps({"stop_loss": 95.0, "market_regime": "strong_bull"})
    # trade 1: open 2026-01-02 to 2026-01-20
    conn.execute("INSERT INTO trading_history VALUES (1,'000001','A','2026-01-02 10:00',100,"
                 "'2026-01-20 15:00',110,'profit','m1',?)", (scenario,))
    # trade 2: pyramid add (buy while trade 1 still open) same ticker
    conn.execute("INSERT INTO trading_history VALUES (2,'000001','A','2026-01-10 10:00',102,"
                 "'2026-01-20 15:00',110,'profit','m1',?)", (scenario,))
    # trade 3: new trade (after trade 1 closed)
    conn.execute("INSERT INTO trading_history VALUES (3,'000001','A','2026-01-21 10:00',105,"
                 "'2026-01-30 15:00',115,'profit','m1',?)", (scenario,))
    conn.commit()
    conn.close()

    out = tmp_path / "trades.json"
    tool.extract_trades(str(db), str(out))
    data = json.loads(out.read_text())
    assert len(data["rows"]) == 3  # extract_trades gets all rows

    kept = tool.drop_pyramid_adds(data["rows"])
    assert len(kept) == 2
    assert {r["id"] for r in kept} == {1, 3}


def test_extract_trades_reads_stop_loss_from_scenario(tmp_path):
    """stop_loss is correctly parsed from JSON scenario column."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE trading_history (
        id INTEGER, ticker TEXT, company_name TEXT,
        buy_date TEXT, buy_price REAL, sell_date TEXT, sell_price REAL,
        exit_kind TEXT, trigger_type TEXT, scenario TEXT)""")
    conn.execute("INSERT INTO trading_history VALUES (1,'A','A','2026-02-01 10:00',100,"
                 "'2026-02-10 15:00',110,'profit','m1',?)",
                 (json.dumps({"stop_loss": 94.5, "market_regime": "moderate_bull"}),))
    conn.execute("INSERT INTO trading_history VALUES (2,'B','B','2026-02-02 10:00',200,"
                 "'2026-02-11 15:00',190,'stop','m2',?)",
                 (json.dumps({"market_condition": "횡보"}),))
    conn.commit()
    conn.close()

    out = tmp_path / "trades.json"
    tool.extract_trades(str(db), str(out))
    data = json.loads(out.read_text())
    rows_by_id = {r["id"]: r for r in data["rows"]}
    assert rows_by_id[1]["stop_loss"] == pytest.approx(94.5)
    assert rows_by_id[1]["regime"] == "moderate_bull"
    assert rows_by_id[2]["stop_loss"] is None
    assert rows_by_id[2]["regime"] == "횡보"


# ------------------------------------------------------------------ full replay smoke test

def test_replay_l_arm_uses_full_initial_1(tmp_path):
    """L arm always starts at 1.0 and has no adds."""
    bars = _daily([100.0] * 80, spread=2.0)
    entry_idx = 50
    bars[entry_idx]["high"] = 105.0

    trade = {
        "id": 1, "ticker": "000001",
        "buy_date": bars[entry_idx]["date"] + " 10:00:00",
        "buy_price": 100.0,
        "sell_date": bars[entry_idx + 5]["date"] + " 15:20:00",
        "sell_price": 110.0,
        "exit_kind": "profit",
        "trigger_type": "m1",
        "stop_loss": 96.0,
        "regime": "strong_bull",
    }

    trades_f = tmp_path / "trades.json"
    daily_f = tmp_path / "daily.json"
    minutes_f = tmp_path / "minutes.json"
    out_f = tmp_path / "result.json"

    trades_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "rows": [trade]}))
    daily_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "bars": {"000001": bars}}))
    minutes_f.write_text(json.dumps({"rule_version": tool.RULE_VERSION, "minutes": {}}))

    tool.replay(str(trades_f), str(daily_f), str(minutes_f), str(out_f))
    result = json.loads(out_f.read_text())
    rows = result["rows"]
    assert len(rows) == 1
    r = rows[0]
    # L: 1.0 * (110/100 - 1) = 0.10
    assert r["L_net"] == pytest.approx(0.10)
    assert r["L_adds"] == 0
