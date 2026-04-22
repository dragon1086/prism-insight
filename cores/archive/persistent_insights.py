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


async def _get_confidence_scores(
    insight_ids: List[int], db_path: Optional[str] = None,
) -> Dict[int, float]:
    if not insight_ids:
        return {}
    path = db_path or str(ARCHIVE_DB_PATH)
    placeholders = ",".join("?" for _ in insight_ids)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            f"""
            SELECT id, COALESCE(confidence_score, 0.0) AS cs
            FROM persistent_insights
            WHERE id IN ({placeholders})
            """,
            insight_ids,
        )
        rows = await cur.fetchall()
    return {r[0]: float(r[1]) for r in rows}


async def search_insights(
    query: str,
    query_embedding: Optional[bytes],
    limit: int = 5,
    exclude_superseded: bool = True,
    db_path: Optional[str] = None,
    confidence_weight: float = 0.15,
    drop_below: float = -0.6,
) -> List[InsightRow]:
    """
    FTS top-50 → 임베딩 재랭킹 + confidence_score 가중치 → top-limit.

    final_score = cosine_sim + confidence_weight * confidence_score
                  (confidence_score ∈ [-1, 1])

    Insights with confidence_score below drop_below are filtered out entirely
    (heavily downvoted answers shouldn't pollute future retrievals).
    """
    candidates = await fts_candidates(
        query, limit=50, exclude_superseded=exclude_superseded, db_path=db_path
    )
    if not candidates:
        return []

    # Confidence filter + boost
    cs_map = await _get_confidence_scores(
        [c.id for c in candidates], db_path=db_path
    )
    candidates = [c for c in candidates if cs_map.get(c.id, 0.0) > drop_below]
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
            sim = 0.0
        else:
            cn = float(np.linalg.norm(cv)) or 1e-9
            sim = float(np.dot(q_vec, cv) / (q_norm * cn))
        boost = confidence_weight * cs_map.get(c.id, 0.0)
        scored.append((sim + boost, c))
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


async def record_feedback(
    insight_id: int,
    user_id: int,
    score: int,                 # +1 / -1
    reason: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """
    Record user feedback (UPSERT — one vote per user per insight).
    Updates persistent_insights.confidence_score = SUM(feedback.score) / 10.0 (clamped).
    Returns True on success.
    """
    if score not in (-1, 0, 1):
        return False
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO insight_feedback (insight_id, user_id, score, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(insight_id, user_id) DO UPDATE SET
                score=excluded.score,
                reason=COALESCE(excluded.reason, reason),
                created_at=datetime('now', 'localtime')
            """,
            (insight_id, user_id, score, reason),
        )
        cur = await db.execute(
            "SELECT COALESCE(SUM(score), 0) FROM insight_feedback WHERE insight_id=?",
            (insight_id,),
        )
        agg = (await cur.fetchone())[0] or 0
        # Clamp to [-1.0, +1.0] using soft normalization (1 vote ≈ 0.2)
        score_norm = max(-1.0, min(1.0, float(agg) / 5.0))
        await db.execute(
            "UPDATE persistent_insights SET confidence_score=? WHERE id=?",
            (score_norm, insight_id),
        )
        await db.commit()
    return True


async def fetch_outcomes_for_tickers(
    tickers: List[str],
    db_path: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Outcome grounding — return {ticker: {…outcomes…, first_analysis_date,
    last_analysis_date, last_price_update}}.

    Latest enrichment per ticker is used for return/MDD/market_phase fields.
    Min/max analysis_date span is added separately so the LLM can cite the
    full data window when mentioning a ticker.
    """
    if not tickers:
        return {}
    path = db_path or str(ARCHIVE_DB_PATH)
    placeholders = ",".join("?" for _ in tickers)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        # Latest enrichment row per ticker
        cur = await db.execute(
            f"""
            SELECT re.ticker, re.return_30d, re.return_90d, re.return_180d,
                   re.return_365d, re.return_current,
                   re.max_drawdown, re.market_phase, re.last_price_update,
                   re.analysis_date
            FROM report_enrichment re
            WHERE re.ticker IN ({placeholders})
            ORDER BY re.analysis_date DESC
            """,
            tickers,
        )
        rows = await cur.fetchall()
        # Analysis date window per ticker (min ~ max)
        cur = await db.execute(
            f"""
            SELECT re.ticker,
                   MIN(re.analysis_date) AS first_analysis_date,
                   MAX(re.analysis_date) AS last_analysis_date,
                   COUNT(*)               AS report_count
            FROM report_enrichment re
            WHERE re.ticker IN ({placeholders})
            GROUP BY re.ticker
            """,
            tickers,
        )
        windows = {r["ticker"]: dict(r) for r in await cur.fetchall()}

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        t = r["ticker"]
        if t in out:
            continue   # keep most recent only
        win = windows.get(t, {})
        out[t] = {
            "return_30d": r["return_30d"],
            "return_90d": r["return_90d"],
            "return_180d": r["return_180d"],
            "return_365d": r["return_365d"],
            "return_current": r["return_current"],
            "max_drawdown": r["max_drawdown"],
            "market_phase": r["market_phase"],
            "last_price_update": r["last_price_update"],
            "analysis_date": r["analysis_date"],
            "first_analysis_date": win.get("first_analysis_date"),
            "last_analysis_date": win.get("last_analysis_date"),
            "report_count": win.get("report_count"),
        }
    return out


# ---------------------------------------------------------------------------
# Semantic facts CRUD (Mem0 pattern — distilled atomic facts per ticker)
# ---------------------------------------------------------------------------

async def upsert_semantic_fact(
    *,
    ticker: str,
    fact_text: str,
    fact_category: str,
    confidence: float = 0.5,
    supporting_insight_ids: Optional[List[int]] = None,
    supporting_report_ids: Optional[List[int]] = None,
    db_path: Optional[str] = None,
) -> int:
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            INSERT INTO ticker_semantic_facts
                (ticker, fact_text, fact_category, confidence,
                 supporting_insight_ids, supporting_report_ids,
                 last_validated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """,
            (
                ticker.upper(), fact_text, fact_category, confidence,
                json.dumps(supporting_insight_ids or []),
                json.dumps(supporting_report_ids or []),
            ),
        )
        await db.commit()
        return int(cur.lastrowid) if cur.lastrowid else -1


async def get_semantic_facts_for_tickers(
    tickers: List[str],
    limit_per_ticker: int = 3,
    exclude_superseded: bool = True,
    db_path: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return {ticker: [{fact, category, confidence, validated_at}, ...]}."""
    if not tickers:
        return {}
    path = db_path or str(ARCHIVE_DB_PATH)
    placeholders = ",".join("?" for _ in tickers)
    supersede_clause = "AND superseded_by IS NULL" if exclude_superseded else ""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""
            SELECT ticker, fact_text, fact_category, confidence, last_validated_at
            FROM ticker_semantic_facts
            WHERE ticker IN ({placeholders})
              {supersede_clause}
            ORDER BY ticker, confidence DESC, last_validated_at DESC
            """,
            [t.upper() for t in tickers],
        )
        rows = await cur.fetchall()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        t = r["ticker"]
        if t not in out:
            out[t] = []
        if len(out[t]) < limit_per_ticker:
            out[t].append({
                "fact": r["fact_text"],
                "category": r["fact_category"],
                "confidence": r["confidence"],
                "validated_at": r["last_validated_at"],
            })
    return out


async def supersede_semantic_fact(
    old_id: int, new_id: int, db_path: Optional[str] = None,
) -> None:
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE ticker_semantic_facts SET superseded_by=? WHERE id=?",
            (new_id, old_id),
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
