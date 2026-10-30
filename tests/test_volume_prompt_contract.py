"""Actual KR/US factories and isolated tracking initialization, never live decisions.

These checks protect the instruction contract and routing, not LLM accuracy or
the profitability of the illustrative price/volume cases.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from test_agent_virtual_initialization import SCRIPT as INITIALIZATION_SCRIPT


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[("KR", "ko"), ("KR", "en"), ("US", "ko"), ("US", "en")])
def prompts(request, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network permitted when constructing prompts")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    market, language = request.param
    path = ROOT / ("prism-us" if market == "US" else "") / "cores/agents/trading_agents.py"
    spec = importlib.util.spec_from_file_location("volume_contract_" + market, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix = "create_us_" if market == "US" else "create_"
    buy = getattr(module, prefix + "trading_scenario_agent")(language=language)
    sell = getattr(module, prefix + "sell_decision_agent")(language=language)
    return market, language, buy.instruction, sell.instruction


def test_volume_cases_preserve_both_valid_setups_and_counterexamples(prompts):
    _, language, buy, sell = prompts
    cases = (
        ("지지선을 유지하고 하락 폭이 축소", "정상 눌림일 수"),
        ("지지선이 무너지면", "보유·매수 근거로 삼지"),
        ("돌파 가격을 유지", "고점 부근이라는 이유만으로 배제하지"),
        ("돌파에 실패하고 가격이 밀리면", "추격 위험"),
        ("신고가·신저가만으로", "매수·매도·반등을 확정하지"),
    ) if language == "ko" else (
        ("holding support with narrowing declines", "may be a normal correction"),
        ("If support breaks", "do not use declining volume as a reason to hold or buy"),
        ("valid high-volume breakout holding its breakout level", "merely because it is near a high"),
        ("failed breakout and falling prices", "chasing risk"),
        ("new high or new low alone", "does not establish a buy, sell or rebound"),
    )
    for text in (buy, sell):
        for case in cases:
            assert all(fragment in text for fragment in case), case


def test_volume_reference_is_not_mixed_or_fabricated(prompts):
    _, language, buy, sell = prompts
    fragments = (
        "전일 대비와 5일 평균 대비 비율을 20일 평균 대비로 해석하지",
        "비교 대상 봉 이전의 확정된 20거래일", "수집 시각·확정 여부",
        "이력 부족·미완성봉은 미확정", "장중·시간외 거래량",
        "거래량만으로 기관 매집이나 분배를 단정하지",
    ) if language == "ko" else (
        "Do not treat previous-day or 5-day-average ratios as 20-day-average ratios",
        "20 completed sessions preceding the evaluated bar", "capture time and finality",
        "insufficient history or unfinished bars unknown", "intraday or extended-hours volume",
        "Never infer institutional accumulation or distribution from volume alone",
    )
    for text in (buy, sell):
        assert all(fragment in text for fragment in fragments)


def test_volume_interpretation_cannot_add_scores_or_override_protection(prompts):
    _, language, buy, sell = prompts
    for text in (buy, sell):
        if language == "ko":
            assert "별도 가점·감점·임계값·진입 차단 조건을 추가하지" in text
            assert "법인 이벤트·손절·트레일링 우선순위를 변경하거나 지연하지" in text
        else:
            assert "add no score bonus, penalty, threshold or entry gate" in text
            assert "Do not override or delay corporate-event, stop-loss or trailing-stop priority" in text
    assert ("20일 평균 대비 200%" if language == "ko" else "200% of 20-day average") in buy
    assert "0.92" in sell and "0.95" in sell
    assert '"should_sell"' in sell and '"portfolio_adjustment"' in sell


def test_sell_has_one_composite_interpretation_and_no_clock_finality(prompts):
    market, language, _, sell = prompts
    for obsolete in (
        "3일 이상 하락+거래량 감소는 추세 전환 의심", "3일 연속 하락 + 거래량 감소",
        "3+ days decline + volume decrease = suspect trend reversal",
        "3 consecutive days decline + volume decrease", "recent 14 days", "최근 14일", "14-day price/volume",
        "Today's volume/candle/price changes all **confirmed complete**", "당일 거래량/캔들/가격 변화 모두 **확정 완료**",
    ):
        assert obsolete not in sell
    assert ("모두 충족" if language == "ko" else "ALL three required") in sell
    # 2026-10-30 user decision: the afternoon review's price stands in for today's close (as the afternoon
    # screening assumes), but a pre-close capture read with tools is still never called a confirmed close.
    assert ("오늘 종가로 간주" if language == "ko" else "counts as today's close") in sell
    assert ("확정 종가라고 부르지는 마십시오" if language == "ko" else "read with tools a confirmed close") in sell


INITIALIZED_PROMPTS = INITIALIZATION_SCRIPT.split("async def main():", 1)[0] + r'''
async def main():
    language = sys.argv[3]
    agent = cls(**kwargs)
    assert await agent.initialize(language=language)
    heading = "### 거래량 해석 기준" if language == "ko" else "### Volume Interpretation"
    for prompt_agent in (agent.trading_agent, agent.sell_decision_agent):
        assert prompt_agent.instruction.count(heading) == 1
    assert blocked == [] and calls == []
    table = "us_stock_holdings" if market == "US" else "stock_holdings"
    assert agent.cursor.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    agent.conn.close()
    print(json.dumps({"market": market, "language": language, "prompts_loaded": 2, "external_attempts": 0}))
asyncio.run(main())
'''


@pytest.mark.parametrize("kind", ["KR_ENHANCED", "US"])
@pytest.mark.parametrize("language", ["ko", "en"])
def test_actual_tracking_initialization_loads_both_volume_prompts(tmp_path, kind, language):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-I", "-c", INITIALIZED_PROMPTS, str(ROOT), kind, language],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr[-5000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "market": "US" if kind == "US" else "KR", "language": language,
        "prompts_loaded": 2, "external_attempts": 0,
    }
