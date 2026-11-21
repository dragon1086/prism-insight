"""Dr. K market-direction shadow model: book examples, rule mechanics, and zero production reach.

Book example bars are NASDAQ Composite daily bars from yfinance (high, low, close, volume).
Two book examples do not reproduce on this data and are pinned as recorded mismatches
(user decision 2026-11-22: keep the book's numbers, do not bend rules to the data).
"""
import ast
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cores import drk_market_direction as D  # noqa: E402
from cores.market_pulse import DailyBar  # noqa: E402

NASDAQ = [
    ("1998-10-01", 1693.84, 1606.61, 1612.33, 856960000),
    ("1998-10-02", 1623.42, 1569.06, 1614.98, 871450000),
    ("1998-10-05", 1614.98, 1511.15, 1536.69, 877180000),
    ("1998-10-06", 1577.87, 1500.60, 1510.89, 887180000),
    ("1998-10-07", 1523.03, 1449.06, 1462.61, 940230000),
    ("1998-10-08", 1462.60, 1343.87, 1419.12, 1209510000),
    ("1998-10-09", 1493.54, 1419.11, 1492.49, 892830000),
    ("2006-03-07", 2280.08, 2259.64, 2268.38, 1908310000),
    ("2006-03-08", 2275.13, 2248.65, 2267.46, 2084040000),
    ("2009-08-27", 2029.27, 1993.03, 2027.73, 2140540000),
    ("2009-08-28", 2059.48, 2017.42, 2028.77, 2340180000),
    ("2009-03-02", 1372.00, 1322.13, 1322.85, 2033110000),
    ("2009-03-03", 1346.88, 1312.98, 1321.01, 2338880000),
    ("2009-03-04", 1370.29, 1333.88, 1353.74, 2305180000),
    ("2009-03-05", 1342.86, 1298.33, 1299.59, 2325840000),
    ("2009-03-06", 1320.51, 1268.54, 1293.85, 2443820000),
    ("2009-03-09", 1316.15, 1265.52, 1268.64, 2037130000),
    ("2009-03-10", 1358.28, 1288.95, 1358.28, 2359730000),
    ("2009-03-11", 1385.29, 1352.60, 1371.64, 2169050000),
    ("2009-03-12", 1427.55, 1355.05, 1426.10, 2392770000),
]


def _bars(start, end):
    return [DailyBar(d, c, v, high=h, low=lo) for d, h, lo, c, v in NASDAQ if start <= d <= end]


def _kind(day):
    bars = {b.date: b for b in _bars("1900", "2100")}
    days = [d for d, *_ in NASDAQ]
    prev = bars[days[days.index(day) - 1]]
    return D.DrkDirection()._classify(bars[day], prev)[0]


def test_1998_10_08_down_on_volume_closing_upper_half_starts_rally_not_distribution():
    assert _kind("1998-10-08") is None
    rec = {r["date"]: r for r in D.replay(_bars("1998-10-01", "1998-10-09"), D.DrkConfig(start_state=D.CASH))}
    assert "rally_day1" in rec["1998-10-08"]["events"]
    assert rec["1998-10-09"]["rally_day"] == 2


def test_2006_03_08_small_drop_closing_upper_half_is_not_distribution():
    assert _kind("2006-03-08") is None


def test_2009_08_28_recorded_mismatch_close_position_027():
    # Book: distribution (long upper wick, close in the bottom 25%). yfinance: close position 0.27.
    bar, prev = _bars("2009-08-28", "2009-08-28")[0], _bars("2009-08-27", "2009-08-27")[0]
    assert round((bar.close - bar.low) / (bar.high - bar.low), 2) == 0.27
    assert bar.volume > prev.volume and 0 < bar.close / prev.close - 1 < 0.001
    assert _kind("2009-08-28") is None


def test_2009_03_12_buy_signal_needs_low_day_counted_as_day1():
    # Book: buy signal 2009-03-12. Counting from the first up close (03-10) it is rally day 3.
    bars = _bars("2009-03-02", "2009-03-12")
    default = {r["date"]: r for r in D.replay(bars, D.DrkConfig(start_state=D.CASH))}
    assert default["2009-03-12"]["rally_day"] == 3
    assert default["2009-03-12"]["state"] == D.CASH                    # recorded mismatch
    opt = {r["date"]: r for r in D.replay(bars, D.DrkConfig(start_state=D.CASH, low_day_is_day1=True))}
    assert "follow_through_buy" in opt["2009-03-12"]["events"]
    assert opt["2009-03-12"]["state"] == D.INVESTED


# --- synthetic mechanics -----------------------------------------------------

def _seq(rows, start="2026-01-01"):
    """rows: (close, volume, high, low); dates are consecutive calendar days (labels only)."""
    import datetime as dt
    base = dt.date.fromisoformat(start)
    return [DailyBar((base + dt.timedelta(days=i)).isoformat(), c, v, high=h, low=lo)
            for i, (c, v, h, lo) in enumerate(rows)]


def _flat(n=5, c=100.0):
    return [(c, 100.0, c + 0.5, c - 0.5)] * n


def _dd(close, vol):
    return (close, vol, close + 1.0, close - 0.1)       # closes near the low


