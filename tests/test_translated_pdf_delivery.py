from __future__ import annotations

import asyncio
import time

import pytest

import pdf_converter
import stock_analysis_orchestrator as orchestrator_module
from cores.agents import telegram_translator_agent
from stock_analysis_orchestrator import (
    StockAnalysisOrchestrator,
    _translated_pdf_limits,
)


def test_translated_pdf_limits_are_bounded_and_configurable() -> None:
    assert _translated_pdf_limits({}) == (3, 360, 1200)
    assert _translated_pdf_limits({
        "PRISM_TRANSLATED_PDF_MAX_CONCURRENCY": "2",
        "PRISM_TRANSLATED_PDF_ITEM_TIMEOUT_SECONDS": "90",
        "PRISM_TRANSLATED_PDF_BATCH_TIMEOUT_SECONDS": "600",
    }) == (2, 90, 600)
    assert _translated_pdf_limits({
        "PRISM_TRANSLATED_PDF_MAX_CONCURRENCY": "invalid",
        "PRISM_TRANSLATED_PDF_ITEM_TIMEOUT_SECONDS": "0",
    }) == (3, 1, 1200)


def test_translator_strict_mode_does_not_return_source_on_failure(monkeypatch) -> None:
    class BrokenLLM:
        async def generate_str(self, **_kwargs):
            raise RuntimeError("translation unavailable")

    class BrokenAgent:
        async def attach_llm(self, _llm_type):
            return BrokenLLM()

    monkeypatch.setattr(
        telegram_translator_agent,
        "create_telegram_translator_agent",
        lambda **_kwargs: BrokenAgent(),
    )
    monkeypatch.setattr(telegram_translator_agent, "log_openai_error", lambda *_args: None)

    assert asyncio.run(telegram_translator_agent.translate_telegram_message("원문")) == "원문"
    with pytest.raises(RuntimeError, match="translation unavailable"):
        asyncio.run(
            telegram_translator_agent.translate_telegram_message(
                "원문", raise_on_error=True
            )
        )


def test_kr_translated_pdfs_use_bounded_parallelism_and_skip_failures(
    monkeypatch, tmp_path
) -> None:
    active = 0
    max_active = 0
    sent = []
    real_sleep = asyncio.sleep

    report_a = tmp_path / "001_회사A_20260827_morning_gpt-5.6-luna.md"
    report_b = tmp_path / "002_회사B_20260827_morning_gpt-5.6-luna.md"
    report_a.write_text("good", encoding="utf-8")
    report_b.write_text("bad", encoding="utf-8")

    async def fake_translate(message, *, to_lang, raise_on_error, **_kwargs):
        nonlocal active, max_active
        assert raise_on_error is True
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
        async def send_document(self, channel_id, path, **_kwargs):
            sent.append((channel_id, path))
            return True

    async def fake_filename(report_file, lang):
        return tmp_path / f"{report_file.stem}_{lang}.md"

    monkeypatch.setenv("PRISM_TRANSLATED_PDF_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("PRISM_TRANSLATED_PDF_ITEM_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("PRISM_TRANSLATED_PDF_BATCH_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(telegram_translator_agent, "translate_telegram_message", fake_translate)
    monkeypatch.setattr(pdf_converter, "PdfRenderer", FakeRenderer)
    monkeypatch.setattr(orchestrator_module, "PDF_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", lambda _delay: _no_wait())

    orchestrator = StockAnalysisOrchestrator.__new__(StockAnalysisOrchestrator)
    orchestrator.telegram_config = FakeConfig()
    orchestrator._create_translated_filename = fake_filename

    asyncio.run(
        orchestrator._send_translated_pdfs(FakeBot(), [str(report_a), str(report_b)])
    )

    assert max_active == 2
    assert len(sent) == 3
    assert not (tmp_path / f"{report_b.stem}_ja.md").exists()


async def _no_wait() -> None:
    return None


def test_kr_translated_pdf_batch_deadline_cancels_late_work(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "001_회사_20260827_morning_gpt-5.6-luna.md"
    report.write_text("slow", encoding="utf-8")
    sent = []

    async def slow_translate(*_args, **_kwargs):
        await asyncio.sleep(1)
        return "translated"

    class FakeRenderer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeConfig:
        broadcast_languages = ["en"]

        @staticmethod
        def get_broadcast_channel_id(_lang):
            return "channel-en"

    class FakeBot:
        async def send_document(self, *_args, **_kwargs):
            sent.append(True)
            return True

    monkeypatch.setattr(telegram_translator_agent, "translate_telegram_message", slow_translate)
    monkeypatch.setattr(pdf_converter, "PdfRenderer", FakeRenderer)
    monkeypatch.setattr(orchestrator_module, "_translated_pdf_limits", lambda: (1, 1, 0.03))

    orchestrator = StockAnalysisOrchestrator.__new__(StockAnalysisOrchestrator)
    orchestrator.telegram_config = FakeConfig()

    started = time.monotonic()
    asyncio.run(orchestrator._send_translated_pdfs(FakeBot(), [str(report)]))

    assert time.monotonic() - started < 0.5
    assert sent == []
