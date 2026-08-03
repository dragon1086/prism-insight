"""Generate reports through Prism's channel-neutral report service.

The second and last place Kakao code imports Prism (design §3.2). Kakao gets
an :class:`AnalysisOutcome` back and never sees Prism's own types, its queue,
or its file layout.
"""

from __future__ import annotations

import asyncio
import logging
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

# Telegram's /ask allows 240s for the same work.
_ASK_TIMEOUT = 240

# Telegram's /ask answers in 3000 characters; Kakao's bubble is far smaller and
# the renderer condenses anyway, but asking for less up front keeps the model
# from padding.
_ASK_ANSWER_BUDGET = 1_200

_ASK_GROUNDING = (
    "\n\n반드시 웹을 검색하고 실제 뉴스/기사 페이지를 스크랩하여 각 주장에 출처를 밝혀라. "
    "너의 사전지식이 아니라 오늘 기준 최신 웹 데이터에 근거하라."
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

    def answer(self, question: str) -> AnalysisOutcome:
        """Answer a free-form question, the same way Telegram's /ask does."""

        try:
            future = asyncio.run_coroutine_threadsafe(_ask(question), _ask_loop())
            text = future.result(timeout=_ASK_TIMEOUT)
        except FuturesTimeoutError:
            logger.warning("Ask timed out after %ss: %r", _ASK_TIMEOUT, question[:80])
            return AnalysisOutcome(succeeded=False, error_code=ASK_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - worker records and moves on
            logger.exception("Ask failed for %r", question[:80])
            return AnalysisOutcome(
                succeeded=False,
                error_code=ASK_ERROR,
                summary=str(exc)[:500],
            )

        if not text or not text.strip():
            return AnalysisOutcome(succeeded=False, error_code=ASK_EMPTY)
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


async def _ask(question: str) -> str | None:
    """Retrieval + analysis for one question.

    Imported inside the coroutine because these modules pull in the whole Prism
    data stack; the Kakao worker should not pay for that unless an ask job
    actually arrives.
    """

    from cores.market_facts_cache import daily_facts
    from cores.search_presets import search_preset
    from report_generator import generate_firecrawl_search_response

    today = datetime.now().strftime("%Y년 %m월 %d일")
    prompt = (
        f"오늘은 {today}입니다. 다음 투자 관련 질문에 대해 최신 정보를 기반으로 답변해줘:\n\n"
        f"{question}\n\n"
        f"한국어로, 카카오톡 메시지 형태로 이모지 포함하여 작성. "
        f"{_ASK_ANSWER_BUDGET}자 이내. 표와 마크다운 문법은 쓰지 마라."
    ) + _ASK_GROUNDING

    # Free-form: the subject is unpredictable, so a KR-press allowlist would
    # drop legitimate sources. Keep recency and the news channel, drop the list.
    search_opts = search_preset("KR", tbs="qdr:m", allowlist=False) | await daily_facts("KR")
    return await generate_firecrawl_search_response(question, prompt, **search_opts)
