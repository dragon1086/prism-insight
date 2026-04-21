# Persistent Insight Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 `/insight` Q&A를 영구 저장하고, 누적 인사이트 + 외부 MCP 도구를 조합해 종목 장기투자 적합성을 판단하는 에이전트를 구축하여 PR #262에 포함한다.

**Architecture:** archive.db 5 신규 테이블 + mcp-agent function calling + FTS5→embedding 재랭킹 + 주간 요약 티어. 단일서버/양서버 모드 모두 유지. Telegram `/insight`는 ConversationHandler + Reply 기반 멀티턴 (30분 TTL).

**Tech Stack:** Python 3.10+, SQLite FTS5, aiosqlite, OpenAI API (gpt-5.4-mini + text-embedding-3-small), mcp-agent, python-telegram-bot, FastAPI, numpy.

**Spec:** `docs/superpowers/specs/2026-04-21-persistent-insight-agent-design.md`

**Branch:** `feat/archive-insight-query-system` (PR #262)

---

## Phase 1 — 로컬 파운데이션

### Task 1: 스키마 마이그레이션

**Files:**
- Modify: `cores/archive/archive_db.py` (init_db 함수에 신규 테이블 추가)
- Test: 수동 — 기존 DB에 대해 init_db() 재실행해서 신규 테이블 생성 확인

- [ ] **Step 1: `cores/archive/archive_db.py` init_db()에 신규 테이블 SQL 추가**

기존 init_db() 내부 `CREATE TABLE IF NOT EXISTS` 구문들 뒤에 아래를 순서대로 추가:

```python
await db.execute("""
    CREATE TABLE IF NOT EXISTS persistent_insights (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id             INTEGER,
        chat_id             INTEGER,
        question            TEXT NOT NULL,
        answer              TEXT NOT NULL,
        key_takeaways       TEXT NOT NULL,
        tools_used          TEXT,
        tickers_mentioned   TEXT,
        evidence_report_ids TEXT,
        embedding           BLOB,
        model_used          TEXT,
        previous_insight_id INTEGER,
        superseded_by       INTEGER,
        created_at          TEXT DEFAULT (datetime('now'))
    )
""")

await db.execute("""
    CREATE INDEX IF NOT EXISTS idx_pi_chat
    ON persistent_insights(chat_id, created_at DESC)
""")

await db.execute("""
    CREATE INDEX IF NOT EXISTS idx_pi_created
    ON persistent_insights(created_at DESC)
""")

await db.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS persistent_insights_fts USING fts5(
        question, key_takeaways,
        content='persistent_insights', content_rowid='id',
        tokenize='unicode61'
    )
""")

# FTS 자동 동기화 트리거
await db.execute("""
    CREATE TRIGGER IF NOT EXISTS persistent_insights_ai
    AFTER INSERT ON persistent_insights BEGIN
        INSERT INTO persistent_insights_fts(rowid, question, key_takeaways)
        VALUES (new.id, new.question, new.key_takeaways);
    END
""")

await db.execute("""
    CREATE TRIGGER IF NOT EXISTS persistent_insights_ad
    AFTER DELETE ON persistent_insights BEGIN
        INSERT INTO persistent_insights_fts(persistent_insights_fts, rowid, question, key_takeaways)
        VALUES ('delete', old.id, old.question, old.key_takeaways);
    END
""")

await db.execute("""
    CREATE TRIGGER IF NOT EXISTS persistent_insights_au
    AFTER UPDATE ON persistent_insights BEGIN
        INSERT INTO persistent_insights_fts(persistent_insights_fts, rowid, question, key_takeaways)
        VALUES ('delete', old.id, old.question, old.key_takeaways);
        INSERT INTO persistent_insights_fts(rowid, question, key_takeaways)
        VALUES (new.id, new.question, new.key_takeaways);
    END
""")

await db.execute("""
    CREATE TABLE IF NOT EXISTS weekly_insight_summary (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start         TEXT NOT NULL UNIQUE,
        week_end           TEXT NOT NULL,
        summary_text       TEXT NOT NULL,
        source_insight_ids TEXT,
        insight_count      INTEGER,
        top_tickers        TEXT,
        created_at         TEXT DEFAULT (datetime('now'))
    )
""")

await db.execute("""
    CREATE INDEX IF NOT EXISTS idx_wis_week
    ON weekly_insight_summary(week_start DESC)
""")

await db.execute("""
    CREATE TABLE IF NOT EXISTS insight_tool_usage (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        insight_id INTEGER NOT NULL,
        tool_name  TEXT NOT NULL,
        call_count INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    )
""")

await db.execute("""
    CREATE INDEX IF NOT EXISTS idx_itu_insight
    ON insight_tool_usage(insight_id)
""")

await db.execute("""
    CREATE TABLE IF NOT EXISTS user_insight_quota (
        user_id INTEGER NOT NULL,
        date    TEXT NOT NULL,
        count   INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, date)
    )
""")

await db.execute("""
    CREATE TABLE IF NOT EXISTS insight_cost_daily (
        date             TEXT PRIMARY KEY,
        input_tokens     INTEGER DEFAULT 0,
        output_tokens    INTEGER DEFAULT 0,
        embedding_tokens INTEGER DEFAULT 0,
        perplexity_calls INTEGER DEFAULT 0,
        firecrawl_calls  INTEGER DEFAULT 0
    )
""")

await db.execute("""
    CREATE VIEW IF NOT EXISTS insight_metrics_daily AS
    SELECT
      DATE(created_at, 'localtime') AS d,
      COUNT(*) AS queries,
      COUNT(DISTINCT user_id) AS unique_users,
      AVG(LENGTH(answer)) AS avg_answer_len
    FROM persistent_insights
    GROUP BY DATE(created_at, 'localtime')
""")
```

- [ ] **Step 2: 마이그레이션 idempotency 확인**

Run:
```bash
rm -f /tmp/test_archive.db
python -c "
import asyncio
from cores.archive.archive_db import init_db
asyncio.run(init_db('/tmp/test_archive.db'))
asyncio.run(init_db('/tmp/test_archive.db'))  # 2회 호출 OK?
print('idempotent OK')
"
```
Expected: `idempotent OK` 출력, 에러 없음

- [ ] **Step 3: 신규 테이블 생성 확인**

```bash
sqlite3 /tmp/test_archive.db ".tables"
```
Expected: `insight_cost_daily insight_tool_usage persistent_insights persistent_insights_fts persistent_insights_fts_config persistent_insights_fts_content persistent_insights_fts_data persistent_insights_fts_docsize persistent_insights_fts_idx user_insight_quota weekly_insight_summary` 외 기존 테이블들 포함

- [ ] **Step 4: Commit**

```bash
git add cores/archive/archive_db.py
git commit -m "feat(archive): add persistent insight + weekly summary + quota tables"
```

---

### Task 2: 임베딩 모듈

**Files:**
- Create: `cores/archive/embedding.py`
- Test: 인라인 self-check (별도 pytest 없음 — 이 프로젝트 관례)

- [ ] **Step 1: embedding.py 생성**

```python
"""
embedding.py — OpenAI 임베딩 생성 + SQLite BLOB 직렬화.

text-embedding-3-small (1536 dim float32) 사용.
BLOB은 numpy float32 배열의 바이트 표현.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def _get_openai_client(api_key: str):
    import openai
    return openai.AsyncOpenAI(api_key=api_key)


async def embed_text(text: str, api_key: str) -> Optional[bytes]:
    """
    텍스트 1건을 임베딩 → float32 BLOB 반환.
    실패 시 None (호출부에서 None 처리하여 embedding=NULL로 저장).
    """
    if not text or not text.strip():
        return None
    try:
        client = _get_openai_client(api_key)
        resp = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text[:8000],   # 모델 input cap
        )
        vec = np.array(resp.data[0].embedding, dtype=np.float32)
        if vec.shape != (EMBEDDING_DIM,):
            logger.warning(f"Unexpected embedding dim: {vec.shape}")
            return None
        return vec.tobytes()
    except Exception as e:
        logger.warning(f"embed_text failed: {e}")
        return None


def decode_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    """BLOB → float32 numpy array."""
    if not blob:
        return None
    try:
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape != (EMBEDDING_DIM,):
            return None
        return vec
    except Exception:
        return None


def cosine(a: bytes, b: bytes) -> float:
    """두 BLOB 사이 cosine similarity. 실패 시 0.0."""
    va = decode_embedding(a)
    vb = decode_embedding(b)
    if va is None or vb is None:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
```

- [ ] **Step 2: 스모크 테스트**

```bash
source venv/bin/activate && python -c "
import asyncio, os
from cores.archive.embedding import embed_text, cosine, decode_embedding
from cores.archive.query_engine import load_api_key

key = load_api_key()
async def main():
    a = await embed_text('삼성전자 장기투자 전망', key)
    b = await embed_text('삼성전자 반도체 실적', key)
    c = await embed_text('AAPL quarterly earnings', key)
    print('similar ko-ko =', cosine(a, b))
    print('cross  ko-en  =', cosine(a, c))

asyncio.run(main())
"
```
Expected: `ko-ko > 0.5`, `ko-en < 0.4` 근처

- [ ] **Step 3: Commit**

```bash
git add cores/archive/embedding.py
git commit -m "feat(archive): add OpenAI embedding utility with BLOB serialization"
```

---

### Task 3: persistent_insights CRUD + 검색

**Files:**
- Create: `cores/archive/persistent_insights.py`

- [ ] **Step 1: 파일 생성 (CRUD + search + quota)**

```python
"""
persistent_insights.py — /insight 대화로 축적되는 영구 인사이트 레이어.

핵심 API:
  save_insight(...)              — 신규 인사이트 저장 (fire-and-forget)
  search_insights(query, limit)  — FTS5 top-50 → 임베딩 재랭킹 top-N
  recent_weekly_summaries(n)     — 최근 n주 요약
  check_and_increment_quota(...) — 일일 쿼터 체크 & 증가
  mark_superseded(...)           — 주간 요약이 커버한 raw 표시
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import numpy as np

from .archive_db import init_db, ARCHIVE_DB_PATH
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
        # tool_usage 기록
        for tool in tools_used:
            await db.execute(
                "INSERT INTO insight_tool_usage (insight_id, tool_name) VALUES (?, ?)",
                (insight_id, tool),
            )
        await db.commit()
        return insight_id


async def fts_candidates(
    query: str,
    limit: int = 50,
    exclude_superseded: bool = True,
    db_path: Optional[str] = None,
) -> List[InsightRow]:
    """FTS5로 후보 추출. 실패 시 빈 리스트."""
    from .archive_db import _sanitize_fts_query
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
    """FTS top-50 → 임베딩 재랭킹 top-limit.

    query_embedding이 없거나 재랭킹 실패 시 FTS 순서 그대로 상위 limit.
    """
    candidates = await fts_candidates(query, limit=50,
                                      exclude_superseded=exclude_superseded,
                                      db_path=db_path)
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
        score = float(np.dot(q_vec, cv) / (q_norm * cn))
        scored.append((score, c))

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
    (allowed, remaining_after_call) 반환.
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
    """주간 요약 잡이 호출. 지정 insight_ids에 superseded_by 세팅."""
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
            (today, input_tokens, output_tokens, embedding_tokens,
             perplexity_calls, firecrawl_calls),
        )
        await db.commit()


async def self_check(db_path: Optional[str] = None) -> Dict[str, Any]:
    """CLI용 헬스체크."""
    await init_db(db_path)
    path = db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM persistent_insights")
        pi_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM weekly_insight_summary")
        ws_count = (await cur.fetchone())[0]
    return {"persistent_insights": pi_count, "weekly_insight_summary": ws_count}


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(self_check()))
```

- [ ] **Step 2: `_sanitize_fts_query` 재사용 가능한지 확인**

```bash
grep -n "_sanitize_fts_query" cores/archive/archive_db.py
```
Expected: 정의 확인 (모듈 레벨 함수)

- [ ] **Step 3: 스모크 테스트 (로컬 archive.db 사용)**

```bash
source venv/bin/activate && python -m cores.archive.persistent_insights
```
Expected: `{'persistent_insights': 0, 'weekly_insight_summary': 0}` (테이블 존재 + 쿼리 성공)

- [ ] **Step 4: save → search round-trip 스모크**

```bash
source venv/bin/activate && python -c "
import asyncio
from cores.archive.persistent_insights import save_insight, fts_candidates

async def main():
    iid = await save_insight(
        user_id=1, chat_id=1,
        question='삼성전자 장기투자 어떤가',
        answer='반도체 사이클 관점에서 긍정적',
        key_takeaways=['반도체 사이클 회복', 'PER 저평가'],
        tools_used=['archive_search'], tickers_mentioned=['005930'],
        evidence_report_ids=[], model_used='gpt-5.4-mini',
    )
    print('saved id =', iid)
    hits = await fts_candidates('삼성전자', limit=5)
    print('fts hits =', len(hits), '/ first q=', hits[0].question if hits else None)

asyncio.run(main())
"
```
Expected: `saved id = <int>`, `fts hits >= 1`, question 매칭

- [ ] **Step 5: Commit**

```bash
git add cores/archive/persistent_insights.py
git commit -m "feat(archive): add persistent_insights CRUD + FTS rerank + quota module"
```

---

## Phase 2 — InsightAgent

### Task 4: 시스템 프롬프트

**Files:**
- Create: `cores/archive/insight_prompts.py`

- [ ] **Step 1: 프롬프트 상수 파일 생성**

```python
"""
insight_prompts.py — InsightAgent 시스템 프롬프트와 structured-output 스키마.
"""

from __future__ import annotations

INSIGHT_SYSTEM_PROMPT = """당신은 PRISM 장기투자 인사이트 엔진입니다.

# 미션
- 사용자 질문에 대해, 먼저 **archive_search_insights**와 **report_retrieve** 도구로
  축적된 지식을 활용해 답변하세요.
- 누적 인사이트에 이미 답이 있으면 외부 도구를 호출하지 마세요.
- 정말 최신 시장 데이터가 필수인 경우에만 외부 도구 사용:
  - 무료: yahoo_finance (US 주가/정보), kospi_kosdaq (KR 주가)
  - 유료(주의): perplexity, firecrawl — 각 도구 전체 대화에서 1회 이하로 제한

# Firecrawl 사용 지침
- 특정 URL이 이미 명확한 경우에만 firecrawl_scrape 사용
- firecrawl_scrape 호출 시 파라미터 필수:
    formats=["markdown"], onlyMainContent=true
- firecrawl_search는 정말 꼭 필요한 경우만 (검색어로 추출이 불가한 정보일 때)

# Perplexity 사용 지침
- 최신 뉴스·이벤트 맥락이 답변에 결정적일 때만 1회 호출

# 응답 형식 (반드시 아래 JSON 구조로)
{
  "answer": "한국어 400~1200자 본문. 합쇼체. 근거 기반으로 명확히.",
  "key_takeaways": ["재사용 가능한 핵심 패턴 1~3개 문장"],
  "tickers_mentioned": ["005930", "AAPL"],
  "tools_used": ["archive_search_insights", "yahoo_finance"],
  "evidence_report_ids": [123, 456]
}

# 스타일
- 종목·지표·기간을 구체적으로 인용
- "추정", "추측"은 명시
- 답변 안에 과장/광고·권유 금지
"""


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer":               {"type": "string"},
        "key_takeaways":        {"type": "array", "items": {"type": "string"}},
        "tickers_mentioned":    {"type": "array", "items": {"type": "string"}},
        "tools_used":           {"type": "array", "items": {"type": "string"}},
        "evidence_report_ids":  {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["answer", "key_takeaways"],
    "additionalProperties": False,
}
```

- [ ] **Step 2: Commit**

```bash
git add cores/archive/insight_prompts.py
git commit -m "feat(archive): add insight agent system prompt with firecrawl guardrails"
```

---

### Task 5: InsightAgent (mcp-agent 래퍼)

**Files:**
- Create: `cores/archive/insight_agent.py`

- [ ] **Step 1: InsightAgent 클래스 작성**

```python
"""
insight_agent.py — /insight 명령을 처리하는 메인 에이전트.

- retrieval: persistent_insights + weekly_summary + report_archive
- synthesis: mcp-agent Agent + AnthropicAugmentedLLM 패턴
  (기존 firecrawl 커맨드와 동일 패턴)
- storage: persistent_insights 자동 저장 (fire-and-forget)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

from . import persistent_insights as pi_store
from .archive_db import ARCHIVE_DB_PATH
from .embedding import embed_text
from .insight_prompts import INSIGHT_SYSTEM_PROMPT
from .query_engine import load_api_key, _MAX_REPORTS_IN_CONTEXT
from .query_engine import QueryEngine

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-mini"


@dataclass
class InsightResult:
    answer: str
    key_takeaways: List[str]
    tickers_mentioned: List[str]
    tools_used: List[str]
    evidence_report_ids: List[int]
    insight_id: Optional[int] = None
    remaining_quota: int = -1
    model_used: str = DEFAULT_MODEL


class InsightAgent:
    def __init__(self, model: str = DEFAULT_MODEL, db_path: Optional[str] = None):
        self.model = model
        self.db_path = db_path or str(ARCHIVE_DB_PATH)
        self._api_key: Optional[str] = None

    async def _build_retrieval_context(self, question: str) -> Dict[str, Any]:
        api_key = self._api_key or load_api_key()
        self._api_key = api_key
        q_emb = await embed_text(question, api_key) if api_key else None

        # 병렬로 3-tier retrieval
        insights_task = pi_store.search_insights(
            question, q_emb, limit=5, exclude_superseded=True, db_path=self.db_path,
        )
        weekly_task = pi_store.recent_weekly_summaries(weeks=4, db_path=self.db_path)
        # 기존 archive 리포트 검색
        engine = QueryEngine(db_path=self.db_path, model=self.model)
        reports_task = engine.retrieve(
            text=question, market=None, ticker=None,
            date_from=None, date_to=None,
        )
        insights, weekly, reports = await asyncio.gather(
            insights_task, weekly_task, reports_task,
            return_exceptions=True,
        )
        if isinstance(insights, Exception):
            logger.warning(f"insight retrieval failed: {insights}")
            insights = []
        if isinstance(weekly, Exception):
            weekly = []
        if isinstance(reports, Exception):
            reports = []
        return {
            "insights": insights,
            "weekly": weekly,
            "reports": reports[:_MAX_REPORTS_IN_CONTEXT] if reports else [],
            "q_emb": q_emb,
        }

    def _format_context(self, ctx: Dict[str, Any]) -> str:
        parts = []
        if ctx["weekly"]:
            parts.append("## 최근 주간 인사이트 요약")
            for w in ctx["weekly"]:
                parts.append(f"- ({w['week_start']}~{w['week_end']}) "
                             f"건수={w.get('insight_count')} 주요종목={w.get('top_tickers')}")
                parts.append(f"  {w['summary_text'][:600]}")
        if ctx["insights"]:
            parts.append("\n## 누적 인사이트 (top-5)")
            for i, ins in enumerate(ctx["insights"], 1):
                parts.append(f"{i}. [{ins.created_at[:10]}] Q: {ins.question[:120]}")
                takeaways = " | ".join(ins.key_takeaways[:3])
                parts.append(f"   takeaways: {takeaways}")
                parts.append(f"   ticker={ins.tickers_mentioned} evidence={ins.evidence_report_ids}")
        if ctx["reports"]:
            parts.append("\n## 관련 분석 리포트 (archive)")
            for r in ctx["reports"]:
                parts.append(
                    f"- [{r.report_date}] {r.ticker} {r.company_name} ({r.market.upper()})"
                )
                excerpt = (r.content_excerpt or "")[:400].replace("\n", " ")
                parts.append(f"  {excerpt}")
        return "\n".join(parts) if parts else "(관련 컨텍스트 없음)"

    async def run(
        self,
        question: str,
        user_id: int,
        chat_id: int,
        daily_limit: int = 20,
        previous_insight_id: Optional[int] = None,
    ) -> InsightResult:
        # 쿼터
        allowed, remaining = await pi_store.check_and_increment_quota(
            user_id, daily_limit, db_path=self.db_path,
        )
        if not allowed:
            return InsightResult(
                answer="일일 `/insight` 호출 한도를 초과했습니다. 자정(KST) 이후 초기화됩니다.",
                key_takeaways=[], tickers_mentioned=[], tools_used=[],
                evidence_report_ids=[], remaining_quota=0, model_used=self.model,
            )

        # Retrieval
        ctx = await self._build_retrieval_context(question)
        context_str = self._format_context(ctx)

        # mcp-agent 호출 (기존 firecrawl 패턴 참고)
        try:
            # 무료 + 유료 MCP 서버 모두 연결
            agent = Agent(
                name="insight_agent",
                instruction=INSIGHT_SYSTEM_PROMPT,
                server_names=["perplexity", "kospi_kosdaq", "yahoo_finance", "firecrawl"],
            )
            async with agent:
                llm = await agent.attach_llm(OpenAIAugmentedLLM)
                user_msg = (
                    f"## 사용자 질문\n{question}\n\n"
                    f"## 컨텍스트\n{context_str}\n\n"
                    f"위 JSON 형식 그대로만 답하세요."
                )
                response_text = await llm.generate_str(
                    message=user_msg,
                    request_params=RequestParams(
                        model=self.model,
                        reasoning_effort="none",
                        maxTokens=4000,
                    ),
                )
        except Exception as e:
            logger.error(f"InsightAgent run failed: {e}", exc_info=True)
            # Fallback: retrieval 원문만 반환
            fallback = (
                "[인사이트 엔진 오류] 관련 컨텍스트만 전달합니다:\n\n"
                + context_str[:3000]
            )
            return InsightResult(
                answer=fallback, key_takeaways=[], tickers_mentioned=[],
                tools_used=[], evidence_report_ids=[],
                remaining_quota=remaining, model_used=self.model,
            )

        # JSON 파싱
        parsed = self._parse_response(response_text)

        # 임베딩 생성 (key_takeaways 기반)
        api_key = self._api_key or load_api_key()
        takeaway_text = " \n".join(parsed["key_takeaways"]) or parsed["answer"][:500]
        emb_blob = await embed_text(takeaway_text, api_key) if api_key else None

        # 저장 (fire-and-forget 실패해도 응답 OK)
        insight_id = None
        try:
            insight_id = await pi_store.save_insight(
                user_id=user_id, chat_id=chat_id,
                question=question, answer=parsed["answer"],
                key_takeaways=parsed["key_takeaways"],
                tools_used=parsed["tools_used"],
                tickers_mentioned=parsed["tickers_mentioned"],
                evidence_report_ids=parsed["evidence_report_ids"],
                model_used=self.model, embedding=emb_blob,
                previous_insight_id=previous_insight_id,
                db_path=self.db_path,
            )
        except Exception as e:
            logger.error(f"save_insight failed: {e}", exc_info=True)

        return InsightResult(
            answer=parsed["answer"],
            key_takeaways=parsed["key_takeaways"],
            tickers_mentioned=parsed["tickers_mentioned"],
            tools_used=parsed["tools_used"],
            evidence_report_ids=parsed["evidence_report_ids"],
            insight_id=insight_id,
            remaining_quota=remaining,
            model_used=self.model,
        )

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        """LLM 응답에서 JSON 추출. 실패 시 answer=raw로 fallback."""
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                return {
                    "answer": str(obj.get("answer") or raw[:1500]),
                    "key_takeaways": [str(x) for x in obj.get("key_takeaways", [])][:5],
                    "tickers_mentioned": [str(x) for x in obj.get("tickers_mentioned", [])][:10],
                    "tools_used": [str(x) for x in obj.get("tools_used", [])][:10],
                    "evidence_report_ids": [
                        int(x) for x in obj.get("evidence_report_ids", []) if str(x).isdigit()
                    ][:10],
                }
            except Exception:
                pass
        logger.warning("InsightAgent: failed to parse JSON response, fallback to raw")
        return {
            "answer": raw[:1500],
            "key_takeaways": [raw[:200]],
            "tickers_mentioned": [],
            "tools_used": [],
            "evidence_report_ids": [],
        }
```

- [ ] **Step 2: MCPApp 초기화 확인**

에이전트가 mcp-agent 패턴으로 실행되려면 전역 `MCPApp`이 필요. 기존 firecrawl 래퍼 확인:

```bash
grep -n "MCPApp\|mcp_app_instance\|_global_mcp_app" cores/ -r --include="*.py" | head -20
```

확인 결과를 바탕으로, 기존 전역 app을 재사용하거나, `async with mcp_app.run()` 컨텍스트 안에서 실행. 이미 `generate_firecrawl_followup_response`가 동일 문제 해결했으므로 그 패턴 복제.

- [ ] **Step 3: 스모크 — 키 없으면 skip**

```bash
source venv/bin/activate && python -c "
import asyncio
from cores.archive.insight_agent import InsightAgent

async def main():
    a = InsightAgent()
    r = await a.run('삼성전자 장기투자 어떤가', user_id=1, chat_id=-1)
    print('answer:', r.answer[:200])
    print('takeaways:', r.key_takeaways)
    print('tools:', r.tools_used)
    print('insight_id:', r.insight_id)

asyncio.run(main())
" 2>&1 | tail -20
```
Expected: answer 출력, insight_id != None, 관련 테이블에 행 추가

- [ ] **Step 4: Commit**

```bash
git add cores/archive/insight_agent.py
git commit -m "feat(archive): add InsightAgent with retrieval + mcp-agent function calling"
```

---

### Task 6: `/insight_agent` API 엔드포인트

**Files:**
- Modify: `archive_api.py`

- [ ] **Step 1: Request/Response 모델 추가**

`archive_api.py`의 기존 QueryRequest/QueryResponse 정의 뒤에 추가:

```python
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
```

- [ ] **Step 2: 엔드포인트 추가**

기존 `/query` 엔드포인트 정의 뒤에 추가:

```python
@app.post("/insight_agent", response_model=InsightAgentResponse)
async def insight_agent_endpoint(
    req: InsightAgentRequest, _key: str = Depends(_verify_key),
):
    if not req.question or len(req.question.strip()) < 2:
        raise HTTPException(status_code=400, detail="question is required")
    if len(req.question) > 2000:
        raise HTTPException(status_code=400, detail="question too long (>2000 chars)")
    try:
        from cores.archive.insight_agent import InsightAgent
        agent = InsightAgent()
        result = await agent.run(
            question=req.question.strip(),
            user_id=req.user_id, chat_id=req.chat_id,
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
    except Exception as e:
        logger.error(f"/insight_agent error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 3: py_compile 확인**

```bash
python -m py_compile archive_api.py && echo OK
```

- [ ] **Step 4: Commit**

```bash
git add archive_api.py
git commit -m "feat(archive): add /insight_agent endpoint for telegram insight command"
```

---

## Phase 3 — Telegram 통합

### Task 7: InsightConversationContext + Reply 핸들러

**Files:**
- Modify: `telegram_ai_bot.py`

- [ ] **Step 1: `FirecrawlConversationContext` 클래스 정의 위치 확인**

```bash
grep -n "class FirecrawlConversationContext" telegram_ai_bot.py
```
이 클래스 정의 바로 아래에 `InsightConversationContext` 추가.

- [ ] **Step 2: `InsightConversationContext` 추가**

```python
class InsightConversationContext:
    """/insight 멀티턴용 컨텍스트. Firecrawl 패턴 미러링."""

    def __init__(self, original_question: str, user_id: int, chat_id: int):
        self.original_question = original_question
        self.user_id = user_id
        self.chat_id = chat_id
        self.last_insight_id: Optional[int] = None
        self.conversation_history: list = []
        self.created_at = datetime.now()

    def is_expired(self, ttl_minutes: int = 30) -> bool:
        return (datetime.now() - self.created_at).total_seconds() > ttl_minutes * 60

    def add_turn(self, user_q: str, bot_a: str, insight_id: Optional[int]):
        self.conversation_history.append({"q": user_q, "a": bot_a, "iid": insight_id})
        if insight_id is not None:
            self.last_insight_id = insight_id
```

- [ ] **Step 3: 봇 __init__에 딕셔너리 등록**

`FirecrawlConversationContext` 딕셔너리 초기화 라인 바로 아래:

```python
self.insight_contexts: Dict[int, InsightConversationContext] = {}  # key: bot message_id
```

- [ ] **Step 4: Reply 핸들러 추가**

`_handle_firecrawl_reply` 함수 바로 아래에 `_handle_insight_reply` 추가:

```python
async def _handle_insight_reply(self, update: Update, ic: InsightConversationContext):
    """/insight 답변에 Reply로 후속 질문. /evaluate/Firecrawl 패턴 미러링."""
    user_question = update.message.text.strip()
    if len(user_question) > 2000:
        await update.message.reply_text("질문은 2000자 이내로 입력해주세요.")
        return
    chat_id = update.effective_chat.id
    waiting = await update.message.reply_text("🧭 누적 인사이트 + 시장 데이터 결합 중…")
    try:
        payload = await self._call_insight_agent(
            question=user_question,
            user_id=update.effective_user.id,
            chat_id=chat_id,
            previous_insight_id=ic.last_insight_id,
        )
        try:
            await waiting.delete()
        except Exception:
            pass
        if not payload:
            await update.message.reply_text(
                "⚠️ 인사이트 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            )
            return
        sent = await update.message.reply_text(payload.answer)
        ic.add_turn(user_question, payload.answer, payload.insight_id)
        # 새 message_id도 동일 컨텍스트로 매핑 (계속 reply 가능)
        self.insight_contexts[sent.message_id] = ic
    except Exception as e:
        logger.error(f"_handle_insight_reply error: {e}", exc_info=True)
        try:
            await waiting.delete()
        except Exception:
            pass
        await update.message.reply_text(f"⚠️ 오류: {type(e).__name__}")
```

- [ ] **Step 5: 메시지 핸들러 라우터에 등록**

기존 reply 라우터 (`firecrawl_contexts` 검사 위치):

```python
# 기존:
if replied_to_msg_id in self.firecrawl_contexts:
    fc_ctx = self.firecrawl_contexts[replied_to_msg_id]
    ...

# 이어서 추가:
elif replied_to_msg_id in self.insight_contexts:
    ic = self.insight_contexts[replied_to_msg_id]
    if ic.is_expired():
        await update.message.reply_text(
            "이전 /insight 세션이 만료되었습니다. /insight로 새로 시작해주세요."
        )
        del self.insight_contexts[replied_to_msg_id]
        return
    await self._handle_insight_reply(update, ic)
    return
```

- [ ] **Step 6: Commit**

```bash
git add telegram_ai_bot.py
git commit -m "feat(telegram): add InsightConversationContext + reply-based multiturn"
```

---

### Task 8: `/insight` 핸들러 교체 + `_call_insight_agent`

**Files:**
- Modify: `telegram_ai_bot.py`

- [ ] **Step 1: `_call_insight_agent` 메서드 추가**

기존 `_call_archive_query` 바로 아래:

```python
@dataclass
class InsightPayload:
    answer: str
    insight_id: Optional[int]
    remaining_quota: int
    tickers_mentioned: list
    tools_used: list


async def _call_insight_agent(
    self, question: str, user_id: int, chat_id: int,
    previous_insight_id: Optional[int] = None,
) -> Optional[InsightPayload]:
    api_url = os.getenv("ARCHIVE_API_URL", "").rstrip("/")
    api_key = os.getenv("ARCHIVE_API_KEY", "")
    daily_limit = int(os.getenv("INSIGHT_DAILY_LIMIT", "20"))

    if api_url:
        import aiohttp
        payload = {
            "question": question, "user_id": user_id, "chat_id": chat_id,
            "daily_limit": daily_limit, "previous_insight_id": previous_insight_id,
        }
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{api_url}/insight_agent",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"/insight_agent {resp.status}: {await resp.text()}")
                        return None
                    data = await resp.json()
                    return InsightPayload(
                        answer=data["answer"],
                        insight_id=data.get("insight_id"),
                        remaining_quota=data.get("remaining_quota", -1),
                        tickers_mentioned=data.get("tickers_mentioned", []),
                        tools_used=data.get("tools_used", []),
                    )
        except Exception as e:
            logger.error(f"_call_insight_agent HTTP error: {e}", exc_info=True)
            return None
    else:
        try:
            from cores.archive.insight_agent import InsightAgent
            agent = InsightAgent()
            r = await agent.run(
                question=question, user_id=user_id, chat_id=chat_id,
                daily_limit=daily_limit,
                previous_insight_id=previous_insight_id,
            )
            return InsightPayload(
                answer=r.answer, insight_id=r.insight_id,
                remaining_quota=r.remaining_quota,
                tickers_mentioned=r.tickers_mentioned,
                tools_used=r.tools_used,
            )
        except Exception as e:
            logger.error(f"Local insight agent failed: {e}", exc_info=True)
            return None
```

`InsightPayload` dataclass는 파일 상단 import 블록 근처에 추가 (기존 dataclass들 옆).

- [ ] **Step 2: 기존 `handle_insight_query` 교체**

`handle_insight_query` 함수 본문을 아래로 대체:

```python
async def handle_insight_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = update.message.text.strip()
    if len(question) > 2000:
        await update.message.reply_text("질문은 2000자 이내로 입력해주세요.")
        return ConversationHandler.END
    logger.info(f"/insight - user={update.effective_user.id}, q='{question[:60]}'")
    chat_id = update.effective_chat.id
    waiting = await update.message.reply_text(
        "🧭 누적 인사이트 + 시장 데이터로 답변 생성 중…"
    )
    try:
        payload = await self._call_insight_agent(
            question=question,
            user_id=update.effective_user.id,
            chat_id=chat_id,
        )
        try:
            await waiting.delete()
        except Exception:
            pass
        if not payload:
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="⚠️ 인사이트 조회에 실패했습니다. 잠시 후 다시 시도해주세요."
            )
            return ConversationHandler.END

        sent = await self.application.bot.send_message(
            chat_id=chat_id, text=payload.answer
        )
        # 멀티턴 컨텍스트 등록
        ic = InsightConversationContext(
            original_question=question,
            user_id=update.effective_user.id, chat_id=chat_id,
        )
        ic.add_turn(question, payload.answer, payload.insight_id)
        self.insight_contexts[sent.message_id] = ic
    except Exception as e:
        logger.error(f"/insight error: {e}", exc_info=True)
        try:
            await waiting.delete()
        except Exception:
            pass
        await self.application.bot.send_message(
            chat_id=chat_id, text=f"⚠️ 오류: {type(e).__name__}"
        )
    return ConversationHandler.END
```

- [ ] **Step 3: py_compile**

```bash
python -m py_compile telegram_ai_bot.py && echo OK
```

- [ ] **Step 4: Commit**

```bash
git add telegram_ai_bot.py
git commit -m "feat(telegram): route /insight through InsightAgent with multiturn support"
```

---

### Task 9: BotFather 명령어 메뉴 동기화

**Files:**
- Modify: `telegram_ai_bot.py`

- [ ] **Step 1: `_register_bot_commands` 메서드 추가**

`TelegramAIBot` 클래스 안, 기존 큰 핸들러 메서드들 아래:

```python
async def _register_bot_commands(self):
    """봇 기동 시 BotFather 명령어 메뉴 동기화."""
    from telegram import BotCommand
    commands = [
        BotCommand("evaluate",     "보유 종목 평가 시작"),
        BotCommand("us_evaluate",  "미국 주식 보유 종목 평가"),
        BotCommand("report",       "국내 종목 리포트"),
        BotCommand("us_report",    "미국 종목 리포트"),
        BotCommand("history",      "분석 히스토리 조회"),
        BotCommand("signal",       "국내 시장 시그널 분석"),
        BotCommand("us_signal",    "미국 시장 시그널 분석"),
        BotCommand("theme",        "국내 테마 진단"),
        BotCommand("us_theme",     "미국 테마 진단"),
        BotCommand("ask",          "자유 질문"),
        BotCommand("insight",      "PRISM 아카이브 기반 장기 인사이트"),
        BotCommand("help",         "도움말"),
    ]
    try:
        await self.application.bot.set_my_commands(commands)
        logger.info(f"Registered {len(commands)} bot commands via BotFather API")
    except Exception as e:
        logger.warning(f"set_my_commands failed: {e}")
```

- [ ] **Step 2: Application.post_init 훅 연결**

봇 애플리케이션 빌드 후 단계에 추가:

```bash
grep -n "Application.builder\|ApplicationBuilder\|build()" telegram_ai_bot.py | head
```

찾은 위치 근처에 연결:
```python
self.application.post_init = lambda app: self._register_bot_commands()
```

- [ ] **Step 3: 실제 명령어 목록 검증**

현재 봇에 등록된 CommandHandler 기준으로 위 목록이 실제와 일치하는지 확인:
```bash
grep -n "CommandHandler(\"" telegram_ai_bot.py | head -30
```

목록에 없는 명령어는 추가, 더 이상 안 쓰는 건 제거.

- [ ] **Step 4: Commit**

```bash
git add telegram_ai_bot.py
git commit -m "feat(telegram): sync command menu with BotFather on bot startup"
```

---

## Phase 4 — 백그라운드 잡

### Task 10: 주간 요약 확장

**Files:**
- Modify: `cores/archive/auto_insight.py`

- [ ] **Step 1: 주간 persistent_insights 압축 함수 추가**

`auto_insight.py` 파일 끝 쪽, CLI 진입점 위에 추가:

```python
async def compress_weekly_insights(
    self, week_start: str, week_end: str,
) -> Optional[int]:
    """
    주어진 주간(week_start ~ week_end)의 persistent_insights를
    요약해서 weekly_insight_summary에 1건 추가 + superseded_by 업데이트.
    Returns summary_id or None (skip 시).
    """
    import json
    import aiosqlite
    from .persistent_insights import mark_superseded
    from .archive_db import ARCHIVE_DB_PATH

    path = self.db_path or str(ARCHIVE_DB_PATH)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id, question, key_takeaways, tickers_mentioned
            FROM persistent_insights
            WHERE superseded_by IS NULL
              AND DATE(created_at) >= ? AND DATE(created_at) <= ?
            ORDER BY id ASC
            """,
            (week_start, week_end),
        )
        rows = await cur.fetchall()

    if not rows or len(rows) < 6:
        logger.info(f"weekly compression skip: {len(rows)} rows in {week_start}~{week_end}")
        return None

    # top_tickers 집계
    tick_counts: Dict[str, int] = {}
    for r in rows:
        for t in json.loads(r["tickers_mentioned"] or "[]"):
            tick_counts[t] = tick_counts.get(t, 0) + 1
    top_tickers = sorted(tick_counts.items(), key=lambda x: -x[1])[:5]

    # LLM 압축
    api_key = load_api_key()
    if not api_key:
        logger.warning("weekly compression skipped: no API key")
        return None
    content_lines = []
    for r in rows:
        tk = json.loads(r["key_takeaways"] or "[]")
        content_lines.append(f"- Q: {r['question'][:80]} | takeaways: {' | '.join(tk[:2])}")
    weekly_input = "\n".join(content_lines)

    system_prompt = (
        "PRISM 장기투자 인사이트 축적 시스템의 주간 압축 엔진입니다. "
        "지난 주 Q&A 요약 목록에서 재사용 가능한 핵심 패턴만 5~10개 bullet로 정리하세요. "
        "개별 질문이 아닌 '공통 패턴' 중심. 한국어 합쇼체."
    )
    try:
        from .query_engine import _get_openai_client
        client = _get_openai_client(api_key)
        resp = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"## 주간 Q&A 목록\n{weekly_input}"},
            ],
            max_completion_tokens=1500,
            reasoning_effort="none",
            temperature=0.3,
        )
        summary_text = resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"weekly compression LLM failed: {e}")
        return None

    # INSERT
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO weekly_insight_summary
                (week_start, week_end, summary_text, source_insight_ids,
                 insight_count, top_tickers)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                week_start, week_end, summary_text,
                json.dumps([r["id"] for r in rows]),
                len(rows),
                json.dumps([{"ticker": t, "count": c} for t, c in top_tickers]),
            ),
        )
        await db.commit()
        summary_id = cur.lastrowid

    await mark_superseded([r["id"] for r in rows], summary_id, db_path=path)
    logger.info(f"weekly compression done: {len(rows)} insights → summary_id={summary_id}")
    return summary_id
```

- [ ] **Step 2: weekly_summary 진입점에서 자동 호출**

기존 `weekly_summary()` 끝부분 또는 `generate_all()` 끝에서:

```python
# 이번 주 월요일 - 1주, 지난 일요일 구간으로 압축
from datetime import datetime, timedelta
today = datetime.now()
last_sunday = today - timedelta(days=today.weekday() + 1)
last_monday = last_sunday - timedelta(days=6)
await self.compress_weekly_insights(
    week_start=last_monday.strftime("%Y-%m-%d"),
    week_end=last_sunday.strftime("%Y-%m-%d"),
)
```

- [ ] **Step 3: py_compile**

```bash
python -m py_compile cores/archive/auto_insight.py && echo OK
```

- [ ] **Step 4: 드라이런 (로컬 DB, 건수 부족 예상 → skip 로그)**

```bash
source venv/bin/activate && python -c "
import asyncio
from cores.archive.auto_insight import AutoInsight
async def main():
    ai = AutoInsight()
    r = await ai.compress_weekly_insights('2026-04-14', '2026-04-20')
    print('result =', r)
asyncio.run(main())
"
```
Expected: `result = None` + skip 로그 (정상)

- [ ] **Step 5: Commit**

```bash
git add cores/archive/auto_insight.py
git commit -m "feat(archive): add weekly compression for persistent_insights"
```

---

## Phase 5 — CLI + 문서

### Task 11: `archive_query.py` 인사이트 통계

**Files:**
- Modify: `archive_query.py`

- [ ] **Step 1: `--insight-stats` CLI 옵션 추가**

기존 argparse 뒤:

```python
p.add_argument(
    "--insight-stats", action="store_true",
    help="persistent_insights 집계 출력 후 종료",
)
```

- [ ] **Step 2: 핸들러 분기**

main() 진입부에 args.insight_stats 분기:

```python
if args.insight_stats:
    import asyncio, aiosqlite
    from cores.archive.archive_db import ARCHIVE_DB_PATH
    async def _stats():
        async with aiosqlite.connect(str(ARCHIVE_DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT COUNT(*) AS c FROM persistent_insights")
            total = (await cur.fetchone())["c"]
            cur = await db.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM persistent_insights"
            )
            uniq = (await cur.fetchone())["c"]
            cur = await db.execute(
                "SELECT tool_name, COUNT(*) AS c FROM insight_tool_usage "
                "GROUP BY tool_name ORDER BY c DESC"
            )
            tools = [dict(r) for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT * FROM insight_cost_daily ORDER BY date DESC LIMIT 7"
            )
            costs = [dict(r) for r in await cur.fetchall()]
            print(f"총 인사이트: {total}")
            print(f"고유 사용자: {uniq}")
            print(f"도구 사용 분포: {tools}")
            print(f"최근 7일 비용: {costs}")
    asyncio.run(_stats())
    return
```

- [ ] **Step 3: 실행 확인**

```bash
source venv/bin/activate && python archive_query.py --insight-stats
```
Expected: 각 값 출력 (아직 0건이어도 정상)

- [ ] **Step 4: Commit**

```bash
git add archive_query.py
git commit -m "feat(archive): add --insight-stats CLI for insight metrics"
```

---

### Task 12: 문서 업데이트

**Files:**
- Modify: `docs/archive/ARCHIVE_DEPLOY_GUIDE.md`
- Modify: `docs/archive/ARCHIVE_API_SETUP.md`

- [ ] **Step 1: `ARCHIVE_DEPLOY_GUIDE.md` Step 2 뒤에 "Step 2.5 /insight 마이그레이션" 추가**

```markdown
## Step 2.5. 인사이트 테이블 마이그레이션 (자동)

archive_db.init_db()가 idempotent이므로 아래 한 번 호출하면 신규 5 테이블 + FTS + 트리거가 생성됩니다.

```bash
python -c "import asyncio; from cores.archive.archive_db import init_db; asyncio.run(init_db())"
```

확인:
```bash
sqlite3 archive.db ".tables"
# 다음이 포함되어야 함: persistent_insights, persistent_insights_fts, weekly_insight_summary,
#                       insight_tool_usage, user_insight_quota, insight_cost_daily
```
```

- [ ] **Step 2: Step 9 봇 재시작 주의사항 업데이트**

```markdown
## Step 9. 텔레그램 봇 재시작

봇 프로세스 재시작. 기동 시:
- `set_my_commands` 자동 호출 → BotFather 메뉴에 `/insight` 노출
- `ARCHIVE_API_URL` 설정 시 양서버 모드, 미설정 시 단일서버 모드
```

- [ ] **Step 3: `ARCHIVE_API_SETUP.md` 엔드포인트 목록에 `/insight_agent` 추가**

기존 엔드포인트 표:

```markdown
| `POST` | `/insight_agent` | 누적 인사이트 + 외부 도구 조합 장기 인사이트 엔진 | Bearer |
```

- [ ] **Step 4: 환경변수 설명 추가**

`ARCHIVE_DEPLOY_GUIDE.md` 또는 `ARCHIVE_API_SETUP.md`에:

```markdown
## 신규 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `INSIGHT_DAILY_LIMIT` | `20` | 사용자당 일일 `/insight` 호출 제한 (KST 자정 리셋) |
| `ARCHIVE_API_URL` | (없음) | 설정 시 two-server 모드, 미설정 시 single-server 모드 |
| `ARCHIVE_API_KEY` | (없음) | 양 서버 공통 Bearer 토큰 |
```

- [ ] **Step 5: Commit**

```bash
git add docs/archive/ARCHIVE_DEPLOY_GUIDE.md docs/archive/ARCHIVE_API_SETUP.md
git commit -m "docs(archive): add persistent insight deployment + endpoint docs"
```

---

## Phase 6 — 배포

### Task 13: PR 업데이트

**Files:**
- PR #262 body

- [ ] **Step 1: 푸시**

```bash
git push origin feat/archive-insight-query-system
```

- [ ] **Step 2: PR 바디 업데이트**

```bash
gh pr view 262 --json body -q .body > /tmp/pr262_body.txt
# 수동 보강 후:
gh pr edit 262 --body-file /tmp/pr262_body.new
```

PR 바디에 "Persistent Insight Agent" 섹션 추가 (파일 수, 스펙 링크, 테이블 리스트, UX 설명).

---

### Task 14: DB 서버 배포 (SSH)

- [ ] **Step 1: db-server 접속 (사용자에게 확인 요청)**

```bash
bash ~/Downloads/vultr_ssh/db-server.sh
```

- [ ] **Step 2: 원격에서 pull + migrate**

```
cd /root/prism-insight
git pull origin feat/archive-insight-query-system
source venv/bin/activate
pip install -r requirements.txt
python -c "import asyncio; from cores.archive.archive_db import init_db; asyncio.run(init_db())"
sqlite3 archive.db ".tables" | tr ' ' '\n' | grep -i insight
```

- [ ] **Step 3: .env에 ARCHIVE_API_KEY 추가 (없으면)**

```bash
grep -q ARCHIVE_API_KEY .env || echo "ARCHIVE_API_KEY=$(openssl rand -hex 32)" >> .env
grep -q ARCHIVE_API_HOST .env || echo "ARCHIVE_API_HOST=127.0.0.1" >> .env
grep -q ARCHIVE_API_PORT .env || echo "ARCHIVE_API_PORT=8765" >> .env
```

- [ ] **Step 4: archive_api 실행 (systemd 또는 nohup)**

```bash
# systemd 권장, 없으면:
nohup python archive_api.py >> logs/archive_api.log 2>&1 &
disown
```

- [ ] **Step 5: 헬스체크**

```bash
curl -s http://127.0.0.1:8765/health
```
Expected: `{"status":"ok","archive_db":true,...}`

---

### Task 15: APP 서버 배포 + SSH 터널

- [ ] **Step 1: app-server 접속**

```bash
bash ~/Downloads/vultr_ssh/app-server.sh
su - prism
cd ~/prism-insight
```

- [ ] **Step 2: 코드 업데이트 + 의존성**

```
git pull origin feat/archive-insight-query-system
source venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 3: SSH 터널 서비스 등록**

```bash
sudo tee /etc/systemd/system/archive-tunnel.service <<'EOF'
[Unit]
Description=SSH tunnel to archive-api on db-server
After=network-online.target

[Service]
User=prism
Environment="AUTOSSH_GATETIME=0"
ExecStart=/usr/bin/autossh -M 0 -N \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
  -L 127.0.0.1:8765:127.0.0.1:8765 \
  root@<DB_SERVER_IP>
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now archive-tunnel
sudo systemctl status archive-tunnel --no-pager
```

- [ ] **Step 4: .env에 ARCHIVE_API_URL / KEY 추가**

```bash
grep -q ARCHIVE_API_URL .env || echo "ARCHIVE_API_URL=http://127.0.0.1:8765" >> .env
# ARCHIVE_API_KEY 는 db-server와 동일 값
```

- [ ] **Step 5: 봇 프로세스 재시작**

```bash
pgrep -af telegram_ai_bot | head
pkill -f telegram_ai_bot
sleep 2
nohup python telegram_ai_bot.py >> logs/telegram_ai_bot.log 2>&1 &
disown
```

- [ ] **Step 6: BotFather 명령어 메뉴 검증**

텔레그램에서 봇 대화창 → `/` 입력 → `/insight` 항목 노출 확인.

---

### Task 16: 스모크 QA

- [ ] **Step 1: `/insight` 명령어**

텔레그램에서 봇에게 `/insight` → "질문을 입력해주세요" 응답 확인.

- [ ] **Step 2: 질문 입력**

"삼성전자 장기투자 적합한가?" → answer + key_takeaways 응답 확인.

- [ ] **Step 3: Reply 후속 질문**

봇 답변에 Reply → "공매도 리스크는 어떤가?" → 컨텍스트 유지된 후속 답변 확인.

- [ ] **Step 4: DB 확인 (db-server)**

```bash
sqlite3 archive.db "SELECT id, question, length(answer), insight_tool_usage.tool_name \
  FROM persistent_insights LEFT JOIN insight_tool_usage \
  ON persistent_insights.id = insight_tool_usage.insight_id ORDER BY id DESC LIMIT 5"
```

- [ ] **Step 5: 인사이트 통계**

```bash
python archive_query.py --insight-stats
```

- [ ] **Step 6: 쿼터 초과 시뮬레이션** (선택)

`INSIGHT_DAILY_LIMIT=2` 설정 후 3회 호출 → 3회째 거부 메시지 확인.

---

## Self-Review Checklist

- [x] 스펙의 모든 요구사항 구현 태스크 존재 (5 테이블, 에이전트, UX, 주간 요약, 도구 가드레일, BotFather 동기화, 배포)
- [x] TDD 대신 smoke/integration 테스트 중심 (안정성 요구 사항 반영)
- [x] 각 Task 끝마다 commit step
- [x] 실제 코드 + 실제 커맨드 포함 (플레이스홀더 없음)
- [x] 서버 배포 절차 명시 (db-server → app-server 순)
- [x] 환경변수 3종 (ARCHIVE_API_URL/KEY, INSIGHT_DAILY_LIMIT) 문서화
- [x] 롤백 경로: 기존 `/query` 유지, 단일서버 모드 여전히 동작

### 추가 안전장치

- 프로덕션 DB는 `archive.db` 파일 하나로 마이그레이션 전 **scp 백업** 권장 (배포 전 스텝에 추가 고려)
- 첫 주 동안 `INSIGHT_DAILY_LIMIT=5` 보수적으로 시작, 문제 없으면 상향

---

## 참고 자료

- 스펙: `docs/superpowers/specs/2026-04-21-persistent-insight-agent-design.md`
- 기존 패턴: `cores/report_generation.py` (mcp-agent + gpt-5.4-mini), `telegram_ai_bot.py::_handle_firecrawl_reply` (Reply 멀티턴)
- OpenAI: `text-embedding-3-small` 1536-dim, $0.02/1M tokens
- MCP 서버: yahoo_finance/perplexity/kospi_kosdaq/firecrawl은 `mcp_agent.config.yaml` 기등록
