"""Actual US tracker loads observations without altering legacy BUY context."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = r'''
import asyncio, json, socket, sqlite3, sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
root, destination, case = sys.argv[1:]
sys.path[:0] = [str(Path(root)/"prism-us"),root]
def forbidden(*a,**kw): raise AssertionError("external boundary forbidden")
with ExitStack() as stack:
 stack.enter_context(patch.object(socket.socket,"connect",forbidden))
 stack.enter_context(patch.object(sqlite3,"connect",forbidden))
 stack.enter_context(patch("dotenv.load_dotenv",return_value=False))
 import cores  # Resolve the US namespace before tracker's legacy path mutation.
 import us_stock_tracking_agent as tracking
 import prism_core.screening_quality as quality
 context={"schema_version":"screening_quality_context_v1", "status":"MISSING",
          "reason":"capture_error", "requested_trade_date":"2026-09-14", "scoring_applied":False}
 if case == "malformed": context["status"]=[]
 if case == "failure":
  stack.enter_context(patch.object(quality,"load_quality_candidates",side_effect=ValueError("fixture")))
 payload={"metadata":{"trade_date":"20260914","trigger_mode":"afternoon",
                      "screening_quality_candidates":{"AAA":context}},
          "Intraday Rise Top":[{"ticker":"AAA","risk_reward_ratio":None}]}
 Path(destination).write_text(json.dumps(payload))
 agent=object.__new__(tracking.USStockTrackingAgent)
 async def stop_before_accounts(*a,**kw): raise RuntimeError("fixture-stop-before-accounts")
 agent.initialize=stop_before_accounts
 assert asyncio.run(agent.run([],trigger_results_file=destination)) is False
 assert agent.trigger_info_map == {"AAA":{"trigger_type":"Intraday Rise Top",
                                         "trigger_mode":"afternoon","risk_reward_ratio":None}}
 if case == "valid": assert agent._screening_quality_contexts["AAA"]["status"] == "MISSING"
 else: assert agent._screening_quality_contexts == {}
'''


@pytest.mark.parametrize('case', ['valid', 'malformed', 'failure', 'disabled'])
def test_actual_tracker_quality_isolated_from_trigger_map(tmp_path, case):
    env = dict(os.environ, US_SCREENING_QUALITY_CAPTURE_ENABLED=str(case != 'disabled').lower(),
               PRISM_DISABLE_SIGNAL_PUBLISH='1')
    result = subprocess.run([sys.executable, '-c', RUN, str(ROOT), str(tmp_path/'batch.json'), case],
                            env=env, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
