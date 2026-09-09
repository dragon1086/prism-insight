"""KR BUY safety regression tests. No broker orders or persistent databases."""
import asyncio
import sys
import sqlite3
import time
import types
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import stock_tracking_agent as mod


def scenario_agent():
    agent = mod.StockTrackingAgent.__new__(mod.StockTrackingAgent)
    agent.cursor = MagicMock()
    agent.cursor.fetchall.return_value = []
    agent._account_scope = MagicMock(return_value=("test", None))
    agent._get_current_slots_count = AsyncMock(return_value=0)
    agent.max_slots = 10
    agent.enable_journal = False
    agent.language = "ko"
    agent.trading_agent = SimpleNamespace(instruction="test instruction", attach_llm=AsyncMock())
    agent._stamp_scenario_market_regime = lambda s: s
    return agent


@pytest.mark.parametrize("budget", [None, 0, 1, 1000])
@pytest.mark.parametrize("risk_valid", [False, True])
def test_pilot_strategy_gate_is_cash_independent_not_risk_independent(monkeypatch, budget, risk_valid):
    import cores.regime_policy as rp
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    monkeypatch.setattr(rp, "get_market_pulse_state", lambda _market: "UPTREND")
    agent = scenario_agent()
    agent.active_account = {"buy_amount_krw": budget}
    agent._buy_floor_regime = lambda: "sideways"
    scenario = {"decision": "Enter", "buy_score": 6, "min_score": 5,
                "target_price": 115 if risk_valid else 99, "stop_loss": 95,
                "risk_reward_ratio": 3,
                "regime_entry_policy": {"mode": "rebound_pilot", "position_fraction": 0.5,
                                        "cash_budget": None}}
    assert agent._evaluate_production_buy_gate(scenario, 100)["allowed"] is risk_valid


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [None, 0, 50])
async def test_pending_pilot_budget_guard_never_opens_broker(monkeypatch, budget):
    agent = scenario_agent()
    broker = MagicMock(side_effect=AssertionError("must not open broker"))
    monkeypatch.setattr(mod.ExecutionService, "domestic", broker)
    prepared = SimpleNamespace(
        scenario={"regime_entry_policy": {"mode": "rebound_pilot", "position_fraction": 0.5}},
        intent=SimpleNamespace(cash_amount=budget),
    )
    result = await agent._execute_pending_kr_entry(prepared, current_price=100)
    assert result["status"] == "blocked_budget"
    assert result["quantity"] == 0
    broker.assert_not_called()


@pytest.mark.parametrize("score,pulse,cap,allowed", [
    (7, "UPTREND", None, True), (7, None, None, False),
    (7, "CORRECTION", None, False), (6, "UPTREND", 500, True),
    (6, "UPTREND", None, False), (6, "UPTREND", 1000, True),
])
@pytest.mark.parametrize("decision", ["Enter", "진입", "매수"])
def test_runtime_policy_reaches_kr_gate_and_broker_boundary(monkeypatch, score, pulse, cap, allowed, decision):
    import cores.regime_policy as rp
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    monkeypatch.setattr(rp, "get_market_pulse_state", lambda market: pulse)
    agent = scenario_agent()
    agent.active_account = {"buy_amount_krw": 1000}
    agent._buy_floor_regime = lambda: "sideways"
    scenario = {"decision": decision, "buy_score": score, "min_score": 5,
                "entry_price": 100, "target_price": 115, "stop_loss": 95,
                "risk_reward_ratio": 3, "expected_return_pct": 15, "expected_loss_pct": 5,
                "market_condition": "sideways"}
    if cap is not None:
        scenario["regime_entry_policy"] = {"mode": "rebound_pilot", "position_fraction": 0.5, "cash_budget": cap}
    result = agent._evaluate_production_buy_gate(scenario, 100, score_override=score)
    assert result["allowed"] is allowed
    validator = agent._buy_quote_validator(scenario)
    if allowed:
        validator(100)
    else:
        with pytest.raises(ValueError, match="broker quote gate"):
            validator(100)


@pytest.mark.asyncio
async def test_quote_uses_execution_service_for_read_only_access(monkeypatch):
    agent = scenario_agent()
    agent.active_account = {'name': 'fake-account'}
    service = SimpleNamespace(get_current_price=MagicMock(return_value={'current_price': 72000}),
                              execute_buy=AsyncMock(), pre_reserved_buy=AsyncMock(), sell=AsyncMock())

    @asynccontextmanager
    async def read_context():
        yield service

    factory = MagicMock(side_effect=lambda **kwargs: read_context())
    monkeypatch.setattr(mod.ExecutionService, 'domestic', factory)
    quote = await agent._get_fresh_buy_quote('005930')
    assert quote['price'] == 72000
    factory.assert_called_once_with(account_name='fake-account')
    service.get_current_price.assert_called_once_with('005930')
    service.execute_buy.assert_not_called()
    service.pre_reserved_buy.assert_not_called()
    service.sell.assert_not_called()


