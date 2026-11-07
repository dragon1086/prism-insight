"""#288 pyramid rows for micro-split holdings (PYRAMID_ROWS_MICRO_SPLIT_ENABLED, user decision 2026-11-08).

A held micro-split winner may open independent rows (max 3), each its own micro-split position.
Flag off: identical to main (micro-split rows blocked from #288).
"""
import asyncio
import importlib.util
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from prism_core import loop_row_split as L
from prism_core import micro_split_live as live
from prism_core import pyramid_ledger as PL

ROOT = Path(__file__).resolve().parents[1]
SEOUL = ZoneInfo("Asia/Seoul")
ENTERED = datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL)


def load(path):
    spec = importlib.util.spec_from_file_location("isolated_" + path.replace("/", "_"), ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KR = load("tracking/helpers.py")
US = load("prism-us/tracking/db_schema.py")
GATES = [("KR", KR.evaluate_pyramid_add_gate), ("US", US.evaluate_us_pyramid_add_gate)]


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "true")
    monkeypatch.delenv("PYRAMID_RESERVE_SLOTS", raising=False)
    monkeypatch.delenv("PYRAMID_RESERVE_BELOW_TICKERS", raising=False)


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.delenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", raising=False)


def _row(initial="0.35", price=10000, adds=(), unit=1_000_000, market="KR", decision=None):
    block = live.entry_record(plan={"initial_nominal": initial, "policy_version": "v3", "plan_hash": "h",
                                    "entry_reference": price}, unit_amount=unit, market=market, entered_at="t0")
    scenario = {"stop_loss": price * 0.93, "sector": "IT", "micro_split": block}
    for i, (delta, add_price) in enumerate(adds):
        scenario, _ = live.apply_add(scenario, delta=delta, price=add_price, at=f"t{i + 1}", bar_end=f"b{i}")
    if decision:
        scenario["_decision_id"] = decision
    return scenario


# ── Gate ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("market,gate", GATES)
def test_flag_off_keeps_the_exact_old_block(flag_off, market, gate):
    assert gate("strong_bull", 10000, 11000, 1, ownership="MICRO_SPLIT") == (
        False, "LEGACY_PYRAMID_BLOCKED_MICRO_SPLIT")


@pytest.mark.parametrize("market,gate", GATES)
def test_flag_on_micro_split_winner_in_strong_bull_may_add_a_row(flag_on, market, gate):
    ok, reason = gate("strong_bull", 10000, 10500, 1, ownership="MICRO_SPLIT")
    assert ok and "add allowed" in reason
    assert gate("parabolic", 10000, 10500, 2, ownership="MICRO_SPLIT")[0]
    assert not gate("strong_bull", 10000, 10499, 1, ownership="MICRO_SPLIT")[0]          # below +5%
    assert gate("moderate_bull", 10000, 11000, 1, ownership="MICRO_SPLIT")[1].startswith("regime")
    assert gate("strong_bull", 10000, 11000, 3, ownership="MICRO_SPLIT")[1].startswith("row count")
    # Other owners stay blocked even with the flag on.
    assert gate("strong_bull", 10000, 11000, 1, ownership="SPLIT_PILOT")[1] == "LEGACY_PYRAMID_BLOCKED_SPLIT_PILOT"
    assert gate("strong_bull", 10000, 11000, 1, ownership="UNKNOWN")[1] == "LEGACY_PYRAMID_OWNERSHIP_UNKNOWN"


@pytest.mark.parametrize("market,module,getter,table", [
    ("KR", KR, KR.get_existing_position_for_ticker, "stock_holdings"),
    ("US", US, US.get_us_existing_position_for_ticker, "us_stock_holdings")])
