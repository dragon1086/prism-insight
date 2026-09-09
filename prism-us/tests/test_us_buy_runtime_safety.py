from __future__ import annotations

import asyncio
import builtins
import io
import json
import sqlite3
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_us_stock_tracking_agent_process_reports as base

mod = base.us_agent_module
Agent = base.USStockTrackingAgent


@pytest.mark.parametrize("score,pulse,cap,allowed", [
    (7, "UPTREND", None, True), (7, None, None, False),
    (7, "CORRECTION", None, False), (6, "UPTREND", 500, True),
    (6, "UPTREND", None, False), (6, "UPTREND", 1000, False),
])
@pytest.mark.parametrize("decision", ["entry", "진입", "매수"])
def test_runtime_policy_reaches_us_gate_and_broker_boundary(monkeypatch, score, pulse, cap, allowed, decision):
    # US historically recognizes "진입", not "매수". Do not broaden entry permission.
    if score == 6 and decision == "매수":
        allowed = False
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    agent = Agent.__new__(Agent)
    agent.active_account = {"buy_amount_usd": 1000}
    agent._buy_floor_regime = lambda: "sideways"
    rp = agent._regime_policy_mod()
    monkeypatch.setattr(rp, "get_market_pulse_state", lambda market: pulse)
    scenario = {"decision": decision, "buy_score": score, "min_score": 5,
                "entry_price": 100, "target_price": 115, "stop_loss": 95,
                "risk_reward_ratio": 3, "expected_return_pct": 15, "expected_loss_pct": 5,
                "market_condition": "sideways"}
    if cap is not None:
        scenario["regime_entry_policy"] = {"mode": "rebound_pilot", "position_fraction": 0.5, "cash_budget": cap}
    result = agent._evaluate_production_buy_gate(scenario, 100, score_override=score)
    assert result["allowed"] is allowed
    validator = agent._buy_quote_validator(scenario, score_override=score)
    if allowed:
        validator(100)
    else:
        with pytest.raises(ValueError, match="broker quote gate"):
            validator(100)


@pytest.fixture(autouse=True)
def offline_transitive_market_data(monkeypatch):
    import pandas as pd
    import yfinance as yf
    ticker = types.SimpleNamespace(info={}, history=lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(yf, "Ticker", lambda *args, **kwargs: ticker)
    monkeypatch.setattr(yf, "download", lambda *args, **kwargs: pd.DataFrame())


@pytest.mark.parametrize("value", [None, False, 0, -1, "nan", float("inf"), "bad"])
def test_invalid_fresh_buy_quote_is_rejected(value):
    with pytest.raises(ValueError):
        Agent._validated_buy_quote(value)


@pytest.mark.asyncio
async def test_independent_quote_has_no_previous_close_or_db_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(
        Ticker=lambda ticker: types.SimpleNamespace(info={"previousClose": 100})
    ))
    agent = Agent.__new__(Agent)
    with pytest.raises(ValueError):
        await agent._refresh_buy_quote("AAPL")


def make_agent(monkeypatch):
    agent = Agent.__new__(Agent)
    agent.account_configs = [
        {"name": name, "account_key": f"vps:{name}:01", "product": "01", "buy_amount_usd": 1000}
        for name in ("primary", "secondary")
    ]
    agent.active_account = None
    agent.db_path = ":memory:"
    agent.conn = sqlite3.connect(":memory:")
    agent.cursor = agent.conn.cursor()
    agent.max_slots = 10
    agent.enable_journal = False
    agent.update_holdings = AsyncMock(return_value=[])
    agent._is_ticker_in_holdings = AsyncMock(return_value=False)
    agent._get_current_slots_count = AsyncMock(return_value=0)
    agent._check_sector_diversity = AsyncMock(return_value=True)
    agent._regime_policy_mod = lambda: None
    agent._buy_floor_regime = lambda: "strong_bull"
    agent._evaluate_production_buy_gate = MagicMock(return_value={"allowed": True})
    agent._save_watchlist_item = AsyncMock(return_value=True)
    agent._link_position_entry_intent = MagicMock()
    agent._refresh_buy_quote = AsyncMock(return_value=101)
    base._install_signal_modules(monkeypatch, [], [])
    monkeypatch.setitem(sys.modules, "reentry_cooldown", types.SimpleNamespace(
        reentry_block=lambda *args, **kwargs: None,
        COOLDOWN_LIVE=False, COOLDOWN_RISK_EXIT_LIVE=False,
    ))
    for name in ("emit_trading_context", "emit_micro_split_shadow", "emit_fill_reconciliation"):
        monkeypatch.setattr(mod, name, MagicMock())
    return agent


