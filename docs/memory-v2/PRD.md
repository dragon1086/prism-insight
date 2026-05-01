# Memory V2 — Semantic Recall + Async Fact Extraction PRD

**Status**: Draft
**Owner**: cokac-bot
**Date**: 2026-05-01
**Target**: prism-insight `/evaluate`, `/us_evaluate`, `/journal` (and `/ask` follow-up)
**Branch**: `feat/memory-v2-semantic-recall`

---

## 1. Problem

The current `tracking/user_memory.py` (UserMemoryManager) is a 2024-era SQLite log. Concretely:

| Aspect | Current | Issue |
|---|---|---|
| Retrieval | `ORDER BY created_at DESC LIMIT N` + ticker `LIKE` | No semantic recall. "내가 단타로 후회했던 종목" → cannot find. |
| Compression | `text[:150]` truncate; Layer 3 = `text[:50]` | 30-day-old memory becomes meaningless. Comment in code says *"Could use LLM in practice, but using rule-based to save costs"*. |
| Facts | None — only raw blobs in `content` JSON | No "user dislikes PER>30", no "burned on 2차 매수 2025-Q4". |
| Profile | `user_preferences` set manually | `preferred_tone`/`investment_style` never auto-derived from behavior. |
| Tagging | `tags` column exists but never populated | Dead schema. |
| Conflict | None | New journal contradicting old → both stored as equal-weight noise. |
| Conversation continuity | `conversation_contexts` is in-memory dict, lost on restart | `/evaluate` follow-up after restart loses thread. |

This produces visible bot behavior: by week 3, `/evaluate AAPL` repeats the same intro every time, ignores that the user already heard the dividend story, and cannot reference the user's actual loss diary on AAPL.

---

## 2. Goals

**Primary**: When a user runs `/evaluate <ticker>`, the response demonstrably uses (a) the user's prior journals on that ticker, (b) extracted facts about the user's investing style, and (c) recent conversation continuity — **without** changing the answer model (still Claude Sonnet 4.6).

**Concrete acceptance criteria**:

1. **Semantic recall works**: A user who journaled "삼성전자 7만원에 물려서 손절했음 다시는 2차 매수 안 함" 3 months ago can `/evaluate 005930` and the response references that loss/lesson — even though "2차 매수" is not in the prompt and the journal is in Layer 3.
2. **Fact extraction works**: After 5+ journals, `user_facts` table contains LLM-extracted statements like `{"fact": "user avoids averaging down on losing positions", "evidence_memory_ids": [12, 34], "confidence": 0.78}`.
3. **Hybrid retrieval beats baseline**: On a synthetic eval set of 30 queries × 100 seeded memories, V2 retrieval Recall@5 ≥ 0.80 vs V1 ≤ 0.40.
4. **Latency budget**: Memory context build ≤ 400ms p95 (was ~150ms p95 — 2.5× slack acceptable).
5. **Cost budget**: ≤ $0.001 per `/evaluate` call additional (Haiku-extracted memory + embedding lookup).
6. **Backward compatible**: All existing `UserMemoryManager` public method signatures preserved. Existing `user_memories.sqlite` works without migration; migration is additive.

**Non-goals** (this iteration):
- Replacing the Sonnet 4.6 response model.
- Building a knowledge graph.
- Cross-user memory.
- Replacing `compress_trading_memory.py`'s principles/intuitions system (tracked separately).

---

## 3. Design

### 3.1 Architecture — 3-Tier Memory

```
┌─────────────────────────────────────────────────────────────┐
│ Tier 1: Episodic (existing user_memories table — preserved) │
│   raw events: journal entries, evaluation responses         │
│   + NEW: embedding (BLOB), fact_extracted (BOOL)            │
└─────────────────────────────────────────────────────────────┘
                          │  (async background task)
                          ▼  Haiku 4.5 fact extractor
┌─────────────────────────────────────────────────────────────┐
│ Tier 2: Semantic Facts (NEW user_facts table)               │
│   distilled statements + evidence pointers + confidence     │
│   { user_id, fact, category, confidence, evidence_ids,     │
│     created_at, superseded_by, embedding }                  │
└─────────────────────────────────────────────────────────────┘
                          │  (rolling derive)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 3: Profile (existing user_preferences — extended)      │
│   derived: investment_style, risk_tolerance, favorite_tickers │
│   computed from facts via simple aggregation                │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Retrieval — Hybrid + RRF

When `build_llm_context(user_id, ticker, user_message)` is called:

```
candidates = []
if ticker:
  candidates += BM25_search(user_id, ticker, k=10)  # FTS5