def test_profit_uses_the_cost_weighted_entry_of_every_leg(flag_on, monkeypatch, market, module, getter, table):
    # Row buy_price is the first leg (10,000); an add at 10,800 lifts the real entry to ~10,316.
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE {table} (ticker TEXT, account_key TEXT, buy_price REAL, scenario TEXT)")
    db.execute(f"INSERT INTO {table} VALUES ('ABC','acc',10000,?)",
               (json.dumps(_row(adds=[("0.25", 10800)], market=market)),))
    summary = getter(db.cursor(), "ABC", account_key="acc")
    assert summary["avg_buy_price"] == pytest.approx(0.60 / (0.35 / 10000 + 0.25 / 10800))
    gate = GATES[0][1] if market == "KR" else GATES[1][1]
    # Rejected counterexample: +6% on the first-leg price is only +2.7% on the real entry.
    assert gate("strong_bull", summary["avg_buy_price"], 10600, 1, ownership=summary["pyramid_ownership"])[1] \
        .startswith("profit")
    # Valid example: +5.7% on the real entry.
    assert gate("strong_bull", summary["avg_buy_price"], 10900, 1, ownership=summary["pyramid_ownership"])[0]
    monkeypatch.setenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "false")  # flag off: the old simple mean
    assert getter(db.cursor(), "ABC", account_key="acc")["avg_buy_price"] == 10000


def test_weighted_entry_mixes_legacy_and_micro_rows_and_fails_closed():
    rows = [(10000, json.dumps({"sector": "IT"})), (11000, json.dumps(_row(initial="0.5", price=11000)))]
    assert live.rows_cost_weighted_entry(rows) == pytest.approx(1.5 / (1 / 10000 + 0.5 / 11000))
    with pytest.raises(Exception):
        live.rows_cost_weighted_entry([(0, "{}")])


# ── Reserve slots for new names (rows) ─────────────────────────────────────────────────────────

def test_reserve_blocks_below_eight_tickers_and_lifts_at_eight(flag_on, monkeypatch):
    seven = [f"T{i}" for i in range(7)]                       # 7 names, 7 rows -> 2 free after the add
    assert live.pyramid_reserve_check(seven, 10)[0]
    eight_rows = seven + ["T0"]                                # 7 names, 8 rows -> 1 free after the add
    ok, reason = live.pyramid_reserve_check(eight_rows, 10)
    assert not ok and reason.startswith("PYRAMID_RESERVE_SLOTS")
    eight_names = [f"T{i}" for i in range(8)]                  # 8 names -> reserve 0, one slot left
    assert live.pyramid_reserve_check(eight_names, 10)[0]
    assert live.pyramid_reserve_check(eight_names + ["T0"], 10)[0]  # last free row
    ok, reason = live.pyramid_reserve_check(eight_names + ["T0", "T1"], 10)  # MAX_SLOTS still holds
    assert not ok and reason.startswith("PYRAMID_MAX_SLOTS")
    monkeypatch.setenv("PYRAMID_RESERVE_SLOTS", "0")           # R=0: only MAX_SLOTS
    assert live.pyramid_reserve_check(eight_rows, 10)[0]
    monkeypatch.setenv("PYRAMID_RESERVE_SLOTS", "two")         # unreadable -> fail closed
    assert live.pyramid_reserve_check(seven, 10) == (False, "PYRAMID_RESERVE_SLOTS:UNKNOWN")


def _agent(rows, market="KR", max_slots=10):
    table = "stock_holdings" if market == "KR" else "us_stock_holdings"
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, scenario TEXT)")
    for row_id, ticker, scenario in rows:
        db.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", (row_id, ticker, "acc", json.dumps(scenario)))
    return SimpleNamespace(cursor=db.cursor(), max_slots=max_slots, _account_scope=lambda: ("acc", "primary"))


def test_pyramid_row_must_be_micro_split_and_pass_the_reserve(flag_on):
    agent = _agent([(1, "ABC", _row())])
    assert live.pyramid_row_check(agent, market="KR", ticker="ABC", plan=None) == (
        False, "PYRAMID_ROW_NO_MICRO_PLAN")   # never a full-slot pyramid row while the flag is on
    assert live.pyramid_row_check(agent, market="KR", ticker="ABC", plan={"plan_hash": "h"})[0]
    broken = SimpleNamespace(cursor=None, max_slots=10, _account_scope=lambda: ("acc", ""))
    assert live.pyramid_row_check(broken, market="KR", ticker="ABC", plan={})[1] == "PYRAMID_RESERVE_SLOTS:UNKNOWN"


# ── New row sizing and in-slot adds ────────────────────────────────────────────────────────────

class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return ENTERED.astimezone(tz) if tz else ENTERED.replace(tzinfo=None)