def core_result(ticker):
    return {
        "success": True, "ticker": ticker, "company_name": ticker,
        "current_price": 100, "decision": "entry", "raw_decision": "entry",
        "sector": "Technology", "rank_change_msg": "",
        "scenario": {"decision": "entry", "buy_score": 9, "min_score": 7,
                     "expected_return_pct": 20, "expected_loss_pct": 10,
                     "target_price": 120, "stop_loss": 90, "risk_reward_ratio": 2,
                     "investment_period": "short", "rationale": "test"},
    }


@pytest.mark.asyncio
async def test_real_failed_holdings_review_excludes_only_failed_account(monkeypatch):
    agent = make_agent(monkeypatch)
    agent.update_holdings = Agent.update_holdings.__get__(agent, Agent)
    agent._get_live_regime_safe = MagicMock(return_value="strong_bull")
    agent.cursor = MagicMock()
    agent.cursor.fetchall.return_value = []
    reviews = []

    def query(sql, params=()):
        if "FROM us_stock_holdings" in sql:
            reviews.append(params[0])
            if params[0] == "vps:primary:01":
                raise sqlite3.OperationalError("incomplete primary holdings review")

    agent.cursor.execute.side_effect = query
    agent._analyze_report_core = AsyncMock(return_value=core_result("AAPL"))
    buys = []

    async def buy(*args, **kwargs):
        buys.append(agent.active_account["name"])
        return base.LegacyPositionWriteResult(True, len(buys))

    agent._buy_stock_with_position = buy
    agent.sell_stock = AsyncMock()
    monkeypatch.setattr(mod.ExecutionService, "us", MagicMock(side_effect=RuntimeError("no broker")))
    try:
        assert await agent.process_reports(["AAPL"]) == (1, 0)
        assert reviews == ["vps:primary:01", "vps:secondary:01"]
        assert buys == ["secondary"]
        agent.sell_stock.assert_not_awaited()
        # Existing non-batch callers keep the previous empty-list error behavior.
        agent.active_account = agent.account_configs[0]
        assert await agent.update_holdings() == []
    finally:
        agent.conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("broker_quote", [102, None, float("nan"), 125])
async def test_buy_uses_fresh_quotes_and_preserves_simulator_on_broker_failure(monkeypatch, broker_quote):
    agent = make_agent(monkeypatch)
    agent.account_configs = agent.account_configs[:1]
    agent._analyze_report_core = AsyncMock(return_value=core_result("AAPL"))
    agent._buy_stock_with_position = AsyncMock(return_value=base.LegacyPositionWriteResult(True, 1))

    broker = MagicMock()
    broker.get_current_price.return_value = {"current_price": broker_quote}
    broker.execute_buy = AsyncMock(return_value={"success": True, "message": "fake"})
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=broker)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(mod.ExecutionService, "us", MagicMock(return_value=context))
    try:
        assert await agent.process_reports(["AAPL"]) == (1, 0)
        assert agent._buy_stock_with_position.await_args.args[2] == 101
        assert agent._evaluate_production_buy_gate.call_args_list[0].args[1] == 101
        if broker_quote == 102:
            assert broker.execute_buy.await_args.kwargs["limit_price"] == 102
            assert float(broker.execute_buy.await_args.kwargs["intent"].limit_price) == 102
        else:
            broker.execute_buy.assert_not_awaited()
    finally:
        agent.conn.close()


