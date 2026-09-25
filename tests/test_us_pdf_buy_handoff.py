"""Real PDF extraction and US BUY handoff; synthetic sources and model transports."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_agent_virtual_initialization import SCRIPT as INITIALIZATION_SCRIPT

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = INITIALIZATION_SCRIPT.split("async def main():", 1)[0] + r'''
import logging
from types import SimpleNamespace
from xml.sax.saxutils import escape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph
from pdf_converter import pdf_to_markdown_text

# Loading the report module is still an import: urllib3 may first load here
# (its ::1 IPv6 probe is denied either way) once no earlier import pulls it in.
phase = "IMPORT"
spec = importlib.util.spec_from_file_location("pdf_handoff_us_analysis", source / "prism-us/cores/us_analysis.py")
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
phase = "INITIALIZE"
state = sys.argv[3]
status = {"status": state, "source": "Yahoo Finance / yfinance",
    "captured_at": "2026-09-23T18:00:00+00:00", "estimate_published_at": None,
    "components": {"earnings_estimate": "available" if state == "partial" else "missing",
                   "revenue_estimate": "error"}}
seen = []
class CaptureBackend:
    async def run(self, spec, message):
        seen.append((spec, message))
        return SimpleNamespace(text="SYNTHETIC company report. Price 100, stop 95, target 120.")

async def main():
    directory = analysis.get_us_agent_directory("Synthetic", "SYNTHETIC", "20260923",
        ["company_status"], "en", {"stock_info": "SYNTHETIC_STOCK",
        "analysis_estimates": "SYNTHETIC_ESTIMATES", "analysis_estimates_status": status})
    definition = directory["company_status"]
    analysis._report_gen_module._report_backend = CaptureBackend()
    body = await analysis.generate_report(definition, "company_status", "Synthetic", "SYNTHETIC",
                                          "20260923", logging.getLogger("pdf-handoff"), "en")
    assert len(seen) == 1 and seen[0][0].instructions == definition.instruction
    assert "SYNTHETIC_ESTIMATES" in seen[0][0].instructions and "Synthetic" in seen[0][1]
    receipt = analysis.render_us_analyst_receipt(status, "en")
    pdf = root / "SYNTHETIC_report.pdf"
    styles = getSampleStyleSheet()
    SimpleDocTemplate(str(pdf)).build([Paragraph(escape(line), styles["Normal"])
        for line in (body + "\n" + receipt).splitlines() if line.strip()])
    extracted = pdf_to_markdown_text(str(pdf))
    normalized = " ".join(extracted.split())
    expected = "partial collection" if state == "partial" else "no usable values collected"
    assert expected in normalized and "Estimate publication time: unverified" in normalized
    assert "Missing/error is not issuer-wide absence" in normalized
    assert "Missing optional data alone is not a separate buy/sell gate" in normalized
    agent = cls(**kwargs)
    assert await agent.initialize(language="en")
    agent._get_trend_facts = lambda ticker: "SYNTHETIC_TREND_NO_PROVIDER"
    agent._pipeline_market_regime = "moderate_bull"
    captured = []
    async def capture_buy(**request):
        captured.append(request)
        return SimpleNamespace(text=json.dumps({"decision": "No Entry", "entry_price": 100,
            "stop_loss": 95, "target_price": 120, "buy_score": 0, "rationale": "synthetic transport"}),
            latency_s=0, mcp_calls=[])
    module.generate_codex_fast_async = capture_buy
    result = await agent._extract_trading_scenario(extracted, ticker="SYNTHETIC", sector="Technology")
    assert len(captured) == 1 and captured[0]["system_prompt"] == agent.trading_agent.instruction
    assert extracted in captured[0]["user_prompt"] and expected in captured[0]["user_prompt"]
    assert "forecast EPS" in captured[0]["system_prompt"]
    assert [result[k] for k in ("entry_price", "stop_loss", "target_price")] == [100, 95, 120]
    assert agent.cursor.execute("SELECT COUNT(*) FROM us_stock_holdings").fetchone()[0] == 0
    assert blocked == [] and calls == []
    agent.conn.close()
    print(json.dumps({"status": state, "real_pdf": True, "real_buy_handoff": True, "external_attempts": 0}))
asyncio.run(main())
'''


@pytest.mark.parametrize("state", ["partial", "missing"])
def test_real_pdf_receipt_reaches_us_buy_prompt_without_external_effects(tmp_path, state):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               PRISM_US_CODEX_FAST_TRADING="1", PRISM_BUY_CODEX_MODEL="gpt-6-astra",
               PRISM_BUY_CODEX_EFFORT="low", PRISM_BUY_CODEX_TIMEOUT="90")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), "US", state],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "status": state, "real_pdf": True, "real_buy_handoff": True, "external_attempts": 0}
