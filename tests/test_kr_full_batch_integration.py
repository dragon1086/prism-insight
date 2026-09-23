"""Real KR orchestration, with external services replaced at their boundaries.

These are not live model/provider/order tests. Screening fixtures enter through
run_batch; the real orchestrator reads its JSON, writes reports, converts each
artifact, prepares notifications and hands only successful PDFs to tracking.
"""
import asyncio
import importlib
import json
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRISM_BATCH_REPORT_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("PRISM_DISABLE_SIGNAL_PUBLISH", "1")
    module = importlib.import_module("stock_analysis_orchestrator")
    events = []
    state = SimpleNamespace(
        candidates=["005930", "000660"], failed_reports=set(), failed_pdfs=set(),
        silent_summary_failures=set(),
        tracking_calls=[], completed=[], forbidden_calls=[], status_messages=[],
        sent_message_paths=[], metadata={"market_participation": {"status": "fixture"}},
    )

    def no_network(*args, **kwargs):
        state.forbidden_calls.append((args, kwargs))
        raise AssertionError("No network, broker, model or live publishing allowed")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for name in ("REPORTS_DIR", "PDF_REPORTS_DIR", "TELEGRAM_MSGS_DIR"):
        directory = tmp_path / name.lower()
        directory.mkdir()
        monkeypatch.setattr(module, name, directory)

    def install(name, **members):
        replacement = ModuleType(name)
        replacement.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, replacement)

    def run_batch(mode, level, output, **kwargs):
        events.append("screen")
        Path(output).write_text(json.dumps({
            "metadata": state.metadata,
            "results": {},
        }))
        if not state.candidates:
            return {}
        return {"Volume Surge Top Stocks": pd.DataFrame(
            {"Company Name": ["회사" + code for code in state.candidates]},
            index=state.candidates,
        )}

    install("trigger_batch", run_batch=run_batch,
            MarketSnapshotUnavailableError=type("MarketSnapshotUnavailableError", (Exception,), {}))

    async def analyze_stock(company_code, **kwargs):
        events.append("report:" + company_code)
        assert kwargs["macro_context"]["market_regime"] == "sideways"
        if company_code in state.failed_reports:
            raise RuntimeError("isolated report failure")
        return "# 분석 보고서\n" + company_code + "\n<!-- DART_DEEP_ANALYSIS_END -->"

    install("cores.main", analyze_stock=analyze_stock)

    def markdown_to_pdf(source, destination, *args, **kwargs):
        ticker = Path(source).name.split("_")[0]
        events.append("pdf:" + ticker)
        assert "분석 보고서" in Path(source).read_text()
        if ticker in state.failed_pdfs:
            raise RuntimeError("isolated PDF failure")
        Path(destination).write_bytes(b"%PDF-fixture")

    install("pdf_converter", markdown_to_pdf=markdown_to_pdf)

    class Summary:
        async def process_report(self, report, output, **kwargs):
            ticker, company = Path(report).stem.split("_")[:2]
            events.append("summary:" + ticker)
            assert Path(report).read_bytes() == b"%PDF-fixture"
            if ticker in state.silent_summary_failures:
                return
            (Path(output) / f"{ticker}_{company}_telegram.txt").write_text("분석 요약")

    install("telegram_summary_agent", TelegramSummaryGenerator=Summary)

    class Bot:
        async def process_messages_directory(self, directory, *args, **kwargs):
            events.append("telegram:summary")
            assert Path(directory).is_dir()
            paths = kwargs["message_paths"]
            assert all(Path(path).exists() for path in paths)
            state.sent_message_paths.extend(paths)

        async def send_message(self, chat_id, message, **kwargs):
            assert chat_id == "fixture-channel"
            assert kwargs["msg_type"] == "trigger"
            state.status_messages.append(message)
            events.append("telegram:status")
            return True

        async def send_document(self, chat_id, document, **kwargs):
            events.append("telegram:pdf:" + Path(document).name.split("_")[0])
            assert chat_id == "fixture-channel"
            assert Path(document).exists()
            return True

    install("telegram_bot_agent", TelegramBotAgent=Bot)

    class Tracker:
        def __init__(self, **kwargs):
            self.last_batch_messages = ["fixture portfolio"]

        async def run(self, paths, *args, **kwargs):
            events.append("tracking")
            state.tracking_calls.append((list(paths), args, kwargs))
            assert all(Path(path).exists() for path in paths)
            return True

    install("stock_tracking_enhanced_agent", EnhancedStockTrackingAgent=Tracker)
    install("stock_tracking_agent", _kr_codex_runtime_enabled=lambda: True, app=None)

    async def ingest(*args, **kwargs):
        events.append("archive")

    install("cores.archive.ingest", ingest_reports_async=ingest)
    install("cores.llm.features.insight_broadcast", broadcast_insight_images=ingest)

    async def publish_campaign(**kwargs):
        events.append("campaign:" + kwargs["status"])

    async def publish_reports(**kwargs):
        events.append("campaign:reports")
        state.published_pdfs = list(kwargs["pdf_paths"])
        state.published_message_paths = list(kwargs["message_paths"])

    async def publish_tracking(**kwargs):
        events.append("campaign:tracking")
        assert kwargs["messages"] == ["fixture portfolio"]

    monkeypatch.setattr(module, "publish_batch_campaign_best_effort", publish_campaign)
    monkeypatch.setattr(module, "publish_batch_reports_best_effort", publish_reports)
    monkeypatch.setattr(module, "publish_batch_tracking_story_best_effort", publish_tracking)
    from observability import micro_split
    monkeypatch.setattr(micro_split, "begin_shadow_batch", lambda **kwargs: "fixture")
    monkeypatch.setattr(micro_split, "get_shadow_batch_context", lambda: None)
    monkeypatch.setattr(micro_split, "complete_shadow_batch", lambda **kwargs: state.completed.append(kwargs))
    monkeypatch.setattr(micro_split, "end_shadow_batch", lambda token: events.append("end"))

    config = SimpleNamespace(
        use_telegram=True, channel_id="fixture-channel", bot_token="fixture",
        broadcast_languages=[], validate_or_raise=lambda: None, log_status=lambda: None,
    )
    orchestrator = module.StockAnalysisOrchestrator(config)

    async def macro(**kwargs):
        events.append("macro")
        return {"market_regime": "sideways", "sector_map": {"005930": "반도체"}}

    original_alert = orchestrator.send_trigger_alert

    async def alert(*args, **kwargs):
        if "status_message" in kwargs:
            return await original_alert(*args, **kwargs)
        events.append("trigger:alert")
        return True

    monkeypatch.setattr(orchestrator, "run_macro_intelligence", macro)
    monkeypatch.setattr(orchestrator, "send_trigger_alert", alert)
    return orchestrator, state, events


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
def test_kr_selected_report_pdf_notification_tracking_chain(pipeline, mode):
    orchestrator, state, events = pipeline
    asyncio.run(orchestrator.run_full_pipeline(mode))
    assert events[:3] == ["macro", "screen", "trigger:alert"]
    assert events.index("report:005930") < events.index("pdf:005930")
    assert events.index("pdf:005930") < events.index("summary:005930")
    assert events.index("telegram:pdf:000660") < events.index("tracking")
    assert events.index("tracking") < events.index("campaign:tracking")
    assert len(state.tracking_calls) == 1
    paths, _, kwargs = state.tracking_calls[0]
    assert [p.name.split("_")[0] for p in paths] == ["005930", "000660"]
    assert kwargs["market_regime"] == "sideways"
    assert kwargs["sector_names"] == ["반도체"]
    assert state.completed == [{"tracking_success": True, "selected_count": 2,
                                "report_count": 2, "pdf_count": 2}]
    assert "end" in events
    assert not state.forbidden_calls
    assert [p.name.split("_")[0] for p in state.sent_message_paths] == ["005930", "000660"]


