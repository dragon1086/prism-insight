"""Re-entry evidence for later analysis (user request 2026-10-21): every signal and every near miss
keeps structured decision facts (50-day line position and slope, volume, relative strength ...), each
virtual attempt keeps its max gain/loss, and every signal or near miss is queued for the forward
max gain/loss pass, whose final result lands on the same trace. Observation only: no trading change.
"""
import json
import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

import test_reentry_campaign as T
from observability import candidate_ledger as CL
from observability import reentry_v3_shadow as V3
from prism_core import decision_snapshot as DS


def _rising(n, start=100.0, step=0.5):
    day = date(2026, 1, 1)
    out = []
    for k in range(n):
        close = start + step * k - (1.5 if k % 5 == 4 else 0)       # a pullback every fifth bar
        out.append({"date": (day + timedelta(days=k)).isoformat(), "open": close, "high": close + 1,
                    "low": close - 1, "close": close, "volume": 1000.0 + (100 if k % 2 else 0)})
    return out


def test_snapshot_measures_ma_slopes_and_keeps_missing_unknown():
    bars = _rising(260)
    snap = DS.snapshot(bars, price=bars[-1]["close"], bench=_rising(260, step=0.1))
    assert snap["contract"] == "decision_snapshot_v1" and snap["status"] == "OK" and snap["missing"] == {}
    assert snap["above_ma50"] is True and snap["ma50_rising"] is True and snap["dist_ma50_pct"] > 0
    assert snap["ma200"] is not None and snap["high_252d"] == pytest.approx(max(b["high"] for b in bars[-252:]))
    assert snap["rs_60d_pctp"] > 0 and snap["updown_volume_ratio_50"] is not None
    short = DS.snapshot(bars[:30], price=90.0)
    assert short["status"] == "PARTIAL" and short["ma50"] is None and short["above_ma50"] is None
    assert short["ma200"] is None and short["rs_60d_pctp"] is None and "ma50" in short["missing"]
    assert DS.snapshot([], price=1.0)["status"] == "MISSING"
    assert "session" not in json.dumps(snap)            # the event sanitizer redacts keys containing it


def test_excursion_uses_held_bars_and_the_exit_price():
    path = [{"high": 110, "low": 98}, {"high": 105, "low": 90}]
    assert DS.excursion(path, 100, 95) == {"max_gain_pct": 10.0, "max_loss_pct": -10.0}
    assert DS.excursion([], 100, None) == {"max_gain_pct": None, "max_loss_pct": None}


@pytest.fixture(autouse=True)
def _ledger_on(monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEDGER_ENABLED", "true")        # conftest turns it off for other tests


def test_signal_event_carries_structured_facts_and_queues_a_forward_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEDGER_DB", str(tmp_path / "ledger.sqlite"))
    bars, root, sent, run, quote = T._runtime(tmp_path, monkeypatch)
    run(70, llm_recheck=False)
    run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=False)
    trig = next(kw["attributes"] for n, kw in sent if n == "reentry_v3.shadow_trigger")
    assert trig["snapshot"]["contract"] == "decision_snapshot_v1"
    for key in ("ma50", "dist_ma50_pct", "ma50_slope_10d_pct", "above_ma50", "atr14_pct", "rs_60d_pctp"):
        assert key in trig["snapshot"], key
    assert trig["level_basis"] and trig["prev_close"] and trig["day_low"] == 12000.0
    assert trig["dist_level_pct"] == pytest.approx((12150 / 11950 - 1) * 100, abs=1e-3)
    assert trig["original"]["decided_on"] and trig["event_ref"]
    rows = sqlite3.connect(tmp_path / "ledger.sqlite").execute(
        "SELECT mode, trigger_type, ticker, close, selected, features_json FROM candidate_ledger").fetchall()
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [("reentry_v3_signal", "R2S", "000001", 12150.0, 1)]
    features = json.loads(rows[0][5])
    assert features["trace_id"] and features["event_ref"] == trig["event_ref"] and features["snapshot"]


def test_a_near_miss_is_recorded_with_its_reason_and_numbers(tmp_path, monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEDGER_DB", str(tmp_path / "ledger.sqlite"))
    bars, root, sent, run, quote = T._runtime(tmp_path, monkeypatch, quote_price=12700.0)   # > L*1.05: chase
    run(70, llm_recheck=False)
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=False)
    assert summary["new_triggers"] == 0 and summary["intraday"]["near_misses"] >= 1
    names = [n for n, _ in sent]
    assert "reentry_v3.shadow_trigger" not in names
    miss = next(kw["attributes"] for n, kw in sent if n == "reentry_v3.shadow_near_miss")
    assert miss["trigger"] == "R2S" and miss["reason"] == "chase" and miss["decision_price"] == 12700.0
    assert miss["snapshot"]["contract"] == "decision_snapshot_v1" and miss["trading_impact"] == "none"
    rows = sqlite3.connect(tmp_path / "ledger.sqlite").execute(
        "SELECT mode, trigger_type, selected, features_json FROM candidate_ledger").fetchall()
    assert rows and all(r[0] == "reentry_v3_near_miss" and r[2] == 0 for r in rows)
    assert json.loads(rows[0][3])["reason"] in V3.NEAR_MISS[rows[0][1]]


