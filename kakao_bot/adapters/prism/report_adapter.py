"""Generate reports through Prism's channel-neutral report service.

The second and last place Kakao code imports Prism (design §3.2). Kakao gets
an :class:`AnalysisOutcome` back and never sees Prism's own types, its queue,
or its file layout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from functools import lru_cache

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
    "전체 답변에서 가장 직접적인 최신 기사 최대 3개만 근거로 사용하고, 각 사실 뒤에는 "
    "근거가 된 검색 결과 번호를 [자료 n] 형식으로 표시하라. 이 번호는 전송 전에 숨겨진다. "
    "현재가·등락률·실적·목표주가처럼 투자 판단에 영향을 주는 숫자는 검증된 시장 데이터가 "
    "있거나 서로 독립된 최신 기사에서 같은 값이 확인될 때만 쓰고, 아니면 생략하라. "
    "검증된 시장 데이터 블록이 없으면 정확한 현재가·장중 고저가·당일 등락률은 절대 쓰지 마라."
)

_INTRADAY_GROUNDING = (
    "\n\n오늘 장중 움직임의 원인은 오늘자 직접 관련 기사로 확인된 내용만 단정하라. "
    "과거 유사 사례로 오늘 원인을 단정하지 마라. 직접 보도가 부족하면 검증된 일봉 형태와 "
    "가능한 요인을 구분하고, 확인되지 않은 원인은 명확히 미확정이라고 밝혀라."
)

_FORECAST_GROUNDING = (
    "\n\n미래 주가를 확정적으로 예언하지 마라. 최신 증권사 목표주가와 실적·밸류에이션 "
    "근거가 확인되면 낙관·기준·비관 시나리오의 조건과 범위로 답하고, 확인되지 않은 "
    "숫자는 만들지 마라."
)

_STOCK_RESEARCH_SUFFIX = re.compile(
    r"\s*(?:지금\s*)?"
    r"(?:사도\s*(?:될까|돼)|살까|매수(?:해도)?\s*(?:될까|괜찮을까)|"
    r"(?:최근\s*)?(?:주가\s*)?전망(?:은|이|을)?"
    r"(?:\s*(?:알려\s*줘|말해\s*줘|분석해\s*줘|어때))?|어때)"
    r"[?？!！.\s]*$",
    re.IGNORECASE,
)

_INTRADAY_EVENT_QUESTION = re.compile(
    r"(?=.*(?:오늘|장중|일봉|급락|급등|하한가|상한가))"
    r"(?=.*(?:왜|이유|원인|무슨\s*일)).*",
    re.IGNORECASE,
)

_STOCK_FORECAST_QUESTION = re.compile(
    r"(?:얼마|어디)까지|목표\s*(?:주)?가|주가\s*예측|(?:오를|내릴)지",
    re.IGNORECASE,
)

_STOCK_ALIASES = {
    "하이닉스": "SK하이닉스",
    "삼전": "삼성전자",
}


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
    subject, is_intraday = _stock_question(question)
    prompt = (
        f"오늘은 {today}입니다. 다음 투자 관련 질문에 대해 최신 정보를 기반으로 답변해줘:\n\n"
        f"{question}\n\n"
        f"한국어로, 카카오톡 메시지 형태로 이모지 포함하여 작성. "
        f"{_ASK_ANSWER_BUDGET}자 이내. 표와 마크다운 문법은 쓰지 마라."
    ) + _ASK_GROUNDING + (_INTRADAY_GROUNDING if is_intraday else "")
    if _STOCK_FORECAST_QUESTION.search(question):
        prompt += _FORECAST_GROUNDING

    queries, use_primary_sources = _ask_search_plan(question)
    # General free-form questions stay unrestricted because their subject is
    # unpredictable. A detected stock question is different: primary business
    # outlets are much safer than social posts and generic "지금 사도 될까"
    # matches, and the expanded queries carry the actual research dimensions.
    market_facts = await daily_facts("KR")
    search_opts = search_preset(
        "KR",
        tbs="qdr:d" if use_primary_sources else "qdr:m",
        allowlist=use_primary_sources,
    ) | market_facts
    if subject:
        stock_facts = await _stock_session_facts(subject)
        if stock_facts:
            existing = search_opts.get("grounded_facts", "").strip()
            search_opts["grounded_facts"] = "\n\n".join(
                part for part in (existing, stock_facts) if part
            )
            if is_intraday:
                search_opts["period_label"] = (
                    f"{datetime.now():%Y-%m-%d} 당일 장중"  # noqa: DTZ005
                )
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

    subject, is_intraday = _stock_question(question)
    if not subject:
        return [question], False

    if is_intraday:
        return (
            [
                f"{subject} 오늘 장중 급락 반등 이유",
                f"{subject} 오늘 주가 하락 수급 차익실현 매도 원인",
            ],
            True,
        )

    return (
        [
            f"{subject} 오늘 최신 뉴스 주가 실적 전망",
            f"{subject} 증권사 목표주가 실적 공시 외국인 기관 수급",
        ],
        True,
    )


def _stock_question(question: str) -> tuple[str | None, bool]:
    """Return a validated stock subject and whether the question is intraday."""

    text = question.strip()
    event = _INTRADAY_EVENT_QUESTION.search(text)
    if event:
        subject = _find_kr_stock_mention(text)
        if subject:
            return subject, True

    if _STOCK_FORECAST_QUESTION.search(text):
        subject = _find_kr_stock_mention(text)
        if subject:
            return subject, False

    match = _STOCK_RESEARCH_SUFFIX.search(text)
    if not match:
        return None, False
    prefix = text[: match.start()].strip()
    subject = _find_kr_stock_mention(prefix)
    if not subject or len(subject) > 40:
        return None, False
    return subject, False


@lru_cache(maxsize=8)
def _load_kr_stock_maps(path: str) -> tuple[dict[str, str], dict[str, str]]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Stock map unavailable for ask detection: %s", exc)
        return {}, {}
    return (
        payload.get("name_to_code", {}) or {},
        payload.get("code_to_name", {}) or {},
    )


def _stock_map_path() -> str:
    return os.getenv("KAKAO_STOCK_MAP_PATH", "stock_map.json")


@lru_cache(maxsize=256)
def _resolve_kr_stock(subject: str) -> tuple[str, str] | None:
    """Resolve only exact names, explicit aliases, or six-digit codes."""

    name_to_code, code_to_name = _load_kr_stock_maps(_stock_map_path())
    candidate = _STOCK_ALIASES.get(subject.strip(), subject.strip())
    if re.fullmatch(r"\d{6}", candidate):
        name = code_to_name.get(candidate)
        return (candidate, name) if name else None
    code = name_to_code.get(candidate)
    return (code, candidate) if code else None


@lru_cache(maxsize=8)
def _searchable_stock_names(path: str) -> tuple[tuple[str, str], ...]:
    name_to_code, _ = _load_kr_stock_maps(path)
    return tuple(
        sorted(
            ((name.casefold(), name) for name in name_to_code if len(name) >= 2),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
    )


def _find_kr_stock_mention(text: str) -> str | None:
    """Find a real stock name anywhere in a conversational question."""

    folded = text.casefold()
    for alias, canonical in sorted(
        _STOCK_ALIASES.items(), key=lambda pair: len(pair[0]), reverse=True
    ):
        if alias.casefold() in folded:
            return canonical

    for folded_name, canonical in _searchable_stock_names(_stock_map_path()):
        if folded_name in folded:
            return canonical
    return None


def _format_stock_session_facts(
    company_name: str,
    ticker: str,
    current: dict,
    previous: dict,
    *,
    observed_at: str,
) -> str:
    """Render authoritative intraday OHLCV without asking the model to calculate."""

    previous_close = int(previous["Close"])
    close = int(current["Close"])
    low = int(current["Low"])
    change_pct = (close / previous_close - 1) * 100
    rebound_pct = (close / low - 1) * 100
    return (
        "[검증된 종목 당일 일봉 데이터]\n"
        f"종목: {company_name} ({ticker})\n"
        f"조회 시각: {observed_at}\n"
        f"전일 종가: {previous_close:,}원\n"
        f"시가: {int(current['Open']):,}원\n"
        f"장중 고가: {int(current['High']):,}원\n"
        f"장중 저가: {low:,}원\n"
        f"현재가: {close:,}원\n"
        f"전일 대비: {change_pct:+.2f}%\n"
        f"저가 대비 반등: {rebound_pct:+.2f}%\n"
        f"거래량: {int(current['Volume']):,}주\n"
        "출처: 네이버 금융 일봉 데이터\n"
        "주의: 당일 장중 값이며 정규장 종가가 아닙니다."
    )


async def _stock_session_facts(subject: str) -> str:
    """Fetch one stock's current daily candle through the existing safe fallback."""

    resolved = _resolve_kr_stock(subject)
    if resolved is None:
        return ""
    ticker, company_name = resolved

    def fetch() -> str:
        import requests

        from cores.naver_market_snapshot import _fetch_daily_pair

        trade_date = datetime.now().strftime("%Y%m%d")  # noqa: DTZ005
        current, previous = _fetch_daily_pair(
            requests.get,
            ticker,
            trade_date,
            timeout=10,
            max_attempts=2,
            retry_wait_sec=0.2,
        )
        return _format_stock_session_facts(
            company_name,
            ticker,
            current,
            previous,
            observed_at=datetime.now().strftime("%Y-%m-%d %H:%M KST"),  # noqa: DTZ005
        )

    try:
        return await asyncio.wait_for(asyncio.to_thread(fetch), timeout=25)
    except Exception as exc:  # noqa: BLE001 - retrieval fails soft
        logger.warning("Intraday facts unavailable for %s: %s", subject, exc)
        return ""
