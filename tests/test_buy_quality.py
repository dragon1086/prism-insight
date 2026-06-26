# test_buy_quality.py

"""
Phase 6 S3 — Buy-quality vision gate unit tests.

All tests are fully mocked: zero network, zero real OpenAI client.
Run with:  .venv/bin/python -m pytest tests/test_buy_quality.py -q
"""

from __future__ import annotations

import logging

import pytest

from cores.llm.features.buy_quality import (
    REGIME_THRESHOLDS,
    BaseAnalysis,
    analyze_base,
    format_vision_pattern_md,
    gate_verdict,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _make_analysis(**overrides) -> BaseAnalysis:
    base = dict(
        base_type="cup-handle",
        base_length_weeks=8,
        depth_pct=22.0,
        handle_present=True,
        handle_in_upper_half=True,
        tightness="tight",
        volume_dryup_in_handle=True,
        pivot_price=100.0,
        dist_to_pivot_pct=1.5,
        rs_line_new_high=True,
        proper_or_faulty="proper",
        quality_score=80,
        confidence=75,
        rationale="tight cup-with-handle, RS new high",
    )
    base.update(overrides)
    return BaseAnalysis(**base)


# --------------------------------------------------------------------------- #
# Schema strictness                                                           #
# --------------------------------------------------------------------------- #
class TestSchemaStrict:
    def test_schema_is_strict_safe(self):
        schema = BaseAnalysis.model_json_schema()
        assert schema.get("additionalProperties") is False
        required = set(schema.get("required", []))
        props = set(schema.get("properties", {}).keys())
        # strict mode requires EVERY property to be in `required`
        assert required == props
        # spot-check the key fields exist
        for field in (
            "base_type",
            "quality_score",
            "confidence",
            "pivot_price",
            "rs_line_new_high",
            "rationale",
        ):
            assert field in props


# --------------------------------------------------------------------------- #
# analyze_base — vision OFF                                                    #
# --------------------------------------------------------------------------- #
class TestAnalyzeBaseVisionOff:
    @pytest.mark.asyncio
    async def test_returns_none_when_vision_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        called = {"n": 0}

        async def _fake_analyze_image(*args, **kwargs):  # pragma: no cover
            called["n"] += 1
            return _make_analysis()

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_image", _fake_analyze_image
        )

        result = await analyze_base(b"\x89PNG\r\n\x1a\n")
        assert result is None
        assert called["n"] == 0  # zero vision calls when off


