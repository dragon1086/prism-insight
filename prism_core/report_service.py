"""Channel-neutral on-demand report generation.

Phase 2 of the Kakao group bot work (design:
``docs/superpowers/specs/2026-07-23-kakao-bot-low-coupling-design.md`` §6.2).

This is the cache -> generate -> save markdown -> save PDF flow lifted verbatim
out of ``analysis_manager``'s Telegram worker so that Telegram and Kakao can
share one implementation without sharing a queue, a delivery path, or any
channel state. Callers get a plain :class:`ReportArtifact` back and decide for
themselves how to deliver it.

Unlike the rest of ``prism_core``, this module performs I/O — it writes report
files. That is inherent to the extracted behavior; the design names this path
explicitly. Keep the channel-specific parts (quota, acking, delivery) out.

Backend functions are resolved from this module's namespace at call time, so
tests and alternative adapters can substitute them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from report_generator import (
    generate_report_response_sync,
    generate_us_report_response_sync,
    get_cached_report,
    get_cached_us_report,
    save_pdf_report,
    save_report,
    save_us_pdf_report,
    save_us_report,
)

logger = logging.getLogger(__name__)

KR_FAILURE_MESSAGE = "Error occurred during analysis."
US_FAILURE_MESSAGE = "Error occurred during US stock analysis."

COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass(frozen=True)
class ReportArtifact:
    """One report generation outcome, free of any channel concern."""

    status: str
    content: str | None = None
    markdown_path: Any | None = None
    pdf_path: Any | None = None
    cached: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == COMPLETED


@dataclass(frozen=True)
class _Backend:
    get_cached: Callable[[str], tuple]
    generate: Callable[[str, str], str | None]
    save_markdown: Callable[[str, str, str], Any]
    save_pdf: Callable[[str, str, Any], Any]
    failure_message: str


def _backend(market: str) -> _Backend:
    """Resolve backend functions at call time so they stay substitutable."""

    if market == "us":
        return _Backend(
            get_cached=get_cached_us_report,
            generate=generate_us_report_response_sync,
            save_markdown=save_us_report,
            save_pdf=save_us_pdf_report,
            failure_message=US_FAILURE_MESSAGE,
        )
    return _Backend(
        get_cached=get_cached_report,
        generate=generate_report_response_sync,
        save_markdown=save_report,
        save_pdf=save_pdf_report,
        failure_message=KR_FAILURE_MESSAGE,
    )


def generate_report(
    ticker: str,
    company_name: str,
    *,
    market: str = "kr",
    cache_only: bool = False,
) -> ReportArtifact:
    """Return a cached report, or generate, persist and return a new one.

    The cache is always consulted first, including when ``cache_only`` is set —
    a cached report is returned even to callers that must not trigger new
    generation. On a cache miss, ``cache_only`` yields :data:`SKIPPED`.

    Raises whatever the underlying generation backend raises; callers own the
    failure policy for their channel.
    """

    backend = _backend(market)

    is_cached, cached_content, cached_file, cached_pdf = backend.get_cached(ticker)
    if is_cached:
        logger.info("Cached report found: %s", cached_file)
        return ReportArtifact(
            status=COMPLETED,
            content=cached_content,
            markdown_path=cached_file,
            pdf_path=cached_pdf,
            cached=True,
        )

    if cache_only:
        return ReportArtifact(status=SKIPPED)

    logger.info("Performing new analysis: %s - %s", ticker, company_name)
    content = backend.generate(ticker, company_name)
    if not content:
        return ReportArtifact(status=FAILED, content=backend.failure_message)

    markdown_path = backend.save_markdown(ticker, company_name, content)
    pdf_path = backend.save_pdf(ticker, company_name, markdown_path)
    return ReportArtifact(
        status=COMPLETED,
        content=content,
        markdown_path=markdown_path,
        pdf_path=pdf_path,
    )
