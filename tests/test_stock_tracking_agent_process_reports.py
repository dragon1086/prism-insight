import asyncio
import logging
import sqlite3
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import trading.domestic_stock_trading as domestic_trading
from stock_tracking_agent import StockTrackingAgent
from tracking.db_schema import TABLE_STOCK_HOLDINGS, TABLE_TRADING_HISTORY


class _FakeAsyncTradingContext:
    def __init__(self, account_name=None, **kwargs):
        self.account_name = account_name

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def async_buy_stock(self, stock_code, limit_price=None, buy_amount=None):
        # buy_amount mirrors the real DomesticStockTrading.async_buy_stock signature
        # (None = full size; set only under PULSE_PILOT_REEXPOSURE pilot sizing).
        return {
            "success": True,
            "message": f"bought for {self.account_name}",
            "partial_success": self.account_name == "kr-primary",
            "successful_accounts": ["kr-primary"],
            "failed_accounts": ["kr-secondary"],
        }

    async def async_sell_stock(self, stock_code, limit_price=None, quantity=None):
        return {
            "success": True,
            "message": f"sold for {self.account_name}",
        }


@pytest.mark.parametrize("pyramiding", [False, True], ids=["single", "pyramiding"])
def test_concurrent_sell_guard_allows_one_kr_order_and_publish(tmp_path, pyramiding):
    from prism_core.execution_service import ExecutionService
    from prism_core.positions import PositionStore

    db_path = tmp_path / "kr-concurrent-sell.sqlite"
    setup = sqlite3.connect(db_path)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute(TABLE_STOCK_HOLDINGS)
    setup.execute(TABLE_TRADING_HISTORY)
    target_row_id = setup.execute(
        """INSERT INTO stock_holdings
           (account_key, account_name, ticker, company_name, buy_price, buy_date)
           VALUES ('ACC1', 'primary', '005930', 'Samsung', 70000,
                   '2026-07-01 09:00:00')"""
    ).lastrowid
    if pyramiding:
        setup.execute(
            """INSERT INTO stock_holdings
               (account_key, account_name, ticker, company_name, buy_price, buy_date)
               VALUES ('ACC1', 'primary', '005930', 'Samsung', 68000,
                       '2026-06-15 09:00:00')"""
        )
    position_store = PositionStore(setup)
    position_store.ensure_schema()
    assert position_store.backfill_legacy_positions("KR")["inserted"] == (
        2 if pyramiding else 1
    )
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)

    effects = {"broker": 0, "publish": 0, "journal": 0}
    effects_lock = threading.Lock()

    class Broker:
        async def async_sell_stock(self, *args, **kwargs):
            with effects_lock:
                effects["broker"] += 1
            return {"success": True}

    stock = {
        "ticker": "005930",
        "company_name": "Samsung",
        "buy_price": 70000,
        "buy_date": "2026-07-01 09:00:00",
        "current_price": 71000,
        "account_key": "ACC1",
        "account_name": "primary",
        "id": target_row_id,
    }

    def run_competitor():
        conn = sqlite3.connect(db_path, timeout=1, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=1000")
        agent = StockTrackingAgent.__new__(StockTrackingAgent)
        agent.conn = conn
        agent.cursor = conn.cursor()
        agent.message_queue = []
        agent._msg_types = []
        agent._account_scope = lambda: ("ACC1", "primary")
        agent._get_trigger_win_rate = lambda _trigger: ""

        async def create_journal(**_kwargs):
            with effects_lock:
                effects["journal"] += 1
            return True

        agent._create_journal_entry = create_journal

        async def exercise():
            barrier.wait(timeout=5)
            sold = await agent.sell_stock(dict(stock), "concurrency regression")
            if sold:
                await ExecutionService(Broker()).execute_sell("005930", quantity=1)
                with effects_lock:
                    effects["publish"] += 1
            return sold, len(agent.message_queue)

        try:
            return asyncio.run(exercise())
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: run_competitor(), range(2)))

    verify = sqlite3.connect(db_path)
    history_count = verify.execute("SELECT COUNT(*) FROM trading_history").fetchone()[0]
    remaining_prices = verify.execute(
        "SELECT buy_price FROM stock_holdings ORDER BY id"
    ).fetchall()
    position_states = verify.execute(
        "SELECT legacy_holding_id, status FROM positions ORDER BY legacy_holding_id"
    ).fetchall()
    verify.close()

    assert sorted(sold for sold, _messages in results) == [False, True]
    assert sum(messages for _sold, messages in results) == 1
    assert history_count == 1
    assert remaining_prices == ([(68000.0,)] if pyramiding else [])
    assert position_states == (
        [(str(target_row_id), "CLOSED"), (str(target_row_id + 1), "OPEN")]
        if pyramiding
        else [(str(target_row_id), "CLOSED")]
    )
    assert effects == {"broker": 1, "publish": 1, "journal": 1}


