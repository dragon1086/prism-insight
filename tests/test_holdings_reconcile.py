"""tools/holdings_reconcile.py: corporate-action detection on the broker-vs-ledger diff (BKH -> HZN, 2026-11-16)."""
import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import holdings_reconcile as HR  # noqa: E402

L = {"BKH": {"buy_price": 76.92, "company_name": "Black Hills Corporation"}}
B_HZN = {"HZN": {"avg_price": 76.9, "quantity": 10, "stock_name": "BRIGHT HORIZON ENERGY"}}


def kinds(findings):
    return [f["kind"] for f in findings]


def test_one_to_one_rename_needs_the_same_share_count_as_the_previous_snapshot():
    f = HR.classify(L, B_HZN, previous={"BKH": 10})
    assert kinds(f) == ["RENAME"] and f[0]["ticker"] == "BKH" and f[0]["new_ticker"] == "HZN"
    assert f[0]["ratio"] == pytest.approx(0.9997, abs=1e-3)
    assert kinds(HR.classify(L, B_HZN)) == ["RENAME_RATIO"]                       # first run: no snapshot
    assert kinds(HR.classify(L, B_HZN, previous={"BKH": 11})) == ["RENAME_RATIO"]  # shares changed


def test_ratio_merger_split_gone_outside_ambiguous_and_stale_quote_are_only_reported():
    # 0.98-for-1 merger: the price moves only 2%, the share count 10 -> 9 is what tells it from a rename
    assert kinds(HR.classify(L, {"HZN": {"avg_price": 78.49, "quantity": 9}}, previous={"BKH": 10})) == ["RENAME_RATIO"]
    assert kinds(HR.classify(L, {"BKH": {"avg_price": 38.46, "quantity": 20}})) == ["SPLIT"]          # 2-for-1 by price
    assert kinds(HR.classify(L, {"BKH": {"avg_price": 70.0, "quantity": 11}}, previous={"BKH": 10})) == ["SPLIT"]  # by count
    assert kinds(HR.classify(L, {"BKH": {"avg_price": 76.9, "quantity": 10}}, previous={"BKH": 10})) == []
    assert kinds(HR.classify(L, {})) == ["GONE"]
    assert kinds(HR.classify(L, {"BKH": {"avg_price": 76.9, "quantity": 10}, "AAPL": {"avg_price": 350.0, "quantity": 2}})) == ["OUTSIDE"]
    two = {"BKH": L["BKH"], "KDP": {"buy_price": 36.5, "company_name": "KDP"}}
    assert kinds(HR.classify(two, {"HZN": B_HZN["HZN"], "XYZ": {"avg_price": 36.5, "quantity": 1}})) == ["AMBIGUOUS"]
    quotes = {"BKH": {"current_price": 86.7, "change_rate": 0.0, "volume": 0}}
    held = {"BKH": {"avg_price": 76.9, "quantity": 10}}
    assert kinds(HR.classify(L, held, quotes=quotes, session_open=True)) == ["STALE_QUOTE"]
    assert kinds(HR.classify(L, held, quotes=quotes, session_open=False)) == []      # pre-open volume is 0 anyway
    assert kinds(HR.classify(L, held, quotes={"BKH": {"change_rate": -2.1, "volume": 5000}}, session_open=True)) == []