def test_churn_and_hold_rules():
    m = D.DrkDirection()
    prev = DailyBar("a", 100.0, 100.0, high=100.5, low=99.5)
    churn = DailyBar("b", 100.15, 130.0, high=100.8, low=99.9)  # +0.15%, volume +30%, close low half
    assert m._classify(churn, prev)[0] == "churn"
    assert D.DrkDirection(D.DrkConfig(churn=False))._classify(churn, prev)[0] is None

    rows = _flat() + [_dd(99.0, 120.0), (98.5, 110.0, 99.2, 98.4), _dd(98.0, 130.0), (98.6, 100, 98.7, 98.0)]
    bars = _seq(rows)
    held = D.DrkConfig(event_dates=frozenset({bars[5].date, bars[7].date}))
    rec = D.replay(bars, held)
    assert "dd_held:down" in rec[5]["events"] and "dd_hold_confirmed" in rec[6]["events"]
    assert "dd_hold_dropped" in rec[8]["events"] and rec[8]["dd_count"] == 1


def _five_dd():
    rows, c, v = _flat(), 100.0, 100.0
    for _ in range(5):
        c, v = c * 0.99, v + 10
        rows.append(_dd(c, v))
    return rows, c


def test_ready_day_then_low_break_is_sell():
    rows, c = _five_dd()
    rows.append((c * 0.995, 50.0, c + 0.2, c - 0.5))      # low under the ready-day low
    rec = D.replay(_seq(rows))
    assert "sell_ready" in rec[9]["events"]
    assert "sell_signal" in rec[10]["events"] and rec[10]["state"] == D.CASH


def test_ready_day_voided_by_high():
    rows, c = _five_dd()
    rows.append((c + 1.5, 50.0, c + 2.0, c))              # high over the ready-day high, low holds
    rec = D.replay(_seq(rows))
    assert "sell_ready_void_high" in rec[10]["events"] and rec[10]["state"] == D.INVESTED


def test_same_bar_break_and_void_decided_by_close():
    rows, c = _five_dd()
    rows.append((c - 0.3, 50.0, c + 2.0, c - 0.5))        # both; close under ready low
    assert "sell_signal_same_bar_close" in D.replay(_seq(rows))[10]["events"]


def _rally_to_buy():
    """CASH: low, up day 1, days 2-3 flat, day 4 +2% on volume -> buy."""
    rows = [(100.0, 100, 101, 99), (95.0, 100, 96, 94.5), (96.0, 100, 96.5, 95.0),
            (96.2, 90, 96.5, 95.5), (96.3, 90, 96.6, 95.6), (98.3, 150, 98.4, 96.4)]
    return rows


def test_follow_through_and_thresholds():
    rec = D.replay(_seq(_rally_to_buy()), D.DrkConfig(start_state=D.CASH))
    assert "follow_through_buy" in rec[5]["events"] and rec[5]["state"] == D.INVESTED
    assert rec[4]["ftd_threshold"] == 1.5
    capped = D.replay(_seq(_rally_to_buy()), D.DrkConfig(start_state=D.CASH, ftd_min_day=5))
    assert capped[5]["state"] == D.CASH
    for name in D.FTD_THRESHOLDS:
        assert name in {"fixed_1.25", "fixed_1.5", "lin_sd60", "lin_mad60", "prop_sd60"}
    assert len(D.FTD_THRESHOLDS) == 5
    sigma_short = D.replay(_seq(_rally_to_buy()), D.DrkConfig(start_state=D.CASH, ftd_threshold="lin_sd60"))
    assert sigma_short[5]["state"] == D.CASH                # under 60 returns => threshold unknown, no FTD
    # Once 60 returns exist the threshold is set, so a long rally is not locked out forever.
    warm = [(100.0 + (i % 2) * 0.5, 100, 101, 99.5) for i in range(70)]
    rows = [(95.0, 100, 96, 94.5)] + warm + [(102.0, 300, 102.1, 100.0)]
    late = D.replay(_seq(rows), D.DrkConfig(start_state=D.CASH, ftd_threshold="lin_sd60"))
    assert late[-1]["state"] == D.INVESTED


def test_buy_invalid_next_day_low():
    rows = _rally_to_buy() + [(97.0, 100, 98.0, 96.0)]       # low 96.0 < signal low 96.4
    rec = D.replay(_seq(rows), D.DrkConfig(start_state=D.CASH))
    assert "buy_invalid_next_day_low" in rec[6]["events"] and rec[6]["state"] == D.CASH


def test_first_distribution_day_after_buy_excluded_without_reset():
    rows = _rally_to_buy() + [(98.5, 100, 98.9, 96.6), _dd(98.0, 160.0), _dd(97.5, 170.0)]
    rec = D.replay(_seq(rows), D.DrkConfig(start_state=D.CASH))
    assert "dd_first_after_buy_excluded" in rec[7]["events"]
    assert "dd_counted" in rec[8]["events"]
    # No reset at the buy: the count keeps any pre-signal distribution days inside the window.
    assert rec[8]["dd_count"] == rec[5]["dd_count"] + 1


def test_event_dates_calendar():
    us = ["2024-03-14", "2024-03-15", "2024-05-30", "2024-05-31", "2024-06-21", "2024-06-27",
          "2024-06-28", "2026-11-20"]
    assert D.event_dates(us, "us") == {"2024-03-15", "2024-05-31", "2024-06-21", "2024-06-28"}
    kr = ["2024-02-29", "2024-06-13", "2024-06-14", "2024-09-12", "2026-11-20"]
    assert D.event_dates(kr, "kr") == {"2024-02-29", "2024-06-13", "2024-09-12"}  # Nov 2026 unfinished


def test_no_production_module_imports_the_shadow_model():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "tools/drk_", ".")) or "/tests/" in rel or rel == "cores/drk_market_direction.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, (ast.Import, ast.ImportFrom)) else []
            mod = node.module if isinstance(node, ast.ImportFrom) else ""
            if "drk_market_direction" in (mod or "") or any("drk_market_direction" in n for n in names):
                offenders.append(rel)
    assert offenders == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
