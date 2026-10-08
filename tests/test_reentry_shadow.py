import json
import sqlite3
from datetime import date, timedelta

import pytest

from observability import reentry_shadow as S
from prism_core import stopout_reentry as R


def _bars(closes, volumes, start=date(2026, 5, 1)):
    rows, day = [], start
    for close, volume in zip(closes, volumes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append({"date": day.isoformat(), "open": close, "high": close * 1.005, "low": close * 0.995,
                     "close": close, "volume": volume})
        day += timedelta(days=1)
    return rows


def _db(path, market="KR", stop_rows=(), watch_rows=()):
    trades, watch = S.TABLES[market]
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE {trades} (account_key TEXT, ticker TEXT, company_name TEXT, buy_date TEXT, "
                 "buy_price REAL, sell_date TEXT, sell_price REAL, profit_rate REAL, trigger_type TEXT, exit_kind TEXT, "
                 "scenario TEXT)")
    conn.execute(f"CREATE TABLE {watch} (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, analyzed_date TEXT, "
                 "current_price REAL, buy_score INTEGER, min_score INTEGER, decision TEXT, skip_reason TEXT, "
                 "trigger_type TEXT, scenario TEXT, was_traded INTEGER DEFAULT 0)")
    for row in stop_rows:
        conn.execute(f"INSERT INTO {trades} VALUES (?,?,?,?,?,?,?,?,?,?,?)", (tuple(row) + (None,))[:11])
    for row in watch_rows:
        conn.execute(f"INSERT INTO {watch} (ticker, company_name, analyzed_date, current_price, buy_score, "
                     "min_score, decision, skip_reason, trigger_type, scenario, was_traded) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     row)
    conn.commit()
    conn.close()


def _scenario(passed=True):
    return json.dumps({"fundamental_check": {"all_passed": passed}, "_decision_id": "d1"})


def test_session_date_maps_kst_stamp_to_new_york_session():
    assert S.session_date("2026-09-26 04:03:07", "US") == "2026-09-25"
    assert S.session_date("2026-09-25 23:30:00", "US") == "2026-09-25"
    assert S.session_date("2026-09-26 04:03:07", "KR") == "2026-09-26"
    assert S.session_date("2026-09-26", "US") == "2026-09-26"


def test_candidates_filters_and_is_read_only(tmp_path):
    db = tmp_path / "t.sqlite"
    _db(db, stop_rows=[("acct", "000001", "A", "2026-09-01 09:40:00", 100, "2026-09-03 10:00:00", 93, -7, "t", "stop"),
                       ("acct", "000002", "B", "2026-09-01 09:40:00", 100, "2026-09-03 10:00:00", 110, 10, "t", "trend_exit"),
                       ("acct", "000003", "C", "2026-06-01 09:40:00", 100, "2026-06-03 10:00:00", 93, -7, "t", "stop")],
        watch_rows=[("000004", "D", "2026-09-10 09:45:00", 50, 6, 8, "Skip", "점수 부족 T1", "t", _scenario(), 0),
                    ("000005", "E", "2026-09-10 09:45:00", 50, 2, 8, "Skip", "점수 부족", "t", _scenario(), 0),
                    ("000006", "F", "2026-09-10 09:45:00", 50, 7, 8, "Skip", "점수 부족", "t", _scenario(False), 0),
                    ("000007", "G", "2026-09-10 09:45:00", 50, 7, 8, "Skip", "x", "t", _scenario(), 1),
                    ("000008", "H", "2026-09-26 09:45:00", 50, 7, 8, "Skip", "x", "t", _scenario(), 0)])
    before = db.read_bytes()
    rows = S.candidates(db, "KR", "2026-09-25")
    assert [(r["source"], r["ticker"]) for r in rows] == [("STOP_EXIT", "000001"), ("LOCATION_SKIP", "000004")]
    assert rows[1]["skip_categories"] == ["trend_gate", "score"] and rows[1]["decision_id"] == "d1"
    assert db.read_bytes() == before


def test_stop_rows_carry_the_original_decision_id(tmp_path):
    db = tmp_path / "t.sqlite"
    _db(db, stop_rows=[("acct", "000001", "A", "2026-09-01 09:40:00", 100, "2026-09-03 10:00:00", 93, -7, "t",
                        "stop", _scenario())], watch_rows=[])
    [row] = S.candidates(db, "KR", "2026-09-25")
    assert row["source"] == "STOP_EXIT" and row["decision_id"] == "d1"


def _stopped_setup():
    closes = [80 + i * 0.3 for i in range(60)] + [100, 96, 93, 95, 97, 99, 102]
    volumes = [1000] * 66 + [2000]
    bars = _bars(closes, volumes)
    row = {"source": "STOP_EXIT", "account_key": "acct", "ticker": "000001", "entry_date": bars[60]["date"],
           "entry_price": 100, "exit_date": bars[62]["date"], "exit_price": 93, "trigger_type": "t", "exit_kind": "stop"}
    return bars, row


def test_advance_enrols_once_emits_signal_once_and_marks_late():
    bars, row = _stopped_setup()
    frames = {"000001": bars, "__benchmark_rows": {"000001": bars}}
    state = {"schema_version": 1, "policy_version": R.POLICY_VERSION, "market": "KR", "watches": []}
    completed = bars[-1]["date"]
    state, fresh = S.advance(state, [row], frames, completed, "KR")
    assert [e["kind"] for _, e in fresh] == ["RECLAIM"]
    watch = state["watches"][0]
    assert watch["enrollment"] == "LATE" and watch["status"] == "RECLAIMED"
    assert fresh[0][1]["market_check"]["ok"] is True
    assert watch["control"]["kind"] == "HOLD_WITHOUT_STOP"
    state, fresh = S.advance(state, [row], frames, completed, "KR")
    assert fresh == [] and len(state["watches"]) == 1


def test_advance_price_basis_and_missing_bars_are_explicit():
    bars, row = _stopped_setup()
    state = {"schema_version": 1, "policy_version": R.POLICY_VERSION, "market": "KR", "watches": []}
    state, _ = S.advance(state, [dict(row, entry_price=150)], {"000001": bars}, bars[-1]["date"], "KR")
    assert state["watches"][0]["status"] == "MISSING_FINAL"
    assert state["watches"][0]["reason"] == "price_basis_mismatch"
    state = {"schema_version": 1, "policy_version": R.POLICY_VERSION, "market": "KR", "watches": []}
    state, _ = S.advance(state, [row], {}, bars[-1]["date"], "KR")
    assert state["watches"][0]["status"] == "PENDING_ENROLL"
    # A later row for the same ticker/source does not open a second live watch.
    later = dict(row, entry_date=bars[63]["date"], exit_date=bars[64]["date"])
    state, _ = S.advance(state, [later], {}, bars[-1]["date"], "KR")
    assert len(state["watches"]) == 1


def test_run_dry_run_writes_nothing(tmp_path, monkeypatch):
    bars, row = _stopped_setup()
    db = tmp_path / "t.sqlite"
    _db(db, stop_rows=[("acct", "000001", "A", row["entry_date"] + " 09:40:00", 100,
                        row["exit_date"] + " 10:00:00", 93, -7, "t", "stop")])
    emitted = []
    monkeypatch.setattr(S, "_emit", lambda *a, **k: emitted.append(a) or {"ok": True})
    path = tmp_path / "state.json"
    collector = lambda tickers, completed: {"000001": bars, "__benchmark_rows": {}}  # noqa: E731
    summary = S.run("KR", bars[-1]["date"], collector=collector, db_path=db, path=path, dry_run=True)
    assert summary["new_signals"] == 1 and not path.exists() and emitted == []
    summary = S.run("KR", bars[-1]["date"], collector=collector, db_path=db, path=path)
    assert path.exists() and [a[0] for a in emitted] == ["reentry.shadow_signal", "reentry.shadow_run"]
    emitted.clear()
    S.run("KR", bars[-1]["date"], collector=collector, db_path=db, path=path)
    assert [a[0] for a in emitted] == ["reentry.shadow_run"]


def test_state_version_mismatch_is_rejected(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"schema_version": 1, "policy_version": "other", "market": "KR", "watches": []}))
    with pytest.raises(ValueError):
        S._load(path, "KR")