candidates += vector_search(user_id, query=user_message or ticker_name, k=10)  # cosine
candidates += recent_journals(user_id, ticker, k=5)  # always include fresh
ranked = RRF(candidates, k=60)
top = ranked[:8]  # ~1500 tokens
facts = top_facts_by_category(user_id, k=5)  # ~500 tokens
context = format(facts) + format(top)
```

**RRF (Reciprocal Rank Fusion)**: `score(d) = Σ 1/(k + rank_i(d))` for each retriever. k=60 standard. Robust without per-retriever weight tuning.

### 3.3 Embeddings — Pluggable + Local-First

- **Default**: Voyage AI `voyage-3-lite` ($0.02/M tokens, 512-dim) via the existing `voyage` HTTP API.
- **Fallback**: OpenAI `text-embedding-3-small` (already have the key — `requirements.txt` shows openai 1.64.0).
- **Storage**: float16 BLOB in SQLite; cosine similarity computed in Python (numpy already a dep).
- **No new infra**: do NOT add Qdrant/Chroma/sqlite-vec. Pure numpy + SQLite is sufficient for our scale (max ~100k memories total project-wide for ≥6 months).

Estimate: 1k embeddings × 512 dims × 2 bytes ≈ 1MB. In-process numpy load on cold start, then in-memory.

### 3.4 Fact Extraction — Async, Haiku 4.5

**Trigger**: After `save_journal()` or after `/evaluate` response written.
**How**: `asyncio.create_task` (fire-and-forget; bot doesn't wait).
**Model**: `claude-haiku-4-5-20251001` ($1/M input, $5/M output).
**Prompt**: structured tool-use → returns `[{fact, category, confidence}, ...]`. Categories fixed enum: `style`, `risk`, `holdings`, `aversion`, `goal`, `event`.
**Conflict**: new fact embedded → cosine vs existing facts in same category. If `sim ≥ 0.85` and contradiction detected by Haiku → mark old as `superseded_by = new.id`.
**Cost per extraction**: ~300 input + 100 output tokens = $0.0008.

### 3.5 Compression — LLM-Powered (Replaces `text[:150]`)

`compress_old_memories()` Layer 1→2: batch 10 memories per Haiku call, produce structured summary with key event + ticker + sentiment + outcome. Layer 2→3: extract single-sentence "lesson learned" form. Falls back to truncate on Haiku failure (so cron job never breaks).

### 3.6 Prompt Caching

Memory context block emitted with `cache_control: {"type": "ephemeral"}` boundary on the system message. Within a 5-minute follow-up window, repeat `/ask`/reply gets ~90% discount on memory tokens. Already supported by anthropic SDK 0.64.0.

### 3.7 API Surface (UserMemoryManager — additive)

Preserved methods (unchanged signatures):
- `save_memory`, `save_journal`, `get_memories`, `get_journals`
- `build_llm_context(user_id, ticker, max_tokens, user_message)`
- `compress_old_memories`, `get_user_preferences`, `update_user_preferences`, `get_memory_stats`

New methods:
- `async extract_facts(user_id, memory_id) -> List[Fact]` — internal, called by background task
- `get_facts(user_id, category=None, limit=10) -> List[Dict]`
- `search_memories(user_id, query, k=10) -> List[Dict]` — hybrid retrieval, public
- `derive_profile(user_id) -> Dict` — refresh investment_style from facts

New module structure:
```
tracking/memory/
  __init__.py        # re-exports UserMemoryManager (legacy import path preserved)
  manager.py         # UserMemoryManager (thin facade)
  schema.py          # _ensure_tables + migrations
  embed.py           # EmbeddingProvider (voyage/openai/null), cosine
  retrieve.py        # BM25 (FTS5) + vector + RRF
  extract.py         # Haiku fact extractor (async)
  compress.py        # Haiku-powered Layer 1→2→3
  facts.py           # user_facts CRUD + conflict resolution
