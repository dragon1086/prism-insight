#!/usr/bin/env python3
"""
archive_api.py — Lightweight FastAPI server for PRISM archive queries.

Runs on the pipeline server (where archive.db lives).
The Telegram bot server calls this API to answer /insight queries.

Usage:
    # Start server (pipeline server)
    ARCHIVE_API_KEY=your_secret uvicorn archive_api:app --host 0.0.0.0 --port 8765

    # Or bind to localhost only (use SSH tunnel from bot server)
    ARCHIVE_API_KEY=your_secret uvicorn archive_api:app --host 127.0.0.1 --port 8765

Endpoints:
    GET  /health
    GET  /stats
    GET  /search?keyword=반도체&market=kr&limit=10
    POST /query   {"question": "...", "market": "kr", "ticker": null}
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Allow running from project root
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from fastapi import FastAPI, HTTPException, Security, Depends
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel
except ImportError as e:
    print(f"fastapi not installed. Run: pip install fastapi uvicorn\nError: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_API_KEY = os.getenv("ARCHIVE_API_KEY", "")
_bearer = HTTPBearer(auto_error=True)


def _verify_key(creds: HTTPAuthorizationCredentials = Security(_bearer)) -> str:
    if not _API_KEY:
        # No key configured → open (dev mode, warn loudly)
        logger.warning("ARCHIVE_API_KEY not set — running in open mode!")
        return "open"
    if creds.credentials != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return creds.credentials


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PRISM Archive API",
    description="Query PRISM report archive via FTS5 and LLM synthesis.",
    version="1.0.0",
    docs_url=None,  # Disable Swagger UI in production
    redoc_url=None,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    question: str
    market: Optional[str] = None       # "kr" | "us" | None (both)
    ticker: Optional[str] = None
    date_from: Optional[str] = None    # YYYY-MM-DD
    date_to: Optional[str] = None      # YYYY-MM-DD
    skip_cache: bool = False
    model: str = "gpt-5.4-mini"


class QueryResponse(BaseModel):
    answer: str
    evidence_count: int
    cached: bool
    model_used: str


class SearchResponse(BaseModel):
    results: list[dict]
    total: int


class StatsResponse(BaseModel):
    stats: dict


class InsightAgentRequest(BaseModel):
    question: str
    user_id: int
    chat_id: int
    daily_limit: int = 20
    previous_insight_id: Optional[int] = None


class InsightAgentResponse(BaseModel):
    answer: str
    key_takeaways: list[str]
    tickers_mentioned: list[str]
    tools_used: list[str]
    evidence_count: int
    insight_id: Optional[int] = None
    remaining_quota: int
    model_used: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check — no auth required."""
    db_path = PROJECT_ROOT / "archive.db"
    return {
        "status": "ok",
        "archive_db": db_path.exists(),
        "archive_db_size_mb": round(db_path.stat().st_size / 1024 / 1024, 2) if db_path.exists() else 0,
    }


@app.get("/stats", response_model=StatsResponse)
async def stats(_key: str = Depends(_verify_key)):
    """Return archive DB statistics."""
    db_path = str(PROJECT_ROOT / "archive.db")
    try:
        import aiosqlite
        from cores.archive.archive_db import init_db
        await init_db(db_path)

        async with aiosqlite.connect(db_path) as conn:
            total = (await (await conn.execute("SELECT COUNT(*) FROM report_archive")).fetchone())[0]
            kr    = (await (await conn.execute("SELECT COUNT(*) FROM report_archive WHERE market='kr'")).fetchone())[0]
            us    = (await (await conn.execute("SELECT COUNT(*) FROM report_archive WHERE market='us'")).fetchone())[0]
            enriched = (await (await conn.execute("SELECT COUNT(*) FROM report_enrichment")).fetchone())[0]
            cached   = (await (await conn.execute("SELECT COUNT(*) FROM insights")).fetchone())[0]
            date_row = await (await conn.execute(
                "SELECT MIN(report_date), MAX(report_date) FROM report_archive"
            )).fetchone()

        return StatsResponse(stats={
            "total_reports": total,
            "kr_reports": kr,
            "us_reports": us,
            "enriched": enriched,
            "cached_insights": cached,
            "date_range": {
                "from": date_row[0] if date_row else None,
                "to": date_row[1] if date_row else None,
            },
        })
    except Exception as e:
        logger.error(f"/stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search", response_model=SearchResponse)
async def search(
    keyword: str,
    market: Optional[str] = None,
    limit: int = 10,
    _key: str = Depends(_verify_key),
):
    """FTS5 keyword search — fast, no LLM."""
    if not keyword or len(keyword.strip()) < 1:
        raise HTTPException(status_code=400, detail="keyword is required")
    limit = min(limit, 50)

    try:
        from cores.archive.archive_db import init_db, search_fts
        await init_db(str(PROJECT_ROOT / "archive.db"))
        rows = await search_fts(keyword.strip(), market=market, limit=limit)
        results = [
            {
                "id": r["id"],
                "ticker": r["ticker"],
                "company_name": r["company_name"],
                "report_date": r["report_date"],
                "market": r["market"],
            }
            for r in rows
        ]
        return SearchResponse(results=results, total=len(results))
    except Exception as e:
        logger.error(f"/search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, _key: str = Depends(_verify_key)):
    """Natural language query with LLM synthesis."""
    if not req.question or len(req.question.strip()) < 2:
        raise HTTPException(status_code=400, detail="question is required")

    question = req.question.strip()[:500]
    market = req.market if req.market in ("kr", "us") else None

    try:
        from cores.archive.query_engine import ask
        result = await ask(
            question,
            market=market,
            ticker=req.ticker,
            date_from=req.date_from,
            date_to=req.date_to,
            skip_cache=req.skip_cache,
            model=req.model,
        )
        return QueryResponse(
            answer=result.answer,
            evidence_count=len(result.evidence_ids),
            cached=result.cached,
            model_used=result.model_used,
        )
    except Exception as e:
        logger.error(f"/query error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/insight_agent", response_model=InsightAgentResponse)
async def insight_agent_endpoint(
    req: InsightAgentRequest, _key: str = Depends(_verify_key),
):
    """Persistent insight agent — retrieval + function calling + auto-save."""
    if not req.question or len(req.question.strip()) < 2:
        raise HTTPException(status_code=400, detail="question is required")
    if len(req.question) > 2000:
        raise HTTPException(status_code=400, detail="question too long (>2000 chars)")

    try:
        from cores.archive.insight_agent import InsightAgent
        agent = InsightAgent()
        result = await agent.run(
            question=req.question.strip(),
            user_id=req.user_id,
            chat_id=req.chat_id,
            daily_limit=req.daily_limit,
            previous_insight_id=req.previous_insight_id,
        )
        return InsightAgentResponse(
            answer=result.answer,
            key_takeaways=result.key_takeaways,
            tickers_mentioned=result.tickers_mentioned,
            tools_used=result.tools_used,
            evidence_count=len(result.evidence_report_ids),
            insight_id=result.insight_id,
            remaining_quota=result.remaining_quota,
            model_used=result.model_used,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"/insight_agent error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("ARCHIVE_API_HOST", "0.0.0.0")
    port = int(os.getenv("ARCHIVE_API_PORT", "8765"))
    logger.info(f"Starting PRISM Archive API on {host}:{port}")
    if not _API_KEY:
        logger.warning("ARCHIVE_API_KEY not set — set it in .env for security!")
    uvicorn.run(app, host=host, port=port, log_level="info")
