import asyncio
import importlib.util
import logging
import sqlite3
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_us_agent_module():
    original_sys_path = list(sys.path)
    original_modules = {
        key: sys.modules.get(key)
        for key in [
            "tracking.db_schema",
            "tracking.journal",
            "tracking.compression",
            "trading.kis_auth",
        ]
    }

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        sys.path.insert(0, str(PRISM_US_DIR))

        import cores.agents.trading_agents as trading_agents

        if not hasattr(trading_agents, "create_us_trading_scenario_agent"):
            trading_agents.create_us_trading_scenario_agent = lambda *args, **kwargs: None
        if not hasattr(trading_agents, "create_us_sell_decision_agent"):
            trading_agents.create_us_sell_decision_agent = lambda *args, **kwargs: None

        tracking_db_schema = types.ModuleType("tracking.db_schema")
        tracking_db_schema.create_us_tables = lambda *args, **kwargs: None
        tracking_db_schema.create_us_indexes = lambda *args, **kwargs: None
        tracking_db_schema.add_sector_column_if_missing = lambda *args, **kwargs: None
        tracking_db_schema.add_market_column_to_shared_tables = lambda *args, **kwargs: None
        tracking_db_schema.migrate_us_performance_tracker_columns = lambda *args, **kwargs: None
        tracking_db_schema.migrate_us_watchlist_history_columns = lambda *args, **kwargs: None
        tracking_db_schema.is_us_ticker_in_holdings = lambda *args, **kwargs: False
        tracking_db_schema.get_us_holdings_count = lambda *args, **kwargs: 0
        def get_us_existing_position_for_ticker(cursor, ticker, account_key=None):
            try:
                if account_key:
                    cursor.execute(
                        "SELECT buy_price FROM us_stock_holdings "
                        "WHERE ticker = ? AND account_key = ?",
                        (ticker, account_key),
                    )
                else:
                    cursor.execute(
                        "SELECT buy_price FROM us_stock_holdings WHERE ticker = ?",
                        (ticker,),
                    )
                prices = [float(row[0]) for row in cursor.fetchall()]
            except sqlite3.Error:
                prices = []
            return {
                "row_count": len(prices),
                "avg_buy_price": sum(prices) / len(prices) if prices else 0.0,
            }

        tracking_db_schema.get_us_existing_position_for_ticker = (
            get_us_existing_position_for_ticker
        )
        tracking_db_schema.evaluate_us_pyramid_add_gate = (
            lambda *args, **kwargs: (False, "no existing position")
        )
        tracking_db_schema.compute_us_fractional_sell_quantity = (
            lambda total, rows: int(total) if int(rows) <= 1 else int(total) // int(rows)
        )
        tracking_db_schema.decide_us_sell_plan = (
            lambda rows, will_queue: (
                "single_full" if int(rows) <= 1 else "full_exit" if will_queue else "fractional"
            )
        )
        sys.modules["tracking.db_schema"] = tracking_db_schema

        tracking_journal = types.ModuleType("tracking.journal")
        tracking_journal.USJournalManager = object
        sys.modules["tracking.journal"] = tracking_journal

        tracking_compression = types.ModuleType("tracking.compression")
        tracking_compression.USCompressionManager = object
        sys.modules["tracking.compression"] = tracking_compression

        kis_auth_module = types.ModuleType("trading.kis_auth")
        kis_auth_module.getEnv = lambda: {"default_mode": "demo"}
        kis_auth_module.get_configured_accounts = lambda **kwargs: []
        sys.modules["trading.kis_auth"] = kis_auth_module

        return _load_module("prism_us_stock_tracking_agent_process_tests", PRISM_US_DIR / "us_stock_tracking_agent.py")
    finally:
        sys.path[:] = original_sys_path
        for key, original in original_modules.items():
            if original is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = original


us_agent_module = _load_us_agent_module()
USStockTrackingAgent = us_agent_module.USStockTrackingAgent


class _FakeAsyncUSTradingContext:
    def __init__(self, account_name=None, **kwargs):
        self.account_name = account_name

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def async_buy_stock(self, ticker, limit_price=None, buy_amount=None):
        # buy_amount mirrors the real USStockTrading.async_buy_stock signature
        # (None = full size; set only under PULSE_PILOT_REEXPOSURE pilot sizing).
        return {
            "success": True,
            "message": f"bought for {self.account_name}",
            "partial_success": self.account_name == "us-primary",
            "successful_accounts": ["us-primary"],
            "failed_accounts": ["us-secondary"],
        }

    async def async_sell_stock(self, ticker, limit_price=None, quantity=None):
        return {
            "success": True,
            "message": f"sold for {self.account_name}",
        }


