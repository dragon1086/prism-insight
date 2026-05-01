# Memory V2 — Migration Plan

**Companion to**: `PRD.md`, `VERIFICATION_PLAN.md`
**Goal**: Zero-downtime, reversible migration from `user_memory.py` (V1) to `tracking/memory/` (V2) on the prod `user_memories.sqlite` DB.

---

## 1. Migration Principles

- **Additive only**: every schema change uses `ADD COLUMN` (NULL default) or `CREATE TABLE IF NOT EXISTS`. Never `DROP`, `RENAME`, or `ALTER` existing columns.
- **Idempotent**: re-running the migration is a no-op.
- **Versioned**: `memory_schema_version` table tracks applied versions.
- **Forward-compatible**: V1 readers can still read the DB after migration (they ignore new columns).
- **Reversible at code layer**: feature flag `MEMORY_V2_ENABLED` toggles new behavior. No code rollback needed for emergency disable.

---

## 2. Phases

### Phase 0 — Pre-flight (manual, before deploy)

```bash
# 1. Confirm prod db path
ls -la user_memories.sqlite

# 2. Snapshot
cp user_memories.sqlite user_memories.sqlite.bak.$(date +%Y%m%d-%H%M%S)

# 3. Sanity check
sqlite3 user_memories.sqlite "SELECT COUNT(*) FROM user_memories; SELECT COUNT(*) FROM user_preferences;"
```

Recorded baseline counts: archive in deploy ticket.

### Phase 1 — Schema Migration (idempotent, runs on bot startup)

`tracking/memory/schema.py::run_migrations(conn)`:

```python
SCHEMA_MIGRATIONS = [
    (1, """
        ALTER TABLE user_memories ADD COLUMN embedding BLOB;
    """),
    (2, """
        ALTER TABLE user_memories ADD COLUMN embedding_model TEXT;
    """),
    (3, """
        ALTER TABLE user_memories ADD COLUMN fact_extracted INTEGER DEFAULT 0;
    """),
    (4, """
        ALTER TABLE user_memories ADD COLUMN sentiment TEXT;
    """),
    (5, """
        ALTER TABLE user_memories ADD COLUMN outcome TEXT;
    """),
    (6, """
        CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fact TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            evidence_memory_ids TEXT,
            embedding BLOB,
            embedding_model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by INTEGER,
            active INTEGER DEFAULT 1
        );
    """),
    (7, """
        CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id, active);
        CREATE INDEX IF NOT EXISTS idx_facts_cat ON user_facts(user_id, category, active);
    """),
    (8, """
        CREATE VIRTUAL TABLE IF NOT EXISTS user_memories_fts USING fts5(
            content_text, ticker, ticker_name,
            content='', tokenize='unicode61'
        );
    """),
    # NOTE: FTS5 'external content' mode (content='user_memories') would require
    # rebuilding the index from scratch; we use 'contentless' mode and write into
    # FTS explicitly on save_memory. This avoids triggers and keeps migration
    # idempotent on partial state.
]
```

Each migration runs inside its own transaction. `memory_schema_version` row inserted on success. Already-applied versions are skipped.

**SQLite quirk**: `ADD COLUMN` is non-transactional in SQLite (auto-commits). We catch `OperationalError: duplicate column` and treat as already-applied.

**Failure mode**: if any migration fails partway, the DB is left in a consistent state because `ADD COLUMN` is per-statement atomic. Bot continues on V1 path (feature flag stays off). Operator inspects logs and re-runs.

### Phase 2 — Backfill Embeddings (background, opt-in)

`scripts/backfill_memory_embeddings.py`:

```bash
python scripts/backfill_memory_embeddings.py \
    --db user_memories.sqlite \
    --batch 32 \
    --since "2025-11-01" \
    --dry-run
```

Behavior:
- Fetches rows where `embedding IS NULL` and `created_at >= --since`.
- Batches via `EmbeddingProvider.embed_batch(texts)`.
- Writes float16 BLOB back; commits per batch.
- Resumable (skips already-embedded rows).
- Rate-limited (configurable `--rps 5`).
- Cost: voyage-3-lite at ~$0.00002 per 1k chars; ~5k existing memories ≈ $0.10 total.

Cron-able for ongoing keepup, but not required — `search_memories` lazy-backfills on read miss.

