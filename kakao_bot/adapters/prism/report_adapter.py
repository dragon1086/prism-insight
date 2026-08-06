"""Generate reports through Prism's channel-neutral report service.

The second and last place Kakao code imports Prism (design §3.2). Kakao gets
an :class:`AnalysisOutcome` back and never sees Prism's own types, its queue,
or its file layout.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime

from kakao_bot.ports.analysis import AnalysisOutcome
from prism_core.report_service import generate_report

logger = logging.getLogger(__name__)

GENERATION_FAILED = "generation_failed"
GENERATION_ERROR = "generation_error"
ASK_EMPTY = "ask_empty"
ASK_ERROR = "ask_error"
ASK_TIMEOUT = "ask_timeout"
EVALUATE_EMPTY = "evaluate_empty"
EVALUATE_ERROR = "evaluate_error"
EVALUATE_TIMEOUT = "evaluate_timeout"

# `generate_evaluation_response` swallows its own exceptions and returns this
# apology as ordinary text. Delivered as-is it would look like a considered
# answer and would never be retried, so it is recognised and turned back into a
# failure. Coupled to a message on purpose — the alternative is shipping "죄송
#합니다" into a chat room as the final word on someone's position.
_EVALUATION_FAILURE_MARK = "평가 중 오류가 발생했습니다"

# Telegram asks for these; a group chat cannot afford the extra round trips, so
# the command carries three arguments and these stand in for the rest.
DEFAULT_TONE = "친근한 투자 동료처럼, 근거는 짚어주되 길지 않게"
DEFAULT_BACKGROUND = ""

# Telegram's /ask allows 240s for the same work.
_ASK_TIMEOUT = 240
# Evaluation runs several MCP tool calls (price series, investor flows, one
# search) before it writes, so it needs more room than a single search does.
_EVALUATE_TIMEOUT = 420
_DEFAULT_PERIOD_MONTHS = 6

# Telegram's /ask answers in 3000 characters; Kakao's bubble is far smaller and
# the renderer condenses anyway, but asking for less up front keeps the model
# from padding.
_ASK_ANSWER_BUDGET = 1_200

_ASK_GROUNDING = (
    "\n\n반드시 웹을 검색하고 실제 뉴스/기사 페이지를 스크랩하여 각 주장에 출처를 밝혀라. "
    "너의 사전지식이 아니라 오늘 기준 최신 웹 데이터에 근거하라. "
    "질문 대상과 직접 관련된 기사가 하나라도 있으면 '최신 근거가 없다'고 답하지 마라. "
    "관련 핵심 사실을 우선 제시하고, 가능하면 최소 2개 사실에 매체명과 발행일을 붙여라. "
    "각 사실 뒤에는 근거가 된 검색 결과 번호를 [자료 n] 형식으로 표시하라. "
    "현재가·등락률·실적·목표주가처럼 투자 판단에 영향을 주는 숫자는 검증된 시장 데이터가 "
    "있거나 서로 독립된 최신 기사에서 같은 값이 확인될 때만 쓰고, 아니면 생략하라. "
    "검증된 시장 데이터 블록이 없으면 정확한 현재가·장중 고저가·당일 등락률은 절대 쓰지 마라."
)

_STOCK_RESEARCH_SUFFIX = re.compile(
    r"\s*(?:지금\s*)?"
    r"(?:사도\s*(?:될까|돼)|살까|매수(?:해도)?\s*(?:될까|괜찮을까)|"
    r"(?:최근\s*)?(?:주가\s*)?전망(?:은|이|을)?"
    r"(?:\s*(?:알려\s*줘|말해\s*줘|분석해\s*줘|어때))?|어때)"
    r"[?？!！.\s]*$",
    re.IGNORECASE,
)


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
        except Exception as exc:  # worker records the failure and moves on
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

    def answer(self, question: str) -> AnalysisOutcome:
        """Answer a free-form question, the same way Telegram's /ask does."""

        try:
            future = asyncio.run_coroutine_threadsafe(_ask(question), _ask_loop())
            text = future.result(timeout=_ASK_TIMEOUT)
        except FuturesTimeoutError:
            logger.warning("Ask timed out after %ss: %r", _ASK_TIMEOUT, question[:80])
            return AnalysisOutcome(succeeded=False, error_code=ASK_TIMEOUT)
        except Exception as exc:  # worker records the failure and moves on
            logger.exception("Ask failed for %r", question[:80])
            return AnalysisOutcome(
                succeeded=False,
                error_code=ASK_ERROR,
                summary=str(exc)[:500],
            )

        if not text or not text.strip():
            return AnalysisOutcome(succeeded=False, error_code=ASK_EMPTY)
        return AnalysisOutcome(succeeded=True, summary=text)

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
        """Evaluate a holding through Prism's own evaluation agent.

        Not the ask path. That one reads the web; this one has to state a
        return against `avg_price`, which needs the real price series and
        investor flows. Routing it through search would invite a plausible
        invented number, and a wrong number about someone's own money is the
        most expensive mistake this bot can make.
        """

        try:
            future = asyncio.run_coroutine_threadsafe(
                _evaluate(
                    ticker,
                    company_name,
                    market=market,
                    avg_price=avg_price,
                    period_months=period_months,
                    tone=tone or DEFAULT_TONE,
                    background=background or DEFAULT_BACKGROUND,
                ),
                _ask_loop(),
            )
            text = future.result(timeout=_EVALUATE_TIMEOUT)
        except FuturesTimeoutError:
            logger.warning("Evaluation timed out for %s", ticker)
            return AnalysisOutcome(succeeded=False, error_code=EVALUATE_TIMEOUT)
        except Exception as exc:  # worker records the failure and moves on
            logger.exception("Evaluation failed for %s", ticker)
            return AnalysisOutcome(
                succeeded=False,
                error_code=EVALUATE_ERROR,
                summary=str(exc)[:500],
            )

        if not text or not text.strip():
            return AnalysisOutcome(succeeded=False, error_code=EVALUATE_EMPTY)
        if _EVALUATION_FAILURE_MARK in text:
            return AnalysisOutcome(succeeded=False, error_code=EVALUATE_ERROR)
        return AnalysisOutcome(succeeded=True, summary=text)


