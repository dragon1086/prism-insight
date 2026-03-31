import atexit
import importlib.util
import sqlite3
import sys
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

CONFIG_DIR = PROJECT_ROOT / "trading" / "config"
CONFIG_FILE = CONFIG_DIR / "kis_devlp.yaml"
_CREATED_TEST_CONFIG = False

if not CONFIG_FILE.exists():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        textwrap.dedent(
            """
            my_agent: test-agent
            default_mode: demo
            auto_trading: true
            default_product_code: "01"
            default_unit_amount: 100000
            default_unit_amount_usd: 250
            my_app: PSREALKEY
            my_sec: real-secret
            paper_app: PSVTTESTKEY
            paper_sec: paper-secret
            my_htsid: test-user
            prod: https://example.com
            vps: https://example.com
            ops: wss://example.com
            vops: wss://example.com
            accounts:
              - name: bootstrap-demo
                mode: demo
                account: "12345678"
                product: "01"
                market: us
                primary: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    _CREATED_TEST_CONFIG = True


if _CREATED_TEST_CONFIG:
    atexit.register(lambda: CONFIG_FILE.unlink(missing_ok=True))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


us_schema = _load_module("prism_us_tracking_db_schema", PRISM_US_DIR / "tracking" / "db_schema.py")
ust = _load_module("prism_us_us_stock_trading", PRISM_US_DIR / "trading" / "us_stock_trading.py")
pending_batch = _load_module("prism_us_pending_order_batch", PRISM_US_DIR / "us_pending_order_batch.py")


class FakeUSTrader:
    init_calls = []

    def __init__(
        self,
        mode=None,
        buy_amount=None,
        auto_trading=None,
        account_name=None,
        account_index=None,
        product_code="01",
    ):
        self.mode = mode or "demo"
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.account_name = account_name
        self.account_index = account_index
        self.product_code = product_code
        self.account_key = f"vps:{account_name}:{product_code}" if account_name else "window-checker"
        type(self).init_calls.append(
            {
                "mode": self.mode,
                "buy_amount": buy_amount,
                "auto_trading": auto_trading,
                "account_name": account_name,
                "product_code": product_code,
            }
        )

    async def async_buy_stock(self, ticker, buy_amount=None, exchange=None, timeout=30.0, limit_price=None):
        success = self.account_name != "us-secondary"
        quantity = 1 if success else 0
        return {
            "success": success,
            "ticker": ticker,
            "quantity": quantity,
            "estimated_amount": quantity * 100.0,
            "message": "ok" if success else "rejected",
        }

    async def async_sell_stock(self, ticker, exchange=None, timeout=30.0, limit_price=None, use_moo=False):
        return {
            "success": True,
            "ticker": ticker,
            "quantity": 1,
            "estimated_amount": 100.0,
            "message": "sold",
        }

    def get_portfolio(self):
        return [{"account_name": self.account_name}]

    def get_account_summary(self):
        return {"account_name": self.account_name}

    def get_current_price(self, ticker, exchange=None):
        return {"ticker": ticker, "account_name": self.account_name}

    def calculate_buy_quantity(self, ticker, buy_amount=None, exchange=None):
        return 4

    def get_holding_quantity(self, ticker):
        return 2

    def is_reserved_order_available(self):
        return True

    def buy_reserved_order(self, ticker, limit_price, buy_amount=None, exchange=None):
        return {"success": True, "message": f"queued-buy-{self.account_name}", "ticker": ticker}

    def sell_reserved_order(self, ticker, limit_price=None, exchange=None):
        return {"success": True, "message": f"queued-sell-{self.account_name}", "ticker": ticker}


@pytest.fixture
def initialized_us_temp_database():
    temp_file = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    db_path = temp_file.name
    temp_file.close()

    cursor, conn = us_schema.initialize_us_database(db_path)
    try:
        yield cursor, conn, db_path
    finally:
        conn.close()
        path = Path(db_path)
        if path.exists():
            path.unlink()


@pytest.mark.asyncio
async def test_async_us_trading_context_returns_single_account_trader(monkeypatch):
    FakeUSTrader.init_calls = []
    monkeypatch.setattr(ust, "USStockTrading", FakeUSTrader)

    async with ust.AsyncUSTradingContext(mode="demo", buy_amount=150.0, account_name="us-main") as trader:
        assert isinstance(trader, FakeUSTrader)
        assert trader.account_name == "us-main"

    assert FakeUSTrader.init_calls == [
        {
            "mode": "demo",
            "buy_amount": 150.0,
            "auto_trading": ust.AsyncUSTradingContext.AUTO_TRADING,
            "account_name": "us-main",
            "product_code": "01",
        }
    ]


@pytest.mark.asyncio
async def test_multi_account_us_context_fans_out_orders_but_reads_primary(monkeypatch):
    FakeUSTrader.init_calls = []
    accounts = [
        {"name": "us-primary", "account_key": "vps:us-primary:01", "product": "01"},
        {"name": "us-secondary", "account_key": "vps:us-secondary:01", "product": "01"},
    ]
    monkeypatch.setattr(ust, "USStockTrading", FakeUSTrader)
    monkeypatch.setattr(ust.ka, "get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr(ust.ka, "resolve_account", lambda **kwargs: accounts[0])

    async with ust.MultiAccountUSTradingContext(mode="demo", buy_amount=300.0) as trader:
        result = await trader.async_buy_stock("AAPL")

        assert result["success"] is False
        assert result["partial_success"] is True
        assert result["successful_accounts"] == ["us-primary"]
        assert result["failed_accounts"] == ["us-secondary"]
        assert [item["account_key"] for item in result["account_results"]] == [
            "vps:us-primary:01",
            "vps:us-secondary:01",
        ]
        assert trader.get_portfolio() == [{"account_name": "us-primary"}]
        assert trader.get_account_summary() == {"account_name": "us-primary"}
        assert trader.get_current_price("AAPL") == {"ticker": "AAPL", "account_name": "us-primary"}
        assert trader.calculate_buy_quantity("AAPL") == 4
        assert trader.get_holding_quantity("AAPL") == 2


def test_us_trader_uses_account_buy_amount_override(monkeypatch):
    account = {
        "name": "us-override",
        "account_key": "vps:90909090:01",
        "product": "01",
        "buy_amount_usd": 456.78,
    }
    monkeypatch.setattr(ust.ka, "resolve_account", lambda **kwargs: account)
    monkeypatch.setattr(ust.ka, "auth", lambda **kwargs: None)
    monkeypatch.setattr(
        ust.ka,
        "getTREnv",
        lambda: SimpleNamespace(my_acct="90909090", my_prod="01", my_token="token"),
    )

    trader = ust.USStockTrading(mode="demo", account_name="us-override")

    assert trader.buy_amount == 456.78
    assert trader.account_key == "vps:90909090:01"


def test_us_schema_migration_backfills_primary_account_scope(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE us_stock_holdings (
            ticker TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            buy_price REAL NOT NULL,
            buy_date TEXT NOT NULL,
            current_price REAL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE us_pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            order_type TEXT NOT NULL,
            limit_price REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date, current_price)
        VALUES ('AAPL', 'Apple Inc.', 180.5, '2026-03-01', 185.0)
        """
    )
    cursor.execute(
        """
        INSERT INTO us_pending_orders (ticker, order_type, limit_price, created_at)
        VALUES ('MSFT', 'buy', 410.0, '2026-03-02 09:00:00')
        """
    )
    conn.commit()

    monkeypatch.setattr(
        us_schema,
        "_get_primary_account_scope",
        lambda: ("vps:us-primary:01", "US Primary", "01", "demo"),
    )

    us_schema.migrate_multi_account_schema(cursor, conn)

    cursor.execute("SELECT account_key, account_name, ticker FROM us_stock_holdings")
    assert cursor.fetchone() == ("vps:us-primary:01", "US Primary", "AAPL")

    cursor.execute("SELECT account_key, account_name, product_code, mode, ticker FROM us_pending_orders")
    assert cursor.fetchone() == ("vps:us-primary:01", "US Primary", "01", "demo", "MSFT")


