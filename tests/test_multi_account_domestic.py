import atexit
import json
import sqlite3
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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
                market: all
                primary: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    _CREATED_TEST_CONFIG = True


if _CREATED_TEST_CONFIG:
    atexit.register(lambda: CONFIG_FILE.unlink(missing_ok=True))

from tracking import db_schema as kr_schema
from tracking import helpers as kr_helpers
from trading import domestic_stock_trading as dst


class FakeDomesticTrader:
    init_calls = []

    def __init__(
        self,
        mode="demo",
        buy_amount=None,
        auto_trading=True,
        account_name=None,
        account_index=None,
        product_code="01",
    ):
        self.mode = mode
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.account_name = account_name
        self.account_index = account_index
        self.product_code = product_code
        self.account_key = f"vps:{account_name}:{product_code}"
        type(self).init_calls.append(
            {
                "mode": mode,
                "buy_amount": buy_amount,
                "auto_trading": auto_trading,
                "account_name": account_name,
                "product_code": product_code,
            }
        )

    async def async_buy_stock(self, stock_code, buy_amount=None, timeout=30.0, limit_price=None):
        success = self.account_name != "kr-secondary"
        quantity = 1 if success else 0
        return {
            "success": success,
            "stock_code": stock_code,
            "quantity": quantity,
            "estimated_amount": quantity * 50000,
            "message": "ok" if success else "rejected",
        }

    async def async_sell_stock(self, stock_code, timeout=30.0, limit_price=None):
        return {
            "success": True,
            "stock_code": stock_code,
            "quantity": 1,
            "estimated_amount": 50000,
            "message": "sold",
        }

    def get_portfolio(self):
        return [{"account_name": self.account_name}]

    def get_account_summary(self):
        return {"account_name": self.account_name}

    def get_current_price(self, stock_code):
        return {"stock_code": stock_code, "account_name": self.account_name}

    def calculate_buy_quantity(self, stock_code, buy_amount=None):
        return 3

    def get_holding_quantity(self, stock_code):
        return 7


@pytest.mark.asyncio
async def test_async_trading_context_returns_single_account_trader(monkeypatch):
    FakeDomesticTrader.init_calls = []
    monkeypatch.setattr(dst, "DomesticStockTrading", FakeDomesticTrader)

    async with dst.AsyncTradingContext(mode="demo", buy_amount=150000, account_name="kr-main") as trader:
        assert isinstance(trader, FakeDomesticTrader)
        assert trader.account_name == "kr-main"

    assert FakeDomesticTrader.init_calls == [
        {
            "mode": "demo",
            "buy_amount": 150000,
            "auto_trading": dst.AsyncTradingContext.AUTO_TRADING,
            "account_name": "kr-main",
            "product_code": "01",
        }
    ]


