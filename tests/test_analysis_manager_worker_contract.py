"""Characterization tests pinning the analysis worker's report behavior.

Written before extracting a channel-neutral report service (Phase 2 of the
Kakao bot work) so the refactor is provably behavior-preserving. These assert
the *current* observable contract of ``start_background_worker``:

- which report_generator function is called, with which arguments
- the resulting ``AnalysisRequest`` field values for every branch
- that the request id always reaches ``bot_instance.result_queue``

If a change here is intentional, update the expectation and say why.
"""

from __future__ import annotations

import queue
import threading

import pytest

from analysis_manager import AnalysisRequest, analysis_queue, start_background_worker
from prism_core import report_service


class FakeBot:
    def __init__(self) -> None:
        self.pending_requests: dict[str, AnalysisRequest] = {}
        self.result_queue: queue.Queue[str] = queue.Queue()


@pytest.fixture(scope="module")
def bot() -> FakeBot:
    """One shared worker; a per-test worker would steal the global queue."""

    instance = FakeBot()
    thread = start_background_worker(instance)
    assert isinstance(thread, threading.Thread)
    return instance


@pytest.fixture
def run_request(bot):
    def _run(request: AnalysisRequest) -> AnalysisRequest:
        analysis_queue.put(request)
        finished_id = bot.result_queue.get(timeout=15)
        assert finished_id == request.id
        assert bot.pending_requests[request.id] is request
        return request

    return _run


class Recorder:
    def __init__(self, return_value=None):
        self.calls: list[tuple] = []
        self._return_value = return_value

    def __call__(self, *args):
        self.calls.append(args)
        if isinstance(self._return_value, Exception):
            raise self._return_value
        return self._return_value


def _patch(monkeypatch, **overrides):
    recorders = {}
    for name, value in overrides.items():
        recorder = Recorder(value)
        recorders[name] = recorder
        monkeypatch.setattr(report_service, name, recorder)
    return recorders


def test_kr_cached_report_short_circuits_generation(monkeypatch, run_request):
    rec = _patch(
        monkeypatch,
        get_cached_report=(True, "cached body", "cached.md", "cached.pdf"),
        generate_report_response_sync=None,
        save_report=None,
        save_pdf_report=None,
    )

    request = run_request(AnalysisRequest("005930", "삼성전자"))

    assert request.status == "completed"
    assert request.result == "cached body"
    assert request.report_path == "cached.md"
    assert request.pdf_path == "cached.pdf"
    assert rec["get_cached_report"].calls == [("005930",)]
    assert rec["generate_report_response_sync"].calls == []
    assert rec["save_report"].calls == []
    assert rec["save_pdf_report"].calls == []


def test_kr_uncached_report_generates_then_saves_markdown_then_pdf(
    monkeypatch, run_request
):
    rec = _patch(
        monkeypatch,
        get_cached_report=(False, None, None, None),
        generate_report_response_sync="fresh body",
        save_report="report.md",
        save_pdf_report="report.pdf",
    )

    request = run_request(AnalysisRequest("005930", "삼성전자"))

    assert request.status == "completed"
    assert request.result == "fresh body"
    assert request.report_path == "report.md"
    assert request.pdf_path == "report.pdf"
    assert rec["generate_report_response_sync"].calls == [("005930", "삼성전자")]
    assert rec["save_report"].calls == [("005930", "삼성전자", "fresh body")]
    # PDF is derived from the markdown path returned by save_report.
    assert rec["save_pdf_report"].calls == [("005930", "삼성전자", "report.md")]


def test_kr_empty_generation_result_fails_without_writing_files(
    monkeypatch, run_request
):
    rec = _patch(
        monkeypatch,
        get_cached_report=(False, None, None, None),
        generate_report_response_sync=None,
        save_report=None,
        save_pdf_report=None,
    )

    request = run_request(AnalysisRequest("005930", "삼성전자"))

    assert request.status == "failed"
    assert request.result == "Error occurred during analysis."
    assert request.report_path is None
    assert request.pdf_path is None
    assert rec["save_report"].calls == []
    assert rec["save_pdf_report"].calls == []


def test_evaluate_request_is_skipped_by_the_worker(monkeypatch, run_request):
    rec = _patch(
        monkeypatch,
        get_cached_report=(False, None, None, None),
        generate_report_response_sync="must not run",
    )

    request = run_request(
        AnalysisRequest("005930", "삼성전자", avg_price=70000.0, period=30)
    )

    assert request.status == "skipped"
    assert request.result is None
    assert rec["generate_report_response_sync"].calls == []


def test_evaluate_request_still_receives_a_cached_report(monkeypatch, run_request):
    """The cache is consulted before the evaluate short-circuit.

    Subtle ordering: an evaluate request is only 'skipped' on a cache miss.
    """

    rec = _patch(
        monkeypatch,
        get_cached_report=(True, "cached body", "cached.md", "cached.pdf"),
        generate_report_response_sync="must not run",
    )

    request = run_request(
        AnalysisRequest("005930", "삼성전자", avg_price=70000.0, period=30)
    )

    assert request.status == "completed"
    assert request.result == "cached body"
    assert request.pdf_path == "cached.pdf"
    assert rec["generate_report_response_sync"].calls == []


def test_us_market_uses_the_us_report_functions(monkeypatch, run_request):
    rec = _patch(
        monkeypatch,
        get_cached_us_report=(False, None, None, None),
        generate_us_report_response_sync="us body",
        save_us_report="us.md",
        save_us_pdf_report="us.pdf",
        get_cached_report=(True, "kr must not run", "x.md", "x.pdf"),
    )

    request = run_request(AnalysisRequest("AAPL", "Apple", market_type="us"))

    assert request.status == "completed"
    assert request.result == "us body"
    assert request.report_path == "us.md"
    assert request.pdf_path == "us.pdf"
    assert rec["get_cached_us_report"].calls == [("AAPL",)]
    assert rec["save_us_report"].calls == [("AAPL", "Apple", "us body")]
    assert rec["save_us_pdf_report"].calls == [("AAPL", "Apple", "us.md")]
    assert rec["get_cached_report"].calls == []


def test_us_cached_report_short_circuits_generation(monkeypatch, run_request):
    rec = _patch(
        monkeypatch,
        get_cached_us_report=(True, "us cached", "us_cached.md", "us_cached.pdf"),
        generate_us_report_response_sync=None,
    )

    request = run_request(AnalysisRequest("AAPL", "Apple", market_type="us"))

    assert request.status == "completed"
    assert request.result == "us cached"
    assert request.report_path == "us_cached.md"
    assert request.pdf_path == "us_cached.pdf"
    assert rec["generate_us_report_response_sync"].calls == []


def test_generation_exception_is_reported_and_still_enqueued(
    monkeypatch, run_request
):
    _patch(
        monkeypatch,
        get_cached_report=(False, None, None, None),
        generate_report_response_sync=RuntimeError("upstream exploded"),
    )

    request = run_request(AnalysisRequest("005930", "삼성전자"))

    assert request.status == "failed"
    assert "upstream exploded" in request.result


def test_analysis_request_exposes_the_fields_the_worker_contract_depends_on():
    request = AnalysisRequest("005930", "삼성전자", chat_id=1, user_id=2, message_id=3)

    assert request.status == "pending"
    assert request.market_type == "kr"
    assert request.result is None
    assert request.report_path is None
    assert request.pdf_path is None
    assert request.id
