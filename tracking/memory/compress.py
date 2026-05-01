"""
Haiku-powered Layer 1→2 and 2→3 compression with truncate fallback.

The cron must NEVER raise. On Haiku failure or timeout we fall back to the
deterministic V1-style truncation (`text[:150]`).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
COMPRESS_TIMEOUT = 15.0


def _truncate_summary(content: Dict[str, Any], ticker: Optional[str]) -> str:
    text = (content or {}).get("text") or (content or {}).get("response_summary") or ""
    text = str(text)[:150].replace("\n", " ").strip()
    return f"{ticker + ': ' if ticker else ''}{text}"


def _truncate_compressed(summary: Optional[str], ticker: Optional[str]) -> str:
    if not summary:
        return ""
    return f"{ticker + ' ' if ticker else ''}{summary[:50].replace(chr(10), ' ').strip()}"


async def _haiku_summarize_batch(anthropic_client, items: List[Dict[str, Any]]) -> List[str]:
    """
    Ask Haiku to summarize a batch of memory rows. Returns list of strings, same length
    and order as ``items``. On any failure returns an empty list (caller falls back).
    """
    if anthropic_client is None or not items:
        return []
    msgs_attr = getattr(anthropic_client, "messages", None)
    if msgs_attr is None:
        return []

    payload = {
        "items": [
            {
                "id": it.get("id"),
                "ticker": it.get("ticker") or "",
                "ticker_name": it.get("ticker_name") or "",
                "text": ((it.get("content") or {}).get("text")
                         or (it.get("content") or {}).get("response_summary")
                         or "")[:600],
            }
            for it in items
        ]
    }
    user_msg = (
        "다음 메모리 항목들을 각각 한 줄(≤ 150자)로 요약하세요. "
        "JSON 배열만 출력하세요: "
        '[{"id": <id>, "summary": "..."}].\n\n'
        + json.dumps(payload, ensure_ascii=False)
    )

    async def _call():
        kwargs = dict(
            model=HAIKU_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": user_msg}],
        )
        create = msgs_attr.create
        if inspect.iscoroutinefunction(create):
            return await create(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: create(**kwargs))

    try:
        resp = await asyncio.wait_for(_call(), timeout=COMPRESS_TIMEOUT)
    except Exception as e:
        logger.warning("memory.compress.haiku_failed err=%s", e)
        return []

    text_blocks: List[str] = []
    for block in getattr(resp, "content", None) or []:
        b_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if b_type == "text":
            t = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else ""
            )
            if t:
                text_blocks.append(t)
    raw = "\n".join(text_blocks).strip()
    if not raw:
        return []

    # Best-effort JSON parse.
    try:
        # Allow leading/trailing prose; find first '['..']' span.
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end >= 0 and end > start:
            arr = json.loads(raw[start : end + 1])
        else:
            arr = json.loads(raw)
    except Exception:
        return []

    by_id = {}
    if isinstance(arr, list):
        for entry in arr:
            if isinstance(entry, dict) and "id" in entry:
                by_id[int(entry["id"])] = str(entry.get("summary", "")).strip()
    return [by_id.get(int(it.get("id") or 0), "") for it in items]


async def compress_layer_1_to_2(
    conn: sqlite3.Connection,
    anthropic_client=None,
    layer1_days: int = 7,
    batch_size: int = 10,
) -> int:
    """
    Move rows older than `layer1_days` from layer 1 → layer 2 with a Haiku summary.
    Falls back to V1 truncate if Haiku is unavailable. Returns rows updated.
    """
    cutoff = (datetime.now() - timedelta(days=layer1_days)).isoformat()
    cur = conn.execute(
        """
        SELECT id, content, ticker, ticker_name
        FROM user_memories
        WHERE compression_layer = 1
          AND created_at < ?
          AND COALESCE(fact_extracted, 0) = 1
        ORDER BY id
        """,
        (cutoff,),
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    updated = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        items = []
        for row in chunk:
            mem_id, content_json, ticker, ticker_name = row
            try:
                content = json.loads(content_json) if content_json else {}
            except Exception:
                content = {"text": content_json or ""}
            items.append(
                {
                    "id": mem_id,
                    "content": content,
                    "ticker": ticker,
                    "ticker_name": ticker_name,
                }
            )

        summaries: List[str] = []
        if anthropic_client is not None:
            try:
                summaries = await _haiku_summarize_batch(anthropic_client, items)
            except Exception as e:
                logger.warning("memory.compress.layer1_2.batch_failed err=%s", e)
                summaries = []

        for idx, it in enumerate(items):
            summary = summaries[idx] if idx < len(summaries) and summaries[idx] else ""
            if not summary:
                summary = _truncate_summary(it["content"], it["ticker"])
            try:
                conn.execute(
                    "UPDATE user_memories SET compression_layer = 2, summary = ? WHERE id = ?",
                    (summary, it["id"]),
                )
                updated += 1
            except Exception as e:
                logger.warning("memory.compress.update_failed err=%s", e)
        conn.commit()
    return updated


async def compress_layer_2_to_3(
    conn: sqlite3.Connection,
    anthropic_client=None,
    layer2_days: int = 30,
    batch_size: int = 10,
) -> int:
    """Move rows older than `layer2_days` from layer 2 → layer 3 (one-line lesson)."""
    cutoff = (datetime.now() - timedelta(days=layer2_days)).isoformat()
    cur = conn.execute(
        """
        SELECT id, summary, ticker, ticker_name
        FROM user_memories
        WHERE compression_layer = 2 AND created_at < ?
        ORDER BY id
        """,
        (cutoff,),
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    updated = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        items = []
        for row in chunk:
            mem_id, summary, ticker, ticker_name = row
            items.append(
                {
                    "id": mem_id,
                    "content": {"text": summary or ""},
                    "ticker": ticker,
                    "ticker_name": ticker_name,
                }
            )

        summaries: List[str] = []
        if anthropic_client is not None:
            try:
                summaries = await _haiku_summarize_batch(anthropic_client, items)
            except Exception:
                summaries = []

        for idx, it in enumerate(items):
            condensed = summaries[idx] if idx < len(summaries) and summaries[idx] else ""
            if not condensed:
                condensed = _truncate_compressed(it["content"].get("text"), it["ticker"])
            try:
                conn.execute(
                    "UPDATE user_memories SET compression_layer = 3, summary = ? WHERE id = ?",
                    (condensed, it["id"]),
                )
                updated += 1
            except Exception as e:
                logger.warning("memory.compress.update_failed err=%s", e)
        conn.commit()
    return updated


def compress_all_sync(
    conn: sqlite3.Connection,
    anthropic_client=None,
    layer1_days: int = 7,
    layer2_days: int = 30,
) -> Dict[str, int]:
    """
    Synchronous wrapper used from cron entry points that don't run an event loop.
    Always returns a dict (never raises). Uses asyncio.run() if no loop is active;
    otherwise schedules and waits via run_until_complete.
    """
    out = {"layer2_count": 0, "layer3_count": 0}
    try:
        async def _run():
            l2 = await compress_layer_1_to_2(
                conn, anthropic_client=anthropic_client, layer1_days=layer1_days
            )
            l3 = await compress_layer_2_to_3(
                conn, anthropic_client=anthropic_client, layer2_days=layer2_days
            )
            return l2, l3

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Caller is inside an event loop — degrade gracefully.
                logger.info("memory.compress.skip_inside_loop")
                return out
        except RuntimeError:
            loop = None
        l2, l3 = asyncio.run(_run())
        out["layer2_count"] = l2
        out["layer3_count"] = l3
    except Exception as e:
        logger.warning("memory.compress.sync_wrapper_failed err=%s", e)
    return out