def test_structural_non_events_are_not_near_misses():
    decision = {"trigger": None, "checks": {"L97": {"R1C": "below_level", "R2S": "no_touch"}}, "details": {}}
    assert V3.near_misses(decision) == []
    decision["checks"]["L97"]["R2S"] = "rr_below_floor"
    decision["details"] = {"L97": {"R2S": {"rr": 0.9, "rr_floor": 1.2}}}
    assert V3.near_misses(decision) == [("R2S", "rr_below_floor", {"rr": 0.9, "rr_floor": 1.2})]
    assert V3.near_misses(dict(decision, trigger={"trigger": "R2S"})) == []


def test_closed_virtual_attempts_keep_max_gain_and_loss(tmp_path, monkeypatch):
    bars, root, sent, run, quote = T._runtime(tmp_path, monkeypatch)
    run(70, llm_recheck=False)
    run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=False)
    run(len(bars) - 1, llm_recheck=False)
    exits = [kw["attributes"] for n, kw in sent if n == "reentry_v3.shadow_exit"]
    assert exits, "the fixture closes its virtual attempt"
    for att in exits:
        assert att["max_gain_pct"] is not None and att["max_loss_pct"] is not None
        assert att["max_loss_pct"] <= att["ret"] * 100 + 1e-6 <= att["max_gain_pct"] + 1e-6
        assert att["bars_held"] >= 1 and att["entry_price"] and att["exit_price"]


def _frame(n, start="2026-01-02", base=100.0):
    days = pd.bdate_range(start, periods=n)
    closes = [base + k for k in range(n)]
    return pd.DataFrame({"Open": closes, "High": [c + 2 for c in closes], "Low": [c - 3 for c in closes],
                         "Close": closes, "Volume": [1000.0] * n}, index=[d.date().isoformat() for d in days])


def test_outcomes_add_5_10_20_session_excursions():
    bars = _frame(40)
    out = CL.compute_outcomes(bars, bars.index[0], 100.0)
    assert out["mfe_5s"] == pytest.approx((107 / 100 - 1) * 100)      # day 5 close 105, high 107
    assert out["mae_5s"] == pytest.approx((98 / 100 - 1) * 100)       # day 1 low
    assert out["mfe_20s"] == pytest.approx((122 / 100 - 1) * 100) and out["mae_10s"] is not None
    early = CL.compute_outcomes(bars.iloc[:4], bars.index[0], 100.0)
    assert early["mfe_5s"] is None and early["mae_5s"] is None          # not observed yet stays unknown


def test_final_outcome_lands_on_the_decision_trace_once(tmp_path, monkeypatch):
    sent = []
    import observability.events as events
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1})
    db = tmp_path / "ledger.sqlite"
    rows = [{"trigger_type": "R2S", "ticker": "AAA", "close": 100.0, "selected": 1,
             "features_json": json.dumps({"trace_id": "t" * 32, "event_ref": "e1", "reason": None})},
            {"trigger_type": "screen", "ticker": "BBB", "close": 100.0, "features_json": "{}"}]
    CL.record(rows[:1], market="US", trade_date="2026-01-02", mode="reentry_v3_signal", db_path=db)
    CL.record(rows[1:], market="US", trade_date="2026-01-02", mode="afternoon", db_path=db)
    frame = _frame(45)
    stats = CL.update_candidate_outcomes("US", db_path=db, fetch_bars=lambda t, s, e: frame, today="2026-03-31",
                                         sleep_sec=0)
    assert stats["complete"] == 2 and stats.get("emitted") == 2
    by_ticker = {kw["ticker"]: kw for name, kw in sent if name == "candidate.forward_outcome"}
    kw = by_ticker["AAA"]
    assert kw["trace_id"] == "t" * 32 and kw["attributes"]["trace_scope"] == "decision"
    screening = by_ticker["BBB"]                       # no decision trace: a stable per-row trace
    assert screening["attributes"]["trace_scope"] == "ledger_row" and len(screening["trace_id"]) == 32
    attrs = kw["attributes"]
    assert attrs["mode"] == "OBSERVATION" and attrs["ledger_mode"] == "reentry_v3_signal" and attrs["event_ref"] == "e1" and attrs["mfe_pct"] is not None
    assert attrs["mfe_10s"] is not None and attrs["outcome_status"] == "COMPLETE"
    CL.update_candidate_outcomes("US", db_path=db, fetch_bars=lambda t, s, e: frame, today="2026-04-30", sleep_sec=0)
    assert len(sent) == 2                                               # complete rows are never re-sent


