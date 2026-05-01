# Memory V2 — Verification Plan

**Companion to**: `PRD.md`
**Goal**: Prove acceptance criteria from PRD §2 with automated, reproducible evidence before merge.

---

## 1. Test Tiers

### Tier A — Unit (pytest, no LLM, no network)

Location: `tests/memory/test_*.py`

| File | Coverage | Must pass |
|---|---|---|
| `test_schema.py` | `_ensure_tables()` idempotent on empty DB and on V1 DB; new columns added; FTS5 trigger correct | 100% |
| `test_embed.py` | `NullEmbedder` returns deterministic zero vectors; `cosine_similarity` matches numpy reference; float16 round-trip preserves cosine within 1e-3 | 100% |
| `test_retrieve_rrf.py` | RRF combiner: identity (single retriever) = same order; merge of disjoint sets = both included; tie-break stable; k=60 default | 100% |
| `test_retrieve_bm25.py` | FTS5 query for ticker `005930` finds matching memories; Korean tokenization (unicode61) finds "삼성전자"; rebuilds index after insert via trigger | 100% |
| `test_facts.py` | `save_fact` insert; `get_facts` returns active only; `supersede(old, new)` flips `active=0` and sets `superseded_by`; conflict detection threshold 0.85 | 100% |
| `test_compress_fallback.py` | When Haiku raises → falls back to truncate; cron never raises out | 100% |
| `test_manager_compat.py` | All V1 method signatures preserved (introspection-based); `save_memory`/`get_memories`/`save_journal` results identical to V1 on V1 fixtures | 100% |

**Run**: `pytest tests/memory/ -q --tb=short`

### Tier B — Integration (real SQLite, mocked LLM)

| Scenario | Expected |
|---|---|
| Save 50 journals → call `build_llm_context(user_id, ticker)` → ≤ 1500 token context, ticker-specific journals prioritized, recent thoughts appended | ✓ |
| Save journal with `embedding=None` (legacy row) → next `search_memories` triggers backfill, embedding populated | ✓ |
| Two contradicting facts saved → second supersedes first; `get_facts(active=1)` returns one | ✓ |
| Concurrent `save_memory` from 5 users (asyncio.gather) → no SQLite lock errors, all rows present | ✓ |
| `MEMORY_V2_ENABLED=false` → only V1 paths used, no calls to `EmbeddingProvider` | ✓ |
| `EmbeddingProvider` raises → retrieval degrades to BM25-only, returns results, logs warning | ✓ |

Mocked Anthropic client returns canned responses (`tests/memory/fixtures/haiku_extract_response.json`).

### Tier C — Retrieval Quality Eval (synthetic gold set)

**Goal**: Prove "Recall@5 ≥ 0.80 vs V1 ≤ 0.40" claim from PRD §2.

**Construction** (`tests/memory/eval/build_eval_set.py`):
1. Generate 100 synthetic Korean trading journals across 20 tickers, 5 themes (단타 후회, 장기보유 신뢰, 손절 어려움, 분할매수, 배당 선호).
2. Generate 30 query-answer pairs: each query targets 3 specific journal IDs (the gold set).
   - Example: query "내가 단타로 손해 본 적 있어?" → gold = [journal_3, journal_17, journal_44].
3. Save all journals with V2 (embeddings + FTS5).

**Run** (`tests/memory/eval/run_eval.py`):
- For each query, retrieve top-5 with V1 (`get_journals` recency) and V2 (`search_memories` hybrid).
- Compute Recall@5 = `|retrieved ∩ gold| / |gold|`.
- Compute MRR (Mean Reciprocal Rank).

**Pass bar**:
- V2 Recall@5 ≥ 0.80 (target), ≥ 0.60 (minimum to merge)
- V2 MRR ≥ 0.50
- V1 baseline reported for honest comparison.

Output: `docs/memory-v2/eval_results.md` with table.

