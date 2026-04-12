"""
query_engine.py — Natural language query engine over the PRISM report archive.

Pipeline:
  1. Check insight cache (24-hour TTL for recent queries)
  2. Parse NL query for ticker / date / market hints
  3. FTS5 retrieval + optional structured filter
  4. Enrich each hit with performance data (return_7d…90d, stop_loss)
  5. Assemble compact LLM context (max ~8 000 chars)
  6. OpenAI chat completion → insight text
  7. Store in insights cache
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
import yaml

from .archive_db import (  # type: ignore[import]
    ARCHIVE_DB_PATH,
    cache_insight,
    get_cached_insight,
    get_report_ids,
    init_db,
    search_fts,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
_SECRETS_PATH = PROJECT_ROOT / "mcp_agent.secrets.yaml"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "gpt-4.1-mini"
_MAX_CONTEXT_CHARS = 8_000   # approximate token budget for retrieved context
_MAX_REPORTS_IN_CONTEXT = 6
_CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ReportSnippet:
    report_id: int
    ticker: str
    company_name: str
    report_date: str
    market: str
    mode: str
    snippet: str = ""
    content_excerpt: str = ""
    enrichment: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    answer: str
    sources: List[ReportSnippet]
    evidence_ids: List[int]
    query_hash: str
    cached: bool = False
    model_used: str = _DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_api_key() -> Optional[str]:
    """Read OpenAI API key from mcp_agent.secrets.yaml."""
    try:
        with open(_SECRETS_PATH) as f:
            secrets = yaml.safe_load(f)
        return (secrets or {}).get("openai", {}).get("api_key")
    except Exception as e:
        logger.debug(f"Could not load secrets: {e}")
    return None


def _query_hash(text: str, market: Optional[str], ticker: Optional[str],
                date_from: Optional[str], date_to: Optional[str]) -> str:
    key = f"{text}|{market}|{ticker}|{date_from}|{date_to}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _parse_hints(text: str) -> Dict[str, Optional[str]]:
    """
    Best-effort extraction of ticker / market / date hints from NL query.

    Does NOT modify the original query text — hints are used only to
    narrow the retrieval step.
    """
    hints: Dict[str, Optional[str]] = {"ticker": None, "market": None,
                                        "date_from": None, "date_to": None}

    # Market hint
    if re.search(r"\b(us|미국|nasdaq|nyse|s&p|s&p500|spy)\b", text, re.IGNORECASE):
        hints["market"] = "us"
    elif re.search(r"\b(kr|코스피|코스닥|한국|국내|kospi|kosdaq)\b", text, re.IGNORECASE):
        hints["market"] = "kr"

    # Ticker hint — US: 2-5 uppercase letters; KR: 6-digit code
    _TICKER_STOPWORDS = frozenset({
        "AI", "US", "OR", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF",
        "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON", "SO", "TO",
        "UP", "WE", "AM", "ARE", "THE", "AND", "FOR", "NOT", "BUT", "ALL",
        "CAN", "HAS", "HER", "HIM", "HIS", "HOW", "ITS", "MAY", "NEW",
        "NOW", "OLD", "OUR", "OUT", "OWN", "SAY", "SHE", "TOO", "USE",
        "ETF", "IPO", "CEO", "CFO", "NYSE", "SEC", "FDA", "GDP",
    })
    m = re.search(r"\b([A-Z]{2,5})\b", text)
    if m and m.group(1) not in _TICKER_STOPWORDS:
        hints["ticker"] = m.group(1)
    m = re.search(r"\b(\d{6})\b", text)
    if m:
        hints["ticker"] = m.group(1)

    # Date hints — ISO dates or Korean shorthand like "10월", "2025년 4분기"
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        hints["date_from"] = m.group(1)

    # "지난 N일" / "최근 N일"
    m = re.search(r"(?:지난|최근)\s*(\d+)일", text)
    if m:
        days = int(m.group(1))
        hints["date_from"] = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    return hints


def _format_enrichment(e: Dict[str, Any]) -> str:
    """Format enrichment dict as a compact human-readable summary."""
    if not e:
        return ""
    parts = []
    if e.get("market_phase"):
        parts.append(f"시장국면={e['market_phase']}")
    for key, label in [("return_7d", "7d"), ("return_30d", "30d"), ("return_90d", "90d")]:
        v = e.get(key)
        if v is not None:
            parts.append(f"{label}수익={v:+.1f}%")
    if e.get("stop_loss_triggered"):
        parts.append(f"손절발동={e.get('stop_loss_date','?')}")
    return "  [" + " | ".join(parts) + "]" if parts else ""


async def _fetch_enrichments(report_ids: List[int],
                              db_path: str) -> Dict[int, Dict[str, Any]]:
    """Bulk fetch enrichment rows for a list of report_ids."""
    if not report_ids:
        return {}
    placeholders = ",".join("?" * len(report_ids))
    result: Dict[int, Dict[str, Any]] = {}
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM report_enrichment WHERE report_id IN ({placeholders})",
                report_ids,
            )
            rows = await cur.fetchall()
            for row in rows:
                result[row["report_id"]] = dict(row)
    except Exception as e:
        logger.warning(f"Enrichment fetch failed: {e}")
    return result


async def _fetch_content_excerpts(report_ids: List[int],
                                   db_path: str,
                                   max_chars: int = 1_200) -> Dict[int, str]:
    """Fetch truncated content for each report_id."""
    if not report_ids:
        return {}
    placeholders = ",".join("?" * len(report_ids))
    result: Dict[int, str] = {}
    try:
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                f"SELECT id, content FROM report_archive WHERE id IN ({placeholders})",
                report_ids,
            )
            rows = await cur.fetchall()
            for row in rows:
                content = row[1] or ""
                result[row[0]] = content[:max_chars] + ("…" if len(content) > max_chars else "")
    except Exception as e:
        logger.warning(f"Content fetch failed: {e}")
    return result


def _build_context(snippets: List[ReportSnippet], max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    """Assemble retrieved snippets into a compact context string for the LLM."""
    lines: List[str] = ["=== PRISM 분석 아카이브 (검색 결과) ===\n"]
    used = len(lines[0])
    for i, s in enumerate(snippets, 1):
        header = (
            f"\n[{i}] {s.ticker} {s.company_name} | {s.report_date} | {s.market.upper()}"
            f"{_format_enrichment(s.enrichment)}\n"
        )
        body = s.content_excerpt or s.snippet
        block = header + body + "\n"
        if used + len(block) > max_chars:
            # Truncate body to fit
            remaining = max_chars - used - len(header) - 50
            if remaining > 100:
                block = header + body[:remaining] + "…\n"
            else:
                break
        lines.append(block)
        used += len(block)
    return "".join(lines)


def _get_openai_client(api_key: str):
    """
    Create an AsyncOpenAI client respecting PRISM_OPENAI_AUTH_MODE.

    When ``chatgpt_oauth`` mode is active, uses the ChatGPT proxy endpoint.
    Otherwise falls back to standard OpenAI API with the provided key.
    """
    import os

    import openai

    auth_mode = os.environ.get("PRISM_OPENAI_AUTH_MODE", "api_key")
    if auth_mode == "chatgpt_oauth":
        try:
            from cores.chatgpt_proxy.constants import CHATGPT_BASE_URL  # type: ignore[import]
            return openai.AsyncOpenAI(api_key=api_key, base_url=CHATGPT_BASE_URL)
        except ImportError:
            logger.debug("chatgpt_proxy not available, falling back to api_key mode")
    return openai.AsyncOpenAI(api_key=api_key)


async def _synthesize(query: str, context: str, api_key: str,
                       model: str) -> str:
    """Call OpenAI chat completion to synthesize an insight from retrieved context."""
    try:
        import openai  # noqa: F811 — lazy; _get_openai_client also imports
    except ImportError:
        return "openai 패키지가 설치되어 있지 않습니다. `pip install openai`"

    system_prompt = (
        "당신은 PRISM 주식 분석 아카이브의 인사이트 엔진입니다. "
        "제공된 분석 리포트 데이터를 근거로 사용자 질문에 답변하세요.\n"
        "- 근거가 없는 내용은 추측이라고 명시하세요.\n"
        "- 수익률, 손절, 시장국면 데이터가 있으면 반드시 인용하세요.\n"
        "- 한국어로 간결하게 답변하세요 (400~800자).\n"
        "- 관련 종목 목록이 있으면 번호 목록으로 정리하세요."
    )

    try:
        client = _get_openai_client(api_key)
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"### 아카이브 데이터\n{context}\n\n### 질문\n{query}"},
            ],
            max_tokens=800,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"LLM synthesis failed: {e}")
        return f"[LLM 합성 실패] 컨텍스트만 반환합니다:\n\n{context[:2000]}"


# ---------------------------------------------------------------------------
# QueryEngine
# ---------------------------------------------------------------------------

class QueryEngine:
    """
    Natural language query engine over the PRISM report archive.

    Usage::

        engine = QueryEngine()
        result = await engine.query("반도체 강세 시기 수익률 높은 종목은?")
        print(result.answer)
        for src in result.sources:
            print(src.ticker, src.report_date, src.enrichment.get("return_30d"))
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
        cache_ttl_hours: int = _CACHE_TTL_HOURS,
    ):
        self.db_path = db_path or str(ARCHIVE_DB_PATH)
        self.model = model
        self.cache_ttl_hours = cache_ttl_hours
        self._api_key: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query(
        self,
        text: str,
        market: Optional[str] = None,
        ticker: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        skip_cache: bool = False,
    ) -> QueryResult:
        """
        Answer a natural language question using the archive.

        Args:
            text: Natural language query (Korean or English)
            market: 'kr', 'us', or None (auto-detect from query text)
            ticker: Specific ticker to focus on (optional)
            date_from: ISO date lower bound (optional)
            date_to: ISO date upper bound (optional)
            skip_cache: Force fresh synthesis even if cached answer exists

        Returns:
            QueryResult with answer, sources, evidence_ids
        """
        await init_db(self.db_path)

        # Merge explicit args with hints parsed from NL
        hints = _parse_hints(text)
        effective_market = market or hints["market"]
        effective_ticker = ticker or hints["ticker"]
        effective_date_from = date_from or hints["date_from"]
        effective_date_to = date_to or hints["date_to"]

        q_hash = _query_hash(text, effective_market, effective_ticker,
                              effective_date_from, effective_date_to)

        # 1. Cache check
        if not skip_cache:
            cached = await get_cached_insight(q_hash, self.db_path)
            if cached:
                try:
                    cached_data = json.loads(cached)
                    return QueryResult(
                        answer=cached_data["answer"],
                        sources=[],
                        evidence_ids=cached_data.get("evidence_ids", []),
                        query_hash=q_hash,
                        cached=True,
                        model_used=cached_data.get("model_used", self.model),
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Corrupted cache entry for {q_hash}, regenerating: {e}")

        # 2. Retrieval
        snippets = await self.retrieve(
            text=text,
            market=effective_market,
            ticker=effective_ticker,
            date_from=effective_date_from,
            date_to=effective_date_to,
        )

        if not snippets:
            return QueryResult(
                answer="관련 분석 리포트를 찾을 수 없습니다. 다른 키워드로 검색해보세요.",
                sources=[],
                evidence_ids=[],
                query_hash=q_hash,
                model_used=self.model,
            )

        # 3. Build context
        context = _build_context(snippets)

        # 4. Synthesize
        api_key = self._api_key or _load_api_key()
        if not api_key:
            answer = (
                "[API 키 없음] 컨텍스트만 반환합니다:\n\n"
                + context[:2000]
            )
        else:
            self._api_key = api_key
            answer = await _synthesize(text, context, api_key, self.model)

        evidence_ids = [s.report_id for s in snippets]

        # 5. Cache the result
        expires_at = (
            datetime.now() + timedelta(hours=self.cache_ttl_hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        await cache_insight(
            query=text,
            query_hash=q_hash,
            insight_text=json.dumps(
                {"answer": answer, "evidence_ids": evidence_ids, "model_used": self.model},
                ensure_ascii=False,
            ),
            evidence_ids=evidence_ids,
            insight_type="nl_query",
            market=effective_market,
            model_used=self.model,
            expires_at=expires_at,
            db_path=self.db_path,
        )

        return QueryResult(
            answer=answer,
            sources=snippets,
            evidence_ids=evidence_ids,
            query_hash=q_hash,
            model_used=self.model,
        )

    async def list_reports(
        self,
        market: Optional[str] = None,
        ticker: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return recent report metadata, optionally filtered."""
        await init_db(self.db_path)
        rows = await get_report_ids(
            ticker=ticker,
            market=market,
            date_from=date_from,
            date_to=date_to,
            db_path=self.db_path,
        )
        return rows[:limit]

    async def search(
        self,
        query: str,
        market: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Raw FTS5 search without LLM synthesis."""
        await init_db(self.db_path)
        return await search_fts(query, market=market, limit=limit, db_path=self.db_path)

    async def stats(self) -> Dict[str, Any]:
        """Return archive statistics (report count, date range, market breakdown)."""
        await init_db(self.db_path)
        result: Dict[str, Any] = {}
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    "SELECT market, COUNT(*) as cnt, MIN(report_date) as earliest, "
                    "MAX(report_date) as latest FROM report_archive GROUP BY market"
                )
                rows = await cur.fetchall()
                result["by_market"] = [
                    {"market": r[0], "count": r[1], "earliest": r[2], "latest": r[3]}
                    for r in rows
                ]
                cur = await db.execute("SELECT COUNT(*) FROM report_enrichment")
                row = await cur.fetchone()
                result["enriched_count"] = row[0] if row else 0
                cur = await db.execute("SELECT COUNT(*) FROM insights")
                row = await cur.fetchone()
                result["cached_insights"] = row[0] if row else 0
        except Exception as e:
            logger.warning(f"Stats query failed: {e}")
        return result

    # ------------------------------------------------------------------
    # Retrieval (public — reusable by auto_insight / Phase 3)
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        text: str,
        market: Optional[str],
        ticker: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> List[ReportSnippet]:
        """
        Hybrid retrieval: FTS5 for text relevance + structured filter for
        ticker/date constraints. Merges and deduplicates by report_id.
        """
        fts_hits: List[Dict] = []
        structured_hits: List[Dict] = []

        # FTS search
        fts_hits = await search_fts(
            text,
            market=market,
            limit=_MAX_REPORTS_IN_CONTEXT * 2,
            db_path=self.db_path,
        )

        # Structured filter (ticker / date range)
        if ticker or date_from or date_to:
            structured_hits = await get_report_ids(
                ticker=ticker,
                market=market,
                date_from=date_from,
                date_to=date_to,
                db_path=self.db_path,
            )

        # Merge: FTS hits first (most relevant), then structured-only hits
        seen: set = set()
        merged: List[Dict] = []
        for hit in fts_hits:
            if hit["id"] not in seen:
                seen.add(hit["id"])
                merged.append(hit)
        for hit in structured_hits:
            if hit["id"] not in seen:
                seen.add(hit["id"])
                merged.append(hit)

        merged = merged[:_MAX_REPORTS_IN_CONTEXT]

        if not merged:
            return []

        # Bulk fetch enrichments + content
        ids = [h["id"] for h in merged]
        enrichments, excerpts = await asyncio.gather(
            _fetch_enrichments(ids, self.db_path),
            _fetch_content_excerpts(ids, self.db_path),
        )

        snippets: List[ReportSnippet] = []
        for hit in merged:
            rid = hit["id"]
            snippets.append(ReportSnippet(
                report_id=rid,
                ticker=hit["ticker"],
                company_name=hit.get("company_name", ""),
                report_date=hit.get("report_date", ""),
                market=hit.get("market", ""),
                mode=hit.get("mode", ""),
                snippet=hit.get("snippet", ""),
                content_excerpt=excerpts.get(rid, ""),
                enrichment=enrichments.get(rid, {}),
            ))

        return snippets


# ---------------------------------------------------------------------------
# Module-level convenience wrapper
# ---------------------------------------------------------------------------

_default_engine: Optional[QueryEngine] = None


async def ask(
    text: str,
    market: Optional[str] = None,
    ticker: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    skip_cache: bool = False,
    model: str = _DEFAULT_MODEL,
) -> QueryResult:
    """
    Module-level convenience function. Creates a default engine on first call.

    Example::

        from cores.archive.query_engine import ask
        result = await ask("반도체 관련주 최근 성과는?", market="kr")
        print(result.answer)
    """
    global _default_engine
    if _default_engine is None or _default_engine.model != model:
        _default_engine = QueryEngine(model=model)
    return await _default_engine.query(
        text, market=market, ticker=ticker,
        date_from=date_from, date_to=date_to,
        skip_cache=skip_cache,
    )
