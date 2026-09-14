"""Report failures must never be delivered or persisted as successful reports."""
import ast
import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import report_generator as generator
from prism_core import report_service as service

ERRORS = [
    "Analysis time exceeded. Check log file: /private/server/report.log",
    "Error occurred during analysis: /private/server/secret.yaml",
    "Error occurred during US stock analysis: credential invalid",
    "Could not find analysis result. Log file: /private/log",
    "Error occurred while parsing analysis result. Log file: /private/log",
    "보고서 생성 중 오류가 발생했습니다: /private/config",
    "US 주식 분석 결과를 찾을 수 없습니다. 로그를 확인하세요.",
    "US 주식 분석 결과 파싱 중 오류가 발생했습니다. 로그를 확인하세요.",
    "US 주식 분석 시간이 초과되었습니다. 다시 시도해주세요.",
    "# 보고서\nAnalysis failed: PermissionError('/private/secret')",
]


@pytest.mark.parametrize("market", ["kr", "us"])
@pytest.mark.parametrize("content", ERRORS + [None, "", {"error": "secret"}])
def test_failed_content_never_saved(monkeypatch, market, content):
    backend = SimpleNamespace(
        get_cached=Mock(return_value=(False, None, None, None)),
        generate=Mock(return_value=content),
        save_markdown=Mock(), save_pdf=Mock(),
        failure_message=generator.REPORT_FAILURE_MESSAGE,
    )
    monkeypatch.setattr(service, "_backend", lambda _: backend)
    result = service.generate_report("000660", "SK하이닉스", market=market)
    assert result.status == service.FAILED
    assert result.content == generator.REPORT_FAILURE_MESSAGE
    assert result.markdown_path is result.pdf_path is None
    backend.save_markdown.assert_not_called()
    backend.save_pdf.assert_not_called()


@pytest.mark.parametrize("market", ["kr", "us"])
@pytest.mark.parametrize("content", ERRORS)
def test_legacy_failure_cache_refused_without_deletion(monkeypatch, tmp_path, market, content):
    reports = tmp_path / "reports"
    pdfs = tmp_path / "pdfs"
    reports.mkdir()
    pdfs.mkdir()
    prefix = "US_" if market == "us" else ""
    monkeypatch.setattr(generator, prefix + "REPORTS_DIR", reports)
    monkeypatch.setattr(generator, prefix + "PDF_REPORTS_DIR", pdfs)
    md = reports / "000660_SK_20260914_analysis.md"
    pdf = pdfs / "000660_SK_20260914_analysis.pdf"
    md.write_text(content, encoding="utf-8")
    pdf.write_bytes(b"legacy sensitive PDF")
    reader = generator.get_cached_us_report if market == "us" else generator.get_cached_report
    assert reader("000660") == (False, "", None, None)
    assert md.read_text(encoding="utf-8") == content
    assert pdf.read_bytes() == b"legacy sensitive PDF"


@pytest.mark.parametrize("market", ["kr", "us"])
@pytest.mark.parametrize("failure", ["timeout", "exception", "missing", "malformed", "error", "success"])
def test_subprocess_failure_has_no_user_error_text(monkeypatch, tmp_path, market, failure):
    monkeypatch.setattr(generator, "__file__", str(tmp_path / "report_generator.py"))
    if failure == "missing":
        output = "private diagnostic only"
    elif failure == "malformed":
        output = "RESULT_START invalid json RESULT_END"
    else:
        output = "RESULT_START" + json.dumps(
            {"success": failure == "success", "result": "# 정상 보고서", "error": "/private/secret"}
        ) + "RESULT_END"
    if market == "kr":
        process = Mock()
        process.communicate.return_value = (output, "")
        if failure == "timeout":
            process.communicate.side_effect = [subprocess.TimeoutExpired("test", 1800), ("", "")]
        factory = Mock(return_value=process)
        if failure == "exception":
            factory.side_effect = RuntimeError("/private/secret")
        monkeypatch.setattr(generator.subprocess, "Popen", factory)
        result = generator.generate_report_response_sync("000660", "SK하이닉스")
        if failure == "timeout":
            process.kill.assert_called_once()
    else:
        run = Mock(return_value=SimpleNamespace(stdout=output, stderr="", returncode=0))
        if failure == "timeout":
            run.side_effect = subprocess.TimeoutExpired("test", 1200)
        elif failure == "exception":
            run.side_effect = RuntimeError("/private/secret")
        monkeypatch.setattr(generator.subprocess, "run", run)
        result = generator.generate_us_report_response_sync("AAPL", "Apple")
    assert result == ("# 정상 보고서" if failure == "success" else None)


@pytest.mark.parametrize("status", ["failed", "completed"])
@pytest.mark.parametrize("market", ["kr", "us"])
def test_delivery_refunds_and_never_sends_failed_pdf(tmp_path, status, market):
    from telegram_ai_bot import TelegramAIBot

    bot = object.__new__(TelegramAIBot)
    transport = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    bot.application = SimpleNamespace(bot=transport)
    bot.refund_daily_limit = Mock()
    pdf = tmp_path / "private.pdf"
    pdf.write_bytes(b"sensitive diagnostic")
    request = SimpleNamespace(
        id="test", chat_id=1, user_id=2, market_type=market,
        status=status, result=ERRORS[0], pdf_path=pdf,
        company_name="SK하이닉스", stock_code="000660",
    )
    asyncio.run(bot.send_report_result(request))
    transport.send_document.assert_not_awaited()
    transport.send_message.assert_awaited_once_with(
        chat_id=1, text=f"⚠️ {generator.REPORT_FAILURE_MESSAGE}"
    )
    bot.refund_daily_limit.assert_called_once_with(2, "us_report" if market == "us" else "report")


@pytest.mark.parametrize("explicit", [None, "https://explicit.example"])
def test_telegram_boot_selects_remote_before_analysis_import(explicit):
    source = Path(__file__).resolve().parents[1] / "telegram_ai_bot.py"
    tree = ast.parse(source.read_text())
    selector = next(node for node in tree.body if isinstance(node, ast.If) and "ARCHIVE_API_URL" in ast.unparse(node))
    dependency = next(node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "analysis_manager")
    dotenv = next(node for node in tree.body if isinstance(node, ast.Expr) and ast.unparse(node) == "load_dotenv()")
    assert dotenv.lineno < selector.lineno < dependency.lineno
    environment = {"ARCHIVE_API_URL": "https://archive.example"}
    if explicit:
        environment["PRISM_MARKET_DATA_REMOTE_URL"] = explicit
    # Execute only the trusted repository's boot selector, not external input.
    exec(compile(ast.Module(body=[selector], type_ignores=[]), str(source), "exec"), {"os": SimpleNamespace(environ=environment)})  # noqa: S102
    assert environment["PRISM_MARKET_DATA_REMOTE_URL"] == (explicit or "https://archive.example")
