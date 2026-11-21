"""decide(), the live cycle with fakes, the lane DB, and replay/cycle agreement."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from prism_inverse.adapters.fakes import FakeEtfInfo, FakeEvents, FakeLongLane, FakeMarketData, MemoryStore
from prism_inverse.adapters.sqlite_store import SqliteStore
from prism_inverse.app.cycle import run_cycle, trace_id
from prism_inverse.app.inputs import build_inputs, snapshot_from_bars
from prism_inverse.app.pulse import pulse_series
from prism_inverse.app.replay import replay
from prism_inverse.domain.engine import LaneInputs, decide
from prism_inverse.domain.models import ENTER, EXIT, HOLD, SKIP, Bar, LeaderState
from prism_inverse.domain.policy import InversePolicy
from prism_inverse.tests.helpers import bars, run_up_then_break, spec

P = replace(InversePolicy(), entry_mode="signal", max_premium_pct_kr=None)  # bar-built snapshots have no NAV


def _bear_index():
    """Uptrend, then a >10% slide (Market Pulse CORRECTION), then chop."""
    up = [100 + i * 0.2 for i in range(120)]
    down = [up[-1] * (1 - 0.006 * (i + 1)) for i in range(30)]
    chop = [down[-1] * (1 + (0.004 if i % 2 else -0.004)) for i in range(30)]
    return bars(up + down + chop, [1e6] * 120 + [1.3e6 + i * 1e4 for i in range(30)] + [1e6] * 30)


def _etf_bars(under):
    return [Bar(b.day, 2000 - b.open * 5, 2000 - b.low * 5, 2000 - b.high * 5, 2000 - b.close * 5, 1e7)
            for b in under]


def _first_correction_day(under):
    series = pulse_series(under)
    return next(d for d in sorted(series) if series[d] == "CORRECTION")


def test_index_lane_enters_on_the_day_the_gate_turns_on():
    under = _bear_index()
    day = _first_correction_day(under)
    s = spec(code="114800", underlying="069500")
    etf = _etf_bars(under)
    inputs = build_inputs(day, "KR", [s], {"069500": under}, {"114800": snapshot_from_bars(s, [b for b in etf if b.day <= day])},
                          {"069500": pulse_series(under)}, {}, [], {}, 1e8)
    actions = [(d.action, d.reason) for d in decide(inputs, P)]
    assert (ENTER, "ENTRY_SIGNAL_DAY") in actions


def test_stock_lane_respects_market_gate_long_lane_and_leader_status():
    under = _bear_index()
    day = _first_correction_day(under)
    closes, vols = run_up_then_break(n_down=60)
    stock = [b for b in bars(closes, vols, start=under[0].day) if b.day <= day]
    policy = replace(P, gap_lookback=20)
    s_idx = spec(code="114800", underlying="069500")
    s_stk = spec(code="0197X0", mult=-2, scope="stock", underlying="000660", kind="stock")
    snaps = {"114800": snapshot_from_bars(s_idx, _etf_bars(under)[:5] * 4),
             "0197X0": replace(snapshot_from_bars(s_stk, _etf_bars(under)[:20]), price=1000.0)}

    def run(long_lane, leader_status):
        inputs = LaneInputs(day, "KR", [(s_idx, snaps["114800"]), (s_stk, snaps["0197X0"])],
                            {"069500": [b for b in under if b.day <= day], "000660": stock},
                            {"069500": ("CORRECTION", "UPTREND")},
                            leaders={"000660": LeaderState("000660", leader_status, day)},
                            long_lane=long_lane, equity=1e8)
        return {d.etf: (d.action, d.reason) for d in decide(inputs, policy) if d.lane == "stock"}

    assert run(frozenset({"000660"}), "broken")["0197X0"] == (SKIP, "LONG_LANE_HOLDS")
    assert run(None, "broken")["0197X0"] == (SKIP, "MISSING_LONG_LANE")
    assert run(frozenset(), "recovered")["0197X0"] == (SKIP, "NOT_BROKEN_LEADER")
    assert run(frozenset(), "broken")["0197X0"] == (ENTER, "ENTRY_SIGNAL_DAY")


def test_open_position_without_bars_is_held_not_exited():
    from prism_inverse.domain.models import Position
    s = spec()
    pos = Position("p1", "114800", "index", -1, 1, 1000.0, 100.0, 105.0, None, date(2020, 1, 1), 1, "069500")
    inputs = LaneInputs(date(2020, 6, 1), "KR", [(s, snapshot_from_bars(s, []))], {}, {}, positions=[pos])
    out = decide(inputs, P)
    assert (out[0].action, out[0].reason) == (HOLD, "MISSING_BARS")
    assert all(d.action != ENTER for d in out)


def _world():
    under = _bear_index()
    etf = _etf_bars(under)
    s = spec(code="114800", underlying="069500")
    return under, etf, s


def test_cycle_observe_records_run_and_data_failure_becomes_skip():
    under, etf, s = _world()
    day = _first_correction_day(under)
    store, events = MemoryStore(), FakeEvents()
    res = run_cycle("KR", day, [s], FakeMarketData({"069500": under}), FakeEtfInfo({}), store, events,
                    FakeLongLane(), P, 1e8, mode="observe")
    assert store.runs[res.run_id]["status"] == "ok"
    assert res.data_errors["114800"].startswith("Unavailable")
    assert [(d.action, d.reason) for d in res.decisions if d.etf == "114800"] == [(SKIP, "MISSING_VALUE_20D")]
    assert events.events and all(t.startswith("inverse.") for t, _ in events.events)


def test_cycle_dry_run_writes_nothing_and_live_is_refused():
    under, etf, s = _world()
    day = _first_correction_day(under)
    store = MemoryStore()
    res = run_cycle("KR", day, [s], FakeMarketData({"069500": under}), FakeEtfInfo({"114800": etf}), store,
                    FakeEvents(), FakeLongLane(), P, 1e8, mode="dry-run")
    assert any(d.action == ENTER for d in res.decisions)
    assert not store.runs and not store.decisions and not store.positions
    with pytest.raises(ValueError):
        run_cycle("KR", day, [s], FakeMarketData({}), FakeEtfInfo({}), store, FakeEvents(), FakeLongLane(), P,
                  1e8, mode="live")


def test_replay_and_cycle_decide_the_same_on_the_same_day():
    under, etf, s = _world()
    days = [b.day for b in under[100:]]
    rep = replay("KR", [s], days, {"069500": under}, {"114800": etf}, P, equity=1e8)
    store = MemoryStore()
    for day in days:
        cyc = run_cycle("KR", day, [s], FakeMarketData({"069500": under}), FakeEtfInfo({"114800": etf}),
                        store, FakeEvents(), FakeLongLane(), P, 1e8, mode="observe")
        strip = lambda ds: [(d.action, d.reason, d.etf, d.qty) for d in ds]
        assert strip(cyc.decisions) == strip(rep.decisions[day]), day
    assert rep.trades and rep.trades[0].entry_price > 0


def test_sqlite_store_roundtrip(tmp_path):
    under, etf, s = _world()
    day = _first_correction_day(under)
    db = SqliteStore(tmp_path / "lane.sqlite")
    res = run_cycle("KR", day, [s], FakeMarketData({"069500": under}), FakeEtfInfo({"114800": etf}), db,
                    FakeEvents(), FakeLongLane(), P, 1e8, mode="observe")
    opened = db.open_positions("KR")
    assert len(opened) == 1 and opened[0].underlying == "069500" and db.attempts("KR") == {"069500": 1}
    status = db.db.execute("SELECT status FROM inv_runs WHERE run_id=?", (res.run_id,)).fetchone()[0]
    assert status == "ok"
    tid = db.db.execute("SELECT trace_id FROM inv_positions").fetchone()[0]
    assert tid == trace_id("KR", opened[0].position_id)
    db.close_position(opened[0].position_id, day, 1500.0, "STOP")
    assert db.open_positions("KR") == []


def test_exit_then_no_same_day_reentry():
    under, etf, s = _world()
    days = [b.day for b in under[100:]]
    rep = replay("KR", [s], days, {"069500": under}, {"114800": etf}, P, equity=1e8)
    for day, ds in rep.decisions.items():
        if any(d.action == EXIT and d.lane == "index" for d in ds):
            assert not any(d.action == ENTER and d.lane == "index" for d in ds)