### Tier D — Latency & Cost Bench

`tests/memory/bench_latency.py` (uses `pytest-benchmark` or stdlib `timeit`):

| Operation | Target p95 | Hard cap |
|---|---|---|
| `save_journal` (sync path, no extraction) | ≤ 30ms | 100ms |
| `save_journal` + spawn extraction task | ≤ 50ms | 150ms |
| `build_llm_context` (1 user, 500 memories, hybrid) | ≤ 400ms | 800ms |
| `compress_old_memories` (10 entries via Haiku, mocked) | ≤ 50ms (mock) | — |

Embedding cost: assert `count_calls(embed_provider) ≤ 2` per `build_llm_context` (one for query, zero or one for backfill).

### Tier E — End-to-End on real bot

Smoke test using a fresh sqlite DB seeded with 20 journals from a fixture user_id (999):

```bash
MEMORY_V2_ENABLED=true python tests/memory/e2e_smoke.py
```

Asserts:
1. `/evaluate 005930` invocation path returns a memory_context string > 200 chars.
2. The context contains at least one fragment from a journal that mentions 005930 OR 삼성전자.
3. After 5 background fact-extraction runs (with mocked Haiku), `user_facts` has ≥ 3 rows for user 999.
4. Re-run `/evaluate` → cache_control headers present in Anthropic call.

---

## 2. Manual Verification Checklist (live bot)

Run on dev bot only (not prod):

- [ ] `/journal 삼성전자 7만원에 물려서 손절. 다시는 물타기 안함` saves; `user_memories` has new row.
- [ ] Wait 30s → `user_facts` populated with style/aversion category.
- [ ] `/evaluate 005930` → response visibly references the loss/lesson (not just generic analysis).
- [ ] `/evaluate AAPL` → response does NOT spuriously reference 삼성 lesson (no cross-ticker bleed).
- [ ] Reply to `/evaluate` response with "그럼 지금 들어갈만해?" → conversation continuity preserved (existing `ConversationContext`).
- [ ] DB inspection: `embedding` column populated for new rows, NULL for old (until backfill).
- [ ] Set `MEMORY_V2_ENABLED=false`, restart bot → all flows still work (V1 fallback).

---

## 3. Regression Guard

Run V1 test fixtures against the new manager. The following V1 invariants MUST hold:

1. `save_memory(...)` returns a positive int (memory_id).
2. `get_memories(user_id, limit=10)` returns list ordered by `created_at DESC`.
3. `get_user_preferences` returns dict with all V1 keys.
4. `compress_old_memories` does not raise; returns dict with `layer2_count` and `layer3_count` keys.
5. `build_llm_context` returns `str` (possibly empty), never raises.

Implemented as `tests/memory/test_v1_regression.py`.

---

## 4. Pre-merge Gate (CI must show all green)

```
pytest tests/memory/ -q
python tests/memory/eval/run_eval.py --check  # exits non-zero if Recall@5 < 0.6
python tests/memory/bench_latency.py --check  # exits non-zero if p95 > hard cap
```

Output uploaded as PR comment.

---

## 5. Soak Plan (post-merge)

1. Day 0: merge with `MEMORY_V2_ENABLED=false` default.
2. Day 0–7: enabled for Rocky's user_id only via allowlist env var `MEMORY_V2_USER_IDS=7726642089`.
3. Daily: log volume of fact extractions, embedding calls, fallback triggers.
4. Day 7: review logs; if no error spike, flip global flag ON.
5. Day 21: remove flag plumbing.

---

## 6. Rollback Triggers

Auto-flip `MEMORY_V2_ENABLED=false` if any of:
- `/evaluate` p95 latency > 12s for 5 min (was ~8s baseline)
- Embedding API error rate > 20% for 10 min
- SQLite write errors > 1/min
- Haiku extraction error rate > 50%

Manual flip: `feature_flags.py` env override; no code rollback needed.