```

Existing `tracking/user_memory.py` becomes a 3-line shim:
```python
from tracking.memory.manager import UserMemoryManager  # noqa: F401
```

---

## 4. Schema Changes (additive, idempotent)

```sql
-- add columns to existing user_memories (NULL-safe)
ALTER TABLE user_memories ADD COLUMN embedding BLOB;            -- float16 vec
ALTER TABLE user_memories ADD COLUMN embedding_model TEXT;      -- e.g. 'voyage-3-lite'
ALTER TABLE user_memories ADD COLUMN fact_extracted INTEGER DEFAULT 0;
ALTER TABLE user_memories ADD COLUMN sentiment TEXT;            -- 'positive'/'negative'/'neutral'
ALTER TABLE user_memories ADD COLUMN outcome TEXT;              -- 'win'/'loss'/'neutral'/'unknown'

-- new FTS5 virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS user_memories_fts USING fts5(
    content_text, ticker, ticker_name,
    content='user_memories', content_rowid='id',
    tokenize='unicode61'
);

-- new user_facts table
CREATE TABLE IF NOT EXISTS user_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    fact TEXT NOT NULL,
    category TEXT NOT NULL,                  -- enum: style|risk|holdings|aversion|goal|event
    confidence REAL DEFAULT 0.5,
    evidence_memory_ids TEXT,                -- JSON array
    embedding BLOB,
    embedding_model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by INTEGER,                   -- FK to self
    active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id, active);
CREATE INDEX IF NOT EXISTS idx_facts_cat ON user_facts(user_id, category, active);

-- migration tracking
CREATE TABLE IF NOT EXISTS memory_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT
);
```

`_ensure_tables()` runs all `IF NOT EXISTS` at startup → safe on existing DBs.
Backfill embeddings runs as opportunistic background task on first read of a memory missing `embedding`.

---

## 5. Cost & Latency Model

**Per `/evaluate` call**:
- Embedding query (1 string ~30 tokens): voyage-3-lite ≈ $0.0000006 (negligible)
- Vector cosine over ~500 user memories: ~5ms numpy
- BM25 FTS5: ~10ms
- Memory context to Sonnet 4.6: same as before (≤1500 tokens)
- **Net additional latency**: 50–150ms
- **Net additional cost**: < $0.0001 per call

**Per `/journal` save (background)**:
- Haiku 4.5 fact extraction: 300+100 tokens ≈ $0.0008
- Embedding (raw text + each fact): negligible
- Total: ~$0.001

**Daily compression** (cron, 3 AM):
- Per memory L1→L2: ~200 tokens Haiku ≈ $0.0002
- 100 memories/day project-wide: $0.02/day

**Monthly project cost ceiling**: ~$10 (well within budget).

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Embedding API outage breaks memory | `EmbeddingProvider` falls back to `NullEmbedder` → degrades to BM25-only retrieval, never crashes |
| Haiku extraction loop on bad input | Hard timeout 10s + per-call try/except + circuit breaker (skip after 5 failures/hour) |
| Schema migration on prod corrupts data | All `ADD COLUMN` are nullable + `IF NOT EXISTS`. Pre-migration backup script provided. |
| Embedding column bloats DB | float16 (2 bytes/dim × 512 = 1KB/row). 100k rows = 100MB. Acceptable. |
| Async fact extraction races with `compress_old_memories` | Both gated on `fact_extracted` flag; compression skips memories with `fact_extracted=0` if <24h old |
| LLM hallucinated facts | confidence threshold 0.6 to surface in prompt; <0.6 stored but not used until corroborated |

---

## 7. Rollout

1. Land schema migration + new modules behind feature flag `MEMORY_V2_ENABLED=true` env var.
2. Default OFF in production. ON in dev.
3. Backfill embeddings for last 30 days of memories (one-shot script).
4. Enable for own user_id (Rocky's) for 1 week soak.
5. Enable globally.
6. Remove flag after 2 weeks stable.

---

## 8. Out of scope / future

- Cross-conversation knowledge graph
- Multi-user/cohort memory ("users with similar style preferred X")
- Vector index (HNSW) — only worth it past ~50k memories per user
- Replacing `compress_trading_memory.py` principles/intuitions