def test_us_schema_migration_handles_quoted_account_names_retains_backups_and_preserves_ids(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE us_stock_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            buy_price REAL NOT NULL,
            buy_date TEXT NOT NULL,
            current_price REAL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE us_pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            order_type TEXT NOT NULL,
            limit_price REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO us_stock_holdings (id, ticker, company_name, buy_price, buy_date, current_price)
        VALUES (7, 'AAPL', 'Apple Inc.', 180.5, '2026-03-01', 185.0)
        """
    )
    cursor.execute(
        """
        INSERT INTO us_pending_orders (ticker, order_type, limit_price, created_at)
        VALUES ('MSFT', 'buy', 410.0, '2026-03-02 09:00:00')
        """
    )
    conn.commit()

    monkeypatch.setattr(
        us_schema,
        "_get_primary_account_scope",
        lambda: ("vps:us-primary:01", "O'Brien US", "01", "demo"),
    )

    us_schema.migrate_multi_account_schema(cursor, conn)

    cursor.execute("SELECT id, account_key, account_name FROM us_stock_holdings")
    assert cursor.fetchone() == (7, "vps:us-primary:01", "O'Brien US")
    cursor.execute("SELECT account_key, account_name, product_code, mode FROM us_pending_orders")
    assert cursor.fetchone() == ("vps:us-primary:01", "O'Brien US", "01", "demo")
    assert us_schema._table_exists(cursor, "us_stock_holdings_pre_multi_account_backup")
    assert us_schema._table_exists(cursor, "us_pending_orders_pre_multi_account_backup")


def test_us_schema_recovers_interrupted_migration(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE us_stock_holdings_legacy (
            ticker TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            buy_price REAL NOT NULL,
            buy_date TEXT NOT NULL,
            current_price REAL
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO us_stock_holdings_legacy (ticker, company_name, buy_price, buy_date, current_price)
        VALUES ('AAPL', 'Apple Inc.', 180.5, '2026-03-01', 185.0)
        """
    )
    cursor.execute(us_schema.TABLE_US_STOCK_HOLDINGS)
    conn.commit()

    monkeypatch.setattr(
        us_schema,
        "_get_primary_account_scope",
        lambda: ("vps:us-primary:01", "US Primary", "01", "demo"),
    )

    us_schema.migrate_multi_account_schema(cursor, conn)

    cursor.execute("SELECT account_key, account_name, ticker FROM us_stock_holdings")
    assert cursor.fetchone() == ("vps:us-primary:01", "US Primary", "AAPL")
    assert not us_schema._table_exists(cursor, "us_stock_holdings_legacy")


