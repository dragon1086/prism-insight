"""Log-only relative-weakness (e) check for runner holdings (tools/runner_rs_shadow.py, 2026-10-14)."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from prism_core import runner_hold as R
from tools import runner_rs_shadow as S


def _days(n, start=date(2026, 8, 3)):
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _bars(closes, days):
    return [{"date": d, "close": c} for d, c in zip(days, closes)]


def test_fires_when_stock_falls_10pp_more_than_index_below_ma20():
    days = _days(30)
    stock = [100.0] * 20 + [130, 128, 125, 120, 116, 112, 110, 108, 106, 104]   # peak 130, last 104 = -20%
    index = [100.0] * 20 + [100, 99, 98, 97, 96, 96, 95, 95, 94, 94]            # -6% over the same days
    facts = S.evaluate_e(_bars(stock, days), _bars(index, days), days[0])
    assert facts["peak_date"] == days[20] and facts["stock_from_peak_pct"] == -20.0
    assert facts["index_from_peak_pct"] == -6.0 and facts["rel_pp"] == -14.0
    assert facts["below_ma20"] is True and facts["fired"] is True


def test_holds_when_the_index_fell_as_much_or_close_is_above_ma20():
    days = _days(30)
    stock = [100.0] * 20 + [130, 128, 125, 120, 116, 112, 110, 108, 106, 104]
    index = [100.0] * 20 + [100, 98, 96, 94, 92, 90, 88, 86, 85, 84]            # -16%: stock only 4%p weaker
    assert S.evaluate_e(_bars(stock, days), _bars(index, days), days[0])["fired"] is False
    strong = [100.0] * 20 + [150, 148, 146, 144, 142, 140, 138, 136, 134, 133]   # -11.3% but far above MA20
    flat = [100.0] * 30
    facts = S.evaluate_e(_bars(strong, days), _bars(flat, days), days[0])
    assert facts["rel_pp"] < -10 and facts["below_ma20"] is False and facts["fired"] is False


def test_missing_index_day_is_unknown_not_hold():
    days = _days(30)
    stock = [100.0] * 20 + [130] + [104] * 9
    facts = S.evaluate_e(_bars(stock, days), _bars([100.0] * 29, days[:29]), days[0])
    assert facts["fired"] is None and facts["rel_pp"] is None


def test_short_history_returns_none():
    days = _days(10)
    assert S.evaluate_e(_bars([100.0] * 10, days), _bars([100.0] * 10, days), days[0]) is None


def _db(tmp_path, scenario):
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, buy_price REAL, "
                 "buy_date TEXT, current_price REAL, scenario TEXT)")
    conn.execute("INSERT INTO stock_holdings VALUES (1, '252990', 'S', 100, '2026-08-03 10:00:00', 104, ?)",
                 (json.dumps(scenario),))
    conn.execute("INSERT INTO stock_holdings VALUES (2, '252990', 'S', 100, '2026-08-03 10:00:00', 104, ?)",
                 (json.dumps(scenario),))
    conn.execute("INSERT INTO stock_holdings VALUES (3, '000001', 'X', 100, '2026-08-03 10:00:00', 100, '{}')")
    conn.commit()
    conn.close()
    return path


def test_run_records_one_row_per_runner_and_never_writes_the_db(tmp_path, monkeypatch):
    block = {"version": R.VERSION, "status": R.RUNNER, "entry_ref": 100, "entry_session": "2026-08-03",
             "hold_until": "2026-09-28"}
    db = _db(tmp_path, {R.SCENARIO_KEY: block})
    before = db.read_bytes()
    monkeypatch.setattr(S, "ROOT", tmp_path)
    days = _days(30)
    stock = _bars([100.0] * 20 + [130, 128, 125, 120, 116, 112, 110, 108, 106, 104], days)
    index = _bars([100.0] * 20 + [100, 99, 98, 97, 96, 96, 95, 95, 94, 94], days)
    events = []
    monkeypatch.setattr("observability.events.emit_event", lambda *a, **k: events.append((a, k)))
    now = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)   # 17:00 KST, after the close
    out = S.run("KR", str(db), now=now, fetch_bars=lambda m, t: stock, fetch_index=lambda code: index)
    assert [r["ticker"] for r in out] == ["252990"]
    assert out[0]["e"]["fired"] is True and set(out[0]["reference"]) == {"KOSDAQ"}
    lines = (tmp_path / "logs" / "runner_rs_shadow_kr.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["e"]["rel_pp"] == -14.0
    assert len(events) == 1 and events[0][0][0] == "runner.rs_shadow"
    assert db.read_bytes() == before


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    block = {"version": R.VERSION, "status": R.RUNNER, "entry_ref": 100, "entry_session": "2026-08-03"}
    db = _db(tmp_path, {R.SCENARIO_KEY: block})
    monkeypatch.setattr(S, "ROOT", tmp_path)
    days = _days(30)
    flat = _bars([100.0] * 30, days)
    S.run("KR", str(db), now=datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc), fetch_bars=lambda m, t: flat,
          fetch_index=lambda code: flat, dry_run=True)
    assert not (tmp_path / "logs").exists()