def _bars_agent(ticker="005930"):
    bars, day = [], date(2026, 9, 1)
    while day < date(2026, 10, 2):
        if day.weekday() < 5:
            bars.append(dict(date=day.isoformat(), open=9900.0, high=10100.0, low=9800.0, close=10000.0,
                             volume=1000.0))
        day += timedelta(days=1)
    captured = (ENTERED - timedelta(minutes=10)).astimezone(timezone.utc).isoformat()
    return SimpleNamespace(_decision_input_bars={ticker: dict(market="KR", bars=bars, captured_at=captured)})


def test_new_pyramid_row_is_micro_split_sized_with_its_own_add_plan(flag_on, monkeypatch):
    import observability.b3_ae_capture as capture
    monkeypatch.setattr(capture, "datetime", _FrozenDateTime)
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    marker = {"entry_no": 2, "parent_row_ids": [1], "origin_trace_id": "t1", "parent_trace_ids": ["t1"]}
    plan, cash, scenario = live.prepare_entry(
        _bars_agent(), market="KR", ticker="005930", current_price=10000,
        scenario={"stop_loss": 9300, "sector": "IT", "buy_score": 9}, decision_ref="report:x.pdf",
        account={"buy_amount_krw": 1_000_000}, trigger_type=None, pyramid_row=marker)
    block = scenario["micro_split"]
    assert plan is not None and 0.30 <= float(block["allocation"]) <= 0.80 and cash < 1_000_000
    assert cash == live.scaled_cash(1_000_000, plan["initial_nominal"], "KR")  # B3 plan, no tilt
    assert block["pyramid_row"] == marker and "add_plan_history" in block  # its own plan record
    assert live.pyramid_signal_fields(scenario) == {"entry_kind": "PYRAMID_ADD", "pyramid_entry_no": 2}
    assert live.dashboard_fields(scenario)["pyramid_entry_no"] == 2
    from prism_core.buy_message import render_buy_message
    text = render_buy_message(market="KR", company_name="삼성", ticker="005930", current_price=10000,
                              scenario=scenario, add_entry=(2, 10100))
    assert f"피라미딩 독립 행 · 이번 행 비중 {round(float(block['allocation']) * 100)}% (1슬롯 기준)" in text


def test_pyramid_row_never_inherits_reentry_sizing(flag_on, monkeypatch):
    # Coordination with #1306 (re-entry first size x REENTRY_V3_FIRST_SIZE_FACTOR when scenario.reentry.version).
    import observability.b3_ae_capture as capture
    monkeypatch.setattr(capture, "datetime", _FrozenDateTime)
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.setenv("REENTRY_V3_FIRST_SIZE_FACTOR", "0.5")
    base = {"stop_loss": 9300, "sector": "IT", "buy_score": 9}
    kwargs = dict(market="KR", ticker="005930", current_price=10000, decision_ref="report:x.pdf",
                  account={"buy_amount_krw": 1_000_000}, trigger_type=None)
    _, normal_cash, _ = live.prepare_entry(_bars_agent(), scenario=dict(base), **kwargs)
    reentry = dict(base, reentry={"version": "v3", "origin_decision_id": "report:old.pdf"})
    _, cash, scenario = live.prepare_entry(_bars_agent(), scenario=reentry, pyramid_row={"entry_no": 2}, **kwargs)
    assert cash == normal_cash and "reentry" not in scenario
    assert "reentry_size" not in scenario["micro_split"]
    assert "reentry" in reentry  # the caller's scenario is not mutated
    _, halved, plain = live.prepare_entry(_bars_agent(), scenario=dict(reentry), **kwargs)  # #1306 still applies
    assert halved < normal_cash and plain["micro_split"]["reentry_size"]["factor"] == "0.5"


class _FakeTrading:
    calls = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute_buy(self, **kwargs):
        _FakeTrading.calls.append(kwargs)
        return {"success": True, "status": "submitted"}