@pytest.mark.parametrize("failure", ["report", "pdf"])
def test_kr_partial_failure_preserves_successful_report_handoff(pipeline, failure):
    orchestrator, state, events = pipeline
    getattr(state, "failed_" + ("reports" if failure == "report" else "pdfs")).add("005930")
    asyncio.run(orchestrator.run_full_pipeline("afternoon"))
    assert len(state.tracking_calls) == 1
    assert [p.name.split("_")[0] for p in state.tracking_calls[0][0]] == ["000660"]
    assert state.published_pdfs == state.tracking_calls[0][0]
    assert "telegram:pdf:005930" not in events
    assert "campaign:tracking" in events
    assert not state.forbidden_calls


@pytest.mark.parametrize("empty_stage", ["candidates", "reports", "pdfs"])
def test_kr_empty_stage_notifies_without_empty_tracking_or_stale_delivery(pipeline, empty_stage):
    """No implicit tracking([]): that can rediscover old reports for BUY."""
    orchestrator, state, events = pipeline
    if empty_stage == "candidates":
        state.candidates = []
    else:
        getattr(state, "failed_" + empty_stage).update(state.candidates)
    asyncio.run(orchestrator.run_full_pipeline("afternoon"))
    assert not state.tracking_calls
    assert "campaign:tracking" not in events
    assert not state.sent_message_paths
    assert "telegram:summary" not in events
    assert len(state.status_messages) == 1
    expected = {
        "candidates": "신규 분석 대상으로 선정된 종목이 없습니다",
        "reports": "보고서 생성이 모두 실패",
        "pdfs": "PDF 생성이 모두 실패",
    }
    assert expected[empty_stage] in state.status_messages[0]
    if empty_stage == "candidates":
        assert "trigger:alert" not in events
        assert "campaign:SKIPPED" in events
    assert "end" in events
    assert not state.forbidden_calls


