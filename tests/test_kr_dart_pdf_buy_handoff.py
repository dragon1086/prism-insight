"""Full KR PDF text reaches both BUY transports, without providers or orders."""
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
from xml.sax.saxutils import escape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph
from pdf_converter import pdf_to_markdown_text
import stock_tracking_agent as module

transport = sys.argv[3]
late_fact = "Conditional contribution obligation 987.65 billion KRW; payable ONLY if project approval occurs."
scenario = {"decision": "No Entry", "entry_price": 100, "stop_loss": 95,
            "target_price": 120, "buy_score": 0, "rationale": "synthetic transport"}

async def main():
    pdf = root / "KR_DART_report.pdf"
    styles = getSampleStyleSheet()
    lines = ["Earlier report paragraph with synthetic business facts. " * 20] * 50
    lines += ["5. DART disclosure deep analysis", late_fact,
              "This contingent obligation is not an unconditional current cash outflow."]
    SimpleDocTemplate(str(pdf)).build([Paragraph(escape(line), styles["Normal"]) for line in lines])
    extracted = pdf_to_markdown_text(str(pdf))
    normalized = " ".join(extracted.split())
    assert len(extracted) > 40000 and normalized.index(late_fact) > 40000
    agent = cls(**kwargs)
    assert await agent.initialize(language="en")
    agent._get_trend_facts = lambda ticker: "SYNTHETIC_TREND_NO_PROVIDER"
    agent._pipeline_market_regime = "moderate_bull"
    captured = []
    async def capture_buy(**request):
        captured.append(("codex", request["user_prompt"]))
        assert request["system_prompt"] == agent.trading_agent.instruction
        if transport == "fallback":
            raise RuntimeError("synthetic unavailable transport")
        return SimpleNamespace(text=json.dumps(scenario), latency_s=0, mcp_calls=[])
    module.generate_codex_fast_async = capture_buy
    class LocalApp:
        context = object()
        @asynccontextmanager
        async def run(self):
            yield self
    agent._instance_mcp_app = LocalApp()
    async def attach(*args):
        return object()
    async def legacy(llm, prompt):
        captured.append(("legacy", prompt))
        return dict(scenario)
    module.attach_isolated_llm = attach
    module._generate_trading_scenario_json = legacy
    result = await agent._extract_trading_scenario(extracted, ticker="017670", sector="Telecom")
    assert [x[0] for x in captured] == (["codex", "legacy"] if transport == "fallback" else ["codex"])
    for _, prompt in captured:
        assert extracted in prompt
        assert late_fact in " ".join(prompt.split())
        assert "not an unconditional current cash outflow" in " ".join(prompt.split())
    if transport == "fallback":
        assert captured[0][1] == captured[1][1]
    assert result["entry_price"] == 100
    assert agent.cursor.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0
    assert blocked == [] and calls == []
    agent.conn.close()
    print(json.dumps({"transport": transport, "real_pdf": True, "external_attempts": 0}))
asyncio.run(main())
'''


@pytest.mark.parametrize("transport", ["codex", "fallback"])
def test_full_kr_pdf_disclosure_reaches_buy_transports(tmp_path, transport):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               PRISM_KR_CODEX_FAST_TRADING="1", PRISM_BUY_CODEX_MODEL="gpt-6-astra",
               PRISM_BUY_CODEX_EFFORT="low", PRISM_BUY_CODEX_TIMEOUT="90")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), "KR", transport],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "transport": transport, "real_pdf": True, "external_attempts": 0}
