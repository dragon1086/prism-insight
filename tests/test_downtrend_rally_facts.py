"""downtrend_rally_v1 (H-1114-nobuy-rally-in-downtrend, 2026-11-15): the rally-fail 'do not buy' flags on the
decision snapshot. Record only: same functions as the inverse lane, no gate, MISSING when history is short."""
from datetime import date, timedelta

from prism_core import decision_snapshot as DS
from prism_inverse.domain import signals as S

# 30 flat sessions, a crash 3 ATR under the 20-day high, then a rally back to the falling 20-day line that fails.
CLOSES = [100.0] * 30 + [95.0, 90.0, 88.0, 89.0, 90.0, 91.0, 92.0, 93.0, 94.0, 94.5, 93.5, 94.0]


def _bars(closes, start=date(2026, 9, 1)):
    return [{"date": (start + timedelta(days=k)).isoformat(), "open": c, "high": c + 0.5, "low": c - 0.5,
             "close": c, "volume": 1000.0} for k, c in enumerate(closes)]


def _direct(bars):
    """The flags straight from the inverse lane's functions at the last bar."""
    a = S.ind([b["open"] for b in bars], [b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars])
    i = len(bars) - 1
    return S.crash_switch(a)[i], a["ma20"][i] < a["ma20"][i - 5], S.kq_rally_fail(a)


def test_flags_match_the_inverse_lane_functions_on_the_judgement_bar():
    bars = _bars(CLOSES)
    switch, falling, fires = _direct(bars)
    assert switch and falling and fires, "fixture must reach the rally-fail setup"
    out = DS.downtrend_rally(bars, bench=_bars([100.0] * 40))
    assert out["status"] == "OK" and out["contract"] == "downtrend_rally_v1"
    assert out["crash_switch"] is True and out["ma20_falling_5d"] is True and out["rally_in_downtrend"] is True
    assert out["rally_near_ma20"] is True                      # 94 is within 1 ATR of (or above) the falling line
    assert out["rally_fail_fired_recent"] is (len(bars) - 1 - fires[-1] < DS.RALLY_FAIL_RECENT_BARS)
    assert out["last_rally_fail_date"] == bars[fires[-1]]["date"]
    assert out["index_crash_switch"] is False and out["index_rally_in_downtrend"] is False
    # the flags flip off once the stock closes back above its 20-day line (the switch latch releases)
    calm = DS.downtrend_rally(_bars(CLOSES + [101.0]), bench=None)
    assert calm["crash_switch"] is False and calm["rally_in_downtrend"] is False
    assert calm["status"] == "PARTIAL" and calm["index_crash_switch"] is None     # no index: unknown, not False


def test_short_history_is_missing_not_false():
    out = DS.downtrend_rally(_bars(CLOSES[-20:]))
    assert out["status"] == "MISSING" and "crash_switch" not in out


def test_snapshot_carries_the_block_without_changing_its_own_status():
    bars = _bars(CLOSES)
    snap = DS.snapshot(bars, price=94.0, bench=_bars([100.0] * 60))
    assert snap["downtrend_rally"]["rally_in_downtrend"] is True
    assert snap["status"] == "PARTIAL" and "downtrend_rally" not in snap["missing"]   # only ma200/high_252d missing
