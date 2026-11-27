from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import report_generator
from cores import evaluation_frame as ef
from prism_core.buy_risk_facts import stop_rr_facts


def _frame(closes):
    idx = pd.bdate_range(end="2026-11-26", periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes],
                         "Close": closes, "Volume": [1000.0] * len(closes)}, index=idx)


NOW = datetime(2026, 11, 27, 9, 0)


def test_losing_position_below_50_day_line_is_a_sell_signal():
    closes = [100.0] * 200 + [100 - i for i in range(1, 61)]   # steady fall to 40
    f = ef.holding_facts(_frame(closes), market="KR", avg_price=50.0, period_months=1,
                         regime="moderate_bear", now=NOW)
    assert f["t1"] and f["profit"] < -7
    assert any("-7%" in s for s in f["sell_signals"])
    assert any("50일선" in s for s in f["sell_signals"])


def test_trailing_band_follows_regime_and_activation():
    closes = [100.0] * 230 + [100 + i for i in range(1, 21)] + [112.0] * 10   # peak 120, now 112
    df = _frame(closes)
    bull = ef.holding_facts(df, market="US", avg_price=100.0, period_months=3, regime="strong_bull", now=NOW)
    bear = ef.holding_facts(df, market="US", avg_price=100.0, period_months=3, regime="strong_bear", now=NOW)
    assert bull["peak"] == 120 and bull["trail_line"] == 120 * 0.92 and not bull["sell_signals"]
    assert bear["trail_line"] == 120 * 0.95 and any("보호선" in s for s in bear["sell_signals"])
    inactive = ef.holding_facts(df, market="US", avg_price=118.0, period_months=3, regime="strong_bull", now=NOW)
    assert inactive["trail_line"] is None   # peak < avg x 1.05


def test_render_states_regime_pulse_and_buy_threshold():
    closes = [100.0 + i * 0.1 for i in range(260)]
    f = ef.holding_facts(_frame(closes), market="KR", avg_price=100.0, period_months=6, regime="moderate_bear", now=NOW)
    pulse = SimpleNamespace(state="CORRECTION", window=25, distribution_days=7)
    rr = stop_rr_facts("moderate_bear", "distribution days: 7", f["close"], 1.0)
    text = ef.render("KR", "moderate_bear", pulse, f, rr)
    assert "보통 약세장" in text and "분산일 7일" in text
    assert "strong_bear" in text and "한 단계 낮춤" in text       # same step-down as the BUY gate
    assert "손익비 하한 1.8" in text
    unknown = ef.render("KR", None, None, f, stop_rr_facts(None, "", f["close"], 1.0))
    assert "확인 불가" in unknown and "손익비 하한 확인 불가" in unknown


def test_evaluation_prompt_carries_frame_and_verdict_line(monkeypatch):
    seen = []

    async def fake_generate(*, agent, message, max_tokens, timeout_seconds=None):
        seen.append(agent.instruction)
        return "📌 프리즘 기준: 보유"

    monkeypatch.setattr(report_generator, "_generate_telegram_text", fake_generate)
    monkeypatch.setattr(report_generator, "build_evaluation_frame", lambda *a: "## PRISM 판단 틀 TEST")
    for fn in (report_generator.generate_evaluation_response, report_generator.generate_us_evaluation_response):
        asyncio.run(fn("X", "X", 100, 3, "친구처럼", ""))
    assert len(seen) == 2
    for instruction in seen:
        assert "## PRISM 판단 틀 TEST" in instruction
        assert "📌 프리즘 기준:" in instruction
        assert "평균단가 낮추기나 손절 등 구체적 대안" not in instruction   # averaging-down advice removed


def test_frame_failure_keeps_evaluation_working(monkeypatch):
    def boom(*a):
        raise RuntimeError("provider down")

    monkeypatch.setattr(report_generator, "build_evaluation_frame", boom)
    assert asyncio.run(report_generator._evaluation_frame("KR", "005930", 1, 1)) == ""
    assert "확인 불가" in report_generator._evaluation_frame_guide("")