@pytest.mark.asyncio
async def test_three_fast_failures_serialize_entire_legacy_host(monkeypatch):
    monkeypatch.setenv("PRISM_KR_CODEX_FAST_TRADING", "1")
    agent = scenario_agent()
    state = {"active": 0, "peak": 0, "calls": 0}

    @asynccontextmanager
    async def host():
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        try:
            yield
        finally:
            await asyncio.sleep(0.01)
            state["active"] -= 1

    async def legacy(*args):
        state["calls"] += 1
        await asyncio.sleep(0.01)
        return {"decision": "No Entry"}

    monkeypatch.setattr(mod, "app", SimpleNamespace(run=host))
    monkeypatch.setattr(mod, "generate_codex_fast_async", AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(mod, "_generate_trading_scenario_json", legacy)
    results = await asyncio.gather(*(agent._extract_trading_scenario("report", "") for _ in range(3)))
    assert all(s["decision"] == "No Entry" for s in results)
    assert state == {"active": 0, "peak": 1, "calls": 3}
    assert not agent._get_db_lock().locked()


@pytest.mark.asyncio
async def test_buy_uses_buy_only_settings(monkeypatch, caplog):
    monkeypatch.setenv("PRISM_KR_CODEX_FAST_TRADING", "1")
    monkeypatch.setenv("PRISM_BUY_CODEX_MODEL", "gpt-6-astra")
    monkeypatch.setenv("PRISM_BUY_CODEX_EFFORT", "high")
    monkeypatch.setenv("PRISM_BUY_CODEX_TIMEOUT", "123")
    agent = scenario_agent()
    generate = AsyncMock(return_value=SimpleNamespace(text='{"decision":"No Entry"}', latency_s=1, mcp_calls=[{}]))
    monkeypatch.setattr(mod, "generate_codex_fast_async", generate)
    with caplog.at_level("INFO"):
        await agent._extract_trading_scenario("report", "")
    assert generate.await_args.kwargs["model"] == "gpt-6-astra"
    assert generate.await_args.kwargs["reasoning_effort"] == "high"
    assert generate.await_args.kwargs["timeout"] == 123
    agent.trading_agent.attach_llm.assert_not_awaited()
    assert "model=gpt-6-astra effort=high service_tier=fast timeout=123" in caplog.text


@pytest.mark.asyncio
async def test_simulator_quote_survives_broker_unavailable_without_db(monkeypatch):
    import trading.domestic_stock_trading as trading
    agent = scenario_agent()
    monkeypatch.setattr(trading, "AsyncTradingContext", MagicMock(side_effect=RuntimeError("no credentials")))
    data = types.ModuleType("krx_data_client")
    day = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    frame = pd.DataFrame({"Close": [72000]}, index=pd.to_datetime([day]))
    data.get_market_ohlcv_by_date = MagicMock(return_value=frame)
    monkeypatch.setitem(sys.modules, "krx_data_client", data)
    quote = await agent._get_fresh_buy_quote("005930")
    assert quote["price"] == 72000
    assert quote["source"] == "krx_same_day_close"
    assert data.get_market_ohlcv_by_date.call_args.args == (day, day, "005930")
    agent.cursor.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
async def test_no_stale_db_price_when_fresh_sources_invalid(monkeypatch, price):
    import trading.domestic_stock_trading as trading
    agent = scenario_agent()
    agent.cursor.fetchone.return_value = (70000,)
    monkeypatch.setattr(trading, "AsyncTradingContext", MagicMock(side_effect=RuntimeError("offline")))
    data = types.ModuleType("krx_data_client")
    data.get_market_ohlcv_by_date = lambda day, end, ticker: pd.DataFrame({"Close": [price]}, index=pd.to_datetime([day]))
    monkeypatch.setitem(sys.modules, "krx_data_client", data)
    with pytest.raises(ValueError):
        await agent._get_fresh_buy_quote("005930")
    agent.cursor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_krx_rewound_prior_session_quote_is_rejected(monkeypatch):
    import trading.domestic_stock_trading as trading
    agent = scenario_agent()
    monkeypatch.setattr(trading, "AsyncTradingContext", MagicMock(side_effect=RuntimeError("offline")))
    prior = datetime.now(ZoneInfo("Asia/Seoul")) - timedelta(days=1)
    data = types.ModuleType("krx_data_client")
    data.get_market_ohlcv_by_date = lambda *a: pd.DataFrame({"Close": [72000]}, index=pd.to_datetime([prior.strftime("%Y%m%d")]))
    monkeypatch.setitem(sys.modules, "krx_data_client", data)
    with pytest.raises(ValueError, match="provider date"):
        await agent._get_fresh_buy_quote("005930")
    agent.cursor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_boundary_refreshes_then_revalidates_existing_gate(monkeypatch, caplog):
    agent = scenario_agent()
    agent._get_fresh_buy_quote = AsyncMock(return_value={"price": 72000, "source": "fake", "provider_date": None, "retrieved_at_monotonic": time.monotonic()})
    agent._evaluate_production_buy_gate = MagicMock(return_value={"allowed": True})
    contract = MagicMock(side_effect=lambda scenario, **kwargs: dict(scenario))
    monkeypatch.setattr(mod, "apply_buy_scenario_contract", contract)
    scenario = {"decision": "Enter", "target_price": 90000, "stop_loss": 65000}
    analysis = {"current_price": 70000, "quote_captured_at_monotonic": time.monotonic() - 300}
    with caplog.at_level("INFO"):
        price = await agent._refresh_buy_boundary("005930", scenario, analysis, buy_score=9)
    assert price == analysis["current_price"] == 72000
    assert contract.call_args.kwargs == {"market": "KR", "entry_price": 72000}
    assert agent._evaluate_production_buy_gate.call_args.args[1] == 72000
    assert "analysis_context_age_s=" in caplog.text
    assert "exchange_age_s=unknown" in caplog.text
    agent._evaluate_production_buy_gate.return_value = {"allowed": False, "reason": "existing rule"}
    with pytest.raises(ValueError, match="existing rule"):
        await agent._refresh_buy_boundary("005930", scenario, analysis, buy_score=9)


@pytest.mark.asyncio
async def test_no_entry_null_prices_survive_watchlist_storage(monkeypatch):
    from tracking.db_schema import TABLE_WATCHLIST_HISTORY, TABLE_ANALYSIS_PERFORMANCE_TRACKER
    agent = scenario_agent()
    agent.conn = sqlite3.connect(":memory:")
    agent.cursor = agent.conn.cursor()
    agent.cursor.execute(TABLE_WATCHLIST_HISTORY)
    agent.cursor.execute(TABLE_ANALYSIS_PERFORMANCE_TRACKER)
    monkeypatch.setattr(mod, "emit_trading_context", MagicMock())
    scenario = {"decision": "NO ENTRY", "target_price": None, "stop_loss": None, "risk_reward_ratio": None}
    try:
        assert await agent._save_watchlist_item(
            ticker="005930", company_name="test", current_price=70000,
            buy_score=0, min_score=5, decision="Skip", skip_reason="No entry",
            scenario=scenario, sector="Tech",
        )
        assert agent.cursor.execute("SELECT target_price, stop_loss, risk_reward_ratio FROM watchlist_history").fetchone() == (None, None, None)
        assert agent.cursor.execute("SELECT target_price, stop_loss, risk_reward_ratio FROM analysis_performance_tracker").fetchone() == (None, None, None)
    finally:
        agent.conn.close()


@pytest.mark.asyncio
async def test_core_applies_contract_to_captured_price(monkeypatch):
    agent = scenario_agent()
    agent._extract_ticker_info = AsyncMock(return_value=("005930", "test"))
    agent._get_current_stock_price = AsyncMock(return_value=70000)
    agent._get_trading_value_rank_change = AsyncMock(return_value=(0, ""))
    agent._extract_trading_scenario = AsyncMock(return_value={"decision": "NO ENTRY", "target_price": None, "stop_loss": None})
    pdf = types.ModuleType("pdf_converter")
    pdf.pdf_to_markdown_text = lambda path: "report"
    monkeypatch.setitem(sys.modules, "pdf_converter", pdf)
    contract = MagicMock(side_effect=lambda scenario, **kw: dict(scenario))
    monkeypatch.setattr(mod, "apply_buy_scenario_contract", contract)
    result = await agent._analyze_report_core("fake.pdf")
    assert result["success"] is True
    assert result["scenario"]["target_price"] is None
    contract.assert_called_once_with(agent._extract_trading_scenario.return_value, market="KR", entry_price=70000)
    assert result["quote_captured_at_monotonic"] <= time.monotonic()