@pytest.mark.asyncio
async def test_kr_buy_dual_writes_open_position():
    from prism_core.positions import PositionStore

    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.conn = sqlite3.connect(":memory:")
    agent.cursor = agent.conn.cursor()
    agent.cursor.execute(TABLE_STOCK_HOLDINGS)
    PositionStore(agent.cursor).ensure_schema()
    agent.conn.commit()
    agent.position_ledger_shadow_enabled = True
    agent.max_slots = 10
    agent.message_queue = []
    agent._msg_types = []
    agent._account_scope = lambda: ("ACC1", "primary")
    agent._get_trigger_win_rate = lambda _trigger: ""

    async def no_holding(_ticker):
        return False

    async def no_slots():
        return 0

    agent._is_ticker_in_holdings = no_holding
    agent._get_current_slots_count = no_slots

    assert await agent.buy_stock(
        "005930", "Samsung", 70000, {"sector": "Technology"}
    )
    legacy = agent.conn.execute(
        "SELECT id, account_key, ticker, buy_price, buy_date FROM stock_holdings"
    ).fetchone()
    mirror = agent.conn.execute(
        "SELECT legacy_holding_id, account_id, symbol, entry_price, opened_at, status "
        "FROM positions"
    ).fetchone()
    assert mirror == (
        str(legacy[0]),
        legacy[1],
        legacy[2],
        legacy[3],
        legacy[4],
        "OPEN",
    )


def test_kr_position_kill_switch_skips_shadow_write():
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.conn = sqlite3.connect(":memory:")
    agent.cursor = agent.conn.cursor()
    agent.position_ledger_shadow_enabled = False

    assert agent._mirror_position_open(
        legacy_holding_id=1,
        account_key="ACC1",
        account_name="primary",
        ticker="005930",
        entry_price=70000,
        opened_at="2026-07-18",
    )
    assert (
        agent.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='positions'"
        ).fetchone()
        is None
    )


def _install_signal_modules(monkeypatch, redis_calls, gcp_calls):
    redis_module = types.ModuleType("messaging.redis_signal_publisher")
    gcp_module = types.ModuleType("messaging.gcp_pubsub_signal_publisher")

    async def publish_buy_signal(**kwargs):
        redis_calls.append(kwargs)

    async def publish_sell_signal(**kwargs):
        redis_calls.append(kwargs)

    async def gcp_publish_buy_signal(**kwargs):
        gcp_calls.append(kwargs)

    async def gcp_publish_sell_signal(**kwargs):
        gcp_calls.append(kwargs)

    redis_module.publish_buy_signal = publish_buy_signal
    redis_module.publish_sell_signal = publish_sell_signal
    gcp_module.publish_buy_signal = gcp_publish_buy_signal
    gcp_module.publish_sell_signal = gcp_publish_sell_signal

    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_module)
    monkeypatch.setitem(sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_module)


@pytest.mark.asyncio
async def test_process_reports_analyzes_once_and_dedupes_signals(monkeypatch, caplog, tmp_path):
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(tmp_path / "stock_tracking.sqlite")
    agent.account_configs = [
        {"name": "kr-primary", "account_key": "vps:kr-primary:01"},
        {"name": "kr-secondary", "account_key": "vps:kr-secondary:01"},
    ]
    agent.active_account = None
    agent.max_slots = 10

    core_calls = []
    holdings_checks = []
    slot_checks = []
    sector_checks = []
    buy_calls = []
    redis_calls = []
    gcp_calls = []

    async def fake_core(report_path):
        core_calls.append(report_path)
        return {
            "success": True,
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "current_price": 70000,
            "scenario": {"buy_score": 8, "min_score": 7, "sector": "Technology"},
            "decision": "Enter",
            "sector": "Technology",
            "rank_change_msg": "Up",
            "rank_change_percentage": 12.0,
        }

    async def fake_update_holdings():
        return []

    async def fake_is_ticker_in_holdings(ticker):
        holdings_checks.append((agent.active_account["name"], ticker))
        return False

    async def fake_get_current_slots_count():
        slot_checks.append(agent.active_account["name"])
        return 0

    async def fake_check_sector_diversity(sector):
        sector_checks.append((agent.active_account["name"], sector))
        return True

    async def fake_buy_stock(ticker, company_name, current_price, scenario, rank_change_msg):
        buy_calls.append((agent.active_account["name"], ticker))
        return True

    agent._analyze_report_core = fake_core
    agent.update_holdings = fake_update_holdings
    agent._is_ticker_in_holdings = fake_is_ticker_in_holdings
    agent._get_current_slots_count = fake_get_current_slots_count
    agent._check_sector_diversity = fake_check_sector_diversity
    agent.buy_stock = fake_buy_stock

    monkeypatch.setattr(domestic_trading, "AsyncTradingContext", _FakeAsyncTradingContext)
    _install_signal_modules(monkeypatch, redis_calls, gcp_calls)

    caplog.set_level(logging.WARNING)

    buy_count, sell_count = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])

    assert buy_count == 2
    assert sell_count == 0
    assert core_calls == ["report-a.pdf"]
    assert holdings_checks == [("kr-primary", "005930"), ("kr-secondary", "005930")]
    assert slot_checks == ["kr-primary", "kr-secondary"]
    assert sector_checks == [("kr-primary", "Technology"), ("kr-secondary", "Technology")]
    assert buy_calls == [("kr-primary", "005930"), ("kr-secondary", "005930")]
    assert len(redis_calls) == 1
    assert len(gcp_calls) == 1
    assert "partial success" in caplog.text.lower()