def test_in_slot_add_targets_the_pyramid_row_not_the_ticker(flag_on, monkeypatch, tmp_path):
    from observability import events
    from prism_core import execution_service
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    first, pyramid = _row(), _row(initial="0.4", price=10800)
    for scenario in (first, pyramid):
        scenario["micro_split"]["add_plan"] = {"plan_hash": "ph", "status": "ACTIVE", "valid_for": "2026-10-05"}
    conn = sqlite3.connect(tmp_path / "h.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "account_key TEXT, scenario TEXT, buy_price REAL, stop_loss REAL)")
    conn.execute("INSERT INTO stock_holdings VALUES (3,'ABC','Abc','acc',?,10000,9300)", (json.dumps(first),))
    conn.execute("INSERT INTO stock_holdings VALUES (7,'ABC','Abc','acc',?,10800,10044)", (json.dumps(pyramid),))
    conn.commit()
    agent = SimpleNamespace(conn=conn, cursor=conn.cursor(), db_path=str(tmp_path / "h.sqlite"),
                            account_configs=[{"account_key": "acc", "name": "primary"}],
                            _set_active_account=lambda account: None, message_queue=[], _msg_types=[])
    monkeypatch.setattr(events, "emit_event", lambda *a, **k: None)
    monkeypatch.setattr(execution_service.ExecutionService, "domestic", _FakeTrading, raising=False)
    meta = {"plan_hash": "ph", "scenario_id": "breakout_1", "session": "2026-10-05", "trigger_price": 11000}
    result = asyncio.run(live.execute_add(
        agent, market="KR", campaign={"position_id": "legacy:KR:7", "account_key": "acc", "symbol": "ABC",
                                      "campaign_id": "c7"},
        decision={"target_allocation": "0.6", "price": 11000, "bar_end": "add-plan:2026-10-05:breakout_1",
                  "add_plan": meta}, now="t1"))
    assert result["status"] == "EXECUTED"
    stored = {r["id"]: json.loads(r["scenario"])["micro_split"]["allocation"]
              for r in conn.execute("SELECT id, scenario FROM stock_holdings")}
    assert stored == {3: "0.35", 7: "0.6"}  # the first row is untouched; the row cap stays 1.00
    with pytest.raises(ValueError, match="exceed one slot"):
        live.apply_add(_row(initial="0.8"), delta="0.25", price=11000, at="t", bar_end="x")


# ── Sector cap ─────────────────────────────────────────────────────────────────────────────────

def test_kr_sector_cap_counts_names_not_pyramid_rows(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE stock_holdings (ticker TEXT, account_key TEXT, scenario TEXT)")
    for ticker in ("A", "A", "A", "B"):
        db.execute("INSERT INTO stock_holdings VALUES (?, 'acc', ?)", (ticker, json.dumps({"sector": "반도체"})))
    for ticker, sector in (("C", "은행"), ("D", "화학")):
        db.execute("INSERT INTO stock_holdings VALUES (?, 'acc', ?)", (ticker, json.dumps({"sector": sector})))
    monkeypatch.delenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", raising=False)
    assert not KR.check_sector_diversity(db.cursor(), "반도체", 3, .3, account_key="acc")   # flag off: rows (4)
    monkeypatch.setenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "true")
    assert KR.check_sector_diversity(db.cursor(), "반도체", 3, .6, account_key="acc")       # names A,B = 2 of 4
    db.execute("INSERT INTO stock_holdings VALUES ('E', 'acc', ?)", (json.dumps({"sector": "반도체"}),))
    assert not KR.check_sector_diversity(db.cursor(), "반도체", 3, .6, account_key="acc")   # 3 distinct names


def test_kr_pyramid_add_is_exempt_from_the_sector_cap_only_with_the_flag(monkeypatch):
    import os
    saved = os.environ.copy()
    try:
        import stock_tracking_agent as kr
    finally:  # the agent import loads .env defaults; keep other tests' environment unchanged
        os.environ.clear()
        os.environ.update(saved)
    agent = SimpleNamespace(_account_scope=lambda: ("acc", ""), cursor=None, MAX_SAME_SECTOR=3,
                            SECTOR_CONCENTRATION_RATIO=.3)
    monkeypatch.setattr(kr, "check_sector_diversity", lambda *a, **k: False)
    check = kr.StockTrackingAgent._check_sector_diversity
    monkeypatch.setenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "true")
    assert asyncio.run(check(agent, "반도체", is_pyramiding_add=True)) is True
    assert asyncio.run(check(agent, "반도체")) is False                  # a new name is still capped
    monkeypatch.delenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED")
    assert asyncio.run(check(agent, "반도체", is_pyramiding_add=True)) is False