def test_kr_incomplete_provider_coverage_is_not_reported_as_no_signal(pipeline):
    orchestrator, state, events = pipeline
    state.candidates = []
    state.metadata = {"snapshot_coverage": {
        "status": "PARTIAL", "requested_count": 100, "current_count": 100,
        "previous_count": 2, "comparable_count": 2,
    }}
    asyncio.run(orchestrator.run_full_pipeline("afternoon"))
    assert len(state.status_messages) == 1
    assert "정상적인 신호 없음으로 해석하지 마세요" in state.status_messages[0]
    assert "비교 가능: 2" in state.status_messages[0]
    assert not state.tracking_calls and not state.forbidden_calls
    assert "campaign:SKIPPED" in events


def test_kr_disabled_telegram_keeps_kakao_artifacts_and_tracking(pipeline):
    orchestrator, state, events = pipeline
    orchestrator.telegram_config.use_telegram = False
    asyncio.run(orchestrator.run_full_pipeline("morning"))
    assert "campaign:reports" in events
    assert "campaign:tracking" in events
    assert not any(event.startswith("telegram:") for event in events)
    assert len(state.tracking_calls) == 1
    assert state.tracking_calls[0][1][0] is None
    assert not state.forbidden_calls


@pytest.mark.parametrize("failed", [True, False])
def test_kr_same_company_old_summary_requires_fresh_generation(pipeline, failed):
    orchestrator, state, _ = pipeline
    module = importlib.import_module("stock_analysis_orchestrator")
    state.candidates = ["005930"]
    old_summary = module.TELEGRAM_MSGS_DIR / "005930_회사005930_telegram.txt"
    old_summary.write_text("이전 배치 요약은 재전송하지 않습니다")
    previous = module.result_fingerprint(old_summary)
    if failed:
        state.silent_summary_failures.add("005930")
    asyncio.run(orchestrator.run_full_pipeline("afternoon"))
    assert len(state.tracking_calls) == 1
    assert not state.forbidden_calls
    if failed:
        assert old_summary.read_text() == "이전 배치 요약은 재전송하지 않습니다"
        assert module.result_fingerprint(old_summary) == previous
        assert state.sent_message_paths == []
        assert state.published_message_paths == []
    else:
        assert old_summary.read_text() == "분석 요약"
        assert state.sent_message_paths == [old_summary]
        assert state.published_message_paths == [old_summary]
