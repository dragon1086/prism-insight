"""
Phase 6 S1 — Vision plumbing unit tests.

All tests are fully mocked: zero network calls, zero real OpenAI client.
Run with:  .venv/bin/python -m pytest tests/test_vision_plumbing.py -q
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SimpleSchema(BaseModel):
    label: str
    confidence: int


def _make_mock_response(text: str) -> MagicMock:
    """Build a fake Responses API response with one message output item."""
    part = MagicMock()
    part.text = text

    message_item = MagicMock()
    message_item.type = "message"
    message_item.content = [part]

    response = MagicMock()
    response.output = [message_item]
    return response


# ---------------------------------------------------------------------------
# capabilities.py tests
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_has_api_key_false_when_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "")
        assert capabilities.has_api_key() is False

    def test_has_api_key_false_for_placeholder(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "chatgpt-oauth-placeholder")
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "")
        assert capabilities.has_api_key() is False

    def test_has_api_key_true_for_real_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")
        from cores.llm import capabilities
        assert capabilities.has_api_key() is True

    def test_has_api_key_true_from_secrets_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "sk-secret")
        assert capabilities.has_api_key() is True
        assert capabilities.resolve_openai_api_key() == "sk-secret"

    def test_resolve_env_takes_priority_over_secrets(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "sk-secret")
        assert capabilities.resolve_openai_api_key() == "sk-env"

    def test_vision_enabled_default_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_enabled() is False

    def test_vision_enabled_on(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        from cores.llm import capabilities
        assert capabilities.vision_enabled() is True

    def test_vision_shadow_default_true(self, monkeypatch):
        monkeypatch.delenv("PRISM_VISION_SHADOW", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_shadow() is True

    def test_vision_shadow_false_when_set(self, monkeypatch):
        monkeypatch.setenv("PRISM_VISION_SHADOW", "false")
        from cores.llm import capabilities
        assert capabilities.vision_shadow() is False

    def test_vision_in_report_default_off(self, monkeypatch):
        # Safety contract: report is byte-identical unless explicitly enabled.
        monkeypatch.delenv("PRISM_FEATURE_VISION_IN_REPORT", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_in_report() is False

    def test_vision_in_report_on(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION_IN_REPORT", "on")
        from cores.llm import capabilities
        assert capabilities.vision_in_report() is True

    def test_vision_model_default(self, monkeypatch):
        monkeypatch.delenv("PRISM_VISION_MODEL", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_model() == "gpt-5.4-mini"

    def test_vision_model_override(self, monkeypatch):
        monkeypatch.setenv("PRISM_VISION_MODEL", "gpt-4o-mini")
        from cores.llm import capabilities
        assert capabilities.vision_model() == "gpt-4o-mini"

    def test_vision_auth_default_api(self, monkeypatch):
        monkeypatch.delenv("PRISM_VISION_AUTH", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_auth() == "api"

    def test_vision_available_false_when_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        from cores.llm import capabilities
        assert capabilities.vision_available() is False

    def test_vision_available_false_when_no_key(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "")
        assert capabilities.vision_available() is False

    def test_vision_available_true_when_on_and_key(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        from cores.llm import capabilities
        assert capabilities.vision_available() is True


# ---------------------------------------------------------------------------
# analyze_image — OFF path (default): returns None, zero client calls
# ---------------------------------------------------------------------------


class TestAnalyzeImageOff:
    @pytest.mark.asyncio
    async def test_returns_none_when_vision_off(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header bytes

        with patch("openai.AsyncOpenAI") as mock_client_cls:
            from cores.llm.features.vision import analyze_image
            result = await analyze_image(str(img), "describe this chart")

        assert result is None
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_encoding_when_vision_off(self, monkeypatch, tmp_path):
        """When vision is off, image bytes must never be read/encoded."""
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

        # Point to a non-existent file — would fail if read was attempted
        with patch("openai.AsyncOpenAI") as mock_client_cls:
            from cores.llm.features.vision import analyze_image
            result = await analyze_image("/nonexistent/path/chart.png", "describe")

        assert result is None
        mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# analyze_image — ON + no key: returns None
# ---------------------------------------------------------------------------


class TestAnalyzeImageOnNoKey:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "")

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch("openai.AsyncOpenAI") as mock_client_cls:
            from cores.llm.features.vision import analyze_image
            result = await analyze_image(str(img), "describe this chart")

        assert result is None
        mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# analyze_image — ON + key present: happy path
# ---------------------------------------------------------------------------


class TestAnalyzeImageHappyPath:
    @pytest.mark.asyncio
    async def test_calls_responses_create_once_and_returns_text(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        mock_response = _make_mock_response("bullish cup-and-handle detected")

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            from cores.llm.features import vision as vision_module
            # Force reimport of openai inside function via patch
            with patch.dict("sys.modules", {}):
                result = await vision_module.analyze_image(
                    str(img), "describe the chart pattern"
                )

        assert result == "bullish cup-and-handle detected"
        mock_client.responses.create.assert_called_once()

        # Verify input contains image
        call_args = mock_client.responses.create.call_args
        input_items = call_args.kwargs.get("input") or call_args.args[0] if call_args.args else call_args.kwargs["input"]
        assert any(
            "input_image" in str(item) or "image_url" in str(item)
            for item in input_items
        )

    @pytest.mark.asyncio
    async def test_structured_output_parsed_from_json(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        json_payload = json.dumps({"label": "cup-handle", "confidence": 87})
        mock_response = _make_mock_response(json_payload)

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            from cores.llm.features import vision as vision_module
            result = await vision_module.analyze_image(
                str(img), "classify the base", schema=SimpleSchema
            )

        assert isinstance(result, SimpleSchema)
        assert result.label == "cup-handle"
        assert result.confidence == 87


# ---------------------------------------------------------------------------
# analyze_image — error path: returns None, logs [VISION_ERROR]
# ---------------------------------------------------------------------------


class TestAnalyzeImageErrorPath:
    @pytest.mark.asyncio
    async def test_api_error_returns_none_and_logs(
        self, monkeypatch, tmp_path, caplog
    ):
        import logging

        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        from openai import APIError

        mock_exc = APIError("rate limit hit", request=MagicMock(), body=None)
        mock_exc.request_id = "req-abc123"  # type: ignore[attr-defined]
        mock_exc.status_code = 429  # type: ignore[attr-defined]

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(side_effect=mock_exc)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            with caplog.at_level(logging.ERROR, logger="cores.llm.features.vision"):
                from cores.llm.features import vision as vision_module
                result = await vision_module.analyze_image(str(img), "analyse")

        assert result is None
        assert any("[VISION_ERROR]" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_none(
        self, monkeypatch, tmp_path, caplog
    ):
        import logging

        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(
            side_effect=RuntimeError("unexpected SDK crash")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            with caplog.at_level(logging.ERROR, logger="cores.llm.features.vision"):
                from cores.llm.features import vision as vision_module
                result = await vision_module.analyze_image(str(img), "analyse")

        assert result is None
        assert any("[VISION_ERROR]" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Multi-image path (Phase 6 S3.5)
# ---------------------------------------------------------------------------


class TestAnalyzeImageMultiImage:
    @pytest.mark.asyncio
    async def test_list_of_two_images_makes_two_input_image_parts(
        self, monkeypatch
    ):
        """A list of 2 images -> 2 input_image parts + 1 input_text in ONE call."""
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        mock_response = _make_mock_response("two-timeframe analysis")

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        daily_bytes = b"\x89PNG\r\n\x1a\nDAILY"
        weekly_bytes = b"\x89PNG\r\n\x1a\nWEEKLY"

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            from cores.llm.features import vision as vision_module
            result = await vision_module.analyze_image(
                [daily_bytes, weekly_bytes],
                "image 1 = DAILY, image 2 = WEEKLY",
            )

        assert result == "two-timeframe analysis"
        # Exactly ONE Responses API call for the whole multi-image message.
        mock_client.responses.create.assert_called_once()

        call_args = mock_client.responses.create.call_args
        input_items = call_args.kwargs["input"]
        content = input_items[0]["content"]

        image_parts = [p for p in content if p.get("type") == "input_image"]
        text_parts = [p for p in content if p.get("type") == "input_text"]

        assert len(image_parts) == 2  # two images, in order
        assert len(text_parts) == 1   # single text prompt
        # Image parts must precede the text part.
        assert content[0]["type"] == "input_image"
        assert content[1]["type"] == "input_image"
        assert content[2]["type"] == "input_text"

    @pytest.mark.asyncio
    async def test_single_image_still_single_input_image(self, monkeypatch):
        """Backward compat: a single (non-list) image -> exactly 1 input_image."""
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")

        mock_response = _make_mock_response("single-image analysis")

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            from cores.llm.features import vision as vision_module
            result = await vision_module.analyze_image(
                b"\x89PNG\r\n\x1a\n", "single image"
            )

        assert result == "single-image analysis"
        mock_client.responses.create.assert_called_once()

        content = mock_client.responses.create.call_args.kwargs["input"][0]["content"]
        image_parts = [p for p in content if p.get("type") == "input_image"]
        text_parts = [p for p in content if p.get("type") == "input_text"]
        assert len(image_parts) == 1
        assert len(text_parts) == 1
