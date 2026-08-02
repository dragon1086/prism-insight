"""
Daily grounding facts for the bot's Firecrawl commands.

Why this module exists
----------------------
The weekly report grounds its numbers in KRX/yfinance source data
(`weekly_market_facts`), so it can say "KOSPI closed at 3,2xx". Every daily bot
command — /signal, /theme, /ask and the US pair — went out with **zero numeric
grounding**: the model saw search snippets and nothing else, so index levels and
flow figures were paraphrased from whatever article ranked first, or hedged away.

`generate_firecrawl_search_response()` already accepts `grounded_facts` and
`period_label`, and the bot already splats `search_opts` straight into it
(`telegram_ai_bot._run_firecrawl_command`). So the wiring is a dict merge — the
work is in *producing* those facts safely on a live event loop.

Why a cache and an executor, not a plain call
---------------------------------------------
`build_kr_facts()` is synchronous and expensive: it walks the KRX all-ticker
OHLCV table to rank the top 300 names by trading value, plus a Naver flow page
per market. On a one-shot batch (the Sunday report) blocking is harmless. Inside
the bot it is not — the bot is a single-loop asyncio app serving many chats, and
a bare call would freeze *every* user for the duration of that scan.

So:
  * the build runs in a thread executor (loop stays responsive),
  * results are shared per (market, session) — cost does not scale with users,
  * one in-flight build per market (single-flight lock), so ten simultaneous
    /ask calls trigger one KRX scan rather than ten,
  * a hard timeout degrades to *no facts* rather than a hung command. The
    fallback path is exactly today's behaviour, so degradation is safe.

Failure is always soft. Empty dict means "answer without numeric grounding",
which is what the bot did before this module existed.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Facts are stable within a session; this bounds staleness while the market moves.
_TTL_OK = 600.0
# Do not hammer a broken upstream, but recover well before the TTL above.
_TTL_EMPTY = 120.0
# A KRX all-ticker scan is seconds, not tens of seconds. Past this the user is
# better served by an ungrounded answer than by a spinner.
_BUILD_TIMEOUT = 30.0

_SUPPORTED = ("KR", "US")

# market -> (stored_at_monotonic, payload)
_cache: dict[str, tuple[float, dict]] = {}
# market -> the build currently running, if any. A timed-out caller walks away
# but the build keeps going, so later callers must attach to it rather than
# starting a second scan of the same data.
_inflight: dict[str, asyncio.Task] = {}
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


def _fresh(entry: Optional[tuple[float, dict]]) -> bool:
    if entry is None:
        return False
    stored_at, payload = entry
    ttl = _TTL_OK if payload else _TTL_EMPTY
    return (time.monotonic() - stored_at) < ttl


async def _lock_for(market: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _locks.get(market)
        if lock is None:
            lock = _locks[market] = asyncio.Lock()
        return lock


def _build(market: str) -> dict:
    """Blocking fact build. Runs in an executor thread — never call directly."""
    from weekly_market_facts import (
        build_kr_facts,
        build_us_facts,
        resolve_recent_sessions,
        resolve_recent_us_sessions,
    )

    if market == "KR":
        sessions = resolve_recent_sessions()
        if not sessions:
            return {}
        baseline, latest = sessions
        # movers compares close(baseline) -> close(latest); passing the same day
        # twice would print +0.0% for every stock, so the prior session matters.
        #
        # movers is OFF here. Measured on app-server: index 0.1s + investor 0.2s
        # against 63.0s for movers alone — it scans every ticker twice through a
        # KRX client that re-logs-in per call. That is fine for the Sunday batch
        # and hopeless for a chat command; with it on, KR grounding never landed
        # inside the timeout and every /signal answer went out ungrounded.
        facts = build_kr_facts(
            latest, latest, kind="daily", baseline=baseline, include_movers=False
        )
    else:
        sessions = resolve_recent_us_sessions()
        if not sessions:
            return {}
        baseline, latest = sessions
        # 지수 등락률은 전일 종가 대비가 관례다. baseline 없이 부르면 시가 대비
        # (장중 변동)가 나와 기사 수치와 어긋난다.
        facts = build_us_facts(latest, latest, kind="daily", baseline=baseline)

    if not facts:
        return {}
    return {
        "grounded_facts": facts,
        "period_label": f"{latest:%Y-%m-%d} (최근 거래일)",
    }


def _store(market: str, task: asyncio.Task) -> None:
    """
    Land a finished build in the cache.

    This is the only writer, so a build that outlives the caller who started it
    still benefits everyone who asks next — which is the whole point of letting
    a timed-out build keep running.
    """
    _inflight.pop(market, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"[facts] {market} daily facts failed: {exc}", exc_info=exc)
        _cache[market] = (time.monotonic(), {})
        return

    payload = task.result()
    _cache[market] = (time.monotonic(), payload)
    if payload:
        logger.info(
            f"[facts] {market} daily facts ready "
            f"({len(payload['grounded_facts'])} chars, {payload['period_label']})"
        )


def _build_task(market: str) -> asyncio.Task:
    """The one build in flight for this market, started if there isn't one."""
    task = _inflight.get(market)
    if task is not None and not task.done():
        return task
    task = asyncio.create_task(asyncio.to_thread(_build, market))
    task.add_done_callback(functools.partial(_store, market))
    _inflight[market] = task
    return task


async def daily_facts(market: str) -> dict:
    """
    Grounding kwargs for the most recent trading session.

    Merge into the retrieval preset at the call site:

        search_opts=search_preset("KR", tbs="qdr:w") | await daily_facts("KR")

    Returns:
        {"grounded_facts": str, "period_label": str}, or {} when the source data
        is unavailable — callers must treat {} as "proceed without grounding".
    """
    market = market.upper()
    if market not in _SUPPORTED:
        raise ValueError(f"unknown market {market!r}; expected one of {_SUPPORTED}")

    entry = _cache.get(market)
    if _fresh(entry):
        return entry[1]

    lock = await _lock_for(market)
    async with lock:
        # Another coroutine may have filled the slot while we waited.
        entry = _cache.get(market)
        if _fresh(entry):
            return entry[1]
        task = _build_task(market)

    # Awaited outside the lock: a caller that gives up on a slow build must not
    # keep everyone else queued behind it.
    #
    # shield() is the point of this whole shape. wait_for() cancels what it
    # awaits, and cancelling an executor call does NOT stop the thread — the KRX
    # scan runs to completion regardless. Without the shield the task would be
    # cancelled, the next caller would start a *second* scan on top of the first,
    # and a slow upstream would multiply threads instead of sharing one.
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_BUILD_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(
            f"[facts] {market} daily facts timed out after {_BUILD_TIMEOUT}s "
            "— answering without numeric grounding; the build continues and "
            "will populate the cache for later callers"
        )
        return {}
    except Exception as e:  # noqa: BLE001 - fail-soft by design
        logger.error(f"[facts] {market} daily facts failed: {e}", exc_info=True)
        return {}


def reset_cache() -> None:
    """
    Drop cached facts and in-flight locks.

    The locks are cleared too: an asyncio.Lock binds to whichever loop first
    awaits it, so a lock left over from a previous loop (the bot restarts, tests
    call asyncio.run repeatedly) would raise once contended on the new one.
    """
    _cache.clear()
    _locks.clear()
    for task in _inflight.values():
        task.cancel()
    _inflight.clear()