def test_us_schema_requires_primary_account_when_migration_needed(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE us_stock_holdings (
            ticker TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            buy_price REAL NOT NULL,
            buy_date TEXT NOT NULL,
            current_price REAL
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date, current_price)
        VALUES ('AAPL', 'Apple Inc.', 180.5, '2026-03-01', 185.0)
        """
    )
    conn.commit()

    def _raise_scope_error():
        raise RuntimeError("KIS auth unavailable")

    monkeypatch.setattr(us_schema, "_get_primary_account_scope", _raise_scope_error)

    with pytest.raises(RuntimeError, match="Unable to verify the primary account"):
        us_schema.migrate_multi_account_schema(cursor, conn)

    cursor.execute("PRAGMA table_info(us_stock_holdings)")
    assert "account_key" not in {row[1] for row in cursor.fetchall()}


def test_us_schema_skips_scope_resolution_when_already_migrated(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    us_schema.create_us_tables(cursor, conn)

    def _raise_scope_error():
        raise AssertionError("Primary account resolution should not be called")

    monkeypatch.setattr(us_schema, "_get_primary_account_scope", _raise_scope_error)

    us_schema.migrate_multi_account_schema(cursor, conn)


def test_initialize_us_database_runs_multi_account_migration_once(monkeypatch):
    calls = []
    temp_file = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    def fake_migration(cursor, conn):
        calls.append("migrated")

    monkeypatch.setattr(us_schema, "migrate_multi_account_schema", fake_migration)

    cursor, conn = us_schema.initialize_us_database(str(temp_path))
    try:
        assert calls == ["migrated"]
    finally:
        conn.close()
        temp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_async_initialize_us_database_migrates_legacy_schema(monkeypatch):
    temp_file = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        conn = sqlite3.connect(str(temp_path))
        conn.execute(
            """
            CREATE TABLE us_stock_holdings (
                ticker TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                buy_price REAL NOT NULL,
                buy_date TEXT NOT NULL,
                current_price REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date, current_price)
            VALUES ('AAPL', 'Apple Inc.', 180.5, '2026-03-01', 185.0)
            """
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            us_schema,
            "_get_primary_account_scope",
            lambda: ("vps:us-primary:01", "US Primary", "01", "demo"),
        )

        async_conn = await us_schema.async_initialize_us_database(str(temp_path))
        async_cursor = await async_conn.execute(
            "SELECT account_key, account_name, ticker FROM us_stock_holdings"
        )
        row = await async_cursor.fetchone()
        await async_cursor.close()
        await async_conn.close()

        assert row == ("vps:us-primary:01", "US Primary", "AAPL")
    finally:
        temp_path.unlink(missing_ok=True)


def test_us_schema_allows_same_ticker_across_accounts(initialized_us_temp_database):
    cursor, conn, _ = initialized_us_temp_database

    cursor.executemany(
        """
        INSERT INTO us_stock_holdings
        (account_key, account_name, ticker, company_name, buy_price, buy_date, current_price)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("vps:us-one:01", "US One", "AAPL", "Apple Inc.", 180.5, "2026-03-01", 185.0),
            ("vps:us-two:01", "US Two", "AAPL", "Apple Inc.", 181.0, "2026-03-02", 186.0),
        ],
    )
    conn.commit()

    assert us_schema.get_us_holdings_count(cursor, account_key="vps:us-one:01") == 1
    assert us_schema.get_us_holdings_count(cursor, account_key="vps:us-two:01") == 1
    assert us_schema.is_us_ticker_in_holdings(cursor, "AAPL", account_key="vps:us-one:01") is True
    assert us_schema.is_us_ticker_in_holdings(cursor, "AAPL", account_key="vps:us-two:01") is True


