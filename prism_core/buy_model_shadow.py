"""Capped SHADOW comparison of an alternative BUY-scenario model (no orders).

After the live Codex BUY scenario returns, the same instruction and prompt are
run once more on PRISM_BUY_SHADOW_MODEL in the background. Only a JSONL record
is written (logs/buy_model_shadow.jsonl); the shadow result never reaches the
decision, order, DB or message path. Disabled unless PRISM_BUY_SHADOW_MODEL is
set, and it stops by itself after PRISM_BUY_SHADOW_MAX_RUNS records.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

LEDGER = Path(__file__).resolve().parents[1] / "logs" / "buy_model_shadow.jsonl"
_SCENARIO_KEYS = ("decision", "buy_score", "min_score", "target_price", "stop_loss",
                  "investment_period", "analysis_failed")

_pending: set[asyncio.Task] = set()
_scheduled = 0
_semaphore: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _max_runs() -> int:
    try:
        return max(0, int(os.getenv("PRISM_BUY_SHADOW_MAX_RUNS", "6")))
    except ValueError:
        return 0


def _recorded_runs() -> int:
    try:
        with LEDGER.open(encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _summary(scenario: Any) -> dict | None:
    if not isinstance(scenario, dict):
        return None
    return {k: scenario.get(k) for k in _SCENARIO_KEYS if k in scenario}


def _append(record: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def schedule_buy_model_shadow(
    *,
    market: str,
    ticker: str,
    system_prompt: str,
    user_prompt: str,
    mcp_profile: str,
    live_model: str,
    live_effort: str | None,
    live_result: Any,
    live_scenario: dict | None,
    generate: Callable[..., Awaitable[Any]],
    parse: Callable[[str], dict | None],
    service_tier: Callable[[], str],
) -> bool:
    """Start one background shadow run if enabled and under the cap."""
    global _scheduled, _semaphore
    model = os.getenv("PRISM_BUY_SHADOW_MODEL", "").strip()
    if not model or model == live_model:
        return False
    if _recorded_runs() + _scheduled >= _max_runs():
        return False
    _scheduled += 1
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore[0] is not loop:
        _semaphore = (loop, asyncio.Semaphore(1))  # one shadow run at a time
    semaphore = _semaphore[1]
    effort = os.getenv("PRISM_BUY_SHADOW_EFFORT", "").strip() or None
    timeout = float(os.getenv("PRISM_BUY_SHADOW_TIMEOUT", "300"))
    codex_bin = os.getenv("PRISM_BUY_SHADOW_CODEX_BIN", "").strip() or None

    async def _run() -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "market": market, "ticker": ticker, "tier": service_tier(),
            "live": {"model": live_model, "effort": live_effort,
                     "latency_s": round(getattr(live_result, "latency_s", 0.0), 2),
                     "usage": getattr(live_result, "usage", None),
                     "scenario": _summary(live_scenario)},
        }
        started = time.monotonic()
        try:
            async with semaphore:
                result = await generate(
                    system_prompt=system_prompt, user_prompt=user_prompt,
                    model=model, reasoning_effort=effort, timeout=timeout,
                    mcp_profile=mcp_profile, require_mcp_calls=True,
                    codex_bin=codex_bin,
                )
            scenario = parse(result.text)
            record["shadow"] = {"model": model, "effort": effort,
                                "latency_s": round(result.latency_s, 2),
                                "usage": result.usage,
                                "mcp_calls": len(result.mcp_calls),
                                "scenario": _summary(scenario)}
            live_decision = (record["live"]["scenario"] or {}).get("decision")
            shadow_decision = (record["shadow"]["scenario"] or {}).get("decision")
            record["decision_match"] = (live_decision is not None
                                        and live_decision == shadow_decision)
        except Exception as e:  # noqa: BLE001 - shadow must never affect trading
            record["shadow"] = {"model": model, "effort": effort,
                                "error": type(e).__name__,
                                "elapsed_s": round(time.monotonic() - started, 2)}
        try:
            _append(record)
        except OSError:
            logger.warning("[BUY_SHADOW] could not write ledger", exc_info=True)
        logger.info("[BUY_SHADOW] %s %s model=%s match=%s error=%s", market, ticker, model,
                    record.get("decision_match"), record["shadow"].get("error", "-"))

    task = loop.create_task(_run())
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return True


async def drain_buy_model_shadows(timeout: float = 420.0) -> None:
    """Wait for in-flight shadow runs before the batch exits; cancel stragglers."""
    if not _pending:
        return
    done, still = await asyncio.wait(set(_pending), timeout=timeout)
    for task in still:
        task.cancel()
    if still:
        logger.warning("[BUY_SHADOW] cancelled %d unfinished shadow run(s)", len(still))
