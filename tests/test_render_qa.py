"""
Tests for cores/llm/features/render_qa.py — Phase 6 S2.

All tests are mock-only (no network, no real vision calls).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from cores.llm.features.render_qa import RenderQAVerdict, check_render, qa_and_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_verdict(ok: bool = True, severity: str = "none") -> RenderQAVerdict:
    return RenderQAVerdict(
        ok=ok,
        issues=[] if ok else ["Korean text rendered as tofu boxes"],
        severity=severity,
        confidence=90,
        notes="test verdict",
    )


# ---------------------------------------------------------------------------
# RenderQAVerdict schema tests
# ---------------------------------------------------------------------------

class TestRenderQAVerdictSchema:
    def test_strict_schema_has_additional_properties_false(self):
        """OpenAI strict json_schema requires additionalProperties: false."""
        schema = RenderQAVerdict.model_json_schema()
        assert schema.get("additionalProperties") is False, (
            f"Expected additionalProperties=False in schema, got: {schema}"
        )

    def test_all_fields_required_in_schema(self):
        schema = RenderQAVerdict.model_json_schema()
        required = set(schema.get("required", []))
        expected = {"ok", "issues", "severity", "confidence", "notes"}
        assert required == expected, f"Expected all fields required, got: {required}"

    def test_valid_verdict_construction(self):
        v = _make_verdict(ok=True)
        assert v.ok is True
        assert v.severity == "none"
        assert isinstance(v.issues, list)

    def test_extra_fields_forbidden(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RenderQAVerdict(
                ok=True,
                issues=[],
                severity="none",
                confidence=80,
                notes="",
                unexpected_field="boom",
            )


# ---------------------------------------------------------------------------
# check_render — vision OFF
# ---------------------------------------------------------------------------

class TestCheckRenderVisionOff:
    @pytest.mark.asyncio
    async def test_returns_none_when_vision_off(self, monkeypatch, tmp_path):
        """When vision is off, check_render must return None without any analyze_image call."""
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=False
        ) as mock_va, patch(
            "cores.llm.features.vision.analyze_image"
        ) as mock_ai:
            result = await check_render(str(img))

        assert result is None
        mock_va.assert_called_once()
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_analyze_image_calls_when_vision_off(self, monkeypatch, tmp_path):
        """Confirm analyze_image import is never reached when vision is off."""
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=False
        ):
            # Point to non-existent path — would fail if reading were attempted
            result = await check_render("/nonexistent/path/chart.png")

        assert result is None


# ---------------------------------------------------------------------------
# check_render — vision ON, happy path
# ---------------------------------------------------------------------------

class TestCheckRenderVisionOn:
    @pytest.mark.asyncio
    async def test_returns_verdict_when_ok(self, tmp_path):
        """When vision is on and analyze_image returns a verdict, check_render returns it."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        expected_verdict = _make_verdict(ok=True, severity="none")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(return_value=expected_verdict),
        ) as mock_ai:
            result = await check_render(str(img))

        assert isinstance(result, RenderQAVerdict)
        assert result.ok is True
        assert result.severity == "none"
        mock_ai.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_verdict_when_fail(self, tmp_path):
        """check_render returns a failing verdict without raising."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        fail_verdict = _make_verdict(ok=False, severity="major")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(return_value=fail_verdict),
        ):
            result = await check_render(str(img))

        assert result is not None
        assert result.ok is False
        assert result.severity == "major"
        assert len(result.issues) > 0

    @pytest.mark.asyncio
    async def test_passes_qa_prompt_to_analyze_image(self, tmp_path):
        """The QA prompt passed to analyze_image must mention rendering defects."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        mock_ai = AsyncMock(return_value=_make_verdict())

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch("cores.llm.features.render_qa.analyze_image", new=mock_ai):
            await check_render(str(img))

        call_args = mock_ai.call_args
        prompt_arg = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("prompt", "")
        assert "rendering" in prompt_arg.lower() or "render" in prompt_arg.lower()

    @pytest.mark.asyncio
    async def test_passes_schema_to_analyze_image(self, tmp_path):
        """analyze_image must be called with schema=RenderQAVerdict."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        mock_ai = AsyncMock(return_value=_make_verdict())

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch("cores.llm.features.render_qa.analyze_image", new=mock_ai):
            await check_render(str(img))

        call_kwargs = mock_ai.call_args.kwargs
        assert call_kwargs.get("schema") is RenderQAVerdict


# ---------------------------------------------------------------------------
# check_render — analyze_image returns None (error/degrade path)
# ---------------------------------------------------------------------------

class TestCheckRenderAnalyzeImageNone:
    @pytest.mark.asyncio
    async def test_returns_none_when_analyze_image_returns_none(self, tmp_path):
        """If analyze_image returns None (error/degrade), check_render returns None gracefully."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(return_value=None),
        ):
            result = await check_render(str(img))

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_analyze_image_raises(self, tmp_path):
        """If analyze_image raises unexpectedly, check_render returns None (never re-raises)."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(side_effect=RuntimeError("unexpected boom")),
        ):
            result = await check_render(str(img))

        assert result is None


# ---------------------------------------------------------------------------
# qa_and_log — logging behaviour
# ---------------------------------------------------------------------------

class TestQaAndLog:
    @pytest.mark.asyncio
    async def test_logs_warning_when_verdict_fails(self, tmp_path, caplog):
        """qa_and_log must emit a WARNING when verdict.ok is False."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        fail_verdict = _make_verdict(ok=False, severity="major")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(return_value=fail_verdict),
        ), caplog.at_level(logging.WARNING, logger="cores.llm.features.render_qa"):
            result = await qa_and_log(str(img), context_label="005930")

        assert result is not None
        assert result.ok is False
        assert any("[RENDER_QA] FAIL" in r.message for r in caplog.records), (
            f"Expected [RENDER_QA] FAIL warning. Records: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_no_warning_when_verdict_ok(self, tmp_path, caplog):
        """qa_and_log must NOT emit a RENDER_QA FAIL warning when verdict.ok is True."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        ok_verdict = _make_verdict(ok=True, severity="none")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(return_value=ok_verdict),
        ), caplog.at_level(logging.WARNING, logger="cores.llm.features.render_qa"):
            result = await qa_and_log(str(img), context_label="035720")

        assert result is not None
        assert result.ok is True
        assert not any("[RENDER_QA] FAIL" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_returns_none_when_vision_off(self, tmp_path):
        """qa_and_log returns None when vision is off (complete no-op)."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=False
        ):
            result = await qa_and_log(str(img), context_label="000660")

        assert result is None

    @pytest.mark.asyncio
    async def test_never_raises_on_internal_error(self, tmp_path):
        """qa_and_log must swallow all errors and return None, never raise."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(side_effect=Exception("catastrophic failure")),
        ):
            # Must not raise
            result = await qa_and_log(str(img), context_label="test")

        assert result is None

    @pytest.mark.asyncio
    async def test_context_label_appears_in_warning(self, tmp_path, caplog):
        """The context_label should appear in the FAIL warning log line."""
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        fail_verdict = _make_verdict(ok=False, severity="minor")

        with patch(
            "cores.llm.features.render_qa.vision_available", return_value=True
        ), patch(
            "cores.llm.features.render_qa.analyze_image",
            new=AsyncMock(return_value=fail_verdict),
        ), caplog.at_level(logging.WARNING, logger="cores.llm.features.render_qa"):
            await qa_and_log(str(img), context_label="MY_STOCK_LABEL")

        assert any("MY_STOCK_LABEL" in r.message for r in caplog.records)
