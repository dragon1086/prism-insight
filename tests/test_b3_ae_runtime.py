"""B3 all-entries SHADOW runtime: entry/exit capture and the per-market worker."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from observability import b3_ae_capture as capture
from prism_core.b3_ae_shadow import B3AeShadowStore
from prism_core.b3_ae_worker import B3AeWorker
from prism_core.oneil_intraday_inputs import build_intraday_inputs

SEOUL = ZoneInfo("Asia/Seoul")
ENTERED = datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL)


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def _agent():
    bars, day = [], date(2026, 9, 1)
    while day < date(2026, 10, 2):
        if day.weekday() < 5:
            bars.append(dict(date=day.isoformat(), open=9900.0, high=10100.0, low=9800.0, close=10000.0,
                             volume=1000.0))
        day += timedelta(days=1)
    bars.append(dict(date="2026-10-02", open=10000.0, high=10500.0, low=9900.0, close=10000.0, volume=10.0))
    return SimpleNamespace(_decision_input_bars={"005930": dict(
        market="KR", bars=bars, captured_at=_iso(ENTERED - timedelta(minutes=10)))})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("B3_AE_SHADOW_ENABLED", "true")
    monkeypatch.setenv("B3_AE_SHADOW_DB", str(tmp_path / "b3.sqlite"))
    db = tmp_path / "holdings.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, "
                 "scenario TEXT, stop_loss REAL)")
    conn.execute("CREATE TABLE trading_history (ticker TEXT, account_key TEXT, buy_date TEXT, sell_date TEXT, "
                 "sell_price REAL, exit_kind TEXT)")
    conn.execute("INSERT INTO stock_holdings VALUES (7, '005930', 'acc', ?, 9300)",
                 (json.dumps({"_decision_id": "report:x.pdf", "sector": "IT"}),))
    conn.commit()
    conn.close()
    return tmp_path, db


def _open():
    return capture.capture_entry(_agent(), market="KR", ticker="005930", account_key="acc",
                                 position_id="legacy:KR:7", entry_price=10000, stop_loss=9300,
                                 decision_ref="report:x.pdf", entered_at=_iso(ENTERED))


def test_atr14_uses_completed_sessions_before_the_entry_day():
    atr, last = capture.atr14(_agent()._decision_input_bars["005930"]["bars"], date(2026, 10, 2))
    assert atr == 300 and last == "2026-10-01"  # today's 600-wide bar is excluded


def test_entry_capture_opens_campaign_and_is_off_by_default(env, monkeypatch):
    state = _open()
    assert state["market"] == "KR" and state["legs"][0]["allocation"] == "0.7777"
    assert state["plan"]["source_decision_ref"] == "report:x.pdf"
    monkeypatch.setenv("B3_AE_SHADOW_ENABLED", "false")
    assert _open() is None


def test_entry_capture_never_raises_without_decision_bars(env):
    assert capture.capture_entry(SimpleNamespace(), market="KR", ticker="005930", account_key="acc",
                                 position_id="legacy:KR:8", entry_price=10000, stop_loss=9300,
                                 decision_ref="r") is None


def _kr_intraday(now, closes):
    day = now.astimezone(SEOUL).date()
    sessions, prior, cursor = [], {}, day
    while len(sessions) < 21:
        if cursor.weekday() < 5:
            opened = datetime(cursor.year, cursor.month, cursor.day, 9, tzinfo=SEOUL)
            sessions.insert(0, dict(trade_date=cursor.isoformat(), open_at=_iso(opened),
                                    close_at=_iso(opened + timedelta(minutes=390))))
        cursor -= timedelta(days=1)
    prior = {row["trade_date"]: 9000 + 20 * index for index, row in enumerate(sessions)}  # rising trend
    opened = datetime(day.year, day.month, day.day, 9, tzinfo=SEOUL)
    count = int((now - opened).total_seconds() // 300)
    bars = [dict(provider_timestamp=_iso(opened + timedelta(minutes=5 * i)), open=closes[-1], high=closes[-1] + 50,
                 low=9900, close=closes[min(i, len(closes) - 1)] if i >= count - 2 else 10000, volume=10,
                 dividends=0, stock_splits=0) for i in range(count)]
    for index, close in enumerate(closes):
        bars[count - len(closes) + index]["close"] = close
    return lambda symbol, as_of, started: build_intraday_inputs(
        symbol=symbol, bars=bars, calendar=dict(calendar_ref="XKRX", sessions=sessions), as_of=as_of,
        retrieved_at=started, retrieval_started_at=started, price_basis_ref="kis-domestic-raw-krw",
        source_ref="fake", kind="LIVE_CAPTURE", volume_required=False, market="KR",
        prior_session_closes=prior)


def _worker(db, store, now, *, closes=(10450, 10460), price=10460, pulse="UPTREND", session=True,
            market_at=None):
    def gates(**kw):
        # Same freshness rule as the real current_gates: no snapshot from the future.
        if datetime.fromisoformat(kw["market"]["observed_at"]) > datetime.fromisoformat(kw["now"]):
            raise ValueError("CURRENT_SNAPSHOT_UNAVAILABLE")
        return dict(observed_at=kw["now"], source_ref="g", admission=True, risk=True, RR=True,
                    sector=True, slot=True, market_pulse=pulse, regime="moderate_bull")
    import prism_core.oneil_runtime_inputs as inputs
    inputs.current_gates = gates
    providers = dict(session_open=lambda moment: session, intraday=_kr_intraday(now, closes),
                     quote=lambda plan, position_id, at: dict(price=str(price), observed_at=at, source_ref="q"),
                     market=lambda: dict(observed_at=now.isoformat(), source_ref="m"))
    if market_at:  # the market snapshot takes time: it advances the shared clock
        moment = [now]

        def market():
            moment[0] = moment[0] + timedelta(seconds=2)
            return dict(observed_at=moment[0].isoformat(), source_ref="m")
        providers["market"] = market
        return B3AeWorker("KR", store=store, holdings_db=db, providers=providers,
                          clock=lambda: moment[0].isoformat())
    return B3AeWorker("KR", store=store, holdings_db=db, providers=providers, clock=lambda: now.isoformat())


@pytest.fixture
def restore_gates():
    import prism_core.oneil_runtime_inputs as inputs
    original = inputs.current_gates
    yield
    inputs.current_gates = original


def test_worker_adds_after_a_completed_boundary_once(env, restore_gates):
    _, db = env
    state = _open()
    store = B3AeShadowStore(capture.store_path())
    now = datetime(2026, 10, 6, 10, 0, 30, tzinfo=SEOUL).astimezone(timezone.utc)
    result = _worker(db, store, now).once()
    assert result["orders_submitted"] == 0
    assert result["rows"][0]["status"] == "ADD", result["rows"]
    legs = store.snapshot(state["campaign_id"])["legs"]
    assert [leg["kind"] for leg in legs] == ["INITIAL", "ADD"]
    assert _worker(db, store, now).once()["rows"][0]["status"] == "WAIT"  # same bar never twice


@pytest.mark.parametrize("kwargs, second, expected", [
    (dict(pulse="UNDER_PRESSURE"), 30, "WAIT"),
    (dict(), 200, None),  # between boundaries: no evaluation row
    (dict(session=False), 30, None),
])
def test_worker_waits_or_skips(env, restore_gates, kwargs, second, expected):
    _, db = env
    _open()
    store = B3AeShadowStore(capture.store_path())
    now = datetime(2026, 10, 6, 10, 0, tzinfo=SEOUL).astimezone(timezone.utc) + timedelta(seconds=second)
    rows = _worker(db, store, now, **kwargs).once()["rows"]
    assert (rows[0]["status"] if rows else None) == expected


def test_exit_hook_and_reconcile_close_with_the_original_exit(env, restore_gates):
    _, db = env
    state = _open()
    store = B3AeShadowStore(capture.store_path())
    capture.capture_exit(market="KR", account_key="acc", position_ids=["legacy:KR:7"], exit_price=9200,
                         exit_at=_iso(ENTERED + timedelta(days=1)), reason="TIER1_STOPLOSS")
    closed = store.snapshot(state["campaign_id"])
    assert closed["status"] == "CLOSED" and Decimal(closed["returns"]["baseline"]) == Decimal("-0.08")
    assert Decimal(closed["returns"]["b3"]) == Decimal("0.7777") * Decimal("-0.08")

    # A second campaign whose exit hook was missed is reconciled from the strategy history.
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM stock_holdings")
    conn.execute("INSERT INTO trading_history VALUES ('005930','acc',?,?,10800,'trend_exit')",
                 ((ENTERED + timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S"),
                  (ENTERED + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    second = capture.capture_entry(_agent(), market="KR", ticker="005930", account_key="acc",
                                   position_id="legacy:KR:9", entry_price=10000, stop_loss=9300,
                                   decision_ref="report:x.pdf", entered_at=_iso(ENTERED))
    now = datetime(2026, 10, 6, 10, 0, 30, tzinfo=SEOUL).astimezone(timezone.utc)
    result = _worker(db, store, now).once()
    assert result["rows"] == [dict(campaign_id=second["campaign_id"], status="CLOSED")]
    reconciled = store.snapshot(second["campaign_id"])
    assert Decimal(reconciled["exit"]["price"]) == 10800 and reconciled["exit"]["reason"] == "RECONCILED:trend_exit"


def test_worker_stamps_the_decision_after_the_market_snapshot(env, restore_gates):
    # 2026-10-02 live smoke: the KR snapshot was observed after the decision clock.
    _, db = env
    state = _open()
    store = B3AeShadowStore(capture.store_path())
    now = datetime(2026, 10, 6, 10, 0, 30, tzinfo=SEOUL).astimezone(timezone.utc)
    rows = _worker(db, store, now, market_at=True).once()["rows"]
    assert rows[0]["status"] == "ADD", rows
    assert store.snapshot(state["campaign_id"])["legs"][-1]["kind"] == "ADD"


def test_fixed_ladder_never_orders_for_live_campaigns(env, restore_gates, monkeypatch):
    # 2026-10-02: the +2%/+4% ladder stays a virtual (SHADOW) ledger; LIVE adds come from add_plan only.
    _, db = env
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    state = capture.capture_entry(_agent(), market="KR", ticker="005930", account_key="acc",
                                  position_id="legacy:KR:7", entry_price=10000, stop_loss=9300,
                                  decision_ref="report:x.pdf", entered_at=_iso(ENTERED), mode="LIVE")
    assert state["mode"] == "LIVE"
    store = B3AeShadowStore(capture.store_path())
    now = datetime(2026, 10, 6, 10, 0, 30, tzinfo=SEOUL).astimezone(timezone.utc)
    worker = _worker(db, store, now)
    calls = []
    worker.providers["live_add"] = lambda campaign, decision, at: calls.append((campaign, decision)) or dict(
        status="EXECUTED", allocation=0.8, cash=1, broker={"success": True})
    result = worker.once()
    assert result["rows"][0]["status"] == "ADD" and result["rows"][0]["mode"] == "LIVE"
    assert "live" not in result["rows"][0] and result["orders_submitted"] == 0 and calls == []
    assert store.snapshot(state["campaign_id"])["legs"][-1]["kind"] == "ADD"  # virtual leg only
