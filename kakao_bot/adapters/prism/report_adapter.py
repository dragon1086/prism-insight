"""Generate reports through Prism's channel-neutral report service.

The second and last place Kakao code imports Prism (design §3.2). Kakao gets
an :class:`AnalysisOutcome` back and never sees Prism's own types, its queue,
or its file layout.
"""

from __future__ import annotations

import logging

from kakao_bot.ports.analysis import AnalysisOutcome
from prism_core.report_service import generate_report

logger = logging.getLogger(__name__)

GENERATION_FAILED = "generation_failed"
GENERATION_ERROR = "generation_error"


class PrismReportAdapter:
    """Adapter implementing :class:`AnalysisPort`."""

    def generate(
        self,
        ticker: str,
        company_name: str,
        *,
        market: str,
    ) -> AnalysisOutcome:
        try:
            artifact = generate_report(ticker, company_name, market=market)
        except Exception as exc:  # noqa: BLE001 - worker records and moves on
            logger.exception("Report generation raised for %s", ticker)
            return AnalysisOutcome(
                succeeded=False,
                error_code=GENERATION_ERROR,
                summary=str(exc)[:500],
            )

        if not artifact.succeeded:
            return AnalysisOutcome(
                succeeded=False,
                error_code=GENERATION_FAILED,
                summary=artifact.content,
            )

        return AnalysisOutcome(
            succeeded=True,
            summary=artifact.content,
            pdf_path=str(artifact.pdf_path) if artifact.pdf_path else None,
        )