# ── Row exits sell only that row's shares ──────────────────────────────────────────────────────

def test_row_exit_sells_its_own_ledger_shares():
    first, pyramid = _row(initial="0.35"), _row(initial="0.4", price=10800, adds=[("0.2", 11000)])
    assert L.row_ledger_quantity(first) == 35                         # 350,000 / 10,000
    assert L.row_ledger_quantity(pyramid) == 37 + 18                  # 400,000/10,800 + 200,000/11,000
    assert L.row_sell_quantity(90, 2, pyramid, [first])[:2] == (55, "PYRAMID_ROW_LEDGER_QTY")
    assert L.row_sell_quantity(90, 2, first, [pyramid])[:2] == (35, "PYRAMID_ROW_LEDGER_QTY")
    # A rejected add order: the broker holds fewer shares; never take the other row's 35.
    assert L.row_sell_quantity(70, 2, pyramid, [first])[:2] == (35, "PYRAMID_ROW_LEDGER_QTY")
    assert L.row_sell_quantity(20, 1, first, []) == (20, "LAST_ROW_REMAINDER", 1, 1.0)   # last row sweeps
    # Recorded order price (US broker re-quote) sizes the leg instead of the leg price.
    requoted = _row(initial="0.35")
    requoted["micro_split"]["legs"][0]["order_price"] = "10500"
    assert L.row_ledger_quantity(requoted) == 33                      # 350,000 / 10,500


@pytest.mark.parametrize("own,other", [(772, 291), (291, 772), (500, 500), (1, 999), (999, 1)])
def test_subscriber_denominator_never_sells_another_rows_share(own, other):
    # Review 2026-11-08: round(1063/772) = 1 made a mirror sell row B too. ceil and >= 2 while rows remain.
    def row(qty):
        return _row(initial="0.5", price=10, unit=20 * qty)  # exactly qty shares
    available = own + other
    quantity, reason, denominator, fraction = L.row_sell_quantity(available, 2, row(own), [row(other)])
    assert quantity == own and denominator >= 2
    assert available // denominator <= own                           # a same-sized mirror never over-sells
    assert fraction == round(own / available, 4)


def test_zero_share_row_sell_is_never_published(monkeypatch):
    from messaging import gcp_pubsub_signal_publisher as gcp
    from messaging import redis_signal_publisher as redis_pub
    assert L.row_sell_quantity(100, 3, {"sector": "IT"}, [_row(), {"sector": "IT"}])[2:] == (0, 0.0)
    for module in (redis_pub, gcp):
        sent = []

        class Publisher:
            async def publish_signal(self, **kwargs):
                sent.append(kwargs)
                return "id"

        async def fake_publisher():
            return Publisher()
        monkeypatch.setattr(module, "get_signal_publisher", fake_publisher)
        args = dict(ticker="ABC", company_name="Abc", price=10, buy_price=9, profit_rate=1.0, sell_reason="stop")
        assert asyncio.run(module.publish_sell_signal(**args, sell_denominator=0, sell_fraction=0.0)) is None
        assert sent == []
        asyncio.run(module.publish_sell_signal(**args, sell_denominator=2, sell_fraction=0.7262))
        assert sent[-1]["extra_data"]["sell_fraction"] == 0.7262 and sent[-1]["extra_data"]["sell_denominator"] == 2
        asyncio.run(module.publish_sell_signal(**args))                 # legacy payload unchanged
        assert "sell_fraction" not in sent[-1]["extra_data"]


def test_unknown_row_quantity_fails_safe():
    legacy, micro, broken = {"sector": "IT"}, _row(), _row()
    broken["micro_split"]["legs"][0]["price"] = "x"
    assert L.row_sell_quantity(100, 2, legacy, [micro])[:2] == (65, "PYRAMID_ROW_QTY_RESIDUAL")
    assert L.row_sell_quantity(100, 3, legacy, [micro, {"sector": "IT"}])[:2] == (0, "PYRAMID_ROW_QTY_UNKNOWN")
    assert L.row_sell_quantity(100, 2, broken, [legacy])[:2] == (0, "PYRAMID_ROW_QTY_UNKNOWN")
    # Legacy-only rows keep floor(qty / N) (#288).
    assert L.row_sell_quantity(33, 3, legacy, [legacy, legacy]) == (11, "LEGACY_EQUAL_SPLIT", 3, None)


