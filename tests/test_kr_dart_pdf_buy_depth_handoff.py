"""DART 5-1 + competitor table in a real KR PDF reach BUY; the flag only changes the system prompt."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_agent_virtual_initialization import SCRIPT as INITIALIZATION_SCRIPT

ROOT = Path(__file__).resolve().parents[1]
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    os.path.expanduser("~/Library/Fonts/NanumGothic.ttf"),
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/nanum/NanumGothic.ttf",
    "/usr/share/fonts/google-nanum-fonts/NanumGothic.ttf",
)
KOREAN_FONT = next((path for path in FONT_CANDIDATES if Path(path).is_file()), None)

SCRIPT = INITIALIZATION_SCRIPT.split("async def main():", 1)[0] + r'''
from contextlib import asynccontextmanager
from types import SimpleNamespace
from xml.sax.saxutils import escape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph
from pdf_converter import pdf_to_markdown_text
from prism_core.buy_report_depth_evidence import MARKER
import stock_tracking_agent as module

transport, language, font_path, flag_on = sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6] == "on"
dart_fact = "일회성 자산처분이익 412억원이 순이익에 포함되어 영업 기반 이익은 감소했습니다."
peer_fact = "피어 중앙값 PER 14.2배 대비 대상 기업 PER 9.1배"
scenario = {"decision": "No Entry", "entry_price": 100, "stop_loss": 95,
            "target_price": 120, "buy_score": 0, "rationale": "synthetic depth transport"}

async def main():
    pdf = root / "KR_DART_depth_report.pdf"
    pdfmetrics.registerFont(TTFont("KRTest", font_path))
    style = ParagraphStyle("kr", parent=getSampleStyleSheet()["Normal"], fontName="KRTest")
    lines = ["2-1. 기업 현황 분석 합성 본문입니다. " * 10] * 20
    lines += ["경쟁사 비교 분석", "시가총액 매출액 영업이익률 순이익률 ROE 부채비율 PER PBR 재무기준", peer_fact,
              "경쟁사 대비 위치 분석", "대상 기업은 피어 대비 영업이익률이 높습니다.",
              "5. DART 주요 재무·사업 위험 분석", "5-1. 실적·현금흐름·차입과 회계 판단", "핵심 포인트", dart_fact,
              "5-3. 주요 약정·기업 사건과 우발위험", "핵심 포인트", "전환사채 잔액 300억원의 전환가액 조정 조항이 있습니다.",
              "6. 투자 전략", "6-1. 합성 전략 본문입니다."]
    SimpleDocTemplate(str(pdf)).build([Paragraph(escape(line), style) for line in lines])
    extracted = pdf_to_markdown_text(str(pdf))
    normalized = " ".join(extracted.split())
    for needle in ("5-1. 실적·현금흐름·차입과 회계 판단", "경쟁사 비교 분석", dart_fact, peer_fact):
        assert needle in normalized, needle
    agent = cls(**kwargs)
    assert await agent.initialize(language=language)
    system_prompt = agent.trading_agent.instruction
    assert (MARKER in system_prompt) is flag_on
    if flag_on:
        assert "| 2-1 (+ DART 5-1) |" in system_prompt
        assert "| 2-2 (+ 경쟁사 비교 분석) |" in system_prompt
        assert ("5-1 실적·현금흐름·차입과 회계 판단" if language == "ko" else "5-1 earnings/cash flow") in system_prompt
    else:
        assert "DART 5-1" not in system_prompt and "경쟁사 비교 분석" not in system_prompt
    agent._get_trend_facts = lambda ticker: "SYNTHETIC_TREND_NO_PROVIDER"
    agent._pipeline_market_regime = "moderate_bull"
    captured = []
    async def capture_buy(**request):
        captured.append(("codex", request["user_prompt"]))
        assert request["system_prompt"] == system_prompt
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
        assert args[0].instruction == system_prompt
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
        flat = " ".join(prompt.split())
        for needle in ("5-1. 실적·현금흐름·차입과 회계 판단", "경쟁사 비교 분석", dart_fact, peer_fact):
            assert needle in flat, needle
    assert result["entry_price"] == 100
    assert agent.cursor.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0
    assert blocked == [] and calls == []
    agent.conn.close()
    print(json.dumps({"transport": transport, "flag": sys.argv[6], "external_attempts": 0}))
asyncio.run(main())
'''


@pytest.mark.skipif(KOREAN_FONT is None, reason="no Korean TTF font for a real Korean PDF")
@pytest.mark.parametrize("flag", ["on", "off"])
@pytest.mark.parametrize("transport", ["codex", "fallback"])
@pytest.mark.parametrize("language", ["ko", "en"])
def test_dart_and_competitor_sections_reach_buy_with_flag(tmp_path, transport, flag, language):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.pop("PRISM_BUY_REPORT_DEPTH_EVIDENCE", None)
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               PRISM_KR_CODEX_FAST_TRADING="1", PRISM_BUY_CODEX_MODEL="gpt-6-astra",
               PRISM_BUY_CODEX_EFFORT="low", PRISM_BUY_CODEX_TIMEOUT="90")
    if flag == "on":
        env["PRISM_BUY_REPORT_DEPTH_EVIDENCE"] = "1"
    result = subprocess.run(
        [sys.executable, "-I", "-c", SCRIPT, str(ROOT), "KR", transport, language, KOREAN_FONT, flag],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "transport": transport, "flag": flag, "external_attempts": 0}
    assert f"[BUY_REPORT_DEPTH] enabled={'true' if flag == 'on' else 'false'} ticker=017670" in (
        result.stderr + result.stdout)