@pytest.mark.asyncio
async def test_broker_quote_inside_levels_rechecks_existing_risk_gate(monkeypatch):
    agent = make_agent(monkeypatch)
    agent.account_configs = agent.account_configs[:1]
    analysis = core_result("AAPL")
    analysis["scenario"].update({
        "entry_price": 100, "target_price": 110, "stop_loss": 95,
        "risk_reward_ratio": 2, "expected_return_pct": 10,
        "expected_loss_pct": 5, "_deterministic_market_regime": "strong_bull",
    })
    agent._analyze_report_core = AsyncMock(return_value=analysis)
    agent._refresh_buy_quote = AsyncMock(return_value=100)
    agent._buy_stock_with_position = AsyncMock(return_value=base.LegacyPositionWriteResult(True, 1))
    real_gate = Agent._evaluate_production_buy_gate.__get__(agent, Agent)
    initial = real_gate(analysis["scenario"], 100, score_override=9, is_add=False)
    refreshed = real_gate(analysis["scenario"], 106, score_override=9, is_add=False)
    assert initial["allowed"] is True
    assert {"rr_below_floor", "stop_exceeds_regime_limit"} <= {
        finding["code"] for finding in refreshed["findings"]
    }
    agent._evaluate_production_buy_gate = MagicMock(wraps=real_gate)
    broker = MagicMock()
    broker.get_current_price.return_value = {"current_price": 106}
    broker.execute_buy = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=broker)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(mod.ExecutionService, "us", MagicMock(return_value=context))
    create_intent = MagicMock(side_effect=AssertionError("rejected quote must not create intent"))
    monkeypatch.setattr(mod.OrderIntent, "create", create_intent)
    try:
        assert await agent.process_reports(["AAPL"]) == (1, 0)
        agent._buy_stock_with_position.assert_awaited_once()
        assert agent._buy_stock_with_position.await_args.args[2] == 100
        assert [call.args[1] for call in agent._evaluate_production_buy_gate.call_args_list] == [100, 106]
        assert agent._evaluate_production_buy_gate.call_args.kwargs == {"score_override": 9, "is_add": False}
        create_intent.assert_not_called()
        broker.execute_buy.assert_not_awaited()
    finally:
        agent.conn.close()


@pytest.mark.asyncio
async def test_parallel_three_analysis_then_sequential_account_buys_in_input_order(monkeypatch):
    monkeypatch.setattr(mod, "US_TRADING_ANALYSIS_CONCURRENCY", 3)
    agent = make_agent(monkeypatch)
    paths = ["A", "B", "C", "D"]
    active = peak = 0
    buys = []

    async def core(path):
        nonlocal active, peak
        assert agent.active_account["name"] == "primary"
        assert agent.update_holdings.await_count == 2
        active += 1
        peak = max(peak, active)
        await asyncio.sleep({"A": .04, "B": .03, "C": .01, "D": .01}[path])
        active -= 1
        return core_result(path)

    async def buy(ticker, *args, **kwargs):
        assert active == 0
        buys.append((agent.active_account["name"], ticker))
        await asyncio.sleep(0)
        return base.LegacyPositionWriteResult(True, len(buys))

    agent._analyze_report_core = core
    agent._buy_stock_with_position = buy
    # Missing broker configuration must not prevent independent simulator buys.
    monkeypatch.setattr(mod.ExecutionService, "us", MagicMock(side_effect=RuntimeError("no broker")))
    try:
        assert await agent.process_reports(paths) == (8, 0)
        assert peak == 3
        assert buys == [(account, ticker) for account in ("primary", "secondary") for ticker in paths]
        assert agent.update_holdings.await_count == 2
    finally:
        agent.conn.close()


@pytest.mark.asyncio
async def test_bad_market_quote_blocks_ledger_and_broker(monkeypatch):
    agent = make_agent(monkeypatch)
    agent._analyze_report_core = AsyncMock(return_value=core_result("AAPL"))
    agent._refresh_buy_quote = AsyncMock(side_effect=ValueError("missing quote"))
    agent._buy_stock_with_position = AsyncMock()
    broker_factory = MagicMock()
    monkeypatch.setattr(mod.ExecutionService, "us", broker_factory)
    try:
        assert await agent.process_reports(["AAPL"]) == (0, 0)
        agent._buy_stock_with_position.assert_not_awaited()
        broker_factory.assert_not_called()
    finally:
        agent.conn.close()


