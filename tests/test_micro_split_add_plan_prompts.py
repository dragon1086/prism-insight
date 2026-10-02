"""Micro-split add plans reach the real KR/US BUY and holdings-review prompts only while LIVE is on.

Off (unset, false, or the other market only), every prompt is byte-identical to
the baseline. On, the per-report appendix is exactly appended and the review's
next_session_add_plan is parsed from both KR sell paths (Codex Fast and mcp-agent)
and the US path, then stored on the holding row. No network, broker or MCP.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_agent_virtual_initialization import SCRIPT as INITIALIZATION_SCRIPT

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = INITIALIZATION_SCRIPT.split("async def main():", 1)[0] + r'''
from contextlib import asynccontextmanager
from types import SimpleNamespace
from prism_core import micro_split_live
from prism_core.add_plan_prompts import buy_block

language = sys.argv[3]
if market == "KR":
    import cores.corporate_status  # KR TIER0 event check (network) is outside this test
    cores.corporate_status.check_event_exit = lambda *a, **k: (False, "")
    import stock_tracking_agent as buy_module
    import stock_tracking_enhanced_agent as sell_module
else:
    buy_module = sell_module = module
table = "stock_holdings" if market == "KR" else "us_stock_holdings"
ticker = "005930" if market == "KR" else "SYNT"
prompt_language = language if market == "KR" else "en"
PLAN = {"thesis_check": "intact", "invalidation": {"close_below": 9500, "stall_sessions": 5},
        "scenarios": [
            {"id": "breakout_1", "lens": ["oneil"], "type": "breakout",
             "trigger": {"price_above": 10500, "confirm": "5m_close", "bars": 2}, "target_allocation": 0.60},
            {"id": "accel_1", "lens": ["druckenmiller"], "type": "acceleration",
             "trigger": {"gap_up_min_pct": 3, "hold_above_open_minutes": 30}, "target_allocation": 0.65}]}
reply = {}
prompts = []

async def capture(**request):
    prompts.append(request["user_prompt"])
    return SimpleNamespace(text=json.dumps(reply["value"]), latency_s=0, mcp_calls=[])

class FakeLLM:
    async def generate_str(self, message, request_params=None):
        prompts.append(message)
        return json.dumps(reply["value"])

async def fake_attach(*args, **kwargs):
    return FakeLLM()

@asynccontextmanager
async def fake_run():
    yield None

def flag(value):
    os.environ.pop("MICRO_SPLIT_LIVE_ENABLED", None)
    os.environ.pop("MICRO_SPLIT_LIVE_MARKETS", None)
    if value == "other":
        os.environ["MICRO_SPLIT_LIVE_ENABLED"] = "true"
        os.environ["MICRO_SPLIT_LIVE_MARKETS"] = "US" if market == "KR" else "KR"
    elif value is not None:
        os.environ["MICRO_SPLIT_LIVE_ENABLED"] = value

async def main():
    agent = cls(**kwargs)
    assert await agent.initialize(language=language)
    agent._get_trend_facts = lambda t: "SYNTHETIC_TREND_NO_PROVIDER"
    agent._pipeline_market_regime = "moderate_bull"
    buy_module.generate_codex_fast_async = capture
    sell_module.generate_codex_fast_async = capture

    # BUY: off variants are byte-identical; on appends exactly the add_plan block.
    reply["value"] = {"decision": "No Entry", "entry_price": 100, "stop_loss": 95, "target_price": 120,
                      "buy_score": 0, "rationale": "synthetic"}
    seen = []
    for value in (None, "false", "other", "true"):
        flag(value)
        await agent._extract_trading_scenario("# synthetic report", ticker=ticker)
        seen.append(prompts[-1])
    baseline = seen[0]
    assert seen[1] == baseline and seen[2] == baseline
    # LIVE on: the score-floor block (PR #873) and then the add_plan block are appended.
    expected = micro_split_live.buy_prompt_block(market, language) + buy_block(market, prompt_language, None)
    assert seen[3].startswith(baseline) and seen[3][len(baseline):] == expected

    # Holdings review on a micro-split row.
    record = micro_split_live.entry_record(
        plan={"initial_nominal": "0.45", "policy_version": "v3", "plan_hash": "h", "entry_reference": 10000},
        unit_amount=1000000, market=market, entered_at="2026-10-01T00:35:00+00:00")
    scenario = json.dumps({"sector": "IT", "stop_loss": 9300, "micro_split": record})
    agent.cursor.execute(
        "INSERT INTO " + table + " (account_key, ticker, company_name, buy_price, buy_date, current_price, "
        "scenario, target_price, stop_loss) VALUES (?,?,?,?,?,?,?,?,?)",
        (agent._account_scope()[0], ticker, "Synthetic", 10000, "2026-10-01 09:35:00", 10300, scenario, 12000, 9300))
    agent.conn.commit()
    row_id = agent.cursor.execute("SELECT id FROM " + table).fetchone()[0]

    def stock():
        row = agent.cursor.execute("SELECT scenario FROM " + table + " WHERE id=?", (row_id,)).fetchone()
        return {"id": row_id, "ticker": ticker, "company_name": "Synthetic", "buy_price": 10000,
                "buy_date": "2026-10-01 09:35:00", "current_price": 10300, "target_price": 12000,
                "stop_loss": 9300, "scenario": row[0]}

    def block():
        row = agent.cursor.execute("SELECT scenario FROM " + table + " WHERE id=?", (row_id,)).fetchone()
        return json.loads(row[0])["micro_split"]

    hold = {"should_sell": False, "sell_reason": "hold", "confidence": 6, "analysis_summary": {},
            "portfolio_adjustment": {"needed": False}, "next_session_add_plan": PLAN}
    reply["value"] = hold
    os.environ["PRISM_" + market + "_CODEX_FAST_SELL"] = "1"
    flag(None)
    await agent._analyze_sell_decision(stock())  # first tracking persists highest_price (prompt note)
    reviews = []
    for value in (None, "false", "other"):
        flag(value)
        await agent._analyze_sell_decision(stock())
        reviews.append(prompts[-1])
        assert "add_plan" not in block()
    assert reviews[1] == reviews[0] and reviews[2] == reviews[0]
    flag("true")
    data = stock()
    await agent._analyze_sell_decision(data)
    expected = micro_split_live.review_prompt_block(data["scenario"], market=market, language=prompt_language,
                                                    stop_loss=9300)
    assert expected.startswith("\n\n### ") and prompts[-1] == reviews[0] + expected
    stored = block()["add_plan"]
    assert stored["status"] == "ACTIVE" and stored["source"] == "REVIEW"
    assert [s["id"] for s in stored["scenarios"]] == ["breakout_1", "accel_1"]
    paths = ["codex"]
    if market == "KR":
        # mcp-agent fallback path parses the same JSON (here a sell decision that cancels the plan).
        os.environ["PRISM_KR_CODEX_FAST_SELL"] = "0"
        sell_module.attach_isolated_llm = fake_attach
        agent.sell_decision_agent = SimpleNamespace(instruction="x", attach_llm=fake_attach)
        agent._instance_mcp_app = SimpleNamespace(run=fake_run, context=None)
        reply["value"] = dict(hold, should_sell=True, sell_reason="weak")
        count = len(prompts)
        await agent._analyze_sell_decision(stock())
        assert len(prompts) == count + 1
        assert block()["add_plan"]["status"] == "CANCELLED"
        paths.append("mcp")
    assert len(block()["add_plan_history"]) == len(paths)
    agent.conn.close()
    print(json.dumps({"ok": True, "paths": paths}))

asyncio.run(main())
'''


@pytest.mark.parametrize("kind, language", [("KR_ENHANCED", "ko"), ("KR_ENHANCED", "en"), ("US", "ko")])
def test_add_plan_prompts_are_live_only_and_review_plans_are_stored(tmp_path, kind, language):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    for key in ("MICRO_SPLIT_LIVE_ENABLED", "MICRO_SPLIT_LIVE_MARKETS", "MICRO_SPLIT_LIVE_ADDS_ENABLED"):
        env.pop(key, None)
    market = "US" if kind == "US" else "KR"
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               DECISION_INPUT_SHADOW_ENABLED="false", PRISM_BUY_CODEX_MODEL="gpt-6-astra",
               PRISM_BUY_CODEX_EFFORT="low", PRISM_BUY_CODEX_TIMEOUT="90",
               **{f"PRISM_{market}_CODEX_FAST_TRADING": "1"})
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), kind, language],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out == {"ok": True, "paths": ["codex", "mcp"] if market == "KR" else ["codex"]}
