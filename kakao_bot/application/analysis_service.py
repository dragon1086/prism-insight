"""Application service that processes a batch of `/report` analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from kakao_bot.domain.models import OutboundDelivery
from kakao_bot.ports.analysis import AnalysisPort
from kakao_bot.ports.repositories import KakaoRepository


@dataclass(frozen=True)
class AnalysisBatchResult:
    claimed: int
    completed: int
    failed: int
    released: int


class AnalysisService:
    """Claim, generate, and record a bounded batch of analysis jobs.

    `AnalysisPort.generate` is a blocking call; this service is synchronous
    on purpose so runtimes can offload it with `asyncio.to_thread`.
    """

    def __init__(
        self,
        repository: KakaoRepository,
        analysis: AnalysisPort,
        *,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._repository = repository
        self._analysis = analysis
        self._max_attempts = max_attempts

    def run_once(
        self,
        *,
        now: datetime | None = None,
        lease_seconds: int = 900,
        limit: int = 1,
    ) -> AnalysisBatchResult:
        run_at = _as_utc(now or datetime.now(timezone.utc))
        jobs = self._repository.claim_analysis_jobs(
            now=run_at,
            lease_seconds=lease_seconds,
            limit=limit,
        )

        completed = 0
        failed = 0
        released = 0

        for job in jobs:
            try:
                outcome = self._analysis.generate(
                    job.ticker,
                    job.company_name,
                    market=job.market,
                )
            except Exception as exc:  # noqa: BLE001 - transient vs. permanent below
                if job.attempt_count >= self._max_attempts:
                    error_code = str(exc) or type(exc).__name__
                    self._repository.fail_analysis_job(
                        job.job_id,
                        error_code=error_code[:1_000],
                        now=run_at,
                    )
                    failed += 1
                else:
                    self._repository.release_analysis_job(job.job_id, now=run_at)
                    released += 1
                continue

            if outcome.succeeded:
                summary = outcome.summary or ""
                self._repository.complete_analysis_job(
                    job.job_id,
                    summary=summary,
                    artifact_token=None,
                    now=run_at,
                )
                completed += 1
                self._repository.enqueue_outbound(
                    OutboundDelivery(
                        delivery_key=_delivery_key(job.job_id),
                        room_id=job.room_id,
                        message_type="analysis_result",
                        payload={
                            "job_id": job.job_id,
                            "ticker": job.ticker,
                            "company_name": job.company_name,
                            "summary": summary,
                            "market": job.market,
                        },
                        created_at=run_at,
                    )
                )
            else:
                error_code = outcome.error_code or "unknown_error"
                self._repository.fail_analysis_job(
                    job.job_id,
                    error_code=error_code,
                    now=run_at,
                )
                failed += 1
                self._repository.enqueue_outbound(
                    OutboundDelivery(
                        delivery_key=_delivery_key(job.job_id),
                        room_id=job.room_id,
                        message_type="analysis_failed",
                        payload={
                            "job_id": job.job_id,
                            "ticker": job.ticker,
                            "company_name": job.company_name,
                            "error_code": error_code,
                        },
                        created_at=run_at,
                    )
                )

        return AnalysisBatchResult(
            claimed=len(jobs),
            completed=completed,
            failed=failed,
            released=released,
        )


def _delivery_key(job_id: str) -> str:
    return f"analysis:{job_id}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
