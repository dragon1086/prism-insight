"""Application service that processes a batch of `/report` analysis jobs."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kakao_bot.domain.models import OutboundDelivery
from kakao_bot.ports.analysis import AnalysisPort
from kakao_bot.ports.repositories import KakaoRepository

logger = logging.getLogger(__name__)

# Enough of the report to contain its executive summary; the renderer never
# shows more, and full reports run to hundreds of kilobytes.
_SUMMARY_PAYLOAD_LIMIT = 8_000

DEFAULT_LINK_TTL_HOURS = 72
_TOKEN_BYTES = 32

# `AnalysisJob.kind` for a free-form question. Anything else is a report, which
# is what every job was before ask existed.
ASK_KIND = "ask"


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
        public_base_url: str | None = None,
        link_ttl_hours: int = DEFAULT_LINK_TTL_HOURS,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if link_ttl_hours <= 0:
            raise ValueError("link_ttl_hours must be positive")
        self._repository = repository
        self._analysis = analysis
        self._max_attempts = max_attempts
        # Without a public base URL there is nowhere to serve a PDF from, so
        # links are simply not minted and the card ships summary-only.
        self._public_base_url = (public_base_url or "").rstrip("/")
        self._link_ttl = timedelta(hours=link_ttl_hours)

    def _mint_report_link(self, job, pdf_path: str | None, now: datetime) -> str | None:
        if not pdf_path or not self._public_base_url:
            return None
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        try:
            self._repository.create_report_link(
                token,
                artifact_path=pdf_path,
                room_id=job.room_id,
                now=now,
                expires_at=now + self._link_ttl,
            )
        except Exception:  # noqa: BLE001 - a missing link must not lose the report
            logger.exception("Could not mint a report link for %s", job.job_id)
            return None
        return token

    def _link_url(self, token: str | None) -> str | None:
        if not token or not self._public_base_url:
            return None
        return f"{self._public_base_url}/kakao/reports/{token}"

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
            is_ask = job.kind == ASK_KIND
            try:
                if is_ask:
                    outcome = self._analysis.answer(_question_of(job))
                else:
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
                token = self._mint_report_link(job, outcome.pdf_path, run_at)
                self._repository.complete_analysis_job(
                    job.job_id,
                    summary=summary,
                    artifact_token=token,
                    now=run_at,
                )
                completed += 1
                # Assigned to a local rather than passed inline so the renderer
                # coverage test can still read the literals out of the AST.
                if is_ask:
                    message_type = "ask_result"
                    payload: dict[str, object] = {
                        "job_id": job.job_id,
                        "question": _question_of(job),
                        "answer": summary[:_SUMMARY_PAYLOAD_LIMIT],
                    }
                else:
                    message_type = "analysis_result"
                    payload = {
                        "job_id": job.job_id,
                        "ticker": job.ticker,
                        "company_name": job.company_name,
                        # The renderer only needs enough to lift the
                        # executive summary; full reports run to hundreds
                        # of kilobytes and would bloat the outbox row.
                        "summary": summary[:_SUMMARY_PAYLOAD_LIMIT],
                        "market": job.market,
                        "pdf_url": self._link_url(token),
                    }
                self._repository.enqueue_outbound(
                    OutboundDelivery(
                        delivery_key=_delivery_key(job.job_id),
                        room_id=job.room_id,
                        message_type=message_type,
                        payload=payload,
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
                if is_ask:
                    message_type = "ask_failed"
                    failure: dict[str, object] = {
                        "job_id": job.job_id,
                        "question": _question_of(job),
                        "error_code": error_code,
                    }
                else:
                    message_type = "analysis_failed"
                    failure = {
                        "job_id": job.job_id,
                        "ticker": job.ticker,
                        "company_name": job.company_name,
                        "error_code": error_code,
                    }
                self._repository.enqueue_outbound(
                    OutboundDelivery(
                        delivery_key=_delivery_key(job.job_id),
                        room_id=job.room_id,
                        message_type=message_type,
                        payload=failure,
                        created_at=run_at,
                    )
                )

        return AnalysisBatchResult(
            claimed=len(jobs),
            completed=completed,
            failed=failed,
            released=released,
        )


def _question_of(job) -> str:
    payload = job.payload or {}
    question = payload.get("question")
    return question if isinstance(question, str) else ""


def _delivery_key(job_id: str) -> str:
    return f"analysis:{job_id}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
