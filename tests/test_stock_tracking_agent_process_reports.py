import logging
import sys
import types

import pytest

import trading.domestic_stock_trading as domestic_trading
from stock_tracking_agent import StockTrackingAgent


class _FakeAsyncTradingContext:
    def __init__(self, account_name=None, **kwargs):
        self.account_name = account_name

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def async_buy_stock(self, stock_code, limit_price=None):
        return {
            "success": True,
            "message": f"bought for {self.account_name}",
            "partial_success": self.account_name == "kr-primary",
            "successful_accounts": ["kr-primary"],
            "failed_accounts": ["kr-secondary"],
        }


def _install_signal_modules(monkeypatch, redis_calls, gcp_calls):
    redis_module = types.ModuleType("messaging.redis_signal_publisher")
    gcp_module = types.ModuleType("messaging.gcp_pubsub_signal_publisher")

    async def publish_buy_signal(**kwargs):
        redis_calls.append(kwargs)

    async def gcp_publish_buy_signal(**kwargs):
        gcp_calls.append(kwargs)

    redis_module.publish_buy_signal = publish_buy_signal
    gcp_module.publish_buy_signal = gcp_publish_buy_signal

    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_module)
    monkeypatch.setitem(sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_module)


@pytest.mark.asyncio
async def test_process_reports_analyzes_once_and_dedupes_signals(monkeypatch, caplog):
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
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


def test_safe_account_log_label_masks_account_key():
    label = StockTrackingAgent._safe_account_log_label(
        {"name": "kr-primary", "account_key": "vps:12345678:01"}
    )

    assert label == "kr-primary (vps:12****78:01)"