@pytest.mark.parametrize("pyramiding", [False, True], ids=["single", "pyramiding"])
def test_concurrent_sell_guard_allows_one_us_order_and_publish(tmp_path, pyramiding):
    from prism_core.execution_service import ExecutionService
    from prism_core.positions import PositionStore

    db_path = tmp_path / "us-concurrent-sell.sqlite"
    setup = sqlite3.connect(db_path)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute(
        """CREATE TABLE us_stock_holdings (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               account_key TEXT NOT NULL, account_name TEXT, ticker TEXT NOT NULL,
               company_name TEXT NOT NULL, buy_price REAL NOT NULL,
               buy_date TEXT NOT NULL, current_price REAL, scenario TEXT,
               trigger_type TEXT, trigger_mode TEXT, sector TEXT
           )"""
    )
    setup.execute(
        """CREATE TABLE us_trading_history (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               account_key TEXT NOT NULL, account_name TEXT, ticker TEXT NOT NULL,
               company_name TEXT NOT NULL, buy_price REAL NOT NULL,
               buy_date TEXT NOT NULL, sell_price REAL NOT NULL,
               sell_date TEXT NOT NULL, profit_rate REAL NOT NULL,
               holding_days INTEGER NOT NULL, scenario TEXT, trigger_type TEXT,
               trigger_mode TEXT, sector TEXT, exit_kind TEXT
           )"""
    )
    target_row_id = setup.execute(
        """INSERT INTO us_stock_holdings
           (account_key, account_name, ticker, company_name, buy_price, buy_date)
           VALUES ('ACC1', 'us-primary', 'AAPL', 'Apple', 180,
                   '2026-07-01 09:00:00')"""
    ).lastrowid
    if pyramiding:
        setup.execute(
            """INSERT INTO us_stock_holdings
               (account_key, account_name, ticker, company_name, buy_price, buy_date)
               VALUES ('ACC1', 'us-primary', 'AAPL', 'Apple', 175,
                       '2026-06-15 09:00:00')"""
        )
    position_store = PositionStore(setup)
    position_store.ensure_schema()
    assert position_store.backfill_legacy_positions("US")["inserted"] == (
        2 if pyramiding else 1
    )
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)

    effects = {"broker": 0, "publish": 0}
    effects_lock = threading.Lock()

    class Broker:
        async def async_sell_stock(self, *args, **kwargs):
            with effects_lock:
                effects["broker"] += 1
            return {"success": True}

    stock = {
        "ticker": "AAPL",
        "company_name": "Apple",
        "buy_price": 180,
        "buy_date": "2026-07-01 09:00:00",
        "current_price": 185,
        "account_key": "ACC1",
        "account_name": "us-primary",
        "sector": "Technology",
        "id": target_row_id,
    }

    def run_competitor():
        conn = sqlite3.connect(db_path, timeout=1, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=1000")
        agent = USStockTrackingAgent.__new__(USStockTrackingAgent)
        agent.conn = conn
        agent.cursor = conn.cursor()
        agent.message_queue = []
        agent._msg_types = []
        agent.enable_journal = False
        agent.journal_manager = None
        agent._account_scope = lambda: ("ACC1", "us-primary")
        agent._get_trigger_win_rate = lambda _trigger: ""

        async def exercise():
            barrier.wait(timeout=5)
            sold = await agent.sell_stock(dict(stock), "concurrency regression")
            if sold:
                await ExecutionService(Broker()).execute_sell("AAPL", quantity=1)
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
    history_count = verify.execute("SELECT COUNT(*) FROM us_trading_history").fetchone()[0]
    remaining_prices = verify.execute(
        "SELECT buy_price FROM us_stock_holdings ORDER BY id"
    ).fetchall()
    position_states = verify.execute(
        "SELECT legacy_holding_id, status FROM positions ORDER BY legacy_holding_id"
    ).fetchall()
    verify.close()

    assert sorted(sold for sold, _messages in results) == [False, True]
    assert sum(messages for _sold, messages in results) == 1
    assert history_count == 1
    assert remaining_prices == ([(175.0,)] if pyramiding else [])
    assert position_states == (
        [(str(target_row_id), "CLOSED"), (str(target_row_id + 1), "OPEN")]
        if pyramiding
        else [(str(target_row_id), "CLOSED")]
    )
    assert effects == {"broker": 1, "publish": 1}


@pytest.mark.asyncio
async def test_us_buy_dual_writes_open_position():
    from prism_core.positions import PositionStore

    agent = USStockTrackingAgent.__new__(USStockTrackingAgent)
    agent.conn = sqlite3.connect(":memory:")
    agent.cursor = agent.conn.cursor()
    agent.cursor.execute(
        """CREATE TABLE us_stock_holdings (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               account_key TEXT NOT NULL, account_name TEXT, ticker TEXT NOT NULL,
               company_name TEXT NOT NULL, buy_price REAL NOT NULL,
               buy_date TEXT NOT NULL, current_price REAL, last_updated TEXT,
               scenario TEXT, target_price REAL, stop_loss REAL,
               trigger_type TEXT, trigger_mode TEXT, sector TEXT
           )"""
    )
    PositionStore(agent.cursor).ensure_schema()
    agent.conn.commit()
    agent.position_ledger_shadow_enabled = True
    agent.max_slots = 10
    agent.message_queue = []
    agent._msg_types = []
    agent._account_scope = lambda: ("ACC1", "us-primary")
    agent._get_trigger_win_rate = lambda _trigger: ""

    async def no_holding(_ticker):
        return False

    async def no_slots():
        return 0

    agent._is_ticker_in_holdings = no_holding
    agent._get_current_slots_count = no_slots

    assert await agent.buy_stock(
        "AAPL", "Apple", 190.0, {"sector": "Technology"}
    )
    legacy = agent.conn.execute(
        "SELECT id, account_key, ticker, buy_price, buy_date FROM us_stock_holdings"
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


def _install_us_trading_module(monkeypatch):
    module = types.ModuleType("trading.us_stock_trading")
    module.AsyncUSTradingContext = _FakeAsyncUSTradingContext
    monkeypatch.setitem(sys.modules, "trading.us_stock_trading", module)


@pytest.mark.asyncio
async def test_process_reports_analyzes_once_and_dedupes_signals(monkeypatch, caplog, tmp_path):
    agent = USStockTrackingAgent.__new__(USStockTrackingAgent)
    agent.db_path = str(tmp_path / "us_stock_tracking.sqlite")
    agent.account_configs = [
        {"name": "us-primary", "account_key": "vps:us-primary:01", "product": "01"},
        {"name": "us-secondary", "account_key": "vps:us-secondary:01", "product": "01"},
    ]
    agent.active_account = None
    agent.max_slots = 10
    agent.enable_journal = False
    agent.conn = sqlite3.connect(":memory:")
    agent.cursor = agent.conn.cursor()

    core_calls = []
    holdings_checks = []
    slot_checks = []
    sector_checks = []
    buy_calls = []
    redis_calls = []
    gcp_calls = []
    watchlist_calls = []

    async def fake_core(report_path):
        core_calls.append(report_path)
        return {
            "success": True,
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "current_price": 180.5,
            "scenario": {"buy_score": 8, "min_score": 7, "sector": "Technology"},
            "decision": "entry",
            "raw_decision": "Enter",
            "sector": "Technology",
            "rank_change_msg": "Up",
            "rank_change_percentage": 9.0,
        }

    async def fake_update_holdings():
        return []

    async def fake_is_ticker_in_holdings(ticker):
        holdings_checks.append((agent.active_account["name"], ticker))
        return False

    async def fake_get_current_slots_count():
        slot_checks.append(agent.active_account["name"])
        return 0

    async def fake_check_sector_diversity(sector, is_pyramiding_add=False):
        sector_checks.append((agent.active_account["name"], sector))
        return True

    async def fake_buy_stock(
        ticker, company_name, current_price, scenario, rank_change_msg, is_add=False
    ):
        buy_calls.append((agent.active_account["name"], ticker))
        return True

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

    _install_signal_modules(monkeypatch, redis_calls, gcp_calls)
    _install_us_trading_module(monkeypatch)

    caplog.set_level(logging.WARNING)

    buy_count, sell_count = await USStockTrackingAgent.process_reports(agent, ["report-a.pdf"])

    assert buy_count == 2
    assert sell_count == 0
    assert core_calls == ["report-a.pdf"]
    assert holdings_checks == [("us-primary", "AAPL"), ("us-secondary", "AAPL")]
    assert slot_checks == ["us-primary", "us-secondary"]
    assert sector_checks == [("us-primary", "Technology"), ("us-secondary", "Technology")]
    assert buy_calls == [("us-primary", "AAPL"), ("us-secondary", "AAPL")]
    assert len(redis_calls) == 1
    assert len(gcp_calls) == 1
    assert watchlist_calls == []
    assert "partial success" in caplog.text.lower()


@pytest.mark.asyncio
async def test_process_reports_saves_watchlist_once_when_not_traded(monkeypatch):
    agent = USStockTrackingAgent.__new__(USStockTrackingAgent)
    agent.account_configs = [
        {"name": "us-primary", "account_key": "vps:us-primary:01", "product": "01"},
        {"name": "us-secondary", "account_key": "vps:us-secondary:01", "product": "01"},
    ]
    agent.active_account = None
    agent.max_slots = 10
    agent.enable_journal = False
    agent.conn = sqlite3.connect(":memory:")
    agent.cursor = agent.conn.cursor()

    watchlist_calls = []

    async def fake_core(report_path):
        return {
            "success": True,
            "ticker": "MSFT",
            "company_name": "Microsoft",
            "current_price": 410.0,
            "scenario": {"buy_score": 6, "min_score": 7, "sector": "Technology"},
            "decision": "no_entry",
            "raw_decision": "No Entry",
            "sector": "Technology",
            "rank_change_msg": "Flat",
            "rank_change_percentage": 1.0,
        }

    async def fake_update_holdings():
        return []

    async def fake_is_ticker_in_holdings(ticker):
        return False

    async def fake_get_current_slots_count():
        return 0

    async def fake_check_sector_diversity(sector, is_pyramiding_add=False):
        return True

    async def fake_buy_stock(*args, **kwargs):
        raise AssertionError("buy_stock should not be called for no-entry decisions")

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

    buy_count, sell_count = await USStockTrackingAgent.process_reports(agent, ["report-a.pdf"])

    assert (buy_count, sell_count) == (0, 0)
    assert len(watchlist_calls) == 1
    assert watchlist_calls[0]["ticker"] == "MSFT"
    assert watchlist_calls[0]["decision"] == "no_entry"


@pytest.mark.asyncio
async def test_process_reports_returns_zero_for_empty_accounts(caplog):
    agent = USStockTrackingAgent.__new__(USStockTrackingAgent)
    agent.account_configs = []
    agent.active_account = None
    agent.max_slots = 10
    agent.enable_journal = False

    caplog.set_level(logging.WARNING)

    buy_count, sell_count = await USStockTrackingAgent.process_reports(agent, ["report-a.pdf"])

    assert (buy_count, sell_count) == (0, 0)
    assert "no accounts configured" in caplog.text.lower()


@pytest.mark.asyncio
async def test_update_holdings_masks_sold_account_payload(monkeypatch, tmp_path):
    agent = USStockTrackingAgent.__new__(USStockTrackingAgent)
    agent.db_path = str(tmp_path / "us_stock_tracking.sqlite")
    agent.conn = sqlite3.connect(":memory:")
    agent.conn.row_factory = sqlite3.Row
    agent.cursor = agent.conn.cursor()
    agent.cursor.execute(
        """
        CREATE TABLE us_stock_holdings (
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
        INSERT INTO us_stock_holdings
        (ticker, company_name, buy_price, buy_date, current_price, scenario, target_price,
         stop_loss, last_updated, trigger_type, trigger_mode, account_key, account_name, sector)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "AAPL",
            "Apple Inc.",
            180.5,
            "2026-03-01 09:00:00",
            185.0,
            "{}",
            None,
            None,
            "2026-03-01 09:00:00",
            "AI Analysis",
            "morning",
            "vps:12345678:01",
            "us-primary",
            "Technology",
        ),
    )
    agent.conn.commit()
    agent.active_account = {"name": "us-primary", "account_key": "vps:12345678:01", "product": "01"}
    agent.message_queue = []
    agent._msg_types = []

    async def fake_get_current_stock_price(ticker):
        return 190.0

    async def fake_analyze_sell_decision(stock):
        return True, "Take profit"

    async def fake_sell_stock(stock, reason, exit_kind=None):
        return True

    agent._get_current_stock_price = fake_get_current_stock_price
    agent._analyze_sell_decision = fake_analyze_sell_decision
    agent.sell_stock = fake_sell_stock

    redis_calls = []
    gcp_calls = []
    _install_signal_modules(monkeypatch, redis_calls, gcp_calls)
    _install_us_trading_module(monkeypatch)

    sold = await USStockTrackingAgent.update_holdings(agent)

    assert len(sold) == 1
    assert sold[0]["account_label"] == "us-primary (vps:12****78:01)"
    assert "account_key" not in sold[0]


def test_safe_account_log_label_masks_account_key():
    label = USStockTrackingAgent._safe_account_log_label(
        {"name": "us-primary", "account_key": "vps:12345678:01"}
    )

    assert label == "us-primary (vps:12****78:01)"