def _main_db(path):
    conn = sqlite3.connect(path)
    for table in ("trading_history", "us_trading_history"):
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT, company_name TEXT, "
                     "buy_price REAL, buy_date TEXT, sell_price REAL, sell_date TEXT, profit_rate REAL, "
                     "holding_days INTEGER, scenario TEXT, trigger_type TEXT, trigger_mode TEXT, sector TEXT, "
                     "exit_kind TEXT)")
    for table in ("stock_holdings", "us_stock_holdings"):
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT, company_name TEXT, "
                     "buy_price REAL, buy_date TEXT, current_price REAL, scenario TEXT, trigger_type TEXT)")
    conn.execute("INSERT INTO us_trading_history VALUES (1,'a','NVDA','Nvidia',200,'2026-10-13 23:50:00',190,"
                 "'2026-10-15 23:00:00',-5,2,?,'Volume Surge','m','Tech','stop')",
                 (json.dumps({"_decision_id": "d-nvda"}),))
    conn.execute("INSERT INTO us_trading_history VALUES (2,'b','NVDA','Nvidia',200,'2026-10-13 23:50:00',190,"
                 "'2026-10-15 23:00:00',-5,2,?,'Volume Surge','m','Tech','stop')",
                 (json.dumps({"_decision_id": "d-nvda"}),))           # second account: same position
    conn.execute("INSERT INTO us_trading_history VALUES (3,'a','OLD','Old',10,'2026-09-01 23:50:00',11,"
                 "'2026-09-05 23:00:00',10,4,'{}','x','m','x','ai')")   # before the trace linkage
    conn.execute("INSERT INTO us_stock_holdings VALUES (9,'a','LLY','Lilly',900,'2026-10-15 23:37:42',910,?,'Gap Up')",
                 (json.dumps({"_decision_id": "d-lly"}),))
    conn.commit()
    conn.close()


def test_real_entries_and_exits_are_followed_on_the_position_trace(tmp_path):
    main, ledger = tmp_path / "main.sqlite", tmp_path / "ledger.sqlite"
    _main_db(main)
    rows = CL.position_rows("US", main_db=main)
    assert sorted(r["ticker"] for r in rows["position_entry"]) == ["LLY", "NVDA", "NVDA"]
    exit_row, = {r["ticker"]: r for r in rows["position_exit"]}.values()
    assert exit_row["trigger_type"] == "stop" and exit_row["close"] == 190 and exit_row["trade_date"] == "2026-10-15"
    features = json.loads(exit_row["features_json"])
    assert features["trace_id"] == CL._trade_trace("US", "d-nvda") and features["profit_rate"] == -5
    assert CL.sync_positions("US", main_db=main, db_path=ledger) == 5          # 3 entries + 2 exits offered
    got = sqlite3.connect(ledger).execute(
        "SELECT mode, ticker, trade_date, close FROM candidate_ledger ORDER BY mode, ticker").fetchall()
    assert got == [("position_entry", "LLY", "2026-10-15", 900.0), ("position_entry", "NVDA", "2026-10-13", 200.0),
                   ("position_exit", "NVDA", "2026-10-15", 190.0)]                # accounts collapse to one row
    conn = sqlite3.connect(ledger)
    conn.execute("UPDATE candidate_ledger SET outcome_status='COMPLETE', mfe_pct=12.0")
    conn.commit()
    conn.close()
    CL.sync_positions("US", main_db=main, db_path=ledger)                      # a later sync keeps outcomes
    assert sqlite3.connect(ledger).execute(
        "SELECT COUNT(*) FROM candidate_ledger WHERE outcome_status='COMPLETE' AND mfe_pct=12.0").fetchone()[0] == 3


def test_the_exit_rule_from_the_sell_side_wins_over_the_guessed_kind(tmp_path):
    main = tmp_path / "main.sqlite"
    _main_db(main)
    conn = sqlite3.connect(main)
    conn.execute("UPDATE us_trading_history SET scenario=? WHERE id=1",
                 (json.dumps({"_decision_id": "d-nvda", "exit_rule": "TIER2_TRAIL"}),))
    conn.execute("DELETE FROM us_trading_history WHERE id=2")
    conn.commit()
    conn.close()
    (row,) = CL.position_rows("US", main_db=main)["position_exit"]
    assert row["trigger_type"] == "TIER2_TRAIL"


def test_snapshot_records_the_ten_day_line():
    from prism_core.decision_snapshot import snapshot
    bars = [{"date": f"2026-01-{d:02d}", "open": 10 + d, "high": 11 + d, "low": 9 + d, "close": 10 + d, "volume": 100}
            for d in range(1, 21)]
    out = snapshot(bars)
    assert out["ma10"] == sum(10 + d for d in range(11, 21)) / 10 and out["dist_ma10_pct"] is not None
    assert out["ma10_slope_10d_pct"] is not None
    short = snapshot(bars[:5])
    assert short["ma10"] is None and short["missing"]["ma10"] == "needs_10_bars"