def test_enabled_requires_exact_policy(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    monkeypatch.setattr(S, "POLICY_PATH", path)
    assert not S.enabled("KR")
    path.write_text(json.dumps(S.POLICY))
    assert S.enabled("KR") and S.enabled("US")
    monkeypatch.setenv("REENTRY_SHADOW_ENABLED", "false")
    assert not S.enabled("KR")


def test_account_key_is_never_copied(tmp_path):
    db = tmp_path / "t.sqlite"
    _db(db, stop_rows=[("prod:12345678:01", "000001", "A", "2026-09-01 09:40:00", 100, "2026-09-03 10:00:00", 93, -7, "t", "stop")])
    rows = S.candidates(db, "KR", "2026-09-25")
    assert rows[0]["account_key"].startswith("acct-") and "12345678" not in json.dumps(rows)


def test_finished_watches_move_to_archive_and_packet_reads_it(tmp_path):
    from tools import build_reentry_evidence_packet as P
    path = tmp_path / "reentry_shadow_state_kr_v1.json"
    old = {"watch_id": "a", "status": "CLOSED", "source": "STOP_EXIT", "enrollment": "PROSPECTIVE",
           "row": {"exit_date": "2026-06-01"}, "events": [{"kind": "RECLAIM", "date": "2026-06-05",
           "market_check": {"ok": True}, "trade": {"status": "CLOSED", "ret": 0.1}}], "control": {}}
    live = {"watch_id": "b", "status": "WATCHING", "source": "STOP_EXIT", "row": {"exit_date": "2026-06-01"},
            "events": []}
    recent = dict(old, watch_id="c", row={"exit_date": "2026-09-01"})
    state = {"schema_version": 1, "policy_version": R.POLICY_VERSION, "market": "KR", "watches": [old, live, recent]}
    assert S.archive_finished(state, path, "2026-09-25") == 1
    assert [w["watch_id"] for w in state["watches"]] == ["b", "c"]
    assert S.archive_finished(state, path, "2026-09-25") == 0
    path.write_text(json.dumps(state))
    assert P.build(P.load_states([path]))["trades"]["KR|PROSPECTIVE|STOP_EXIT|RECLAIM|all"]["n"] == 2


def test_concurrent_run_is_skipped_cleanly(tmp_path):
    import fcntl
    path = tmp_path / "state.json"
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        summary = S.run("KR", "2026-09-25", collector=lambda *a: {}, db_path=tmp_path / "none.sqlite", path=path)
    assert summary["skipped"] == "lock_held"


def test_run_summary_and_signal_payload_survive_event_sanitizer(tmp_path, monkeypatch):
    from observability.events import build_event
    bars, row = _stopped_setup()
    db = tmp_path / "t.sqlite"
    _db(db, stop_rows=[("acct", "000001", "A", row["entry_date"] + " 09:40:00", 100,
                        row["exit_date"] + " 10:00:00", 93, -7, "t", "stop")])
    events = []
    monkeypatch.setattr(S, "_emit", lambda name, market, **kw: events.append(kw["attributes"]) or {"ok": 1})
    S.run("KR", bars[-1]["date"], collector=lambda *a: {"000001": bars, "__benchmark_rows": {"000001": bars}},
          db_path=db, path=tmp_path / "s.json")
    for attributes in events:
        assert "[REDACTED]" not in json.dumps(build_event("x", service="s", attributes=attributes)["attributes"])
    assert events[-1]["completed_market_day"] == bars[-1]["date"]


def test_kr_collector_spaces_and_retries(tmp_path, monkeypatch):
    import pandas as pd

    from tools import run_reentry_shadow as T
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    bars, _ = _stopped_setup()
    frame = pd.DataFrame([{"Open": b["open"], "High": b["high"], "Low": b["low"], "Close": b["close"],
                           "Volume": b["volume"]} for b in bars], index=pd.to_datetime([b["date"] for b in bars]))
    calls = []

    class Source:
        def price_history(self, symbol, start, end, adjusted=True):
            calls.append(symbol)
            if calls.count(symbol) == 1:
                raise RuntimeError("EGW00201")
            return frame

        def index_history(self, symbol, start, end):
            return frame

    class Master:
        markets = {"000001": "KOSPI"}

    out = T.collect_kr(["000001"], bars[-1]["date"], source=Source(), master=Master(), cache_dir=tmp_path)
    assert out["000001"][-1]["date"] == bars[-1]["date"] and calls == ["000001", "000001"]
    assert out["__benchmark_rows"]["000001"]


def test_enrolment_is_point_in_time_and_independent_of_run_count():
    bars, row = _stopped_setup()
    # Second stop-out on the same ticker: one while the first watch is live, one after it ended.
    during = dict(row, entry_date=bars[63]["date"], exit_date=bars[64]["date"])
    frames = {"000001": bars, "__benchmark_rows": {}}
    fail = [dict(b) for b in bars]
    for b in fail[63:]:
        b.update(open=80.0, high=80.5, low=79.5, close=80.0)   # deep failure ends the first watch on bar 63
    after = dict(row, entry_date=fail[64]["date"], exit_date=fail[65]["date"], entry_price=80.0, exit_price=80.0)

    def once(rows, frame_set, runs):
        state = {"schema_version": 1, "policy_version": R.POLICY_VERSION, "market": "KR", "watches": []}
        for _ in range(runs):
            state, _ = S.advance(state, rows, frame_set, bars[-1]["date"], "KR")
        return sorted(w["row"]["exit_date"] for w in state["watches"])

    assert once([row, during], frames, 1) == once([row, during], frames, 3) == [row["exit_date"]]
    fail_frames = {"000001": fail, "__benchmark_rows": {}}
    first, again = once([row, after], fail_frames, 1), once([row, after], fail_frames, 3)
    assert first == again == [row["exit_date"], after["exit_date"]]
