"""Real KR trigger/scoring/JSON integration with reused isolated data fixture."""
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = runpy.run_path(str(ROOT / "tests/test_kr_batch_watch_integration.py"))["RUN"]
# Keep the real fixture's data boundaries and socket ban, not its watch-on reruns.
RUN = FIXTURE.split("    if enabled:\n", 1)[0]
RUN = RUN.replace("calls = []", 'snapshot.attrs["observed_date"] = "20260916"\ncalls = []')
RUN = RUN.replace('watch_batch_ref="kr-b1")',
                  'watch_batch_ref="kr-b1", macro_context={"sector_map": dict.fromkeys(tickers, "IT"), "market_regime": "strong_bull"})')
RUN += '\nprint("PROVIDER_CALLS=" + json.dumps(calls))\n'


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
def test_report_price_leaders_do_not_change_selection_scores_or_provider_calls(tmp_path, mode):
    results = []
    provider_calls = []
    for enabled in ("0", "1", "malformed"):
        output = tmp_path / f"{mode}-{enabled}.json"
        env = dict(os.environ, PYTHONHASHSEED="0", PRISM_DISABLE_SIGNAL_PUBLISH="1",
                   PRISM_OBSERVABILITY_SPOOL=str(tmp_path / f"{enabled}-events.jsonl"),
                   REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED="false",
                   PRISM_REPORT_INSIGHT_PREFETCH="1" if enabled == "malformed" else enabled)
        run = RUN
        if enabled == "malformed":
            # Simulate unexpected optional metadata extraction failure, not a provider failure.
            run = run.replace("calls = []", """from prism_core import snapshot_price_leaders as leaders
def broken_metadata(*args, **kwargs):
    raise RuntimeError('/private/optional-error')
leaders.build_snapshot_price_leaders = broken_metadata
calls = []""")
        proc = subprocess.run([sys.executable, "-c", run, mode, "off", str(output)], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=45, check=False)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        provider_calls.append(next(line for line in proc.stdout.splitlines() if line.startswith("PROVIDER_CALLS=")))
        payload = json.loads(output.read_text())
        payload["metadata"].pop("run_time")
        if enabled in ("1", "malformed"):
            packet = payload["metadata"].pop("snapshot_price_leaders")
            if enabled == "1":
                assert packet["status"] == "OK"
                assert packet["observed_date"] == "2026-09-16"
                assert len(packet["groups"]["IT"]["leaders"]) == 3
            else:
                assert packet["status"] == "UNKNOWN"
                assert "private" not in str(packet)
            assert "snapshot_price_leaders" not in payload["metadata"].get("market_intelligence", {})
        else:
            assert "snapshot_price_leaders" not in payload["metadata"]
        results.append(payload)
    assert provider_calls[0] == provider_calls[1] == provider_calls[2]
    assert results[0] == results[1] == results[2]
