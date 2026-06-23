# test_insight_image.py

"""
Phase 6 S6 — annotated insight-image renderer tests.

ROOT pytest session, mock-only: NO network, NO pykrx, NO real vision calls.
All chart generation / vision is mocked. matplotlib runs on the Agg backend so
no display is required.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless; must precede pyplot import
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from cores.llm.features.buy_quality import BaseAnalysis, validate_levels  # noqa: E402
from cores.llm.features import insight_image  # noqa: E402
from cores.llm.features.insight_image import (  # noqa: E402
    build_insight_image_for,
    render_insight_image,
    _build_caption,
    _classify_axes,
    _ko_base_type,
    _ko_verdict,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
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
        pivot_price=72000.0,
        dist_to_pivot_pct=1.5,
        rs_line_new_high=True,
        proper_or_faulty="proper",
        quality_score=82,
        confidence=75,
        rationale="tight cup-with-handle, RS new high",
        support_levels=[68000.0],
        resistance_levels=[75000.0],
        buy_point=72000.0,
        stop_loss=66000.0,
    )
    base.update(overrides)
    return BaseAnalysis(**base)


def _tiny_fig():
    """A minimal price-like fig: axes[0] with a y-range ~ a KR stock price band."""
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1, 2, 3], [67000, 70000, 71000, 73000])
    ax.set_ylim(65000, 76000)
    return fig


def _three_panel_fig():
    """A price+volume+RS fig mimicking the mplfinance O'Neil daily layout.

    axes[0] = price (tall), then a shorter volume panel, then a thin RS panel
    added LAST (matching ``_add_rs_panel``'s add-after-mpf ordering).
    """
    fig = plt.figure(figsize=(12, 9))
    price_ax = fig.add_axes([0.08, 0.40, 0.84, 0.50])
    price_ax.plot([0, 1, 2, 3], [67000, 70000, 71000, 73000])
    price_ax.set_ylim(65000, 76000)
    vol_ax = fig.add_axes([0.08, 0.20, 0.84, 0.15])
    vol_ax.bar([0, 1, 2, 3], [10, 12, 9, 14])
    rs_ax = fig.add_axes([0.08, 0.02, 0.84, 0.10])  # thin, added LAST
    rs_ax.plot([0, 1, 2, 3], [100, 102, 101, 105])
    return fig


# --------------------------------------------------------------------------- #
# validate_levels                                                              #
# --------------------------------------------------------------------------- #
class TestValidateLevels:
    def test_keeps_in_band_drops_out_of_band(self):
        levels = [70000.0, 1.0, 999999.0, 72000.0]
        kept = validate_levels(levels, price_min=65000.0, price_max=76000.0)
        assert kept == [70000.0, 72000.0]

    def test_empty_input_returns_empty(self):
        assert validate_levels([], 100.0, 200.0) == []

    def test_drops_non_positive(self):
        kept = validate_levels([0.0, -5.0, 150.0], 100.0, 200.0)
        assert kept == [150.0]

    def test_padding_widens_band(self):
        # price_max=100, pad=0.25 -> upper bound 125; 120 kept, 130 dropped.
        kept = validate_levels([120.0, 130.0], 80.0, 100.0, pad=0.25)
        assert kept == [120.0]

    def test_handles_swapped_min_max(self):
        kept = validate_levels([150.0], price_min=200.0, price_max=100.0)
        assert kept == [150.0]


# --------------------------------------------------------------------------- #
# render_insight_image                                                         #
# --------------------------------------------------------------------------- #
class TestRenderInsightImage:
    def test_returns_nonempty_bytes(self):
        fig = _tiny_fig()
        analysis = _make_analysis()
        out = render_insight_image(
            fig, analysis,
            ticker="005930", company_name="삼성전자",
            price_min=65000.0, price_max=76000.0,
        )
        assert isinstance(out, bytes)
        assert len(out) > 0

    def test_none_figure_returns_none(self):
        analysis = _make_analysis()
        out = render_insight_image(
            None, analysis,
            ticker="005930", company_name="삼성전자",
            price_min=65000.0, price_max=76000.0,
        )
        assert out is None

    def test_out_of_band_levels_do_not_crash(self):
        fig = _tiny_fig()
        # Absurd levels that validate_levels must drop; render still succeeds.
        analysis = _make_analysis(
            support_levels=[1.0],
            resistance_levels=[10_000_000.0],
            buy_point=9_999_999.0,
            stop_loss=0.0,
        )
        out = render_insight_image(
            fig, analysis,
            ticker="005930", company_name="삼성전자",
            price_min=65000.0, price_max=76000.0,
        )
        assert isinstance(out, bytes)
        assert len(out) > 0

    def test_three_panel_fig_renders_and_adds_caption_band(self):
        # price + volume + RS panels -> relayout must add a caption-band axes
        # (one MORE axes than the 3 input panels) and still return JPEG bytes.
        fig = _three_panel_fig()
        n_before = len(fig.axes)
        analysis = _make_analysis()
        out = render_insight_image(
            fig, analysis,
            ticker="005930", company_name="삼성전자",
            price_min=65000.0, price_max=76000.0,
        )
        assert isinstance(out, bytes)
        assert len(out) > 0
        # A dedicated caption-band axes was added on top of the 3 panels.
        assert len(fig.axes) == n_before + 1


# --------------------------------------------------------------------------- #
# Korean caption + helpers                                                     #
# --------------------------------------------------------------------------- #
class TestKoreanCaption:
    def test_enum_to_korean_labels(self):
        assert _ko_base_type("cup-handle") == "컵앤핸들"
        assert _ko_base_type("faulty") == "부적합/결함"
        assert _ko_verdict("proper") == "적합"
        assert _ko_verdict("faulty") == "부적합/결함"
        # Unknown values fall back to the raw string (no crash).
        assert _ko_base_type("mystery-base") == "mystery-base"

    def test_caption_is_korean_and_untruncated(self):
        long_rationale = (
            "타이트한 컵앤핸들 베이스이며 상대강도 신고가를 동반합니다. "
            "거래량은 핸들 구간에서 뚜렷하게 감소했고 피벗 돌파 시 진입을 "
            "고려할 수 있는 구조입니다. 다만 시장 환경에 따라 변동성이 있으니 "
            "분할 접근이 바람직합니다."
        )
        analysis = _make_analysis(rationale=long_rationale)
        caption = _build_caption(
            analysis, ticker="005930", company_name="삼성전자",
            supports=[68000.0], resistances=[75000.0],
            buy=[72000.0], stop=[66000.0],
        )
        # Korean labels present.
        assert "베이스 유형" in caption
        assert "품질점수" in caption
        assert "판정" in caption
        assert "RS 신고가" in caption
        assert "매수 피벗" in caption
        assert "손절" in caption
        assert "지지" in caption and "저항" in caption
        assert "컵앤핸들" in caption
        assert "적합" in caption
        # Disclaimer present and rationale NOT truncated (no ellipsis).
        assert "투자 조언이 아닙니다" in caption
        assert "..." not in caption
        # Full rationale retained (textwrap may insert line breaks, so compare
        # with whitespace collapsed — the point is nothing was truncated).
        collapsed = "".join(caption.split())
        assert "분할접근이바람직합니다" in collapsed

    def test_classify_axes_three_panels(self):
        fig = _three_panel_fig()
        price_ax, vol_ax, rs_ax = _classify_axes(fig)
        assert price_ax is fig.axes[0]
        assert rs_ax is fig.axes[-1]          # RS added last
        assert vol_ax is not None and vol_ax is not price_ax and vol_ax is not rs_ax
        plt.close(fig)

    def test_classify_axes_single_panel(self):
        fig = _tiny_fig()
        price_ax, vol_ax, rs_ax = _classify_axes(fig)
        assert price_ax is fig.axes[0]
        assert vol_ax is None and rs_ax is None
        plt.close(fig)


# --------------------------------------------------------------------------- #
# build_insight_image_for — vision OFF path                                    #
# --------------------------------------------------------------------------- #
class TestBuildInsightImageFor:
    @pytest.mark.asyncio
    async def test_returns_none_when_vision_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)

        called = {"analyze": 0, "chart": 0}

        async def _fake_analyze(*a, **k):  # pragma: no cover
            called["analyze"] += 1
            return _make_analysis()

        def _fake_chart(*a, **k):  # pragma: no cover
            called["chart"] += 1
            return _tiny_fig()

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_base_oneil", _fake_analyze,
            raising=False,
        )
        monkeypatch.setattr(
            "cores.stock_chart.create_oneil_daily_chart", _fake_chart,
            raising=False,
        )

        out = await build_insight_image_for("005930", "삼성전자")
        assert out is None
        # No work done when vision is off.
        assert called["analyze"] == 0
        assert called["chart"] == 0

    @pytest.mark.asyncio
    async def test_reuses_single_analysis_when_vision_on(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")
        # Force vision_available() True regardless of capability internals.
        monkeypatch.setattr(insight_image, "vision_available", lambda: True)

        calls = {"analyze": 0}

        async def _fake_analyze(ticker, company_name=None, market=None, **k):
            calls["analyze"] += 1
            return _make_analysis()

        def _fake_chart(ticker, company_name=None, market=None, **k):
            return _tiny_fig()

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_base_oneil", _fake_analyze,
        )
        monkeypatch.setattr(
            "cores.stock_chart.create_oneil_daily_chart", _fake_chart,
        )

        out = await build_insight_image_for("005930", "삼성전자")
        assert isinstance(out, bytes)
        assert len(out) > 0
        # Exactly ONE vision analysis call (no second vision call for drawing).
        assert calls["analyze"] == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_analysis_none(self, monkeypatch):
        monkeypatch.setattr(insight_image, "vision_available", lambda: True)

        async def _fake_analyze(*a, **k):
            return None

        monkeypatch.setattr(
            "cores.llm.features.buy_quality.analyze_base_oneil", _fake_analyze,
        )

        out = await build_insight_image_for("005930", "삼성전자")
        assert out is None


# --------------------------------------------------------------------------- #
# Schema strictness (new S6 fields must stay strict-json-schema safe)          #
# --------------------------------------------------------------------------- #
class TestSchemaStrictWithNewFields:
    def test_schema_is_strict_safe(self):
        schema = BaseAnalysis.model_json_schema()
        assert schema.get("additionalProperties") is False
        required = set(schema.get("required", []))
        props = set(schema.get("properties", {}).keys())
        # strict mode requires EVERY property in `required`, including new ones.
        assert required == props
        for field in (
            "support_levels",
            "resistance_levels",
            "buy_point",
            "stop_loss",
        ):
            assert field in props
            assert field in required

    def test_defaults_allow_omitting_new_fields(self):
        # Older callers that don't pass the new fields still construct cleanly.
        a = BaseAnalysis(
            base_type="flat",
            base_length_weeks=6,
            depth_pct=12.0,
            handle_present=False,
            handle_in_upper_half=False,
            tightness="normal",
            volume_dryup_in_handle=False,
            pivot_price=100.0,
            dist_to_pivot_pct=0.0,
            rs_line_new_high=False,
            proper_or_faulty="proper",
            quality_score=60,
            confidence=50,
            rationale="flat base",
        )
        assert a.support_levels == []
        assert a.resistance_levels == []
        assert a.buy_point == 0.0
        assert a.stop_loss == 0.0
