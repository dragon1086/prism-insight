"""
Phase 6 S6 — insight-image BROADCAST wiring tests (US pytest session).

Separate session from KR because US resolves ``cores`` to ``prism-us/cores``
(shadowing). The shared insight modules are reachable via the
``prism-us/cores/llm -> ../../cores/llm`` symlink.

Mock-only: NO network, NO yfinance/pykrx, NO real vision calls. Verifies the
broadcast gate defaults OFF, the helper no-ops when OFF, and an image send is
attempted when ON + vision available (build mocked).

Run (US session):
    cd prism-insight/prism-us && python3 -m pytest tests/test_insight_broadcast_us.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure prism-us is the import root so `cores` shadows to prism-us/cores.
_PRISM_US = Path(__file__).resolve().parents[1]
if str(_PRISM_US) not in sys.path:
    sys.path.insert(0, str(_PRISM_US))

from cores.llm.capabilities import insight_image_enabled  # noqa: E402
from cores.llm.features import insight_broadcast  # noqa: E402


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_photo_bytes(self, chat_id, image_bytes, caption=None, market=None):
        self.sent.append((chat_id, image_bytes, caption, market))
        return True


class TestInsightImageGateUS:
    def test_gate_defaults_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_INSIGHT_IMAGE", raising=False)
        assert insight_image_enabled() is False


class TestBroadcastInsightImagesUS:
    @pytest.mark.asyncio
    async def test_noop_when_gate_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_INSIGHT_IMAGE", raising=False)
        monkeypatch.setattr(
            "cores.llm.capabilities.vision_available", lambda: True, raising=False
        )
        bot = _FakeBot()
        await insight_broadcast.broadcast_insight_images(
            bot, "chat", ["AAPL_Apple_20260101_buy_gpt5.4-mini.pdf"], market="us"
        )
        assert bot.sent == []

    @pytest.mark.asyncio
    async def test_sends_when_gate_on_and_vision_available(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_INSIGHT_IMAGE", "on")
        monkeypatch.setattr(
            "cores.llm.capabilities.vision_available", lambda: True, raising=False
        )

        async def _fake_build(ticker, company_name=None, market=None, **k):
            return b"JPEGBYTES"

        monkeypatch.setattr(
            "cores.llm.features.insight_image.build_insight_image_for",
            _fake_build, raising=False,
        )

        bot = _FakeBot()
        await insight_broadcast.broadcast_insight_images(
            bot, "chat-us", ["AAPL_Apple_20260101_buy_gpt5.4-mini.pdf"], market="us"
        )
        assert len(bot.sent) == 1
        chat_id, img, caption, market = bot.sent[0]
        assert chat_id == "chat-us"
        assert img == b"JPEGBYTES"
        assert "AAPL" in caption
        assert market == "us"
