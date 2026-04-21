"""
persistent_insights.py — /insight 대화로 축적되는 영구 인사이트 레이어.

핵심 API:
  save_insight(...)                — 신규 인사이트 저장 (+ tool_usage 기록)
  fts_candidates(query, limit)     — FTS5 후보 추출
  search_insights(query, q_emb, …) — FTS 후보 → 임베딩 재랭킹 top-N
  recent_weekly_summaries(n)       — 최근 n주 요약
  check_and_increment_quota(...)   — 일일 쿼터 체크 & 증가
  mark_superseded(ids, summary_id) — 주간 요약이 커버한 raw 표시
  increment_cost(...)              — insight_cost_daily UPSERT
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import numpy as np

from .archive_db import ARCHIVE_DB_PATH, _sanitize_fts_query, init_db
from .embedding import decode_embedding

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


@dataclass
class InsightRow:
    id: int
    user_id: Optional[int]
    chat_id: Optional[int]
    question: str
    answer: str
    key_takeaways: List[str]
    tools_used: List[str]
    tickers_mentioned: List[str]
    evidence_report_ids: List[int]
    embedding: Optional[bytes]
    model_used: Optional[str]
    previous_insight_id: Optional[int]
    superseded_by: Optional[int]
    created_at: str


def _loads(s: Optional[str], default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _row_to_insight(r: aiosqlite.Row) -> InsightRow:
    return InsightRow(
        id=r["id"],
        user_id=r["user_id"],
        chat_id=r["chat_id"],
        question=r["question"],
        answer=r["answer"],
        key_takeaways=_loads(r["key_takeaways"], []),
        tools_used=_loads(r["tools_used"], []),
        tickers_mentioned=_loads(r["tickers_mentioned"], []),
        evidence_report_ids=_loads(r["evidence_report_ids"], []),
        embedding=r["embedding"],
        model_used=r["model_used"],
        previous_insight_id=r["previous_insight_id"],
        superseded_by=r["superseded_by"],
        created_at=r["created_at"],
    )


async def save_insight(
    *,
    user_id: Optional[int],
    chat_id: Optional[int],
    question: str,
    answer: str,
    key_takeaways: List[str],
    tools_used: List[str],
    tickers_mentioned: List[str],
    evidence_report_ids: List[int],
    model_used: str,
    embedding: Optional[bytes] = None,
    previous_insight_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> int:
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            INSERT INTO persistent_insights (
                user_id, chat_id, question, answer,
                key_takeaways, tools_used, tickers_mentioned, evidence_report_ids,
                embedding, model_used, previous_insight_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, chat_id, question, answer,
                json.dumps(key_takeaways, ensure_ascii=False),
                json.dumps(tools_used, ensure_ascii=False),
                json.dumps(tickers_mentioned, ensure_ascii=False),
                json.dumps(evidence_report_ids),
                embedding, model_used, previous_insight_id,
            ),
        )
        insight_id = cur.lastrowid
        # Tool usage breakdown
        if insight_id is not None:
            for tool in tools_used or []:
                await db.execute(
                    "INSERT INTO insight_tool_usage (insight_id, tool_name) VALUES (?, ?)",
                    (insight_id, tool),
                )
        await db.commit()
        return int(insight_id) if insight_id is not None else -1


async def fts_candidates(
    query: str,
    limit: int = 50,
    exclude_superseded: bool = True,
    db_path: Optional[str] = None,
) -> List[InsightRow]:
    """FTS5로 후보 추출. 실패 시 빈 리스트."""
    path = db_path or str(ARCHIVE_DB_PATH)
    safe = _sanitize_fts_query(query)
    supersede_clause = "AND pi.superseded_by IS NULL" if exclude_superseded else ""
    try:
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"""
                SELECT pi.*
                FROM persistent_insights_fts fts
                JOIN persistent_insights pi ON pi.id = fts.rowid
                WHERE persistent_insights_fts MATCH ?
                  {supersede_clause}
                ORDER BY rank
                LIMIT ?
                """,
                (safe, limit),
            )
            rows = await cur.fetchall()
            return [_row_to_insight(r) for r in rows]
    except aiosqlite.OperationalError as e:
        logger.warning(f"persistent_insights FTS failed: {e}")
        return []


async def search_insights(
    query: str,
    query_embedding: Optional[bytes],
    limit: int = 5,
    exclude_superseded: bool = True,
    db_path: Optional[str] = None,
) -> List[InsightRow]:
    """
    FTS top-50 → 임베딩 재랭킹 top-limit.
    query_embedding이 없거나 후보가 적으면 FTS 순서 그대로 상위 limit.
    """
    candidates = await fts_candidates(
        query, limit=50, exclude_superseded=exclude_superseded, db_path=db_path
    )
    if not candidates:
        return []
    if not query_embedding or len(candidates) <= limit:
        return candidates[:limit]

    q_vec = decode_embedding(query_embedding)
    if q_vec is None:
        return candidates[:limit]
    q_norm = float(np.linalg.norm(q_vec)) or 1e-9

    scored: List[Tuple[float, InsightRow]] = []
    for c in candidates:
        cv = decode_embedding(c.embedding)
        if cv is None:
            scored.append((0.0, c))
            continue
        cn = float(np.linalg.norm(cv)) or 1e-9
        s = float(np.dot(q_vec, cv) / (q_norm * cn))
        scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


async def recent_weekly_summaries(
    weeks: int = 4, db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT week_start, week_end, summary_text, insight_count, top_tickers
            FROM weekly_insight_summary
            ORDER BY week_start DESC
            LIMIT ?
            """,
            (weeks,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


def _kst_date_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


async def check_and_increment_quota(
    user_id: int,
    daily_limit: int,
    db_path: Optional[str] = None,
) -> Tuple[bool, int]:
    """
    Returns (allowed, remaining_after_call).
    daily_limit <= 0 이면 무제한.
    """
    if daily_limit <= 0:
        return True, 999999
    path = db_path or str(ARCHIVE_DB_PATH)
    today = _kst_date_str()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT count FROM user_insight_quota WHERE user_id=? AND date=?",
            (user_id, today),
        )
        row = await cur.fetchone()
        current = int(row["count"]) if row else 0
        if current >= daily_limit:
            return False, 0
        new_count = current + 1
        await db.execute(
            """
            INSERT INTO user_insight_quota (user_id, date, count)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET count=excluded.count
            """,
            (user_id, today, new_count),
        )
        await db.commit()
        return True, max(0, daily_limit - new_count)