@pytest.mark.parametrize("last,strict,expected", [("", True, None), ("", False, 100), ("nan", True, None), ("102", True, 102)])
def test_broker_strict_quote_rejects_base_without_changing_default(monkeypatch, last, strict, expected):
    original_open = builtins.open
    def fake_open(path, *args, **kwargs):
        if str(path).endswith("kis_devlp.yaml"):
            return io.StringIO("{}")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setitem(sys.modules, "kis_auth", MagicMock())
    old_path = list(sys.path)
    try:
        trading = base._load_module("us_strict_quote_tests", base.PRISM_US_DIR / "trading/us_stock_trading.py")
    finally:
        sys.path[:] = old_path
    trader = trading.USStockTrading.__new__(trading.USStockTrading)
    response = MagicMock()
    response.isOK.return_value = True
    response.getBody.return_value = types.SimpleNamespace(output={"last": last, "base": "100"})
    trader._request = MagicMock(return_value=response)
    quote = trader.get_current_price("AAPL", "NASD", strict=strict)
    assert (quote["current_price"] if quote else None) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("market_open,reserved_available", [(True, True), (False, True), (False, False)])
async def test_internal_refetch_cannot_replace_validated_order_limit(monkeypatch, market_open, reserved_available):
    original_open = builtins.open
    def fake_open(path, *args, **kwargs):
        if str(path).endswith("kis_devlp.yaml"):
            return io.StringIO("{}")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setitem(sys.modules, "kis_auth", MagicMock())
    old_path = list(sys.path)
    try:
        trading = base._load_module("us_validated_limit_tests", base.PRISM_US_DIR / "trading/us_stock_trading.py")
    finally:
        sys.path[:] = old_path
    trader = trading.USStockTrading.__new__(trading.USStockTrading)
    trader.buy_amount = 1000
    trader.auto_trading = True
    trader.mode = "demo"
    trader.trenv = types.SimpleNamespace(my_acct="fake", my_prod="01")
    trader._get_stock_lock = AsyncMock(return_value=asyncio.Lock())
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    # This later read differs radically from the caller-validated quote.
    trader.get_current_price = MagicMock(return_value={"current_price": 50})
    trader.is_market_open = MagicMock(return_value=market_open)
    trader.is_reserved_order_available = MagicMock(return_value=reserved_available)
    trader._queue_pending_order = MagicMock(return_value={"success": True, "order_no": "queued"})
    response = MagicMock()
    response.isOK.return_value = True
    response.getBody.return_value = types.SimpleNamespace(output={"ODNO": "fake-order"})
    trader._request = MagicMock(return_value=response)
    monkeypatch.setattr(trading.asyncio, "sleep", AsyncMock())
    result = await trader.async_buy_stock("AAPL", 1000, "NASD", limit_price=100)
    assert result["success"] is True
    trader.get_current_price.assert_called_once()
    if not market_open and not reserved_available:
        trader._request.assert_not_called()
        assert trader._queue_pending_order.call_args.kwargs["limit_price"] == 100
        assert trader._queue_pending_order.call_args.kwargs["buy_amount"] == 1000
    else:
        params = trader._request.call_args.args[2]
        assert params["ORD_QTY" if market_open else "FT_ORD_QTY"] == "10"
        assert params["OVRS_ORD_UNPR" if market_open else "FT_ORD_UNPR3"] == "100.00"


@pytest.mark.asyncio
async def test_no_entry_null_levels_store_without_quote_or_formatting_failure(monkeypatch):
    agent = make_agent(monkeypatch)
    agent.cursor = MagicMock()
    agent.cursor.lastrowid = 1
    agent._get_trigger_win_rate = MagicMock(return_value=None)
    agent._msg_types = []
    agent.message_queue = []
    scenario = mod.apply_buy_scenario_contract({
        "decision": "no_entry", "target_price": None, "stop_loss": None,
        "risk_reward_ratio": None, "expected_return_pct": None,
        "expected_loss_pct": None,
    }, market="US", entry_price=100)
    try:
        result = await Agent._save_watchlist_item(
            agent, "AAPL", "Apple", 100, 5, 7, "no_entry", "not enough evidence", scenario, "Technology"
        )
        assert result is True
        performance_params = agent.cursor.execute.call_args_list[1].args[1]
        assert performance_params[5] == "NEUTRAL"
        assert performance_params[6:8] == (None, None)
        agent._refresh_buy_quote.assert_not_awaited()
    finally:
        agent.conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,expected_success", [("entry", False), ("no_entry", True)])
