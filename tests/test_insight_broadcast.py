"""
Phase 6 S6 — insight-image BROADCAST wiring tests (KR / ROOT pytest session).

Mock-only: NO network, NO pykrx, NO real vision calls. Verifies:
  - the new broadcast gate (insight_image_enabled) defaults OFF,
  - broadcast_insight_images is a no-op when the gate is OFF,
  - an image send is attempted when gate ON + vision available.

Run (KR / root session):
    cd prism-insight && python3 -m pytest tests/test_insight_broadcast.py -q
"""

from __future__ import annotations

import pytest  # noqa: E402

from cores.llm.capabilities import insight_image_enabled  # noqa: E402
from cores.llm.features import insight_broadcast  # noqa: E402


# --------------------------------------------------------------------------- #
# Gate defaults                                                               #
# --------------------------------------------------------------------------- #
class TestInsightImageGate:
    def test_gate_defaults_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_INSIGHT_IMAGE", raising=False)
        assert insight_image_enabled() is False

    def test_gate_on_when_env_set(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_INSIGHT_IMAGE", "on")
        assert insight_image_enabled() is True

    def test_gate_off_for_other_values(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_INSIGHT_IMAGE", "shadow")
        assert insight_image_enabled() is False


# --------------------------------------------------------------------------- #
# Fake bot                                                                    #
# --------------------------------------------------------------------------- #
class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_photo_bytes(self, chat_id, image_bytes, caption=None, market=None):
        self.sent.append((chat_id, image_bytes, caption, market))
        return True


# --------------------------------------------------------------------------- #
# broadcast_insight_images                                                    #
# --------------------------------------------------------------------------- #
class TestBroadcastInsightImages:
    @pytest.mark.asyncio
    async def test_noop_when_gate_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_INSIGHT_IMAGE", raising=False)
        # Even if vision were available, the broadcast gate is off.
        monkeypatch.setattr(
            "cores.llm.capabilities.vision_available", lambda: True, raising=False
        )

        build_calls = {"n": 0}

        async def _fake_build(*a, **k):  # pragma: no cover
            build_calls["n"] += 1
            return b"JPEGBYTES"

        monkeypatch.setattr(
            "cores.llm.features.insight_image.build_insight_image_for",
            _fake_build, raising=False,
        )

        bot = _FakeBot()
        await insight_broadcast.broadcast_insight_images(
            bot, "chat", ["005930_삼성전자_20260101_buy_gpt5.4-mini.pdf"], market=None
        )
        assert bot.sent == []
        assert build_calls["n"] == 0

    @pytest.mark.asyncio
    async def test_sends_image_when_gate_on_and_vision_available(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_INSIGHT_IMAGE", "on")
        monkeypatch.setattr(
            "cores.llm.capabilities.vision_available", lambda: True, raising=False
        )

        seen = {"ticker": None, "company": None, "market": "unset"}

        async def _fake_build(ticker, company_name=None, market=None, **k):
            seen["ticker"] = ticker
            seen["company"] = company_name
            seen["market"] = market
            return b"JPEGBYTES"

        monkeypatch.setattr(
            "cores.llm.features.insight_image.build_insight_image_for",
            _fake_build, raising=False,
        )

        bot = _FakeBot()
        await insight_broadcast.broadcast_insight_images(
            bot, "chat-123",
            ["005930_삼성전자_20260101_buy_gpt5.4-mini.pdf"],
            market="KR",
        )
        assert len(bot.sent) == 1
        chat_id, img, caption, market = bot.sent[0]
        assert chat_id == "chat-123"
        assert img == b"JPEGBYTES"
        assert "삼성전자" in caption
        # "KR" is normalised to None so KR auto-detection runs.
        assert seen["ticker"] == "005930"
        assert seen["market"] is None

    @pytest.mark.asyncio
    async def test_skips_when_image_none(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_INSIGHT_IMAGE", "on")
        monkeypatch.setattr(
            "cores.llm.capabilities.vision_available", lambda: True, raising=False
        )

        async def _fake_build(*a, **k):
            return None

        monkeypatch.setattr(
            "cores.llm.features.insight_image.build_insight_image_for",
            _fake_build, raising=False,
        )

        bot = _FakeBot()
        await insight_broadcast.broadcast_insight_images(
            bot, "chat", ["AAPL_Apple_20260101_buy_gpt5.4-mini.pdf"], market="us"
        )
        assert bot.sent == []

    @pytest.mark.asyncio
    async def test_never_raises_on_send_failure(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_INSIGHT_IMAGE", "on")
        monkeypatch.setattr(
            "cores.llm.capabilities.vision_available", lambda: True, raising=False
        )

        async def _fake_build(*a, **k):
            return b"JPEGBYTES"

        monkeypatch.setattr(
            "cores.llm.features.insight_image.build_insight_image_for",
            _fake_build, raising=False,
        )

        class _BoomBot:
            async def send_photo_bytes(self, *a, **k):
                raise RuntimeError("telegram down")

        # Must not raise into the batch.
        await insight_broadcast.broadcast_insight_images(
            _BoomBot(), "chat",
            ["005930_삼성전자_20260101_buy_gpt5.4-mini.pdf"], market=None
        )
