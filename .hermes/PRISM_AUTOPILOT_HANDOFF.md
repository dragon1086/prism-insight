# PRISM Guarded-Autopilot Handoff

## Current milestone

- Task: Phase 1A Task 6 — legacy DB read-only manifest and copy migration
- Branch: `prism-insight/t_5cf5fd17-prism-phase-1a-task-6-legacy-db-read-onl`
- Implementation state: locally complete and verified; PR #5 is green on Python 3.10/3.11/3.12; handoff checkpoint and squash merge are pending
- Runtime state: dormant inspection/copy foundation and explicit CLI tools only; no application entrypoint, scheduler, dashboard, provider, strategy, LLM, paper-broker, broker, account, or real legacy database path was wired

## Implemented scope

- Added a table-by-table legacy manifest with destination database/table, disposition, transform version, required/optional column allowlists, and explicit deferred handling for unsupported holdings, trades, watchlists, performance, journals, adjustments, account/user memory, event, pending-order, and ops tables.
- Added `mode=ro` plus `query_only` source access, a WAL-aware SQLite online-backup snapshot, and pre/post streaming SHA-256, size, and mtime fingerprints for the source database and WAL.
- Added deterministic typed row checksums, NFC text normalization, stable legacy lesson IDs, source/transformed/rejected counts, destination verification, and metadata-only JSON reports that omit source paths and raw/private row values.
- Added copy-only creation of new Task 5 `research.sqlite`, `paper.sqlite`, and `ops.sqlite` databases in a hidden staging directory, owner-scoped `O_EXCL` destination reservation, explicit connection closure and WAL checkpointing, atomic directory publication, and cleanup restricted to newly created artifacts.
- Added read-only inspection and migration CLIs with `--dry-run`, fixed metadata-only failure codes, and a Korean runbook at `docs/LEGACY_DB_MIGRATION.md`.
- Added the storage test group as an explicit fail-closed step in every existing GitHub Actions Python 3.10/3.11/3.12 matrix job.

## Contract decisions

- Option A is deliberately bounded: only `trading_intuitions` and `trading_principles` currently transform to `research.lessons`. Any non-empty deferred or unknown table blocks the whole migration rather than guessing a target representation.
- Imported records are always `status=LEGACY_UNVALIDATED`, `strategy_id=LEGACY_UNVALIDATED`, `activation_allowed=false`, and `score_adjustment=0`. Legacy `is_active` never becomes a target activation field.
- Source and transformed checksums are separate domains. SQLite storage classes are type-tagged and each canonical row is length-prefixed before an ordered SHA-256 fold. Destination business rows are re-read and compared by count and checksum before publication.
- Source `PRAGMA user_version` currently supports only legacy value `0`. Missing required columns, unexpected columns, views/virtual tables, invalid rows, non-finite transform values, source mutation, destination mismatch, and collisions fail closed.
- A crash can leave a hidden staging directory or reservation lock. The runbook requires provenance verification before manual orphan cleanup; the tool never deletes an existing destination or foreign lock.

## Verification

- `python -m compileall -q prism_core tools/audit_broker_boundaries.py` — passed.
- `python tools/audit_broker_boundaries.py` — passed, 0 violations (legacy inventory unchanged).
- `python -m pytest tests/storage -q` — 41 passed, including 15 Task 6 tests.
- Exact local CI command groups plus `tests/data` — 427 passed, 1 deselected.
- Task 6 tests cover read-only write rejection; source main/WAL checksum and mtime preservation; live-writer and no-live-writer at-rest WAL snapshots; deterministic/type-sensitive source and transformed checksums; real destination count mismatch detection; stable IDs/reruns; research/paper/ops routing; inert legacy status/score metadata; missing/unexpected columns; unsupported versions/schema objects/non-empty tables; invalid rows; scoped rollback; dangling destination and reservation collisions; CLI dry-run and redaction.
- `python -m pip check` — no broken requirements.
- `.github/workflows/ci.yml` parsed successfully with PyYAML.
- `git diff --check` and staged diff checks — passed after the final handoff update.
- Python 3.10/3.12 are not installed locally; GitHub Actions run 30057684422 passed on Python 3.10, 3.11, and 3.12 for feature head `86e7faba381ebe275ecb81540da57508d3bdc521`.


## Independent review

- Pre-implementation Claude read-only architecture/data-safety review recommended bounded Option A, online backup from a read-only source, strict allowlists, distinct checksum domains, terminal rejects, and atomic staging; these decisions were accepted.
- First final Claude review found no blocking corruption issue but requested proof for checksum sensitivity/real verification mismatch and at-rest WAL behavior, plus streaming file hashes, explicit SQLite connection closure, and stronger collision reservation.
- All requested items were implemented and test-covered. A regenerated-snapshot Claude read-only closing review found no high or medium issue, verified the remediations statically, and recommended merge conditional on GPT-operated tests.
- Remaining low items are documented availability/hygiene tradeoffs only: manual cleanup after hard crash and deliberate blocking by orphan locks. Unresolved high/medium findings: none.

## Side effects and safety

- Only pytest temporary SQLite files were opened. No repository/user SQLite database, including `stock_tracking_db.sqlite`, was opened, inspected, copied, or modified.
- No broker/account/order call, credential access/change, external message, AgentNews fetch, deployment, runtime activation, or actual legacy migration occurred.
- Feature commit `86e7fab` was pushed and PR #5 (`https://github.com/mienne/prism-insight/pull/5`) was created. No merge has occurred at this checkpoint.

## Merge state and next task

- Merge state: PR #5 is mergeable and its first exact-head CI matrix is green; squash merge follows this handoff-only checkpoint commit and its second exact-head CI matrix. The final Kanban run record is authoritative for post-merge SHA verification.
- Next approved task after verified merge only: Phase 1A Task 7 — security master plus provider/alias identity.
- Stop conditions remain: block on merge conflict, unresolved high/medium data-safety review, compatibility break, destructive or in-place user-data migration, required-gate failure after three reasoned attempts, unrelated conflicting changes, credentials/broker/live/risk scope, or an unverifiable exact-head CI/merge gate.