def test_loop_split_and_batch_read_scope_rows_by_id(flag_on):
    first, pyramid = _row(), _row(initial="0.4", price=10800)
    rows = [{"id": 3, "scenario": json.dumps(first)}, {"id": 7, "scenario": json.dumps(pyramid)}]
    split = L.new_split_state(2, rows)
    assert L.split_sell_quantity(split, 72, row_id=7) == 37 and split.pop("last_reason") == "PYRAMID_ROW_LEDGER_QTY"
    split["remaining"] -= 1
    assert L.split_sell_quantity(split, 72, row_id=3) == 35            # remainder of the snapshot
    legacy = L.new_split_state(3, [{"id": 1, "scenario": None}] * 3)
    assert "rows" not in legacy and L.split_sell_quantity(legacy, 33) == 11
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, scenario TEXT)")
    db.execute("INSERT INTO stock_holdings VALUES (3,'ABC','acc',?)", (json.dumps(first),))
    db.execute("INSERT INTO stock_holdings VALUES (7,'ABC','acc',?)", (json.dumps(pyramid),))
    db.execute("INSERT INTO stock_holdings VALUES (9,'ABC','other',?)", (json.dumps({"sector": "IT"}),))
    stock = {"id": 7, "ticker": "ABC", "account_key": "acc", "scenario": json.dumps(pyramid)}
    assert L.batch_row_sell_quantity(db.cursor(), "KR", stock, 72, 2)[:2] == (37, "PYRAMID_ROW_LEDGER_QTY")
    assert L.batch_row_sell_quantity(db.cursor(), "KR", dict(stock, account_key=None), 72, 2)[:2] == (
        0, "PYRAMID_ROW_QTY_UNKNOWN")


def test_batch_split_follows_the_rows_not_the_flag_and_never_raises(flag_off):
    first, pyramid = _row(), _row(initial="0.4", price=10800)
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, scenario TEXT)")
    db.execute("INSERT INTO stock_holdings VALUES (3,'ABC','acc',?)", (json.dumps(first),))
    legacy_row = json.dumps({"sector": "IT"})
    db.execute("INSERT INTO stock_holdings VALUES (5,'XYZ','acc',?)", (legacy_row,))
    db.execute("INSERT INTO stock_holdings VALUES (6,'XYZ','acc',?)", (legacy_row,))
    # Flag off, but a micro-split row is held: the ledger path (same as hardstop / trend-exit).
    stock = {"id": 7, "ticker": "ABC", "account_key": "acc", "scenario": json.dumps(pyramid)}
    assert L.batch_row_sell_quantity(db.cursor(), "KR", stock, 72, 2)[:2] == (37, "PYRAMID_ROW_LEDGER_QTY")
    legacy = {"id": 4, "ticker": "XYZ", "account_key": "acc", "scenario": legacy_row}
    assert L.batch_row_sell_quantity(db.cursor(), "KR", legacy, 33, 3) == (11, "LEGACY_EQUAL_SPLIT", 3, None)

    class Broken:
        def execute(self, *a):
            raise sqlite3.OperationalError("database is locked")
    assert L.batch_row_sell_quantity(Broken(), "KR", stock, 72, 2) == (0, "PYRAMID_ROW_QTY_UNKNOWN", 0, 0.0)
    assert L.batch_row_sell_quantity(Broken(), "KR", legacy, 33, 3) == (11, "LEGACY_EQUAL_SPLIT", 3, None)


def test_record_order_price_updates_only_the_first_leg(flag_on):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, scenario TEXT)")
    db.execute("INSERT INTO us_stock_holdings VALUES (7, ?)", (json.dumps(_row(market="US", price=100, unit=1000)),))
    db.execute("INSERT INTO us_stock_holdings VALUES (8, ?)", (json.dumps({"sector": "IT"}),))
    db.commit()
    agent = SimpleNamespace(conn=db, cursor=db.cursor())
    assert live.record_order_price(agent, market="US", row_id=7, price=101.25)
    leg = json.loads(db.execute("SELECT scenario FROM us_stock_holdings WHERE id=7").fetchone()[0])[
        "micro_split"]["legs"][0]
    assert leg["order_price"] == "101.25" and leg["price"] == "100"
    assert not live.record_order_price(agent, market="US", row_id=8, price=101.25)   # legacy row untouched
    assert not live.record_order_price(agent, market="US", row_id=99, price=101.25)  # missing row, no raise


