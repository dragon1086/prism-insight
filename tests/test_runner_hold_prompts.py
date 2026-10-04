"""Runner hold rule in the real KR/US sell-review paths (no network, broker or MCP).

A non-runner prompt is byte-identical with the rule on or off; a protected runner
gets exactly the per-holding appendix, and the parsed LLM decision is guarded
before its side effects (a target sell becomes a hold, a trailing new_stop_loss
is not written to the row).
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
from types import SimpleNamespace
from prism_core import runner_hold as R
from prism_core import runner_hold_live as L

language = sys.argv[3]
if market == "KR":
    import cores.corporate_status  # KR TIER0 event check (network) is outside this test
    cores.corporate_status.check_event_exit = lambda *a, **k: (False, "")
    import stock_tracking_enhanced_agent as sell_module
else:
    sell_module = module
table = "stock_holdings" if market == "KR" else "us_stock_holdings"
ticker = "005930" if market == "KR" else "SYNT"
prompt_language = language if market == "KR" else "en"
reply, prompts = {}, []

async def capture(**request):
    prompts.append(request["user_prompt"])
    return SimpleNamespace(text=json.dumps(reply["value"]), latency_s=0, mcp_calls=[])

async def main():
    agent = cls(**kwargs)
    assert await agent.initialize(language=language)
    sell_module.generate_codex_fast_async = capture
    os.environ["PRISM_" + market + "_CODEX_FAST_SELL"] = "1"
    agent.cursor.execute(
        "INSERT INTO " + table + " (account_key, ticker, company_name, buy_price, buy_date, current_price, "
        "scenario, target_price, stop_loss) VALUES (?,?,?,?,?,?,?,?,?)",
        (agent._account_scope()[0], ticker, "Synthetic", 100, "2026-08-03 10:00:00", 130,
         json.dumps({"sector": "IT", "stop_loss": 93}), 125, 100))
    agent.conn.commit()
    row_id = agent.cursor.execute("SELECT id FROM " + table).fetchone()[0]

    def stock():
        row = agent.cursor.execute("SELECT scenario, stop_loss FROM " + table + " WHERE id=?", (row_id,)).fetchone()
        return {"id": row_id, "ticker": ticker, "company_name": "Synthetic", "buy_price": 100,
                "buy_date": "2026-08-03 10:00:00", "current_price": 130, "target_price": 125,
                "stop_loss": row[1], "scenario": row[0]}

    block = R.new_record({"status": R.RUNNER, "reason": "RUNNER", "since": "2026-08-10", "session": 5,
                          "trigger_close": 121.0, "gain_pct": 21.0, "ma50_at_trigger": 102.0,
                          "ma50_extension": 1.186}, market=market, entry_ref=100, entry_session="2026-08-03",
                         detected_at="t")
    view = {"record": block, "phase": R.HOLD, "exit": None, "today": "2026-09-01",
            "exit_facts": {"status": "OK", "date": "2026-08-31", "close": 129.0, "ma50": 110.0, "ma20": 122.0},
            "facts": {"current_price": 130.0, "stop_loss": 100, "gain_now_pct": 30.0, "sessions_since_entry": 20}}
    target_sell = {"should_sell": True, "sell_reason": "목표가 도달, 횡보 국면 전량 매도", "confidence": 7,
                   "analysis_summary": {}, "portfolio_adjustment": {"needed": True, "new_stop_loss": 120,
                                                                    "new_target_price": 140, "urgency": "high"}}
    reply["value"] = {"should_sell": False, "sell_reason": "hold", "confidence": 6, "analysis_summary": {},
                      "portfolio_adjustment": {"needed": False}}
    os.environ["RUNNER_HOLD_ENABLED"] = "true"
    await agent._analyze_sell_decision(stock())  # first tracking persists highest_price (prompt note)

    # Non-runner: byte-identical with the rule on and off.
    await agent._analyze_sell_decision(stock())
    baseline = prompts[-1]
    os.environ["RUNNER_HOLD_ENABLED"] = "false"
    await agent._analyze_sell_decision(stock())
    assert prompts[-1] == baseline

    # Runner, rule off: still byte-identical and the sell passes through unchanged.
    reply["value"] = target_sell
    data = dict(stock(), **{L.CTX_KEY: view})
    sold, _ = await agent._analyze_sell_decision(data)
    assert prompts[-1] == baseline and sold is True

    # Runner, rule on: exact appendix; the target sell becomes a hold; the stop is not raised.
    os.environ["RUNNER_HOLD_ENABLED"] = "true"
    agent.cursor.execute("UPDATE " + table + " SET stop_loss=100 WHERE id=?", (row_id,))
    agent.conn.commit()
    data = dict(stock(), **{L.CTX_KEY: view})
    sold, reason = await agent._analyze_sell_decision(data)
    expected = R.prompt_block(view, market=market, language=prompt_language)
    assert expected.startswith("\n\n### ") and prompts[-1] == baseline + expected
    assert sold is False and reason.startswith("[RUNNER_HOLD]")
    assert stock()["stop_loss"] == 100
    agent.conn.close()
    print(json.dumps({"ok": True}))

asyncio.run(main())
'''


@pytest.mark.parametrize("kind, language", [("KR_ENHANCED", "ko"), ("KR_ENHANCED", "en"), ("US", "ko")])
def test_runner_appendix_and_guard_in_the_real_sell_review(tmp_path, kind, language):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    for key in ("MICRO_SPLIT_LIVE_ENABLED", "MICRO_SPLIT_LIVE_MARKETS", "RUNNER_HOLD_ENABLED", "RUNNER_HOLD_MARKETS"):
        env.pop(key, None)
    market = "US" if kind == "US" else "KR"
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               DECISION_INPUT_SHADOW_ENABLED="false", PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl"),
               **{f"PRISM_{market}_CODEX_FAST_TRADING": "1"})
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), kind, language],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}