### Phase 3 — FTS Bootstrap

After migration #8, populate FTS5 from existing rows:

```python
# tracking/memory/schema.py::bootstrap_fts(conn)
cur = conn.execute("""
    SELECT id, json_extract(content, '$.text'), ticker, ticker_name
    FROM user_memories
    WHERE id NOT IN (SELECT rowid FROM user_memories_fts)
""")
for row in cur:
    conn.execute(
        "INSERT INTO user_memories_fts(rowid, content_text, ticker, ticker_name) VALUES (?,?,?,?)",
        row
    )
```

Runs in chunks of 1000. ~30k rows = ~5s.

### Phase 4 — Code Cutover

1. Add new modules under `tracking/memory/`.
2. Replace `tracking/user_memory.py` content with re-export shim.
3. Existing imports `from tracking.user_memory import UserMemoryManager` continue to work.
4. New code may import `from tracking.memory import UserMemoryManager` (preferred).

### Phase 5 — Feature Flag Rollout (per VERIFICATION_PLAN §5)

```python
# tracking/memory/manager.py
import os
MEMORY_V2_ENABLED = os.getenv("MEMORY_V2_ENABLED", "false").lower() == "true"
MEMORY_V2_USER_IDS = set(
    int(x) for x in os.getenv("MEMORY_V2_USER_IDS", "").split(",") if x.strip()
)

def _v2_enabled_for(user_id: int) -> bool:
    if MEMORY_V2_ENABLED:
        return True
    return user_id in MEMORY_V2_USER_IDS
```

`build_llm_context` and `save_memory` route based on `_v2_enabled_for(user_id)`. Old behavior is the default.

---

## 3. Rollback Plan

### Soft rollback (no DB change)

Set env: `MEMORY_V2_ENABLED=false` and `MEMORY_V2_USER_IDS=` then restart bot.
- All reads/writes go through V1 path.
- New columns ignored.
- `user_facts` table dormant.

ETA to safe state: ~30s (bot restart).

### Hard rollback (revert code)

```bash
git revert <merge-commit-sha>
```
DB is forward-compatible: V1 code reads existing rows fine because new columns default NULL.

### Data rollback (only if corruption suspected)

Restore from snapshot taken in Phase 0:
```bash
systemctl stop telegram-bot
mv user_memories.sqlite user_memories.sqlite.broken
cp user_memories.sqlite.bak.<TS> user_memories.sqlite
systemctl start telegram-bot
```
Window of data loss: from snapshot time to restore. Unlikely to need this since migrations are additive.

---

## 4. Compatibility Matrix

| Code Version | DB Schema | Behavior |
|---|---|---|
| V1 | V1 | original |
| V1 | V2 | works (new columns ignored, FTS+facts orphan but dormant) |
| V2 (flag off) | V1 | works via fallback paths; lazy migration on first save |
| V2 (flag off) | V2 | works on V1 codepaths; V2 paths only if user in allowlist |
| V2 (flag on) | V2 | full V2 |
| V2 (flag on) | V1 | first call triggers `run_migrations()`; then full V2 |

---

## 5. Verification Of Migration

After deploy, run from bot host:

```bash
sqlite3 user_memories.sqlite <<'EOF'
SELECT version, applied_at FROM memory_schema_version ORDER BY version;
SELECT COUNT(*) AS total, SUM(embedding IS NOT NULL) AS embedded FROM user_memories;
SELECT category, COUNT(*) FROM user_facts GROUP BY category;
SELECT COUNT(*) FROM user_memories_fts;
EOF
```

Expected after Phase 1 only (no backfill yet):
```
1|<ts>
2|<ts>
...
8|<ts>
total=N, embedded=0
no rows in user_facts
fts count = 0
```

Expected after Phase 2+3:
```
embedded ≈ total (within 5%)
fts count ≈ total
```

---

## 6. Observability During Migration

Add structured log events:
- `memory.migration.applied version=N elapsed_ms=…`
- `memory.backfill.batch processed=… failed=… elapsed_ms=…`
- `memory.fts.bootstrap rows=… elapsed_ms=…`
- `memory.v2.enabled user_id=… reason=global|allowlist`
- `memory.embedding.fallback reason=…`

Operator dashboard: tail logs and grep `memory\.`.