@pytest.mark.parametrize("module,getter,table", [
    (KR, KR.get_existing_position_for_ticker, "stock_holdings"),
    (US, US.get_us_existing_position_for_ticker, "us_stock_holdings")])
def test_pilot_or_unknown_rows_still_block_with_the_flag_on(monkeypatch, module, getter, table):
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE {table} (ticker TEXT, account_key TEXT, buy_price REAL, scenario TEXT)")
    db.execute(f"INSERT INTO {table} VALUES ('ABC','acc',10000,?)", (json.dumps(_row()),))
    db.execute(f"INSERT INTO {table} VALUES ('ABC','acc',10000,?)",
               (json.dumps({"regime_entry_policy": {"mode": "rebound_pilot", "position_fraction": .5}}),))
    monkeypatch.setenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "true")
    assert getter(db.cursor(), "ABC", account_key="acc")["pyramid_ownership"] == "SPLIT_PILOT"
    db.execute(f"UPDATE {table} SET scenario='[1]' WHERE scenario LIKE '%rebound_pilot%'")
    assert getter(db.cursor(), "ABC", account_key="acc")["pyramid_ownership"] == "UNKNOWN"
    monkeypatch.setenv("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "false")  # flag off: unchanged precedence
    assert getter(db.cursor(), "ABC", account_key="acc")["pyramid_ownership"] == "MICRO_SPLIT"


# ── ClickStack ledger ──────────────────────────────────────────────────────────────────────────

def test_gate_row_and_slot_events_carry_inputs_and_lineage(flag_on, monkeypatch):
    from observability import events
    from prism_core.trace_ids import trade_trace
    sent = []
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: sent.append((name, kw)) or {"ok": 1})
    agent = _agent([(3, "ABC", _row(decision="report:abc1.pdf")), (4, "XYZ", {"sector": "IT"})])
    origin = trade_trace("KR", decision_id="report:abc1.pdf")
    PL.emit_gate(agent, market="KR", ticker="ABC", stage="gate", allowed=False,
                 reason="profit 2.70% < required 5.0%", existing={"avg_buy_price": 10316, "row_count": 1,
                                                                   "pyramid_ownership": "MICRO_SPLIT"},
                 current_price=10600, regime="strong_bull", decision_id="report:abc2.pdf")
    name, kw = sent[-1]
    attrs = kw["attributes"]
    assert name == "pyramid.gate_evaluated" and kw["trace_id"] == origin   # on the first row's trace
    assert attrs["reason_code"] == "PROFIT_BELOW_MIN" and attrs["profit_pct"] == pytest.approx(2.75, abs=.01)
    assert (attrs["ticker_rows"], attrs["rows_held"], attrs["distinct_tickers"], attrs["free_rows"],
            attrs["reserve_applied"], attrs["flag_enabled"], attrs["planned_allocation"]) == (
        1, 2, 2, 8, 2, True, None)
    PL.emit_gate(agent, market="KR", ticker="ABC", stage="pre_gate", allowed=False, reason="x", existing={},
                 current_price=None)
    assert sent[-1][1]["attributes"]["profit_pct"] is None and sent[-1][1]["attributes"]["regime"] is None

    marker = PL.lineage(agent, "KR", "ABC")
    assert marker == {"entry_no": 2, "parent_row_ids": [3], "origin_trace_id": origin, "parent_trace_ids": [origin]}
    new = _row(initial="0.4", decision="report:abc2.pdf")
    new["micro_split"]["pyramid_row"] = marker
    PL.emit_row_opened(agent, market="KR", ticker="ABC", position_id="legacy:KR:8", scenario=new)
    name, kw = sent[-1]
    assert name == "pyramid.row_opened" and kw["trace_id"] == trade_trace("KR", decision_id="report:abc2.pdf")
    assert kw["attributes"]["row_id"] == "8" and kw["attributes"]["row_number"] == 2
    assert kw["attributes"]["origin_trace_id"] == origin and kw["attributes"]["parent_row_ids"] == [3]
    assert kw["attributes"]["allocation"] == 0.4 and kw["attributes"]["stop_loss"] == 9300

    PL.emit_slot_blocked(agent, market="KR", ticker="NEW", score=7, price=5000, decision_id="report:new.pdf",
                         limit="MAX_SLOTS")
    name, kw = sent[-1]
    assert name == "entry.slot_blocked" and kw["attributes"]["pyramid_rows_held"] == 0
    assert kw["attributes"]["buy_score"] == 7 and kw["attributes"]["decision_price"] == 5000