# --------------------------------------------------------------------------- #
# analyze_base — vision ON                                                     #
# --------------------------------------------------------------------------- #
class TestAnalyzeBaseVisionOn:
    @pytest.mark.asyncio
    async def test_returns_analysis_when_on(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        expected = _make_analysis(quality_score=82)

        async def _fake_analyze_image(image, prompt, *, schema=None, model=None):
            assert schema is BaseAnalysis
            return expected

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_image", _fake_analyze_image
        )

        result = await analyze_base(b"\x89PNG\r\n\x1a\n")
        assert isinstance(result, BaseAnalysis)
        assert result.quality_score == 82

    @pytest.mark.asyncio
    async def test_pivot_cross_check_penalises_on_deviation(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        # model says pivot 100; numeric pivot 130 => 30% deviation > 3% tol
        returned = _make_analysis(pivot_price=100.0, quality_score=80)

        async def _fake_analyze_image(image, prompt, *, schema=None, model=None):
            return returned

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_image", _fake_analyze_image
        )

        result = await analyze_base(b"img", numeric_pivot=130.0)
        assert result is not None
        assert result.quality_score == 55  # 80 - 25 penalty
        assert "pivot cross-check" in result.rationale

    @pytest.mark.asyncio
    async def test_pivot_cross_check_no_penalty_within_tolerance(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        returned = _make_analysis(pivot_price=100.0, quality_score=80)

        async def _fake_analyze_image(image, prompt, *, schema=None, model=None):
            return returned

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_image", _fake_analyze_image
        )

        # 101 vs 100 = 1% deviation, within 3% tol => unchanged
        result = await analyze_base(b"img", numeric_pivot=101.0)
        assert result is not None
        assert result.quality_score == 80
        assert "pivot cross-check" not in result.rationale

    @pytest.mark.asyncio
    async def test_returns_none_when_analyze_image_returns_none(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        async def _fake_analyze_image(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_image", _fake_analyze_image
        )

        result = await analyze_base(b"img")
        assert result is None


# --------------------------------------------------------------------------- #
# gate_verdict — per-regime thresholds (§1)                                    #
# --------------------------------------------------------------------------- #
class TestGateVerdict:
    def test_thresholds_ordering(self):
        # §1 behaviour: lenient bull, strict sideways, very strict bear, block parabolic
        assert REGIME_THRESHOLDS["strong_bull"] < REGIME_THRESHOLDS["sideways"]
        assert REGIME_THRESHOLDS["moderate_bull"] < REGIME_THRESHOLDS["sideways"]
        assert REGIME_THRESHOLDS["sideways"] < REGIME_THRESHOLDS["strong_bear"]
        assert REGIME_THRESHOLDS["moderate_bear"] <= REGIME_THRESHOLDS["strong_bear"]
        assert REGIME_THRESHOLDS["parabolic"] >= REGIME_THRESHOLDS["strong_bear"]

    def test_fixed_score_flips_across_regimes(self):
        # A mid-quality base (score 65): passes lenient bull, fails strict bear/sideways
        analysis = _make_analysis(quality_score=65)

        bull = gate_verdict(analysis, "strong_bull")
        assert bull["would_buy"] is True
        assert bull["threshold"] == 55

        side = gate_verdict(analysis, "sideways")
        assert side["would_buy"] is False
        assert side["threshold"] == 75

        bear = gate_verdict(analysis, "strong_bear")
        assert bear["would_buy"] is False
        assert bear["threshold"] == 90

    def test_high_score_passes_everywhere_except_when_faulty(self):
        analysis = _make_analysis(quality_score=95)
        for regime in REGIME_THRESHOLDS:
            v = gate_verdict(analysis, regime)
            assert v["would_buy"] is True, regime

    def test_faulty_base_is_auto_no_entry(self):
        analysis = _make_analysis(quality_score=99, proper_or_faulty="faulty")
        v = gate_verdict(analysis, "strong_bull")
        assert v["would_buy"] is False
        assert "faulty" in v["reason"]

    def test_none_base_is_no_entry(self):
        analysis = _make_analysis(quality_score=99, base_type="none")
        v = gate_verdict(analysis, "strong_bull")
        assert v["would_buy"] is False
        assert "no constructive base" in v["reason"]

    def test_unknown_regime_uses_default_threshold(self):
        analysis = _make_analysis(quality_score=74)
        v = gate_verdict(analysis, "totally_unknown_regime")
        assert v["threshold"] == 75
        assert v["would_buy"] is False

    def test_verdict_shape(self):
        v = gate_verdict(_make_analysis(), "sideways")
        assert set(v.keys()) == {
            "would_buy",
            "regime",
            "threshold",
            "quality_score",
            "reason",
        }


# --------------------------------------------------------------------------- #
# Shadow logging path (integration of analyze_base + gate_verdict + logging)    #
# --------------------------------------------------------------------------- #
class TestShadowLoggingPath:
    @pytest.mark.asyncio
    async def test_shadow_path_logs_and_does_not_raise(self, monkeypatch, caplog):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        expected = _make_analysis(quality_score=82, base_type="cup-handle")

        async def _fake_analyze_image(image, prompt, *, schema=None, model=None):
            return expected

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_image", _fake_analyze_image
        )

        logger = logging.getLogger("cores.llm.features.buy_quality")

        # Simulate the shadow hook from cores/analysis.py
        with caplog.at_level(logging.INFO, logger="cores.llm.features.buy_quality"):
            analysis = await analyze_base(b"img")
            assert analysis is not None
            verdict = gate_verdict(analysis, "strong_bull")
            logger.info(
                "[BUY_QUALITY][SHADOW] code=%s regime=%s would_buy=%s "
                "qscore=%s thr=%s base=%s",
                "005930",
                "strong_bull",
                verdict["would_buy"],
                verdict["quality_score"],
                verdict["threshold"],
                analysis.base_type,
            )

        assert any("[BUY_QUALITY][SHADOW]" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# analyze_base_oneil — two-timeframe (daily + weekly) path (Phase 6 S3.5)       #
# All chart generation + vision are mocked: NO network / NO pykrx.             #
# --------------------------------------------------------------------------- #
class TestAnalyzeBaseOneil:
    @pytest.mark.asyncio
    async def test_returns_none_when_vision_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        from cores.llm.features.buy_quality import analyze_base_oneil

        called = {"charts": 0}

        def _fake_daily(*a, **k):  # pragma: no cover
            called["charts"] += 1
            return object()

        monkeypatch.setattr(
            "cores.stock_chart.create_oneil_daily_chart", _fake_daily, raising=False
        )

        result = await analyze_base_oneil("005930", "삼성전자")
        assert result is None
        assert called["charts"] == 0  # no chart work when vision off

    @pytest.mark.asyncio
    async def test_two_timeframe_sends_two_images(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        import cores.llm.features.buy_quality as bq

        # Mock chart generators (avoid matplotlib/pykrx) — return sentinels.
        monkeypatch.setattr(
            "cores.stock_chart.create_oneil_daily_chart",
            lambda *a, **k: "DAILY_FIG",
        )
        monkeypatch.setattr(
            "cores.stock_chart.create_oneil_weekly_chart",
            lambda *a, **k: "WEEKLY_FIG",
        )
        # Mock fig->bytes so no real rendering happens.
        monkeypatch.setattr(
            bq, "_fig_to_bytes", lambda fig, **k: f"{fig}_BYTES".encode()
        )

        captured = {}

        async def _fake_analyze_image(images, prompt, *, schema=None, model=None):
            captured["images"] = images
            captured["prompt"] = prompt
            captured["schema"] = schema
            return _make_analysis(quality_score=88, rs_line_new_high=True)

        monkeypatch.setattr(bq, "analyze_image", _fake_analyze_image)

        result = await bq.analyze_base_oneil("005930", "삼성전자", regime="strong_bull")

        assert isinstance(result, BaseAnalysis)
        assert result.quality_score == 88
        # A list of EXACTLY two images (daily first, weekly second).
        assert isinstance(captured["images"], list)
        assert len(captured["images"]) == 2
        assert captured["images"][0] == b"DAILY_FIG_BYTES"
        assert captured["images"][1] == b"WEEKLY_FIG_BYTES"
        assert captured["schema"] is BaseAnalysis
        # Prompt names the two timeframes for the model.
        assert "DAILY" in captured["prompt"] and "WEEKLY" in captured["prompt"]

    @pytest.mark.asyncio
    async def test_returns_none_when_chart_unavailable(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        import cores.llm.features.buy_quality as bq

        # Daily chart returns None (e.g. no data) -> whole call returns None.
        monkeypatch.setattr(
            "cores.stock_chart.create_oneil_daily_chart", lambda *a, **k: None
        )
        monkeypatch.setattr(
            "cores.stock_chart.create_oneil_weekly_chart",
            lambda *a, **k: "WEEKLY_FIG",
        )

        async def _fake_analyze_image(*a, **k):  # pragma: no cover
            raise AssertionError("analyze_image must not be called")

        monkeypatch.setattr(bq, "analyze_image", _fake_analyze_image)

        result = await bq.analyze_base_oneil("005930", "삼성전자")
        assert result is None


# --------------------------------------------------------------------------- #
# format_vision_pattern_md — report subsection rendering (soft, not a gate)     #
# --------------------------------------------------------------------------- #
class TestFormatVisionPatternMd:
    def test_ko_renders_descriptive_subsection(self):
        a = _make_analysis(
            base_type="cup-handle",
            quality_score=80,
            confidence=75,
            pivot_price=23250.0,
            dist_to_pivot_pct=-1.2,
            support_levels=[22000.0, 21500.0],
            resistance_levels=[24000.0],
            stop_loss=21000.0,
            rationale="타이트한 손잡이컵, RS 신고가.",
        )
        md = format_vision_pattern_md(a, "ko")
        assert md  # non-empty
        assert "### 차트 패턴 분석" in md
        assert "참고용" in md            # framed as informational, not a directive
        assert "cup-handle" in md
        assert "80/100" in md
        assert "23,250" in md            # pivot rendered with thousands separator
        assert "22,000" in md and "24,000" in md  # support & resistance
        assert "타이트한 손잡이컵" in md  # rationale surfaced
        # Contract: this is SOFT report content, never a buy gate.
        assert "would_buy" not in md
        assert md.endswith("\n\n")

    def test_en_renders_english_header(self):
        a = _make_analysis(rationale="tight cup, RS new high")
        md = format_vision_pattern_md(a, "en")
        assert "### Chart Pattern Analysis" in md
        assert "would_buy" not in md

    def test_empty_levels_and_no_pivot_do_not_break(self):
        a = _make_analysis(
            pivot_price=0.0,
            stop_loss=0.0,
            support_levels=[],
            resistance_levels=[],
        )
        md = format_vision_pattern_md(a, "ko")
        assert md
        assert "없음" in md              # empty level lists render as 없음
        assert "피벗" not in md          # no pivot line when pivot_price <= 0

    def test_never_raises_on_bad_input(self):
        # A malformed object must not blow up the report pipeline -> returns "".
        assert format_vision_pattern_md(object(), "ko") == ""  # type: ignore[arg-type]