async def mark_superseded(
    insight_ids: List[int],
    summary_id: int,
    db_path: Optional[str] = None,
) -> int:
    if not insight_ids:
        return 0
    path = db_path or str(ARCHIVE_DB_PATH)
    placeholders = ",".join("?" for _ in insight_ids)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            f"UPDATE persistent_insights SET superseded_by=? WHERE id IN ({placeholders})",
            (summary_id, *insight_ids),
        )
        await db.commit()
        return cur.rowcount


async def increment_cost(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    embedding_tokens: int = 0,
    perplexity_calls: int = 0,
    firecrawl_calls: int = 0,
    db_path: Optional[str] = None,
) -> None:
    """insight_cost_daily UPSERT — fire-and-forget."""
    path = db_path or str(ARCHIVE_DB_PATH)
    today = _kst_date_str()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO insight_cost_daily
                (date, input_tokens, output_tokens, embedding_tokens,
                 perplexity_calls, firecrawl_calls)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                input_tokens     = input_tokens + excluded.input_tokens,
                output_tokens    = output_tokens + excluded.output_tokens,
                embedding_tokens = embedding_tokens + excluded.embedding_tokens,
                perplexity_calls = perplexity_calls + excluded.perplexity_calls,
                firecrawl_calls  = firecrawl_calls + excluded.firecrawl_calls
            """,
            (
                today, input_tokens, output_tokens, embedding_tokens,
                perplexity_calls, firecrawl_calls,
            ),
        )
        await db.commit()


async def self_check(db_path: Optional[str] = None) -> Dict[str, Any]:
    """CLI 헬스체크 — 테이블 접근 + 개수 집계."""
    await init_db(db_path)
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM persistent_insights")
        pi_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM weekly_insight_summary")
        ws_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM insight_tool_usage")
        tu_count = (await cur.fetchone())[0]
    return {
        "persistent_insights": pi_count,
        "weekly_insight_summary": ws_count,
        "insight_tool_usage": tu_count,
    }


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(self_check()))