@pytest.mark.asyncio
async def test_process_reports_returns_zero_for_empty_accounts(caplog):
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.account_configs = []
    agent.active_account = None
    agent.max_slots = 10

    caplog.set_level(logging.WARNING)

    buy_count, sell_count = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])

    assert (buy_count, sell_count) == (0, 0)
    assert "no accounts configured" in caplog.text.lower()


@pytest.mark.asyncio
async def test_process_reports_saves_watchlist_once_when_not_traded(monkeypatch):
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.account_configs = [
        {"name": "kr-primary", "account_key": "vps:kr-primary:01"},
        {"name": "kr-secondary", "account_key": "vps:kr-secondary:01"},
    ]
    agent.active_account = None
    agent.max_slots = 10

    watchlist_calls = []

    async def fake_core(report_path):
        return {
            "success": True,
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "current_price": 70000,
            "scenario": {"buy_score": 6, "min_score": 7, "sector": "Technology"},
            "decision": "Skip",
            "sector": "Technology",
            "rank_change_msg": "Flat",
        }

    async def fake_update_holdings():
        return []

    async def fake_is_ticker_in_holdings(ticker):
        return False

    async def fake_get_current_slots_count():
        return 0

    async def fake_check_sector_diversity(sector):
        return True

    async def fake_buy_stock(*args, **kwargs):
        raise AssertionError("buy_stock should not be called for non-entry decisions")

    async def fake_save_watchlist_item(**kwargs):
        watchlist_calls.append(kwargs)
        return True

    agent._analyze_report_core = fake_core
    agent.update_holdings = fake_update_holdings
    agent._is_ticker_in_holdings = fake_is_ticker_in_holdings
    agent._get_current_slots_count = fake_get_current_slots_count
    agent._check_sector_diversity = fake_check_sector_diversity
    agent.buy_stock = fake_buy_stock
    agent._save_watchlist_item = fake_save_watchlist_item

    buy_count, sell_count = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])

    assert (buy_count, sell_count) == (0, 0)
    assert len(watchlist_calls) == 1
    assert watchlist_calls[0]["ticker"] == "005930"
    assert watchlist_calls[0]["decision"] == "Skip"


@pytest.mark.asyncio
async def test_update_holdings_masks_sold_account_payload(monkeypatch, tmp_path):
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(tmp_path / "stock_tracking.sqlite")
    agent.conn = sqlite3.connect(":memory:")
    agent.conn.row_factory = sqlite3.Row
    agent.cursor = agent.conn.cursor()
    agent.cursor.execute(
        """
        CREATE TABLE stock_holdings (
            id INTEGER PRIMARY KEY,
            ticker TEXT,
            company_name TEXT,
            buy_price REAL,
            buy_date TEXT,
            current_price REAL,
            scenario TEXT,
            target_price REAL,
            stop_loss REAL,
            last_updated TEXT,
            trigger_type TEXT,
            trigger_mode TEXT,
            account_key TEXT,
            account_name TEXT,
            sector TEXT
        )
        """
    )
    agent.cursor.execute(
        """
        INSERT INTO stock_holdings
        (ticker, company_name, buy_price, buy_date, current_price, scenario, target_price,
         stop_loss, last_updated, trigger_type, trigger_mode, account_key, account_name, sector)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "005930",
            "Samsung Electronics",
            70000,
            "2026-03-01 09:00:00",
            71000,
            "{}",
            None,
            None,
            "2026-03-01 09:00:00",
            "AI Analysis",
            "morning",
            "vps:12345678:01",
            "kr-primary",
            "Technology",
        ),
    )
    agent.conn.commit()
    agent.active_account = {"name": "kr-primary", "account_key": "vps:12345678:01"}
    agent.message_queue = []
    agent._msg_types = []

    async def fake_get_current_stock_price(ticker):
        return 72000

    async def fake_analyze_sell_decision(stock):
        return True, "Take profit"

    async def fake_sell_stock(stock, reason, exit_kind=None):
        return True

    agent._get_current_stock_price = fake_get_current_stock_price
    agent._analyze_sell_decision = fake_analyze_sell_decision
    agent.sell_stock = fake_sell_stock

    redis_calls = []
    gcp_calls = []
    monkeypatch.setattr(domestic_trading, "AsyncTradingContext", _FakeAsyncTradingContext)
    _install_signal_modules(monkeypatch, redis_calls, gcp_calls)

    sold = await StockTrackingAgent.update_holdings(agent)

    assert len(sold) == 1
    assert sold[0]["account_label"] == "kr-primary (vps:12****78:01)"
    assert "account_key" not in sold[0]


def test_safe_account_log_label_masks_account_key():
    label = StockTrackingAgent._safe_account_log_label(
        {"name": "kr-primary", "account_key": "vps:12345678:01"}
    )

    assert label == "kr-primary (vps:12****78:01)"