def test_pending_order_batch_uses_stored_account_context(monkeypatch):
    temp_file = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        conn = sqlite3.connect(str(temp_path))
        conn.execute(
            """
            CREATE TABLE us_pending_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_key TEXT NOT NULL,
                account_name TEXT,
                product_code TEXT,
                mode TEXT,
                ticker TEXT NOT NULL,
                order_type TEXT NOT NULL,
                limit_price REAL NOT NULL,
                buy_amount REAL,
                exchange TEXT,
                status TEXT DEFAULT 'pending',
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT,
                order_result TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO us_pending_orders
            (account_key, account_name, product_code, mode, ticker, order_type, limit_price, buy_amount, exchange, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                "vps:batch-account:01",
                "batch-account",
                "03",
                "real",
                "AAPL",
                "buy",
                190.0,
                500.0,
                "NASD",
                "2099-03-27 10:00:00",
            ),
        )
        conn.commit()
        conn.close()

        FakeUSTrader.init_calls = []
        monkeypatch.setattr(pending_batch, "DB_PATH", temp_path)
        monkeypatch.setattr(ust, "USStockTrading", FakeUSTrader)
        trading_package = sys.modules.get("trading")
        if trading_package is None:
            trading_package = _load_module("trading", PROJECT_ROOT / "trading" / "__init__.py")
        monkeypatch.setitem(sys.modules, "trading.us_stock_trading", ust)
        monkeypatch.setattr(trading_package, "us_stock_trading", ust, raising=False)

        frozen_now = ust.datetime.datetime(2099, 3, 27, 10, 5, 0, tzinfo=pending_batch.KST)

        class FrozenDateTime(ust.datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz else frozen_now.replace(tzinfo=None)

        monkeypatch.setattr(pending_batch.datetime, "datetime", FrozenDateTime)

        pending_batch.process_pending_orders(dry_run=False)

        assert any(
            call["account_name"] is None for call in FakeUSTrader.init_calls
        )
        assert {
            "mode": "real",
            "buy_amount": None,
            "auto_trading": None,
            "account_name": "batch-account",
            "product_code": "03",
        } in FakeUSTrader.init_calls

        conn = sqlite3.connect(str(temp_path))
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM us_pending_orders WHERE ticker = 'AAPL'")
        assert cursor.fetchone()[0] == "executed"
        conn.close()
    finally:
        if temp_path.exists():
            temp_path.unlink()