async def test_core_validates_entry_but_preserves_no_entry_nulls(monkeypatch, decision, expected_success):
    agent = Agent.__new__(Agent)
    agent._extract_ticker_info = AsyncMock(return_value=("AAPL", "Apple"))
    agent._get_current_stock_price = AsyncMock(return_value=100)
    agent._get_trading_value_rank_change = AsyncMock(return_value=(0, ""))
    scenario = {"decision": decision, "target_price": None, "stop_loss": None,
                "risk_reward_ratio": None, "expected_return_pct": None,
                "expected_loss_pct": None}
    agent._extract_trading_scenario = AsyncMock(return_value=scenario)
    monkeypatch.setitem(sys.modules, "pdf_converter", types.SimpleNamespace(pdf_to_markdown_text=lambda path: "report"))
    result = await agent._analyze_report_core("fake.pdf")
    assert result["success"] is expected_success
    if expected_success:
        assert result["scenario"]["target_price"] is None
        assert result["scenario"]["stop_loss"] is None
        assert result["analysis_quote_at"] >= result["context_started_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_us_buy_routes_astra_effort_and_timeout_to_async_backend(monkeypatch, cancelled):
    agent = make_agent(monkeypatch)
    agent.active_account = agent.account_configs[0]
    agent.cursor = MagicMock()
    agent.cursor.fetchall.return_value = []
    agent.get_journal_context = MagicMock(return_value="")
    agent.get_score_adjustment = MagicMock(return_value=(0, []))
    agent._get_trend_facts = MagicMock(return_value="")
    agent._stamp_scenario_market_regime = lambda scenario: scenario
    agent.trading_agent = types.SimpleNamespace(instruction="test instruction")
    monkeypatch.setenv("PRISM_US_CODEX_FAST_TRADING", "1")
    monkeypatch.setenv("PRISM_BUY_CODEX_MODEL", "gpt-6-astra")
    monkeypatch.setenv("PRISM_BUY_CODEX_EFFORT", "high")
    monkeypatch.setenv("PRISM_BUY_CODEX_TIMEOUT", "180")
    backend = AsyncMock(return_value=types.SimpleNamespace(
        text=json.dumps(core_result("AAPL")["scenario"]), latency_s=1, mcp_calls=[{}]
    ))
    if cancelled:
        backend.side_effect = asyncio.CancelledError
    monkeypatch.setattr(mod, "generate_codex_fast_async", backend)
    monkeypatch.setattr(mod, "generate_codex_fast", MagicMock(side_effect=AssertionError("BUY must be async")))
    try:
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await agent._extract_trading_scenario("report", ticker="AAPL")
        else:
            result = await agent._extract_trading_scenario("report", ticker="AAPL")
            assert result["decision"] == "entry"
        assert backend.await_args.kwargs["model"] == "gpt-6-astra"
        assert backend.await_args.kwargs["reasoning_effort"] == "high"
        assert backend.await_args.kwargs["timeout"] == 180
        assert backend.await_args.kwargs["mcp_profile"] == "us_trading"
    finally:
        agent.conn.close()


@pytest.mark.asyncio
async def test_failed_codex_preserves_serialized_legacy_fallback(monkeypatch):
    agent = make_agent(monkeypatch)
    agent.active_account = agent.account_configs[0]
    agent.cursor = MagicMock()
    agent.cursor.fetchall.return_value = []
    agent.get_journal_context = MagicMock(return_value="")
    agent.get_score_adjustment = MagicMock(return_value=(0, []))
    agent._get_trend_facts = MagicMock(return_value="")
    agent._stamp_scenario_market_regime = lambda scenario: scenario
    active = peak = 0

    async def legacy(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return json.dumps(core_result("AAPL")["scenario"])

    @asynccontextmanager
    async def run():
        yield

    agent.trading_agent = types.SimpleNamespace(
        instruction="test", attach_llm=AsyncMock(return_value=types.SimpleNamespace(generate_str=legacy))
    )
    monkeypatch.setattr(mod, "app", types.SimpleNamespace(run=run))
    monkeypatch.setenv("PRISM_US_CODEX_FAST_TRADING", "1")
    monkeypatch.setattr(mod, "generate_codex_fast_async", AsyncMock(side_effect=RuntimeError("unavailable")))
    try:
        results = await asyncio.gather(*(agent._extract_trading_scenario("report", ticker=ticker) for ticker in ("A", "B", "C")))
        assert peak == 1
        assert all(result["decision"] == "entry" for result in results)
    finally:
        agent.conn.close()
