"""Ports the Kakao application uses to reach Prism analysis capabilities.

Dependency direction is one-way (design §3.2): nothing under
`kakao_bot/domain` or `kakao_bot/application` may import Prism modules. These
protocols are the seam — adapters in `kakao_bot/adapters/prism` implement them
and are the only Kakao code that knows Prism exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class ResolvedTicker:
    """A ticker that has been confirmed to exist."""

    ticker: str
    company_name: str
    market: str


@dataclass(frozen=True)
class TickerResolution:
    """Either a resolved ticker, or a message explaining why it failed.

    The failure message is user-facing text produced by the resolver, which
    already knows how to phrase "여러 종목이 검색되었습니다" and friends.
    """

    ticker: ResolvedTicker | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.ticker is not None


@dataclass(frozen=True)
class AnalysisOutcome:
    """One completed (or failed) report generation."""

    succeeded: bool
    summary: str | None = None
    pdf_path: str | None = None
    error_code: str | None = None
    metadata: Mapping[str, object] | None = None


class TickerResolverPort(Protocol):
    def resolve(self, query: str, *, market: str | None) -> TickerResolution:
        """Turn user input into a confirmed ticker, or explain the failure."""


class AnalysisPort(Protocol):
    def generate(
        self,
        ticker: str,
        company_name: str,
        *,
        market: str,
    ) -> AnalysisOutcome:
        """Produce a report. Blocking; callers run this off the event loop."""

    def answer(self, question: str) -> AnalysisOutcome:
        """Answer a free-form question from current web sources.

        There is no ticker and no PDF — the answer arrives whole in
        ``summary``. Blocking, like :meth:`generate`.
        """

    def evaluate(
        self,
        ticker: str,
        company_name: str,
        *,
        market: str,
        avg_price: float,
        period_months: int | None,
        tone: str,
        background: str,
    ) -> AnalysisOutcome:
        """Judge a holding against its entry price.

        Distinct from :meth:`answer` on purpose. This one has to state a
        return, so it needs the actual price series and flow data rather than
        whatever the web happens to say — a number invented from search results
        would be worse than no answer. No PDF; the verdict arrives in
        ``summary``. Blocking, like the rest.
        """
