"""KR inverse signal recorder: state machine, signals, virtual trades, storage, research parity."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from prism_inverse.adapters.fakes import FakeEvents, FakeMarketData, MemoryStore
from prism_inverse.adapters.registry import load_kr_sectors
from prism_inverse.adapters.sqlite_store import SCHEMA_VERSION, SqliteStore
from prism_inverse.app import signals_cycle as C
from prism_inverse.domain import signals as S
from prism_inverse.domain.models import Bar
from prism_inverse.domain.virtual import CLOSED, OPEN, PENDING_ENTRY, PENDING_EXIT, VirtualTrade, advance
from prism_inverse.tests.helpers import bars

# ---------------------------------------------------------------- state machine


def _sb_case():
    c = [110, 95, 105, 95, 105, 95, 125, 95, 105, 95]
    ma, pk = [100.0] * len(c), [120.0] * len(c)
    return c, ma, pk, (lambda i: c[i] < ma[i] and c[i] <= 0.9 * pk[i])


def test_second_breaks_without_and_with_rearm():
    c, ma, pk, ok = _sb_case()
    assert S.second_breaks(c, ma, pk, ok) == [3, 9]          # 5 needs a re-arm; 6 resets at the old peak
    assert S.second_breaks(c, ma, pk, ok, rearm=True) == [3, 5, 9]
    weak = [True] * len(c)
    weak[3] = False                                          # weak filter blocks day 3, re-break at 5 still counts
    assert S.second_breaks(c, ma, pk, ok, weak) == [5, 9]


def test_second_breaks_window_and_episode_limits():
    n = 130
    c = [95.0] + [105.0] * 101 + [95.0] * (n - 102)          # re-break on day 102: past the 100-day window
    assert S.second_breaks(c, [100.0] * n, [120.0] * n, lambda i: c[i] < 100) == []
    c2 = [95.0] + [105.0] * 121 + [95.0, 105.0, 95.0]        # day 121 ends the episode; 122 is a new first break
    assert S.second_breaks(c2, [100.0] * len(c2), [120.0] * len(c2), lambda i: c2[i] < 100) == [124]


def test_kq_f1_break_threshold_is_three_atr_under_peak():
    def run(first):
        a = {"c": [first, 105.0, 95.0], "ma50": [110.0, 100.0, 100.0], "peak": [130.0] * 3, "atr": [10.0] * 3}
        return S.kq_f1(a)
    assert run(100.0) == [2]      # 100 <= 130 - 30: first break, reclaim, re-break
    assert run(100.5) == []       # 0.5 short of 3 ATR: no first break, no signal
    assert S.kq_f1({"c": [100.0, 105.0, 95.0], "ma50": [110.0, 100.0, 100.0], "peak": [130.0] * 3,
                    "atr": [None] * 3}) == []  # unknown ATR never counts as a break


def test_kq_x10_fires_only_on_the_first_close_below_the_10_day_line():
    a = {"c": [10.0, 9.0, 8.0, 11.0, 9.0, None, 8.0], "ma10": [9.5, 9.5, 9.5, 9.5, 9.5, 9.5, 9.5]}
    assert S.kq_x10(a) == [1, 4]  # 2 stays below; 6 follows an unknown close, so it is not a confirmed first break
    assert S.kq_x10({"c": [10.0, 9.0], "ma10": [None, 9.5]}) == []  # unknown line the day before


def test_virtual_trade_times_out_after_10_sessions():
    cal = _days(15)
    und = {d: _bar(d, 100, 99) for d in cal[1:]}                  # a little in favour: no stop, target, trail or no-progress exit
    etf = {d: _bar(d, 5000, 5000) for d in cal[1:]}
    t = advance(VirtualTrade("KQ_X10-x", "KQ_X10", "251340", "229200", D0, atr=1.0), cal, etf, und)
    assert (t.exit_reason, t.exit_signal_date, t.exit_date) == ("TIME", cal[10], cal[11])


# ---------------------------------------------------------------- S17/S18 crash switch, failed rally, switched re-break


def test_latch_and_crash_switch():
    assert S.latch([False, True, False, False, True], [False, False, False, True, False]) == [False, True, True, False, True]
    a = {"c": [100.0, 96.0, 97.0, 99.0, 101.0], "hi20": [100.0] * 5, "atr": [1.0] * 5, "ma20": [100.0] * 5}
    assert S.crash_switch(a) == [False, True, True, True, False]  # on at 4 ATR under the high, off on the close above MA20
    a["hi20"][1] = None
    assert S.crash_switch(a) == [False, False, True, True, False]  # unknown high never switches on (day 2 does, at 3 ATR)


def test_kq_fr_fires_on_the_first_close_under_ma5_after_a_two_atr_rally():
    c = [100.0, 94.0, 93.0, 95.5, 96.0, 94.0, 93.0, 95.0, 96.0, 94.0]
    a = {"c": c, "hi20": [100.0] * 10, "atr": [1.0] * 10, "ma20": [100.0] * 10,
         "ma5": [99.0, 99.0, 99.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0]}
    # low 93 at 2; 95.5 >= 93 + 2 rallies; 94 < ma5 after 96 >= ma5 at 4 -> fire 5; low resets to 94 and 96 at 8 rallies -> fire 9
    assert S.kq_fr(a) == [5, 9]
    a["ma20"] = [90.0] * 10                                        # switch off from day 1: nothing can fire
    assert S.kq_fr(a) == []


def test_switched_second_breaks_keeps_only_breaks_while_the_switch_is_on():
    c = [100.0, 95.0, 105.0, 95.0, 105.0, 95.0]
    a = {"c": c, "ma50": [100.0] * 6, "peak": [130.0] * 6, "atr": [10.0] * 6}
    assert S.switched_second_breaks(a, [True] * 6) == [3, 5]
    assert S.switched_second_breaks(a, [True, True, True, False, True, True]) == [5]


def test_virtual_no_progress_trail_and_breakeven_exits():
    cal = _days(12)
    etf = {d: _bar(d, 5000, 5000) for d in cal}
    und = {d: _bar(d, 100, 100) for d in cal}                     # flat: still at entry on session 3 -> no progress
    t = advance(_trade(), cal, etf, und)
    assert (t.exit_reason, t.exit_signal_date) == ("NOPROG", cal[3])
    closes = [100, 99, 98.5, 98, 97.5, 100.2, 100]               # low 97.5 (2.5 ATR in favour) moved the stop to entry
    und = {d: _bar(d, 100, c) for d, c in zip(cal[1:], closes)}
    t = advance(_trade(), cal, etf, und)
    assert (t.exit_reason, t.exit_signal_date) == ("STOP", cal[6])
    closes[5:] = [99.9, 99.8]                                     # under the moved stop: still open
    und = {d: _bar(d, 100, c) for d, c in zip(cal[1:], closes)}
    assert advance(_trade(), cal, etf, und).status == OPEN
    from prism_inverse.domain.virtual import Rules
    und = {d: _bar(d, 100, c) for d, c in zip(cal[1:], [100, 99, 98, 99.1])}   # low 98 + 1 ATR trail = 99
    t = advance(_trade(), cal, etf, und, Rules(stop_atr=5, target_atr=9, max_held=20, trail_atr=1))
    assert (t.exit_reason, t.exit_signal_date) == ("TRAIL", cal[4])


def test_synthetic_two_x_return_is_daily_reset_on_the_underlying():
    from prism_inverse.domain.virtual import US_RULES
    cal = _days(5)
    und = {cal[1]: _bar(cal[1], 100, 98), cal[2]: _bar(cal[2], 98, 95), cal[3]: _bar(cal[3], 95, 96), cal[4]: _bar(cal[4], 95.5, 96)}
    t = advance(VirtualTrade("US_2BR_XLK-x", "US_2BR_XLK", "XLK", "XLK", D0, atr=1.0), cal, und, und, US_RULES)
    assert (t.status, t.exit_reason, t.exit_signal_date, t.exit_date, t.exit_open) == (CLOSED, "TARGET", cal[2], cal[3], 95)
    assert t.ret == pytest.approx((1 - 2 * (98 / 100 - 1)) * (1 - 2 * (95 / 98 - 1)) * (1 - 2 * (95 / 95 - 1)) - 1 - 0.0015)
    assert (t.stop, t.target) == (102, 97)


# ---------------------------------------------------------------- sector panel


def _walk(rng, n, drift, start=100.0):
    out, x = [], start
    for _ in range(n):
        x *= 1 + drift + rng.uniform(-0.01, 0.01)
        out.append(x)
    return out


def test_leader_needs_top_third_and_15pct():
    rng = random.Random(1)
    n = 200
    closes = {"A": _walk(rng, n, 0.004), "B": _walk(rng, n, 0.0045), "C": _walk(rng, n, 0.0005)}
    panel = S.sector_panel(closes, [100.0] * n)
    assert not any(panel["leader"]["A"][:126])                 # no 126-day return yet
    assert panel["leader"]["A"][-1] and panel["leader"]["B"][-1] and not panel["leader"]["C"][-1]
    closes["B"] = _walk(rng, n, -0.001)                        # can rank 2/3 but never +15% -> not a leader
    closes["C"] = _walk(rng, n, -0.003)
    panel = S.sector_panel(closes, [100.0] * n)
    assert panel["leader"]["A"][-1] and not panel["leader"]["B"][-1]


def test_weak_is_relative_strength_below_its_50_day_mean():
    c = [100.0] * 60 + [90.0]
    panel = S.sector_panel({"X": c}, [100.0] * 61)
    assert panel["weak"]["X"][60] and not panel["weak"]["X"][59]  # equal to the mean is not weak


def test_driver_is_most_correlated_leader_only():
    rng = random.Random(7)
    n = 200
    closes = {"A": _walk(rng, n, 0.004), "B": _walk(rng, n, 0.0045), "C": _walk(rng, n, 0.0)}
    panel = S.sector_panel(closes, [100.0] * n)
    t = n - 1
    assert S.driver_at(closes, panel["leader"], closes["B"], t)[0] == "B"
    g, r = S.driver_at(closes, panel["leader"], closes["A"], t)
    assert g == "A" and r == pytest.approx(1.0)
    assert S.driver_at(closes, panel["leader"], closes["C"], t)[0] in ("A", "B")  # C is not a leader
    assert S.driver_at(closes, panel["leader"], closes["A"], 100) == (None, None)  # no leader yet


# ---------------------------------------------------------------- virtual trades

D0 = date(2026, 1, 5)


def _days(n):
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bar(day, o, c):
    return Bar(day, o, max(o, c), min(o, c), c, 1.0)


def _trade():
    return VirtualTrade("KQ_F1-x", "KQ_F1", "251340", "229200", D0, atr=1.0)


def test_virtual_stop_with_pending_next_open_fills():
    cal = _days(4)
    und = {cal[1]: _bar(cal[1], 100, 101), cal[2]: _bar(cal[2], 101, 102), cal[3]: _bar(cal[3], 102, 102)}
    etf = {cal[1]: _bar(cal[1], 5000, 4990), cal[2]: _bar(cal[2], 4990, 4900), cal[3]: _bar(cal[3], 4890, 4900)}
    t = advance(_trade(), cal[:1], etf, und)
    assert t.status == PENDING_ENTRY and t.error is None       # next open not known yet
    t = advance(t, cal[:2], etf, und)
    assert (t.status, t.entry_date, t.entry_open, t.entry_underlying) == (OPEN, cal[1], 5000, 100)
    assert (t.stop, t.target) == (101.5, 97)
    t = advance(t, cal[:3], etf, und)
    assert (t.status, t.exit_signal_date, t.exit_reason) == (PENDING_EXIT, cal[2], "STOP")
    t = advance(t, cal, etf, und)
    assert (t.status, t.exit_date, t.exit_open) == (CLOSED, cal[3], 4890)
    assert t.ret == pytest.approx(4890 / 5000 - 1 - 0.001)


def test_virtual_target_and_time_exits():
    cal = _days(25)
    und = {d: _bar(d, 100, 100) for d in cal[1:]}
    etf = {d: _bar(d, 5000, 5000) for d in cal[1:]}
    und[cal[3]] = _bar(cal[3], 100, 97)
    t = advance(_trade(), cal, etf, und)
    assert (t.status, t.exit_signal_date, t.exit_reason, t.exit_date) == (CLOSED, cal[3], "TARGET", cal[4])
    und = {d: _bar(d, 100, 99) for d in cal[1:]}
    t = advance(_trade(), cal, etf, und)
    assert (t.exit_reason, t.exit_signal_date, t.exit_date) == ("TIME", cal[10], cal[11])  # 10th held session


def test_missing_bar_keeps_trade_pending_and_never_skips_a_day():
    cal = _days(3)
    und = {d: _bar(d, 100, 100) for d in cal}
    etf = {cal[2]: _bar(cal[2], 5100, 5100)}                  # entry-day ETF bar missing
    t = advance(_trade(), cal, etf, und)
    assert t.status == PENDING_ENTRY and t.error == f"MISSING_ENTRY_BAR {cal[1].isoformat()}"
    etf[cal[1]] = _bar(cal[1], 5000, 5000)                    # arrives on a later run
    t = advance(t, cal, etf, und)
    assert t.status == OPEN and t.entry_date == cal[1] and t.entry_open == 5000 and t.error is None
    del und[cal[2]]
    t = advance(t, cal, etf, und)
    assert t.status == OPEN and t.error == f"MISSING_UNDERLYING_BAR {cal[2].isoformat()}"


# ---------------------------------------------------------------- recorder end to end

SECTORS = ["S1", "S2", "S3"]


def _kq_closes():
    up = [100 + 100 * i / 149 for i in range(150)]   # peak 200, slide, reclaim the 50-day line below the peak, re-break
    return up + [200 - 5 * (i + 1) for i in range(10)] + [150 + 2.6 * (i + 1) for i in range(15)] + \
        [189 - 4 * (i + 1) for i in range(20)]


def _market(n_extra=0):
    kq = _kq_closes()
    fire = next(i for i in S.kq_f1(S.ind(*_ohlc(bars(kq)))))
    n = fire + 1 + n_extra
    rng = random.Random(3)
    series = {"229200": bars(kq[:n]), "069500": bars([100.0] * n), "251340": bars([5000.0 + i for i in range(n)]),
              "114800": bars([4000.0] * n), "091160": bars([100.0] * n), "252670": bars([3000.0] * n)}
    for g in SECTORS:
        series[g] = bars(_walk(rng, n, -0.002))  # falling sectors: no leader, so no driver
    return series, series["229200"][fire].day


def _ohlc(bs):
    return [b.open for b in bs], [b.high for b in bs], [b.low for b in bs], [b.close for b in bs]


def test_recorder_fires_queues_and_fills_next_open(tmp_path):
    series, fire_day = _market()
    store, events = SqliteStore(tmp_path / "lane.sqlite"), FakeEvents()
    r = C.run_signals(fire_day, SECTORS, "069500", FakeMarketData(series), store, events, mode="observe")
    status, inputs, error = r.days["KQ_F1"]
    assert status == C.FIRED and error is None and inputs["virtual"] == f"QUEUED:KQ_F1-{fire_day}"
    assert {f for f, (s, _, _) in r.days.items() if s == C.NOT_FIRED} == {"KQ_DRIVER", "K200_DRIVER", "KQ_X10", "KQ_FR", "K200_SEMI"}
    [t] = store.active_virtual_trades()
    assert t.status == PENDING_ENTRY and t.atr == inputs["atr"]
    again = C.run_signals(fire_day, SECTORS, "069500", FakeMarketData(series), store, events, mode="observe")
    assert again.days["KQ_F1"][1]["virtual"] == "ALREADY_RECORDED"

    series, _ = _market(n_extra=1)
    nxt = series["251340"][-1].day
    C.run_signals(nxt, SECTORS, "069500", FakeMarketData(series), store, events, mode="observe")
    [t] = [x for x in store.active_virtual_trades() if x.family == "KQ_F1"]  # KQ_X10 keeps its own trade
    assert (t.status, t.entry_date, t.entry_open) == (OPEN, nxt, series["251340"][-1].open)
    assert t.entry_underlying == series["229200"][-1].open and t.stop == pytest.approx(t.entry_underlying + 1.5 * t.atr)
    kinds = [e for e, _ in events.events]
    assert kinds.count("inverse.signal_evaluated") == 18 and kinds.count("inverse.virtual_entry") == 1
    tids = {f["trace_id"] for e, f in events.events if f["attributes"].get("family") == "KQ_F1" or e != "inverse.signal_evaluated"}
    assert len(tids) == 2  # signal day and the trade share one trace; the next day's evaluation has its own
    rows = store.db.execute("SELECT family, status, inputs_json FROM inv_signal_days").fetchall()
    assert len(rows) == 18 and json.loads(rows[0][2])["underlying"] == "229200"


def _fr_closes():
    """Crash to 150 (switch on), a 12-point rally that fails under the 5-day line, then the _us_closes tail."""
    up = [100 + 100 * i / 149 for i in range(150)]
    return up + [200 - 5 * (i + 1) for i in range(10)] + [150, 152, 154, 156, 158, 160, 162, 161, 157, 155, 158, 161, 163, 160, 156] \
        + [150 + 2 * (i + 1) for i in range(20)] + [190 - 6 * (i + 1) for i in range(25)]


def _us_closes():
    """Peak 200, crash to 150 (switch on, first break), rally over the 50-day line (switch off), fast slide (switch on, re-break)."""
    up = [100 + 100 * i / 149 for i in range(150)]
    return up + [200 - 5 * (i + 1) for i in range(10)] + [150 + 2 * (i + 1) for i in range(20)] + [190 - 6 * (i + 1) for i in range(25)]


def test_us_recorder_fires_per_sector_and_judges_synthetic_two_x(tmp_path):
    kq = _us_closes()
    a = S.ind(*_ohlc(bars(kq, spread=0.001)))
    fire = next(i for i in S.switched_second_breaks(a, S.crash_switch(a)))
    n = fire + 1
    series = {"XLK": bars(kq[:n], spread=0.001), "XLE": bars([100.0] * n)}
    day = series["XLK"][-1].day
    store, events = SqliteStore(tmp_path / "lane.sqlite"), FakeEvents()
    r = C.run_us_signals(day, {"XLK": "AMS", "XLE": "AMS"}, FakeMarketData(series), store, events, mode="observe")
    assert r.days["US_2BR_XLK"][0] == C.FIRED and r.days["US_2BR_XLK"][1]["switch"] is True
    assert r.days["US_2BR_XLE"][0] == C.NOT_FIRED and r.days["US_2BR_XLE"][1]["switch"] is False
    [t] = store.active_virtual_trades()
    assert (t.family, t.etf, t.underlying, t.status) == ("US_2BR_XLK", "XLK", "XLK", PENDING_ENTRY)
    assert {f["market"] for _, f in events.events} == {"US"}
    # the KR run leaves the US book alone (no bars for it, so no MISSING error is written)
    kr = C.run_signals(day, SECTORS, "069500", FakeMarketData(_market()[0]), store, FakeEvents(), mode="observe")
    assert kr.trades == [] and store.active_virtual_trades()[0].error is None
    # one more session: fill at the open, judged as -2x on XLK itself
    nxt = bars(kq[:n + 1], spread=0.001)[-1]
    series["XLK"].append(nxt); series["XLE"].append(_bar(nxt.day, 100, 100))
    C.run_us_signals(nxt.day, {"XLK": "AMS", "XLE": "AMS"}, FakeMarketData(series), store, FakeEvents(), mode="observe")
    [t] = store.active_virtual_trades()
    assert (t.status, t.entry_open, t.entry_underlying, t.stop) == (OPEN, nxt.open, nxt.open, pytest.approx(nxt.open + 2 * t.atr))


def test_unavailable_or_stale_input_is_missing_not_not_fired():
    series, day = _market()
    r = C.run_signals(day, SECTORS, "069500", FakeMarketData(series, fail=frozenset({"S2"})), MemoryStore(),
                      FakeEvents())
    assert r.days["KQ_DRIVER"][0] == r.days["K200_DRIVER"][0] == C.MISSING and "S2: Unavailable" in r.days["KQ_DRIVER"][2]
    assert r.days["KQ_F1"][0] == C.FIRED
    series["229200"] = series["229200"][:-1]                  # no bar on the run date
    r = C.run_signals(day, SECTORS, "069500", FakeMarketData(series), MemoryStore(), FakeEvents())
    assert r.days["KQ_F1"][0] == r.days["KQ_DRIVER"][0] == C.MISSING and "NO_BAR_ON" in r.days["KQ_F1"][2]
    assert r.days["K200_DRIVER"][0] == C.NOT_FIRED


def test_schema_v1_db_upgrades_in_place(tmp_path):
    path = tmp_path / "lane.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE schema_version (version INTEGER NOT NULL); INSERT INTO schema_version VALUES (1);"
                         "CREATE TABLE inv_runs (run_id TEXT PRIMARY KEY, market TEXT, trade_date TEXT, mode TEXT,"
                         " policy_version TEXT, config_hash TEXT, started_at TEXT, finished_at TEXT, status TEXT,"
                         " error TEXT); INSERT INTO inv_runs (run_id) VALUES ('old');")
    store = SqliteStore(path)
    assert SCHEMA_VERSION == 3 and store.db.execute("SELECT version FROM schema_version").fetchall() == [(3,)]
    assert store.db.execute("SELECT run_id FROM inv_runs").fetchall() == [("old",)]
    store.save_virtual_trade(_trade())
    assert store.active_virtual_trades() == [_trade()]
    closed = VirtualTrade("KQ_F1-x", "KQ_F1", "251340", "229200", D0, 1.0, status=CLOSED, ret=0.02)
    store.save_virtual_trade(closed)
    assert not store.add_virtual_trade(_trade())               # re-running an old date never reopens a closed trade
    assert store.db.execute("SELECT status, ret FROM inv_virtual_trades").fetchall() == [("CLOSED", 0.02)]


def test_runner_off_by_default_and_never_live(monkeypatch, capsys):
    from tools import run_inverse_signals as R

    monkeypatch.delenv("INVERSE_SIGNALS_MODE", raising=False)
    assert R.main(["--market", "kr"]) == 0 and "off" in capsys.readouterr().out
    assert R.main(["--market", "us"]) == 0 and "off" in capsys.readouterr().out
    monkeypatch.setenv("INVERSE_SIGNALS_MODE", "live")
    assert R.main(["--market", "kr"]) == 2 and R.main(["--market", "us"]) == 2


def test_us_sector_registry_matches_research_list():
    from prism_inverse.adapters.registry import load_us_sectors

    sectors = load_us_sectors()
    assert len(sectors) == 28 and set(sectors.values()) == {"AMS", "NAS"} and sectors["SMH"] == "NAS"


def test_kis_kr_data_spaces_calls():
    import time

    from prism_inverse.adapters.kis_data import KisKrData

    stamps = []

    class Src:
        def _fetch(self, *a):
            stamps.append(time.monotonic())

    src = Src()
    KisKrData(src, min_interval=0.05)
    for _ in range(3):
        src._fetch("u", "tr", {})
    assert len(stamps) == 3 and min(b - a for a, b in zip(stamps, stamps[1:])) >= 0.045


def test_sector_registry_matches_research_list():
    sectors, bench = load_kr_sectors()
    assert bench == "069500" and len(sectors) == len(set(sectors)) == 24 and bench not in sectors


# ---------------------------------------------------------------- parity with the research engine
# Research logic re-implemented inline (s4_track.ind/second_breaks, s6_driver.group_frame + driver).


def _research_second_breaks(c, ma, pk, ok_break, weak=None, window=100, rearm=False):
    out, st, b1, pkb = [], None, 0, 0.0
    for i in range(len(c) - 1):
        if not (np.isfinite(c[i]) and np.isfinite(ma[i])):
            continue
        if st is None:
            if ok_break(i):
                st, b1, pkb = "B", i, pk[i]
        elif c[i] >= pkb or i - b1 > 120:
            st = None
        elif (st == "B" or (rearm and st == "D")) and c[i] > ma[i]:
            st = "R"
        elif st == "R" and c[i] < ma[i] and (weak is None or weak[i]) and i - b1 <= window:
            st = "D"; out.append(i)
    return out


def _nan(xs):
    return np.array([np.nan if x is None else x for x in xs], float)


def _choppy(rng, n, start=100.0, vol=0.015):
    out, x, drift = [], start, 0.0
    for i in range(n):
        if i % 60 == 0:
            drift = rng.choice((0.006, -0.006, 0.002, -0.003))
        x *= 1 + drift + rng.gauss(0, vol)
        out.append(x)
    return out


def test_parity_with_research_on_synthetic_panel():
    rng = random.Random(1109)
    n = 520
    groups = [f"G{k}" for k in range(6)]
    raw = {g: _choppy(rng, n) for g in groups}
    raw["G5"][:80] = [None] * 80                                  # late listing
    bench_c, kq_c = _choppy(rng, n), _choppy(rng, n)

    # index indicators + F1
    kq = bars(kq_c)
    o, h, l, c = _ohlc(kq)
    mine = S.ind(o, h, l, c)
    x = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c})
    pc = x.Close.shift()
    tr = pd.concat([x.High - x.Low, (x.High - pc).abs(), (x.Low - pc).abs()], axis=1).max(1)
    ra = dict(c=x.Close.to_numpy(), atr=tr.rolling(20).mean().to_numpy(), ma50=x.Close.rolling(50).mean().to_numpy(),
              peak=x.Close.rolling(120, min_periods=60).max().to_numpy())
    ra["ma10"] = x.Close.rolling(10).mean().to_numpy()
    ra["ma5"], ra["ma20"] = (x.Close.rolling(q).mean().to_numpy() for q in (5, 20))
    ra["hi20"] = x.Close.rolling(20).max().to_numpy()
    for k in ("atr", "ma5", "ma10", "ma20", "ma50", "hi20", "peak"):
        np.testing.assert_allclose(_nan(mine[k]), ra[k], rtol=1e-12, equal_nan=True)
    # s17_wait.events: 'A3' switch, '2Br' and 'FR_2_5' entries
    cl, at = x.Close, pd.Series(ra["atr"])
    on = ((cl.rolling(20).max() - cl) >= 3 * at).fillna(False).to_numpy()
    sw, s = np.zeros(n, bool), False
    for i in range(n):
        s = (not (cl[i] > ra["ma20"][i])) if s else bool(on[i]); sw[i] = s
    assert S.crash_switch(mine) == sw.tolist() and sw.any()
    rc, rm, rp, rt = ra["c"], ra["ma50"], ra["peak"], ra["atr"]
    ref_2br = [i for i in _research_second_breaks(rc, rm, rp, lambda i: rc[i] < rm[i] and np.isfinite(rp[i]) and rc[i] <= rp[i] - 3 * rt[i],
                                                   rearm=True) if sw[i]]
    assert [i for i in S.switched_second_breaks(mine, sw.tolist()) if i < n - 1] == ref_2br and ref_2br
    fired = 0
    for closes in (_choppy(random.Random(5), n), _fr_closes()):       # random noise rarely rallies 2 ATR inside a switch
        o, h, l, c = _ohlc(bars(closes, spread=0.001))
        n = len(c)
        mine = S.ind(o, h, l, c)
        rc, rt, ma5, m20 = (_nan(mine[k]) for k in ("c", "atr", "ma5", "ma20"))
        hi = pd.Series(rc).rolling(20).max().to_numpy()
        sw, s = np.zeros(n, bool), False
        for i in range(n):
            s = (not (rc[i] > m20[i])) if s else bool((hi[i] - rc[i]) >= 3 * rt[i]); sw[i] = s
        ref_fr, L, rallied = [], None, False
        for i in range(n):
            if not sw[i]: L, rallied = None, False; continue
            if L is None or rc[i] < L: L, rallied = rc[i], False
            elif np.isfinite(rt[i]) and rc[i] >= L + 2 * rt[i]: rallied = True
            if rallied and rc[i] < ma5[i] and rc[i - 1] >= ma5[i - 1]: ref_fr.append(i); rallied = False; L = rc[i]
        assert S.kq_fr(mine) == ref_fr
        fired += len(ref_fr)
    assert fired
    n, mine = len(kq_c), S.ind(*_ohlc(kq))                           # back to the panel series
    below = x.Close.to_numpy() < ra["ma10"]
    ref_x10 = [i for i in np.where(below & ~np.r_[False, below[:-1]])[0] if i >= 10]  # s11 research rule after warm-up
    assert S.kq_x10(mine) == ref_x10 and ref_x10
    rc, rm, rp, rt = ra["c"], ra["ma50"], ra["peak"], ra["atr"]
    ref_f1 = _research_second_breaks(rc, rm, rp, lambda i: rc[i] < rm[i] and np.isfinite(rp[i]) and rc[i] <= rp[i] - 3 * rt[i])
    assert [i for i in S.kq_f1(mine) if i < n - 1] == ref_f1 and ref_f1

    # sector panel, rearm breaks, driver
    Cdf = pd.DataFrame({g: _nan(raw[g]) for g in groups})
    m = pd.Series(bench_c)
    ma50 = Cdf.rolling(50).mean(); r126 = Cdf / Cdf.shift(126) - 1
    leader = ((r126.rank(axis=1, pct=True) >= 2 / 3) & (r126 >= 0.15)).astype(float).rolling(120, min_periods=1).max() > 0
    peak = Cdf.rolling(120, min_periods=60).max(); rs = Cdf.div(m, axis=0); weak = rs < rs.rolling(50).mean()
    panel = S.sector_panel(raw, bench_c)
    breaks = S.sector_breaks(raw, panel, rearm=True)
    total = 0
    for g in groups:
        assert panel["leader"][g] == leader[g].tolist() and panel["weak"][g] == weak[g].tolist()
        cc, mm, pk, ld, wk = (v[g].to_numpy() for v in (Cdf, ma50, peak, leader, weak))
        ref = _research_second_breaks(cc, mm, pk, lambda i: ld[i] and cc[i] < mm[i] and np.isfinite(pk[i])
                                      and cc[i] <= 0.9 * pk[i] and wk[i], wk, rearm=True)
        assert sorted(i for i in breaks[g] if i < n - 1) == ref
        total += len(ref)
    gr = Cdf.pct_change(fill_method=None)
    cor = gr.rolling(60, min_periods=48).corr(pd.Series(kq_c).pct_change()).where(leader)
    drv = cor.fillna(-np.inf).idxmax(axis=1).where(cor.notna().any(axis=1))
    checked = 0
    for t in range(130, n):
        g, r = S.driver_at(raw, panel["leader"], kq_c, t)
        assert g == (None if pd.isna(drv[t]) else drv[t]), t
        if g is not None:
            assert r == pytest.approx(cor[g][t], abs=1e-9)
            checked += 1
    assert checked > 100 and total > 0  # the panel exercised leaders, drivers and sector re-breaks
