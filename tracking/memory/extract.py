"""
Async fact extraction from raw memories using Claude Haiku 4.5.

Fire-and-forget pattern:
  - `run_extraction_after_save(memory_id, db_path, anthropic_client)`
    asyncio.create_task -friendly. Pulls memory, asks Haiku for facts, writes
    them to user_facts, runs supersede check, sets `fact_extracted=1`.

Robustness:
  - Hard 10s timeout via `asyncio.wait_for`.
  - Module-level circuit breaker (skip if last 5 calls in past hour failed).
  - All errors logged and swallowed; never raises out of the background task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

from tracking.memory import facts as facts_mod
from tracking.memory import schema
from tracking.memory.embed import get_default_provider, to_blob

logger = logging.getLogger(__name__)


HAIKU_MODEL = "claude-haiku-4-5-20251001"
EXTRACTION_TIMEOUT = 10.0
BREAKER_WINDOW_SEC = 3600
BREAKER_THRESHOLD = 5


@dataclass
class Fact:
    fact: str
    category: str
    confidence: float


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #
_FAILURES: Deque[float] = deque(maxlen=20)
_FAILURES_LOCK = threading.Lock()


def _breaker_open() -> bool:
    with _FAILURES_LOCK:
        now = time.time()
        cutoff = now - BREAKER_WINDOW_SEC
        while _FAILURES and _FAILURES[0] < cutoff:
            _FAILURES.popleft()
        return len(_FAILURES) >= BREAKER_THRESHOLD


def _record_failure() -> None:
    with _FAILURES_LOCK:
        _FAILURES.append(time.time())


def reset_breaker() -> None:
    with _FAILURES_LOCK:
        _FAILURES.clear()


# --------------------------------------------------------------------------- #
# Tool schema
# --------------------------------------------------------------------------- #
_TOOL_NAME = "record_user_facts"
_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Record distilled user facts (style/risk/holdings/aversion/goal/event) "
        "extracted from a single trading-journal memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": list(facts_mod.CATEGORIES),
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                    "required": ["fact", "category", "confidence"],
                },
            }
        },
        "required": ["facts"],
    },
}


_SYSTEM_PROMPT = (
    "You distill investor-style facts from a single Korean trading-journal entry. "
    "Return only the structured tool call. Categories: "
    "style=trading style, risk=risk preference, holdings=holdings/positions, "
    "aversion=biases/dislikes, goal=stated goals, event=specific events. "
    "Keep each fact concrete, ≤80 chars, in Korean."
)


def _build_user_message(memory: Dict[str, Any]) -> str:
    content = memory.get("content") or {}
    text = content.get("text") or content.get("response_summary") or ""
    ticker = memory.get("ticker") or ""
    ticker_name = memory.get("ticker_name") or ""
    return (
        f"종목: {ticker_name}({ticker})\n"
        f"내용: {text}\n"
        "→ 위 기록에서 사용자 특성/사실을 추출하세요. "
        "추출할 내용이 없으면 빈 배열을 반환합니다."
    )


def _extract_tool_facts(response) -> List[Fact]:
    """Parse Anthropic tool-use response into a list[Fact]."""
    out: List[Fact] = []
    blocks = getattr(response, "content", None) or []
    for block in blocks:
        b_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if b_type != "tool_use":
            continue
        inp = getattr(block, "input", None) or (
            block.get("input") if isinstance(block, dict) else None
        ) or {}
        for raw in inp.get("facts", []) or []:
            try:
                f = Fact(
                    fact=str(raw["fact"]).strip(),
                    category=str(raw["category"]).strip(),
                    confidence=float(raw.get("confidence", 0.5)),
                )
                if f.fact:
                    out.append(f)
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------- #
# Public coroutines
# --------------------------------------------------------------------------- #
async def extract_facts_from_memory(
    memory_id: int,
    db_path: str,
    anthropic_client,
) -> List[Fact]:
    """Call Haiku to extract facts from a single memory. Returns [] on failure."""
    if anthropic_client is None:
        return []
    if _breaker_open():
        logger.warning("memory.extract.skipped reason=breaker_open memory_id=%d", memory_id)
        return []

    # Fetch the memory row.
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT content, ticker, ticker_name FROM user_memories WHERE id = ?",
                (memory_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("memory.extract.db_read_failed memory_id=%d err=%s", memory_id, e)
        return []
    if row is None:
        return []

    content_json, ticker, ticker_name = row
    try:
        content = json.loads(content_json) if content_json else {}
    except Exception:
        content = {"text": str(content_json or "")}
    memory = {
        "content": content,
        "ticker": ticker or "",
        "ticker_name": ticker_name or "",
    }
    user_msg = _build_user_message(memory)

    async def _call():
        # Anthropic SDK >= 0.50 exposes async client; if a sync client was passed,
        # run in default executor to avoid blocking the event loop.
        msgs_attr = getattr(anthropic_client, "messages", None)
        if msgs_attr is None:
            return None
        create = msgs_attr.create
        kwargs = dict(
            model=HAIKU_MODEL,
            max_tokens=400,
            system=_SYSTEM_PROMPT,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_msg}],
        )
        if asyncio.iscoroutinefunction(create):
            return await create(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: create(**kwargs))

    try:
        resp = await asyncio.wait_for(_call(), timeout=EXTRACTION_TIMEOUT)
    except asyncio.TimeoutError:
        _record_failure()
        logger.warning("memory.extract.timeout memory_id=%d", memory_id)
        return []
    except Exception as e:
        _record_failure()
        logger.warning("memory.extract.failed memory_id=%d err=%s", memory_id, e)
        return []

    if resp is None:
        return []
    return _extract_tool_facts(resp)


def _persist_fact_with_supersede(
    db_path: str,
    user_id: int,
    memory_id: int,
    fact: Fact,
    anthropic_client=None,
) -> Optional[int]:
    """Embed fact, persist, then run cosine-similarity supersede check (sync helper)."""
    try:
        provider = get_default_provider()
        emb = provider.embed(fact.fact)
        emb_model = provider.name
    except Exception as e:
        logger.warning("memory.extract.embed_failed err=%s", e)
        emb = None
        emb_model = None

    conn = sqlite3.connect(db_path)
    try:
        # Cosine-based candidate detection.
        candidates = facts_mod.find_similar(
            conn, user_id=user_id, category=fact.category, embedding=emb, threshold=0.85
        )

        new_id = facts_mod.save_fact(
            conn,
            user_id=user_id,
            fact=fact.fact,
            category=fact.category,
            confidence=fact.confidence,
            evidence_memory_ids=[memory_id],
            embedding=emb,
            embedding_model=emb_model,
        )
        for c in candidates:
            try:
                facts_mod.supersede(conn, old_id=int(c["id"]), new_id=new_id)
            except Exception as e:
                logger.warning("memory.extract.supersede_failed err=%s", e)
        return new_id
    except Exception as e:
        logger.warning("memory.extract.persist_failed err=%s", e)
        return None
    finally:
        conn.close()


async def run_extraction_after_save(
    memory_id: int,
    db_path: str,
    anthropic_client,
    user_id: Optional[int] = None,
) -> int:
    """
    Top-level async task: extract → persist → mark fact_extracted=1.
    Never raises. Returns count of facts saved.
    """
    if anthropic_client is None:
        return 0

    # Get user_id when not supplied.
    if user_id is None:
        try:
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.execute(
                    "SELECT user_id FROM user_memories WHERE id = ?", (memory_id,)
                )
                row = cur.fetchone()
                user_id = int(row[0]) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("memory.extract.user_lookup_failed err=%s", e)
            return 0
    if user_id is None:
        return 0

    try:
        extracted = await extract_facts_from_memory(memory_id, db_path, anthropic_client)
    except Exception as e:
        logger.warning("memory.extract.unhandled err=%s", e)
        return 0

    saved = 0
    for f in extracted:
        try:
            new_id = _persist_fact_with_supersede(
                db_path, int(user_id), memory_id, f, anthropic_client
            )
            if new_id:
                saved += 1
        except Exception as e:
            logger.warning("memory.extract.save_loop_failed err=%s", e)

    # Mark memory as processed regardless of whether facts were emitted.
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE user_memories SET fact_extracted = 1 WHERE id = ?",
                (memory_id,),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("memory.extract.flag_failed err=%s", e)

    return saved
