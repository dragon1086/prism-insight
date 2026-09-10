"""Fresh real-class pipeline tests with synthetic decision/read doubles.

No actual model call, prospective signal, fill or SHADOW profitability evidence.
Reuse the initializer's independent credential/network audit in a fresh process.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_agent_virtual_initialization import SCRIPT as INITIALIZATION_SCRIPT

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = INITIALIZATION_SCRIPT.split("async def main():", 1)[0] + r'''
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
import hashlib
from prism_core import isolated_strategy_effects as effects
from prism_core.strategy_ledger import StrategyLedger
from dataclasses import replace
mode = sys.argv[3]

def deny(*args, **kwargs):
    blocked.append("EXTERNAL_EFFECT")
    raise AssertionError("EXTERNAL_EFFECT_FORBIDDEN")

def envelope(value, stamp):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"value": value, "source": "synthetic full-class fixture", "value_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "as_of": stamp, "observed_at": stamp}

async def main():
    agent = cls(**kwargs)
    assert await agent.initialize()
    for name, arguments in [("_buy_stock_with_position", ("SYNTHETIC", "test", 100, {})),
                            ("send_telegram_message", (None,))]:
        try:
            await getattr(agent, name)(*arguments)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Direct legacy effect helper was not guarded: " + name)
    old, new = ("005930", "000660") if market == "KR" else ("SNDK", "SPGI")
    stamp = datetime.now(timezone.utc).isoformat()
    payload = {"quote": {ticker: envelope(100, stamp) for ticker in (old, new)},
        "regime": {"MARKET": envelope({"regime": "moderate_bull", "summary": "synthetic regime fixture"}, stamp)},
        "pulse": {"MARKET": envelope("UPTREND", stamp)},
        "journal": {ticker: envelope({"context": "synthetic empty-history fixture", "adjustment": 0, "reasons": []}, stamp) for ticker in (old, new)},
        "corporate_status": {ticker: envelope("00", stamp) for ticker in (old, new)},
        "corporate_event": {ticker: envelope({"should_exit": False, "reason": "synthetic no-event fixture"}, stamp) for ticker in (old, new)}}
    if mode == "missing_quote":
        payload["quote"].pop(old)
    if mode == "hold":
        payload["quote"][old] = envelope(110, stamp)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    ledger = StrategyLedger(root / "strategy.sqlite")
    ledger.create_book("book", market, mode="SHADOW")
    registration = effects.EffectsRegistration("case", "book", "a" * 64, stamp,
        ((old, "old-campaign"), (new, "new-campaign")), context_hash=digest)
    seed_adapter = effects.IsolatedStrategyEffects(agent, ledger, registration)
    seed_adapter.record_entry(ticker=old, company_name="SYNTHETIC old", price=100,
        scenario={"stop_loss": 95, "target_price": 120}, is_add=False)
    adapter = effects.IsolatedStrategyEffects(agent, ledger, registration,
        pipeline_context=effects.EffectsPipelineContext("case", digest, raw))
    agent._no_order_effects = adapter
    effects.EFFECTS_RUNTIME_ENABLED = True  # UNIT PROCESS ONLY, never source flag.
    # These are explicit decision/source doubles, not a fabricated provider or
    # successful ExecutionService receipt. Existing deterministic gates remain.
    scenario = {"stop_loss": 95, "target_price": 120, "buy_score": 9, "min_score": 7,
        "decision": "Enter", "market_condition": "moderate_bull", "sector": "Technology",
        "entry_price": 100, "expected_return_pct": 20, "expected_loss_pct": 5,
        "risk_reward_ratio": 4, "rationale": "synthetic pipeline fixture"}
    core = {"success": True, "ticker": new, "company_name": "SYNTHETIC new", "current_price": 100,
        "scenario": scenario, "sector": "Technology", "decision": "entry" if market == "US" else "Enter"}
    if mode == "model_failure":
        core = {"success": False, "error": "synthetic failed model"}
    if mode == "projection_failure":
        def projection_failure(*args):
            raise ValueError("synthetic sink failure")
        adapter._project = projection_failure
    agent._analyze_report_core = AsyncMock(return_value=core)
    agent._analyze_sell_decision = AsyncMock(return_value=(True, "synthetic SELL decision double"))
    if mode == "cooldown":
        agent._analyze_sell_decision = AsyncMock(return_value=(True, "Stop-loss synthetic SELL decision double"))
    if mode == "hold":
        agent._analyze_sell_decision = AsyncMock(return_value=(False, "synthetic HOLD decision double"))
    if mode == "delayed_sell":
        async def delayed_sell(stock):
            effects._source_now = lambda: (datetime.fromisoformat(stamp) + timedelta(seconds=180)).isoformat()
            return True, "synthetic SELL after elapsed source clock"
        agent._analyze_sell_decision = delayed_sell
    agent._get_current_stock_price = AsyncMock(side_effect=deny)
    agent._get_fresh_buy_quote = AsyncMock(side_effect=deny)
    agent._refresh_buy_quote = AsyncMock(side_effect=deny)
    agent._buy_stock_with_position = AsyncMock(side_effect=deny)
    agent.sell_stock = AsyncMock(side_effect=deny)
    if market == "US":
        policy = agent._regime_policy_mod()
    else:
        import cores.regime_policy as policy
    policy.get_market_pulse_state = lambda *args: "UPTREND"
    # Fixed sources for policy evaluation; no real account cash is introduced.
    agent._get_trigger_win_rate = lambda *args, **kw: {}
    if mode not in {"success", "hold", "cooldown"}:
        try:
            await agent.process_reports(["SYNTHETIC-report.pdf"])
        except effects.EffectsFailure:
            pass
        else:
            raise AssertionError("An incomplete case was reported as a completed empty batch")
        assert not any(c["symbol"] == new for c in ledger.snapshot("book")["campaigns"])
        if mode == "projection_failure":
            assert ledger.snapshot("book")["occupied_slots"] == 0
        assert blocked == [], blocked
        print(json.dumps({"real_class_pipeline": kind, "failure": mode, "case_failed_closed": True}))
        agent.conn.close()
        return
    result = await agent.process_reports(["SYNTHETIC-report.pdf"])
    expected_sells = 0 if mode == "hold" else 1
    assert result == (1, expected_sells), (result, ledger.snapshot("book"), adapter.observations)
    snapshot = ledger.snapshot("book")
    assert snapshot["occupied_slots"] == (2 if mode == "hold" else 1) and snapshot["executions"] == []
    old_campaign = next(c for c in snapshot["campaigns"] if c["symbol"] == old)
    assert old_campaign["status"] == ("OPEN" if mode == "hold" else "CLOSED")
    if mode == "hold":
        assert float(old_campaign["unrealized_contribution"]) == .1
    assert next(c for c in snapshot["campaigns"] if c["symbol"] == new)["status"] == "OPEN"
    if mode == "cooldown":
        registration2 = replace(registration, case_id="reentry", campaigns=((old, "reentry-campaign"), (new, "new-campaign")))
        adapter2 = effects.IsolatedStrategyEffects(agent, ledger, registration2,
            pipeline_context=effects.EffectsPipelineContext("reentry", digest, raw))
        agent._no_order_effects = adapter2
        agent._analyze_sell_decision = AsyncMock(return_value=(False, "synthetic HOLD on remaining campaign"))
        agent._analyze_report_core = AsyncMock(return_value={**core, "ticker": old, "company_name": "SYNTHETIC attempted reentry"})
        assert await agent.process_reports(["SYNTHETIC-reentry.pdf"]) == (0, 0)
        assert not any(c["campaign_id"] == "reentry-campaign" for c in ledger.snapshot("book")["campaigns"])
    assert blocked == [], blocked
    assert agent.telegram_token is None and agent.telegram_bot is None
    assert calls == [], "No MCP/model should start in a synthetic decision test"
    print(json.dumps({"real_class_pipeline": kind, "synthetic_decisions": True, "buy": 1, "sell": expected_sells,
        "broker_or_network_attempts": 0, "account_executions": 0}))
    agent.conn.close()
asyncio.run(main())
'''


@pytest.mark.parametrize("kind", ["KR", "KR_ENHANCED", "US"])
@pytest.mark.parametrize("mode", ["success", "missing_quote", "model_failure", "projection_failure", "delayed_sell", "hold", "cooldown"])
def test_real_class_no_order_pipeline_in_fresh_audited_process(tmp_path, kind, mode):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
        PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), kind, mode],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr[-9000:]
    if mode not in {"success", "hold", "cooldown"}:
        assert json.loads(result.stdout.strip().splitlines()[-1]) == {
            "real_class_pipeline": kind, "failure": mode, "case_failed_closed": True}
        return
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "real_class_pipeline": kind, "synthetic_decisions": True, "buy": 1, "sell": 0 if mode == "hold" else 1,
        "broker_or_network_attempts": 0, "account_executions": 0,
    }