def _db(tmp_path):
    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
    create table us_stock_holdings (id integer primary key, account_key text, ticker text, company_name text,
        buy_price real, current_price real, target_price real, stop_loss real, scenario text);
    create table us_holding_decisions (id integer primary key, ticker text);
    create table loop_a_position_state (ticker text, market text);
    create table positions (id text, market text, symbol text, status text, legacy_holding_id text, entry_intent_id text);
    create table us_trading_history (ticker text);
    insert into us_stock_holdings values (124, 'prod:1:01', 'BKH', 'Black Hills Corporation', 76.92, 86.7, 91.884, 82.68,
        '{"highest_price": 87.56}');
    insert into us_holding_decisions values (1, 'BKH');
    insert into loop_a_position_state values ('BKH', 'US'), ('BKH', 'KR');
    insert into positions values ('legacy:US:124', 'US', 'BKH', 'OPEN', '124', 'intent-1'), ('old', 'US', 'BKH', 'CLOSED', '99', 'x'),
        ('legacy:US:142', 'US', 'LLY', 'OPEN', '142', null);   -- legacy_holding_id is stored as text in production
    insert into us_stock_holdings values (142, 'prod:1:01', 'LLY', 'Eli Lilly', 1459.21, 1427.84, 1750.0, 1400.0, '{}');
    insert into us_trading_history values ('BKH');
    """)
    conn.commit()
    conn.close()
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "core_hold_us.json").write_text(json.dumps({"BKH": {"stage": 2}, "note": "BKH"}))
    (rt / "us_exchange_cache.json").write_text(json.dumps({"BKH": "NYSE"}))
    return db, rt


def test_apply_rename_touches_live_tables_and_state_files_but_not_history(tmp_path):
    db, rt = _db(tmp_path)
    changed = HR.apply_rename("US", "BKH", "HZN", db_path=db, runtime_dir=rt, name="Bright Horizon Energy Corporation")
    assert changed == {"us_stock_holdings": 1, "us_holding_decisions": 1, "loop_a_position_state": 1, "positions": 1,
                       "core_hold_us.json": 2}
    conn = sqlite3.connect(db)
    assert conn.execute("select ticker, company_name, buy_price from us_stock_holdings where id = 124").fetchone() == \
        ("HZN", "Bright Horizon Energy Corporation", 76.92)
    assert conn.execute("select ticker from loop_a_position_state where market='KR'").fetchone() == ("BKH",)
    assert conn.execute("select symbol from positions where status='CLOSED'").fetchone() == ("BKH",)
    assert conn.execute("select ticker from us_trading_history").fetchone() == ("BKH",)
    assert json.loads((rt / "core_hold_us.json").read_text()) == {"HZN": {"stage": 2}, "note": "HZN"}   # every quoted token


def test_apply_rename_with_a_ratio_scales_the_price_columns_and_records_the_action(tmp_path):
    db, rt = _db(tmp_path)
    HR.apply_rename("US", "BKH", "HZN", db_path=db, runtime_dir=rt, ratio=0.98)
    row = sqlite3.connect(db).execute("select buy_price, current_price, target_price, stop_loss, scenario from us_stock_holdings").fetchone()
    assert row[:4] == pytest.approx((76.92 * 0.98, 86.7 * 0.98, 91.884 * 0.98, 82.68 * 0.98))
    scenario = json.loads(row[4])
    assert scenario["highest_price"] == pytest.approx(87.56 * 0.98) and scenario["corporate_action"]["ratio"] == 0.98


def test_dry_run_changes_nothing(tmp_path):
    db, rt = _db(tmp_path)
    changed = HR.apply_rename("US", "BKH", "HZN", db_path=db, runtime_dir=rt, dry_run=True)
    assert changed["us_stock_holdings"] == 1
    assert sqlite3.connect(db).execute("select ticker from us_stock_holdings where id = 124").fetchone() == ("BKH",)


class _Service:
    account_key = "prod:1:01"

    def __init__(self, rows, quotes=None):
        self.rows, self.quotes = rows, quotes or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def get_portfolio(self, strict=False):
        return self.rows

    def get_current_price(self, ticker):
        return self.quotes.get(ticker)


def test_run_applies_the_one_to_one_rename_and_alerts_once_per_day(tmp_path):
    db, rt = _db(tmp_path)
    sent = []
    account = {"name": "legacy-real-stock", "account_key": "prod:1:01"}
    def factory(market, name):
        return _Service([{"ticker": "HZN", "quantity": 10, "avg_price": 76.9, "stock_name": "BRIGHT HORIZON"}])
    now = datetime(2026, 11, 18, 9, 50, tzinfo=ZoneInfo("America/New_York"))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"snapshot|US|prod:1:01": {"date": "2026-11-17", "quantities": {"BKH": 10}}}))
    report, alerts = asyncio.run(HR.run("US", db_path=db, runtime_dir=rt, state_path=state,
                                        accounts=[account], service_factory=factory, now=now, sender=sent.append))
    assert len(alerts) == 1 and "종목코드 변경(1:1) 자동 반영: BKH → HZN" in alerts[0] and "10→10주" in alerts[0] and "반영:" in alerts[0]
    assert json.loads(state.read_text())["snapshot|US|prod:1:01"]["quantities"] == {"HZN": 10.0}
    assert sqlite3.connect(db).execute("select ticker from us_stock_holdings where id = 124").fetchone() == ("HZN",)
    assert sent and sent[0].startswith("[주의] 보유 종목 대조")
    # second run the same day: the ledger now matches the broker, nothing to report
    report, alerts = asyncio.run(HR.run("US", db_path=db, runtime_dir=rt, state_path=tmp_path / "state.json",
                                        accounts=[account], service_factory=factory, now=now, sender=sent.append))
    assert report == [] and alerts == []


def test_simulator_only_holdings_are_not_compared_with_the_broker(tmp_path):
    db, rt = _db(tmp_path)
    ledger = HR.ledger_view("US", "prod:1:01", db)
    assert set(ledger) == {"BKH"}          # LLY: open position without an entry intent (budget below one share)
    assert HR.classify(ledger, {"BKH": {"avg_price": 76.9, "quantity": 10}}, previous={"BKH": 10}) == []


def test_run_keeps_a_failed_balance_inquiry_as_missing(tmp_path):
    db, rt = _db(tmp_path)

    class Broken(_Service):
        def get_portfolio(self, strict=False):
            raise RuntimeError("EGW00201")

    account = {"name": "legacy-real-stock", "account_key": "prod:1:01"}
    sent = []
    report, alerts = asyncio.run(HR.run("US", db_path=db, runtime_dir=rt, state_path=tmp_path / "state.json",
                                        accounts=[account], service_factory=lambda m, n: Broken([]), sender=sent.append,
                                        now=datetime(2026, 11, 18, 9, 50, tzinfo=ZoneInfo("America/New_York"))))
    assert len(report) == 1 and "증권사 잔고 조회 실패" in report[0]
    assert sqlite3.connect(db).execute("select ticker from us_stock_holdings where id = 124").fetchone() == ("BKH",)   # not GONE
