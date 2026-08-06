"""Runtime entry point for the durable Kakao `/report` analysis worker."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.adapters.prism.report_adapter import PrismReportAdapter
from kakao_bot.application.analysis_service import (
    AnalysisBatchResult,
    AnalysisService,
)
from kakao_bot.ports.analysis import AnalysisPort

logger = logging.getLogger(__name__)

DEFAULT_KAKAO_REPORT_DATA_SOURCES = "fdr,naver,krx"


class AnalysisWorkerConfigurationError(ValueError):
    """Raised when analysis worker runtime configuration is invalid."""


@dataclass(frozen=True)
class AnalysisWorkerRuntimeConfig:
    database_path: Path = Path("kakao_bot.sqlite")
    poll_seconds: float = 5.0
    batch_size: int = 1
    lease_seconds: int = 900
    max_attempts: int = 3


def load_config(
    environ: Mapping[str, str] | None = None,
) -> AnalysisWorkerRuntimeConfig:
    values = os.environ if environ is None else environ

    database_path = Path(
        values.get("KAKAO_BOT_DATABASE_PATH", "kakao_bot.sqlite")
    ).expanduser()
    poll_seconds = _positive_float(
        values.get("KAKAO_ANALYSIS_POLL_SECONDS", "5"),
        "KAKAO_ANALYSIS_POLL_SECONDS",
    )
    batch_size = _positive_int(
        values.get("KAKAO_ANALYSIS_BATCH_SIZE", "1"),
        "KAKAO_ANALYSIS_BATCH_SIZE",
    )
    lease_seconds = _positive_int(
        values.get("KAKAO_ANALYSIS_LEASE_SECONDS", "900"),
        "KAKAO_ANALYSIS_LEASE_SECONDS",
    )
    max_attempts = _positive_int(
        values.get("KAKAO_ANALYSIS_MAX_ATTEMPTS", "3"),
        "KAKAO_ANALYSIS_MAX_ATTEMPTS",
    )
    return AnalysisWorkerRuntimeConfig(
        database_path=database_path,
        poll_seconds=poll_seconds,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )


async def _start_llm_runtime(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Start the OAuth proxy before the worker creates any LLM clients.

    The batch orchestrators already own this lifecycle, but the standalone
    Kakao analysis worker did not.  In ``chatgpt_oauth`` mode that left the
    worker without ``OPENAI_BASE_URL`` or ``OPENAI_API_KEY`` and every report,
    ask, and evaluation job failed only after it had been accepted from chat.
    """
    values = os.environ if environ is None else environ
    if values.get("PRISM_OPENAI_AUTH_MODE", "").strip() != "chatgpt_oauth":
        return False

    from cores.chatgpt_proxy import inject_env, start_proxy

    inject_env()
    if not await start_proxy():
        raise AnalysisWorkerConfigurationError(
            "ChatGPT OAuth proxy could not start; analysis worker will not accept jobs"
        )
    return True


async def _stop_llm_runtime(started: bool) -> None:
    if not started:
        return
    from cores.chatgpt_proxy import stop_proxy

    await stop_proxy()


async def run_analysis_worker_once(
    config: AnalysisWorkerRuntimeConfig,
    *,
    analysis: AnalysisPort | None = None,
    now: datetime | None = None,
) -> AnalysisBatchResult:
    with SQLiteKakaoRepository(config.database_path) as repository:
        service = _analysis_service(config, repository, analysis=analysis)
        return await asyncio.to_thread(
            service.run_once,
            now=now,
            lease_seconds=config.lease_seconds,
            limit=config.batch_size,
        )


async def run_analysis_worker(
    config: AnalysisWorkerRuntimeConfig,
    *,
    stop_event: asyncio.Event | None = None,
) -> int:
    stop = stop_event or asyncio.Event()
    with SQLiteKakaoRepository(config.database_path) as repository:
        service = _analysis_service(config, repository)
        while not stop.is_set():
            try:
                result = await asyncio.to_thread(
                    service.run_once,
                    lease_seconds=config.lease_seconds,
                    limit=config.batch_size,
                )
                if result.claimed:
                    logger.info(
                        "Kakao analysis batch "
                        "(claimed=%d, completed=%d, failed=%d, released=%d)",
                        result.claimed,
                        result.completed,
                        result.failed,
                        result.released,
                    )
            except Exception:
                logger.exception("Kakao analysis worker cycle failed")

            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=config.poll_seconds,
                )
            except TimeoutError:
                pass
    return 0


def _analysis_service(
    config: AnalysisWorkerRuntimeConfig,
    repository: SQLiteKakaoRepository,
    *,
    analysis: AnalysisPort | None = None,
) -> AnalysisService:
    resolved_analysis = analysis or PrismReportAdapter()
    return AnalysisService(
        repository,
        resolved_analysis,
        max_attempts=config.max_attempts,
        # Unset means no public endpoint exists yet, in which case no link is
        # minted and the card ships summary-only rather than a dead button.
        public_base_url=_report_public_base_url(),
        link_ttl_hours=int(os.getenv("KAKAO_REPORT_LINK_TTL_HOURS", "72")),
    )


def _report_public_base_url(environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve the deployed PDF-link setting with legacy compatibility."""

    values = os.environ if environ is None else environ
    return values.get("KAKAO_BOT_PUBLIC_BASE_URL") or values.get(
        "KAKAO_PUBLIC_BASE_URL"
    )


def _configure_report_data_sources(
    environ: MutableMapping[str, str] | None = None,
) -> str:
    """Give Kakao reports a public recent-flow fallback without overriding ops.

    ``fdr,krx`` was deployed as an outage-era fallback before the public recent
    flow source existed. Upgrade only that exact legacy value; a deliberately
    configured order such as ``kis,fdr,krx`` remains untouched.
    """

    values = os.environ if environ is None else environ
    if values.get("PRISM_REPORT_DATA_SOURCES") == "fdr,krx":
        values["PRISM_REPORT_DATA_SOURCES"] = DEFAULT_KAKAO_REPORT_DATA_SOURCES
    return values.setdefault(
        "PRISM_REPORT_DATA_SOURCES", DEFAULT_KAKAO_REPORT_DATA_SOURCES
    )


async def async_main() -> int:
    llm_runtime_started = False
    try:
        config = load_config()
        _configure_report_data_sources()
        llm_runtime_started = await _start_llm_runtime()
    except AnalysisWorkerConfigurationError as exc:
        logger.error("%s", exc)
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed_signals.append(sig)
        except NotImplementedError:
            pass
    try:
        return await run_analysis_worker(config, stop_event=stop)
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
        await _stop_llm_runtime(llm_runtime_started)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("KAKAO_ANALYSIS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(async_main())


def _positive_float(raw: str, name: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise AnalysisWorkerConfigurationError(f"{name} must be numeric") from exc
    if value <= 0:
        raise AnalysisWorkerConfigurationError(f"{name} must be positive")
    return value


def _positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise AnalysisWorkerConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise AnalysisWorkerConfigurationError(f"{name} must be positive")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