_loop_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def _ask_loop() -> asyncio.AbstractEventLoop:
    """A long-lived event loop of our own, running in its own thread.

    `asyncio.run()` is deliberately not used here. `daily_facts` pulls in
    `krx_data_client`, which calls `nest_asyncio.apply()` at import time, and
    that patch makes `asyncio.run`'s teardown raise "Timeout should be used
    inside a task" out of `shutdown_default_executor` — turning a perfectly
    good answer into a failed job.

    Submitting to an already-running loop reproduces the shape Telegram's
    `/ask` has been running in production all along: the coroutine executes as
    a genuine Task on a live loop, and the broken teardown path is never
    entered. The loop is created once and reused; the worker is long-lived.
    """

    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(
                target=_loop.run_forever,
                name="kakao-ask-loop",
                daemon=True,
            ).start()
        return _loop


async def _evaluate(
    ticker: str,
    company_name: str,
    *,
    market: str,
    avg_price: float,
    period_months: int | None,
    tone: str,
    background: str,
) -> str | None:
    """One evaluation. KR and US have separate agents with the same shape.

    Imported inside the coroutine for the same reason as `_ask`: these modules
    drag in the whole Prism data stack, and a worker that only ever handles
    reports should not pay for it.
    """

    from report_generator import (
        generate_evaluation_response,
        generate_us_evaluation_response,
    )

    responder = (
        generate_us_evaluation_response
        if market == "us"
        else generate_evaluation_response
    )
    return await responder(
        ticker,
        company_name,
        avg_price,
        # The agent reads this as months and the prompt branches on it (short
        # holdings get technical framing, long ones fundamentals), so a missing
        # period becomes the neutral middle rather than zero.
        period_months if period_months is not None else _DEFAULT_PERIOD_MONTHS,
        tone,
        background,
    )


async def _ask(question: str) -> str | None:
    """Retrieval + analysis for one question.

    Imported inside the coroutine because these modules pull in the whole Prism
    data stack; the Kakao worker should not pay for that unless an ask job
    actually arrives.
    """

    from cores.market_facts_cache import daily_facts
    from cores.search_presets import search_preset
    from report_generator import generate_firecrawl_search_response

    # Local time on purpose: this is the date the user and the market are
    # living in, not a timestamp to be compared across zones.
    today = datetime.now().strftime("%Y년 %m월 %d일")  # noqa: DTZ005
    prompt = (
        f"오늘은 {today}입니다. 다음 투자 관련 질문에 대해 최신 정보를 기반으로 답변해줘:\n\n"
        f"{question}\n\n"
        f"한국어로, 카카오톡 메시지 형태로 이모지 포함하여 작성. "
        f"{_ASK_ANSWER_BUDGET}자 이내. 표와 마크다운 문법은 쓰지 마라."
    ) + _ASK_GROUNDING

    queries, use_primary_sources = _ask_search_plan(question)
    # General free-form questions stay unrestricted because their subject is
    # unpredictable. A detected stock question is different: primary business
    # outlets are much safer than social posts and generic "지금 사도 될까"
    # matches, and the expanded queries carry the actual research dimensions.
    search_opts = search_preset(
        "KR",
        tbs="qdr:d" if use_primary_sources else "qdr:m",
        allowlist=use_primary_sources,
    ) | await daily_facts("KR")
    if use_primary_sources:
        # Firecrawl's news vertical currently returns fresh but weakly related
        # results for Korean stock queries. The web vertical finds the actual
        # article pages and respects the primary-outlet allowlist; the shared
        # temporal gate widens to a week/month only when today is genuinely quiet.
        search_opts["sources"] = ["web"]
        search_opts["include_source_links"] = True
    return await generate_firecrawl_search_response(queries, prompt, **search_opts)


def _ask_search_plan(question: str) -> tuple[list[str], bool]:
    """Turn a conversational stock question into evidence-oriented queries."""

    match = _STOCK_RESEARCH_SUFFIX.search(question.strip())
    if not match:
        return [question], False

    subject = question[: match.start()].strip()
    if not subject or len(subject) > 40:
        return [question], False

    return (
        [
            f"{subject} 오늘 최신 뉴스 주가 실적 전망",
            f"{subject} 증권사 목표주가 실적 공시 외국인 기관 수급",
        ],
        True,
    )
