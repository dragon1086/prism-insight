from __future__ import annotations

import asyncio

import pdf_converter
import us_stock_analysis_orchestrator as orchestrator_module
from us_stock_analysis_orchestrator import (
    USStockAnalysisOrchestrator,
    _translated_pdf_limits,
)


def test_us_translated_pdf_limits_match_kr_contract() -> None:
    assert _translated_pdf_limits({}) == (3, 360, 1800, 2)
    assert _translated_pdf_limits({
        "PRISM_TRANSLATED_PDF_MAX_CONCURRENCY": "2",
        "PRISM_TRANSLATED_PDF_ITEM_TIMEOUT_SECONDS": "90",
        "PRISM_TRANSLATED_PDF_BATCH_TIMEOUT_SECONDS": "600",
        "PRISM_TRANSLATED_PDF_MAX_ATTEMPTS": "3",
    }) == (2, 90, 600, 3)


def test_us_translated_pdfs_use_bounded_parallelism_and_skip_failures(
    monkeypatch, tmp_path
) -> None:
    active = 0
    max_active = 0
    sent = []
    real_sleep = asyncio.sleep

    report_a = tmp_path / "AAA_Alpha_20260827_morning_gpt-5.6-luna.md"
    report_b = tmp_path / "BBB_Beta_20260827_morning_gpt-5.6-luna.md"
    report_a.write_text("good", encoding="utf-8")
    report_b.write_text("bad", encoding="utf-8")

    async def fake_translate(message, *, to_lang, raise_on_error, **_kwargs):
        nonlocal active, max_active
        assert raise_on_error is True
        assert _kwargs["reasoning_effort"] == "low"
        active += 1
        max_active = max(max_active, active)
        await real_sleep(0.02)
        active -= 1
        if message == "bad" and to_lang == "ja":
            raise RuntimeError("cannot translate")
        return f"{to_lang}:{message}"

    class FakeRenderer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def render(self, _source, output):
            return output

    class FakeConfig:
        broadcast_languages = ["en", "ja"]

        @staticmethod
        def get_broadcast_channel_id(lang):
            return f"channel-{lang}"

    class FakeBot:
        async def send_document(self, channel_id, path, **kwargs):
            assert kwargs["market"] == "us"
            sent.append((channel_id, path))
            return True

    monkeypatch.setenv("PRISM_TRANSLATED_PDF_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("PRISM_TRANSLATED_PDF_ITEM_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("PRISM_TRANSLATED_PDF_BATCH_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(orchestrator_module, "translate_telegram_message", fake_translate)
    monkeypatch.setattr(pdf_converter, "PdfRenderer", FakeRenderer)
    monkeypatch.setattr(orchestrator_module, "US_PDF_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", lambda _delay: _no_wait())

    orchestrator = USStockAnalysisOrchestrator.__new__(USStockAnalysisOrchestrator)
    orchestrator.telegram_config = FakeConfig()

    asyncio.run(
        orchestrator._send_translated_pdfs(FakeBot(), [str(report_a), str(report_b)])
    )

    assert max_active == 2
    assert len(sent) == 3
    assert not (tmp_path / f"{report_b.stem}_ja.md").exists()


async def _no_wait() -> None:
    return None


def test_us_translated_pdf_retries_one_timeout_and_sends_once(
    monkeypatch, tmp_path, caplog
) -> None:
    report = tmp_path / "AAA_Alpha_20260901_afternoon_gpt-5.6-luna.md"
    report.write_text("flaky", encoding="utf-8")
    real_sleep = asyncio.sleep
    attempts = 0
    sent = []

    async def flaky_translate(message, *, reasoning_effort, **_kwargs):
        nonlocal attempts
        assert message == "flaky"
        assert reasoning_effort == "low"
        attempts += 1
        if attempts == 1:
            await real_sleep(0.03)
        return "translated"

    class FakeRenderer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def render(self, _source, output):
            return output

    class FakeConfig:
        broadcast_languages = ["ja"]

        @staticmethod
        def get_broadcast_channel_id(_lang):
            return "channel-ja"

    class FakeBot:
        async def send_document(self, channel_id, path, **kwargs):
            assert kwargs["market"] == "us"
            sent.append((channel_id, path))
            return True

    monkeypatch.setattr(orchestrator_module, "translate_telegram_message", flaky_translate)
    monkeypatch.setattr(pdf_converter, "PdfRenderer", FakeRenderer)
    monkeypatch.setattr(orchestrator_module, "US_PDF_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(
        orchestrator_module,
        "_translated_pdf_limits",
        lambda: (1, 0.01, 1, 2),
    )
    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", lambda _delay: _no_wait())

    orchestrator = USStockAnalysisOrchestrator.__new__(USStockAnalysisOrchestrator)
    orchestrator.telegram_config = FakeConfig()

    with caplog.at_level("INFO"):
        asyncio.run(orchestrator._send_translated_pdfs(FakeBot(), [str(report)]))

    assert attempts == 2
    assert len(sent) == 1
    assert "status=retry" in caplog.text
    assert "attempt=1 next_attempt=2" in caplog.text
