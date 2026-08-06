from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.analysis_service import AnalysisService
from kakao_bot.domain.models import AnalysisJob, ApprovalStatus, OutboundDelivery
from kakao_bot.ports.analysis import AnalysisOutcome
from kakao_bot.runtime.analysis_worker_main import (
    AnalysisWorkerConfigurationError,
    _configure_report_data_sources,
    _configure_report_model,
    _configure_report_parallelism,
    _report_public_base_url,
    _start_llm_runtime,
    _stop_llm_runtime,
)

NOW = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)


class FakeAnalysisPort:
    """Records calls and returns/raises whatever the test queues up."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = []

    def generate(self, ticker, company_name, *, market):
        self.calls.append((ticker, company_name, market))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_job(
    *,
    job_id: str = "job-1",
    room_id: str = "room-1",
    user_id: str = "user-1",
    ticker: str = "005930",
    company_name: str = "Samsung Electronics",
    market: str = "kr",
) -> AnalysisJob:
    return AnalysisJob(
        job_id=job_id,
        room_id=room_id,
        user_id=user_id,
        ticker=ticker,
        company_name=company_name,
        market=market,
    )


def prepare_room(repository: SQLiteKakaoRepository, room_id: str = "room-1") -> None:
    repository.discover_room(room_id)
    repository.set_room_approval(room_id, ApprovalStatus.APPROVED)


def test_successful_job_completes_and_enqueues_analysis_result(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)

        analysis = FakeAnalysisPort(
            AnalysisOutcome(succeeded=True, summary="Buy signal detected.")
        )
        service = AnalysisService(repository, analysis)

        result = service.run_once(now=NOW, lease_seconds=900, limit=1)

        assert result.claimed == 1
        assert result.completed == 1
        assert result.failed == 0
        assert result.released == 0
        assert len(analysis.calls) == 1

        [job_row] = repository.list_analysis_jobs()
        assert job_row["status"] == "COMPLETED"
        assert job_row["summary"] == "Buy signal detected."

        [delivery] = repository.list_outbox()
        assert delivery["message_type"] == "analysis_result"
        assert delivery["delivery_key"] == "analysis:job-1"


def test_explicit_failure_marks_job_failed_and_enqueues_notice(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)

        analysis = FakeAnalysisPort(
            AnalysisOutcome(succeeded=False, error_code="generation_failed")
        )
        service = AnalysisService(repository, analysis)

        result = service.run_once(now=NOW, lease_seconds=900, limit=1)

        assert result.claimed == 1
        assert result.failed == 1
        assert result.completed == 0
        assert result.released == 0

        [job_row] = repository.list_analysis_jobs()
        assert job_row["status"] == "FAILED"
        assert job_row["error_code"] == "generation_failed"

        [delivery] = repository.list_outbox()
        assert delivery["message_type"] == "analysis_failed"
        assert delivery["delivery_key"] == "analysis:job-1"


def test_exception_below_attempt_threshold_is_released_for_retry(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)

        analysis = FakeAnalysisPort(RuntimeError("transient upstream error"))
        service = AnalysisService(repository, analysis, max_attempts=3)

        result = service.run_once(now=NOW, lease_seconds=900, limit=1)

        assert result.released == 1
        assert result.failed == 0

        [job_row] = repository.list_analysis_jobs()
        assert job_row["status"] == "PENDING"
        assert job_row["lease_expires_at"] is None

        # Released job can be claimed again.
        [reclaimed] = repository.claim_analysis_jobs(
            now=NOW + timedelta(seconds=1), lease_seconds=900, limit=1
        )
        assert reclaimed.job_id == "job-1"
        assert reclaimed.attempt_count == 2
        assert repository.list_outbox() == ()


def test_exception_at_or_above_attempt_threshold_is_confirmed_failed(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)
        # Simulate two prior failed attempts by claiming and releasing twice
        # so attempt_count reaches the threshold on the next claim.
        repository.claim_analysis_jobs(now=NOW, lease_seconds=900, limit=1)
        repository.release_analysis_job("job-1", now=NOW)
        repository.claim_analysis_jobs(now=NOW, lease_seconds=900, limit=1)
        repository.release_analysis_job("job-1", now=NOW)

        analysis = FakeAnalysisPort(RuntimeError("permanent upstream error"))
        service = AnalysisService(repository, analysis, max_attempts=3)

        result = service.run_once(now=NOW, lease_seconds=900, limit=1)

        assert result.failed == 1
        assert result.released == 0

        [job_row] = repository.list_analysis_jobs()
        assert job_row["status"] == "FAILED"
        assert "permanent upstream error" in job_row["error_code"]


def test_delivery_key_is_job_id_based_and_deduplicates_on_reprocessing(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)

        analysis = FakeAnalysisPort(
            AnalysisOutcome(succeeded=True, summary="First summary."),
        )
        service = AnalysisService(repository, analysis)
        service.run_once(now=NOW, lease_seconds=900, limit=1)

        # Simulate the same job being processed again after a restart (the
        # job row is reset to PENDING as if it had never completed): the
        # delivery_key derived from job_id must be the same, so re-enqueuing
        # is a no-op and the outbox does not gain a second entry.
        assert (
            repository.enqueue_outbound(
                OutboundDelivery(
                    delivery_key="analysis:job-1",
                    room_id="room-1",
                    message_type="analysis_result",
                    payload={"job_id": "job-1"},
                    created_at=NOW,
                )
            )
            is False
        )
        assert len(repository.list_outbox()) == 1


def test_empty_queue_does_nothing(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        analysis = FakeAnalysisPort()
        service = AnalysisService(repository, analysis)

        result = service.run_once(now=NOW, lease_seconds=900, limit=1)

        assert result.claimed == 0
        assert result.completed == 0
        assert result.failed == 0
        assert result.released == 0
        assert analysis.calls == []
        assert repository.list_outbox() == ()


def test_batch_size_is_respected(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        for index in range(3):
            repository.enqueue_analysis_job(
                make_job(job_id=f"job-{index}"), now=NOW + timedelta(seconds=index)
            )

        analysis = FakeAnalysisPort(
            AnalysisOutcome(succeeded=True, summary="Summary A."),
            AnalysisOutcome(succeeded=True, summary="Summary B."),
        )
        service = AnalysisService(repository, analysis)

        result = service.run_once(now=NOW, lease_seconds=900, limit=2)

        assert result.claimed == 2
        assert result.completed == 2
        assert len(analysis.calls) == 2
        assert len(repository.list_outbox()) == 2


def test_report_public_base_url_uses_deployed_env_name():
    assert (
        _report_public_base_url(
            {"KAKAO_BOT_PUBLIC_BASE_URL": "https://analysis.example.test"}
        )
        == "https://analysis.example.test"
    )


def test_report_public_base_url_keeps_legacy_env_compatibility():
    assert (
        _report_public_base_url(
            {"KAKAO_PUBLIC_BASE_URL": "https://legacy.example.test"}
        )
        == "https://legacy.example.test"
    )


def test_kakao_reports_default_to_recent_investor_flow_fallback():
    environ = {}

    assert _configure_report_data_sources(environ) == "fdr,naver,krx"
    assert environ["PRISM_REPORT_DATA_SOURCES"] == "fdr,naver,krx"


def test_explicit_report_source_order_wins_over_kakao_default():
    environ = {"PRISM_REPORT_DATA_SOURCES": "kis,fdr,krx"}

    assert _configure_report_data_sources(environ) == "kis,fdr,krx"


def test_legacy_report_source_order_gains_recent_flow_fallback():
    environ = {"PRISM_REPORT_DATA_SOURCES": "fdr,krx"}

    assert _configure_report_data_sources(environ) == "fdr,naver,krx"


def test_kakao_reports_default_to_luna_with_medium_reasoning():
    environ = {}

    assert _configure_report_model(environ) == ("gpt-5.6-luna", "medium")
    assert environ["REPORT_MODEL"] == "gpt-5.6-luna"
    assert environ["REPORT_EFFORT"] == "medium"


def test_explicit_report_model_configuration_wins_over_kakao_default():
    environ = {"REPORT_MODEL": "custom-model", "REPORT_EFFORT": "medium"}

    assert _configure_report_model(environ) == ("custom-model", "medium")


def test_kakao_reports_default_to_three_way_parallelism():
    environ = {}

    assert _configure_report_parallelism(environ) == ("true", "3")
    assert environ["PRISM_PARALLEL_REPORT"] == "true"
    assert environ["PRISM_PARALLEL_REPORT_MAX_CONCURRENCY"] == "3"


def test_explicit_report_parallelism_wins_over_kakao_default():
    environ = {
        "PRISM_PARALLEL_REPORT": "false",
        "PRISM_PARALLEL_REPORT_MAX_CONCURRENCY": "2",
    }

    assert _configure_report_parallelism(environ) == ("false", "2")


@pytest.mark.asyncio
async def test_chatgpt_oauth_worker_starts_and_stops_proxy(monkeypatch):
    from cores import chatgpt_proxy

    calls = []

    def fake_inject_env():
        calls.append("inject")

    async def fake_start_proxy():
        calls.append("start")
        return True

    async def fake_stop_proxy():
        calls.append("stop")

    monkeypatch.setattr(chatgpt_proxy, "inject_env", fake_inject_env)
    monkeypatch.setattr(chatgpt_proxy, "start_proxy", fake_start_proxy)
    monkeypatch.setattr(chatgpt_proxy, "stop_proxy", fake_stop_proxy)

    started = await _start_llm_runtime(
        {"PRISM_OPENAI_AUTH_MODE": "chatgpt_oauth"}
    )
    await _stop_llm_runtime(started)

    assert started is True
    assert calls == ["inject", "start", "stop"]


@pytest.mark.asyncio
async def test_non_oauth_worker_does_not_start_proxy(monkeypatch):
    from cores import chatgpt_proxy

    async def unexpected_start():
        raise AssertionError("proxy should not start")

    monkeypatch.setattr(chatgpt_proxy, "start_proxy", unexpected_start)

    assert await _start_llm_runtime({"PRISM_OPENAI_AUTH_MODE": "api_key"}) is False


@pytest.mark.asyncio
async def test_oauth_proxy_start_failure_stops_worker(monkeypatch):
    from cores import chatgpt_proxy

    monkeypatch.setattr(chatgpt_proxy, "inject_env", lambda: None)

    async def failed_start():
        return False

    monkeypatch.setattr(chatgpt_proxy, "start_proxy", failed_start)

    with pytest.raises(AnalysisWorkerConfigurationError, match="could not start"):
        await _start_llm_runtime(
            {"PRISM_OPENAI_AUTH_MODE": "chatgpt_oauth"}
        )