@pytest.mark.asyncio
async def test_multi_account_trading_context_fans_out_orders_but_reads_primary(monkeypatch):
    FakeDomesticTrader.init_calls = []
    accounts = [
        {"name": "kr-primary", "account_key": "vps:kr-primary:01", "product": "01"},
        {"name": "kr-secondary", "account_key": "vps:kr-secondary:01", "product": "01"},
    ]
    monkeypatch.setattr(dst, "DomesticStockTrading", FakeDomesticTrader)
    monkeypatch.setattr(dst.ka, "get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr(dst.ka, "resolve_account", lambda **kwargs: accounts[0])

    async with dst.MultiAccountTradingContext(mode="demo", buy_amount=200000) as trader:
        result = await trader.async_buy_stock("005930")

        assert result["success"] is False
        assert result["partial_success"] is True
        assert result["successful_accounts"] == ["kr-primary"]
        assert result["failed_accounts"] == ["kr-secondary"]
        assert [item["account_key"] for item in result["account_results"]] == [
            "vps:kr-primary:01",
            "vps:kr-secondary:01",
        ]
        assert trader.get_portfolio() == [{"account_name": "kr-primary"}]
        assert trader.get_account_summary() == {"account_name": "kr-primary"}
        assert trader.get_current_price("005930") == {
            "stock_code": "005930",
            "account_name": "kr-primary",
        }
        assert trader.calculate_buy_quantity("005930") == 3
        assert trader.get_holding_quantity("005930") == 7


def test_domestic_request_serializes_activation_and_fetch(monkeypatch):
    order = []
    barrier = threading.Barrier(2)
    results = []
    results_lock = threading.Lock()

    trader = dst.DomesticStockTrading.__new__(dst.DomesticStockTrading)

    def fake_activate():
        order.append(f"activate-{threading.current_thread().name}")

    def fake_fetch(api_url, tr_id, hashkey, params, **kwargs):
        order.append(f"fetch-start-{threading.current_thread().name}")
        time.sleep(0.05)
        order.append(f"fetch-end-{threading.current_thread().name}")
        return {"api_url": api_url, "tr_id": tr_id}

    trader._activate_account = fake_activate
    monkeypatch.setattr(dst.ka, "_url_fetch", fake_fetch)

    def worker():
        barrier.wait()
        value = dst.DomesticStockTrading._request(trader, "/uapi/test", "TEST0001", {})
        with results_lock:
            results.append(value)

    thread_a = threading.Thread(target=worker, name="A")
    thread_b = threading.Thread(target=worker, name="B")
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert len(results) == 2
    assert order in [
        [
            "activate-A",
            "fetch-start-A",
            "fetch-end-A",
            "activate-B",
            "fetch-start-B",
            "fetch-end-B",
        ],
        [
            "activate-B",
            "fetch-start-B",
            "fetch-end-B",
            "activate-A",
            "fetch-start-A",
            "fetch-end-A",
        ],
    ]


def test_domestic_trader_uses_account_buy_amount_override(monkeypatch):
    account = {
        "name": "kr-override",
        "account_key": "vps:10101010:01",
        "product": "01",
        "buy_amount_krw": 54321,
    }
    monkeypatch.setattr(dst.ka, "resolve_account", lambda **kwargs: account)
    monkeypatch.setattr(dst.ka, "auth", lambda **kwargs: None)
    monkeypatch.setattr(
        dst.ka,
        "getTREnv",
        lambda: SimpleNamespace(my_acct="10101010", my_prod="01", my_token="token"),
    )

    trader = dst.DomesticStockTrading(mode="demo", account_name="kr-override")

    assert trader.buy_amount == 54321
    assert trader.account_key == "vps:10101010:01"


def test_kr_schema_migration_backfills_primary_account(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE stock_holdings (
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
        CREATE TABLE trading_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            buy_price REAL NOT NULL,
            buy_date TEXT NOT NULL,
            sell_price REAL NOT NULL,
            sell_date TEXT NOT NULL,
            profit_rate REAL NOT NULL,
            holding_days INTEGER NOT NULL
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date, current_price)
        VALUES ('005930', 'Samsung Electronics', 70000, '2026-03-01', 71000)
        """
    )
    cursor.execute(
        """
        INSERT INTO trading_history
        (ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days)
        VALUES ('000660', 'SK Hynix', 120000, '2026-02-01', 132000, '2026-03-01', 10.0, 28)
        """
    )
    conn.commit()

    monkeypatch.setattr(
        kr_schema,
        "_get_primary_account_scope",
        lambda: ("vps:11112222:01", "KR Primary"),
    )

    kr_schema.migrate_multi_account_schema(cursor, conn)

    cursor.execute("PRAGMA table_info(stock_holdings)")
    stock_columns = {row[1] for row in cursor.fetchall()}
    assert {"id", "account_key", "account_name", "ticker"}.issubset(stock_columns)

    cursor.execute("SELECT account_key, account_name, ticker FROM stock_holdings")
    assert cursor.fetchone() == ("vps:11112222:01", "KR Primary", "005930")

    cursor.execute("SELECT account_key, account_name, ticker FROM trading_history")
    assert cursor.fetchone() == ("vps:11112222:01", "KR Primary", "000660")


def test_kr_schema_recovers_interrupted_stock_holdings_migration(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE stock_holdings_legacy (
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
        INSERT INTO stock_holdings_legacy (ticker, company_name, buy_price, buy_date, current_price)
        VALUES ('005930', 'Samsung Electronics', 70000, '2026-03-01', 71000)
        """
    )
    cursor.execute(kr_schema.TABLE_STOCK_HOLDINGS)
    conn.commit()

    monkeypatch.setattr(
        kr_schema,
        "_get_primary_account_scope",
        lambda: ("vps:11112222:01", "KR Primary"),
    )

    kr_schema.migrate_multi_account_schema(cursor, conn)

    cursor.execute("SELECT account_key, account_name, ticker FROM stock_holdings")
    assert cursor.fetchone() == ("vps:11112222:01", "KR Primary", "005930")
    assert not kr_schema._table_exists(cursor, "stock_holdings_legacy")


def test_kr_schema_requires_primary_account_when_migration_needed(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE stock_holdings (
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
        INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date, current_price)
        VALUES ('005930', 'Samsung Electronics', 70000, '2026-03-01', 71000)
        """
    )
    conn.commit()

    def _raise_scope_error():
        raise RuntimeError("KIS auth unavailable")

    monkeypatch.setattr(kr_schema, "_get_primary_account_scope", _raise_scope_error)

    with pytest.raises(RuntimeError, match="Unable to verify the primary account"):
        kr_schema.migrate_multi_account_schema(cursor, conn)

    cursor.execute("PRAGMA table_info(stock_holdings)")
    assert "account_key" not in {row[1] for row in cursor.fetchall()}


def test_kr_schema_skips_scope_resolution_when_already_migrated(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    kr_schema.create_all_tables(cursor, conn)

    def _raise_scope_error():
        raise AssertionError("Primary account resolution should not be called")

    monkeypatch.setattr(kr_schema, "_get_primary_account_scope", _raise_scope_error)

    kr_schema.migrate_multi_account_schema(cursor, conn)


def test_kr_helpers_apply_account_scope(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    kr_schema.create_all_tables(cursor, conn)
    kr_schema.create_indexes(cursor, conn)

    rows = [
        (
            "vps:kr-one:01",
            "KR One",
            "005930",
            "Samsung Electronics",
            70000,
            "2026-03-01",
            71000,
            json.dumps({"sector": "Technology"}),
            "Technology",
        ),
        (
            "vps:kr-two:01",
            "KR Two",
            "005930",
            "Samsung Electronics",
            70000,
            "2026-03-02",
            70500,
            json.dumps({"sector": "Finance"}),
            "Finance",
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO stock_holdings
        (account_key, account_name, ticker, company_name, buy_price, buy_date, current_price, scenario, sector)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM stock_holdings WHERE ticker = '005930'")
    assert cursor.fetchone()[0] == 2

    assert kr_helpers.is_ticker_in_holdings(cursor, "005930", account_key="vps:kr-one:01") is True
    assert kr_helpers.get_current_slots_count(cursor, account_key="vps:kr-one:01") == 1
    assert kr_helpers.get_current_slots_count(cursor, account_key="vps:kr-two:01") == 1
    assert kr_helpers.check_sector_diversity(
        cursor,
        "Technology",
        max_same_sector=1,
        concentration_ratio=0.6,
        account_key="vps:kr-one:01",
    ) is False
    assert kr_helpers.check_sector_diversity(
        cursor,
        "Technology",
        max_same_sector=1,
        concentration_ratio=0.6,
        account_key="vps:kr-two:01",
    ) is True