def test_entry_context_of_a_pyramid_row_links_to_the_first_row(monkeypatch):
    from observability import trading_context
    sent = []
    monkeypatch.setattr(trading_context, "emit_event", lambda name, **kw: sent.append(kw) or {"ok": 1})
    scenario = _row(decision="report:abc2.pdf")
    scenario["micro_split"]["pyramid_row"] = {"entry_no": 2, "origin_trace_id": "o" * 32,
                                              "parent_trace_ids": ["o" * 32]}
    trading_context.emit_trading_context("entry.executed", market="KR", ticker="ABC", decision_id="report:abc2.pdf",
                                         scenario=scenario)
    assert sent[0]["attributes"]["origin_trace_id"] == "o" * 32
    assert sent[0]["attributes"]["parent_trace_ids"] == ["o" * 32]


def test_clickstack_trace_lists_linked_traces(monkeypatch, capsys):
    import tools.clickstack_query as cq
    calls = []

    def query(sql, params, *, local):
        calls.append(sql)
        if len(calls) == 1:
            return [{"kst": "2026-11-09 10:00:00", "event": "entry.executed",
                     "Body": json.dumps({"market": "KR", "ticker": "ABC", "attributes": {}})}]
        return [{"trace": "p" * 32, "first_kst": "2026-11-12 10:00:00", "event": "pyramid.row_opened"}]
    monkeypatch.setattr(cq, "run_query", query)
    cq.cmd_trace(SimpleNamespace(trace_id="o" * 32, local=False, raw=False, full=False))
    assert "origin_trace_id" in calls[1] and f"linked trace {'p' * 32}" in capsys.readouterr().out


@pytest.mark.parametrize("path", ["stock_tracking_enhanced_agent.py", "prism-us/us_stock_tracking_agent.py"])
def test_batch_paths_size_check_and_record_pyramid_rows(path):
    import ast
    calls = {}
    for node in ast.walk(ast.parse((ROOT / path).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.setdefault(node.func.attr, []).append(node)
    prepare = calls["prepare_entry"]
    assert len(prepare) == 1 and any(k.arg == "pyramid_row" for k in prepare[0].keywords)
    assert calls.get("pyramid_slot_check") and calls.get("emit_row_opened")
    # The free-row reserve runs before sizing, so a blocked row never records an add plan.
    assert calls["pyramid_slot_check"][0].lineno < prepare[0].lineno
    assert "sizing" in {k.value.value for c in calls["emit_gate"] for k in c.keywords if k.arg == "stage"}


def test_every_ledger_event_records_the_sizing_switches(monkeypatch):
    from observability.events import build_event
    for key, value in (("PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "true"), ("PYRAMID_RESERVE_SLOTS", "2"),
                       ("PYRAMID_RESERVE_BELOW_TICKERS", "8"), ("MICRO_SPLIT_LIVE_ENABLED", "true"),
                       ("MICRO_SPLIT_LIVE_ADDS_ENABLED", "true"), ("REENTRY_V3_FIRST_SIZE_FACTOR", "0.5"),
                       ("RUNNER_HOLD_ENABLED", "true")):
        monkeypatch.setenv(key, value)
    config = build_event("test", service="test")["config"]
    assert config["PYRAMID_RESERVE_BELOW_TICKERS"] == "8" and config["REENTRY_V3_FIRST_SIZE_FACTOR"] == "0.5"
    assert {"PYRAMID_ROWS_MICRO_SPLIT_ENABLED", "MICRO_SPLIT_LIVE_ADDS_ENABLED", "RUNNER_HOLD_ENABLED"} <= set(config)
