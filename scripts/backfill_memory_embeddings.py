#!/usr/bin/env python3
"""
Backfill embeddings + FTS index for legacy user_memories rows.

Idempotent: only touches rows where ``embedding IS NULL`` or that are missing
from the FTS index. Resumable via batched commits. Dry-run flag prints the plan
without writing.

Usage:
    python scripts/backfill_memory_embeddings.py \
        --db user_memories.sqlite [--batch 100] [--limit 0] [--dry-run] \
        [--since YYYY-MM-DD] [--rps N]

Examples:
    # Backfill all rows:
    python scripts/backfill_memory_embeddings.py --db user_memories.sqlite

    # Backfill only rows created on or after 2025-01-01, rate-limited to 5 req/s:
    python scripts/backfill_memory_embeddings.py --db user_memories.sqlite \\
        --since 2025-01-01 --rps 5

    # Dry-run preview for recent rows:
    python scripts/backfill_memory_embeddings.py --db user_memories.sqlite \\
        --since 2025-06-01 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from typing import Iterable, List, Optional, Tuple

# Make the project importable when run from anywhere.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tracking.memory.embed import get_default_provider, to_blob
from tracking.memory.schema import bootstrap_fts, run_migrations

logger = logging.getLogger("backfill_memory_embeddings")


def _extract_text(content_json: str) -> str:
    try:
        obj = json.loads(content_json) if content_json else {}
    except Exception:
        return content_json or ""
    if isinstance(obj, dict):
        for k in ("text", "raw_input", "response_summary", "summary"):
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
    return str(obj)


def _missing_rows(
    conn: sqlite3.Connection,
    limit: int = 0,
    since: Optional[str] = None,
) -> Iterable[Tuple[int, str]]:
    sql = "SELECT id, content FROM user_memories WHERE embedding IS NULL"
    params: List = []
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    sql += " ORDER BY id"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def main():
    ap = argparse.ArgumentParser(
        description="Backfill embeddings + FTS index for legacy user_memories rows.",
    )
    ap.add_argument("--db", required=True, help="path to SQLite DB")
    ap.add_argument("--batch", type=int, default=100, help="commit batch size")
    ap.add_argument("--limit", type=int, default=0,
                    help="max rows to process this run (0=all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print plan, do not write")
    ap.add_argument("--skip-fts", action="store_true",
                    help="skip FTS bootstrap step")
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                    help="only backfill rows where created_at >= this date")
    ap.add_argument("--rps", type=float, default=None,
                    help="global rate limit: sleep 1/N seconds between batches")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not os.path.exists(args.db):
        logger.error("DB not found: %s", args.db)
        sys.exit(2)

    conn = sqlite3.connect(args.db)
    try:
        run_migrations(conn)

        # Step 1 — FTS bootstrap.
        if not args.skip_fts:
            if args.dry_run:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM user_memories "
                    "WHERE id NOT IN (SELECT rowid FROM user_memories_fts)"
                )
                pending = cur.fetchone()[0]
                logger.info("dry-run: would bootstrap %d FTS rows", pending)
            else:
                inserted = bootstrap_fts(conn)
                logger.info("FTS bootstrap inserted=%d", inserted)

        # Step 2 — embedding backfill.
        rows = list(_missing_rows(conn, args.limit, since=args.since))
        if not rows:
            logger.info("no rows missing embeddings — done")
            return

        logger.info("rows pending embedding=%d", len(rows))
        if args.dry_run:
            for row in rows[:5]:
                logger.info("would embed id=%d", row[0])
            return

        provider = get_default_provider()
        logger.info("embedder=%s dim=%d", provider.name, provider.dim)

        rps_sleep = (1.0 / args.rps) if args.rps and args.rps > 0 else None

        batch_payload: List[Tuple[bytes, str, int]] = []
        for memory_id, content_json in rows:
            text = _extract_text(content_json)
            try:
                vec = provider.embed(text)
                blob = to_blob(vec)
            except Exception as e:
                logger.warning("embed_failed id=%d err=%s — skipping", memory_id, e)
                continue
            batch_payload.append((blob, provider.name, int(memory_id)))

            if len(batch_payload) >= args.batch:
                conn.executemany(
                    "UPDATE user_memories SET embedding=?, embedding_model=? WHERE id=?",
                    batch_payload,
                )
                conn.commit()
                logger.info("committed batch=%d", len(batch_payload))
                batch_payload = []
                if rps_sleep:
                    time.sleep(rps_sleep)

        if batch_payload:
            conn.executemany(
                "UPDATE user_memories SET embedding=?, embedding_model=? WHERE id=?",
                batch_payload,
            )
            conn.commit()
            logger.info("committed final batch=%d", len(batch_payload))

        logger.info("backfill complete")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
