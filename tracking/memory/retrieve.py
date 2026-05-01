"""
Retrieval primitives — BM25 (FTS5), vector cosine, and Reciprocal Rank Fusion.

`bm25_search`, `vector_search`, `recent_memories` all return list of memory rows
(as dicts identical in shape to V1 `get_memories`). `rrf_combine` is a pure
function that merges multiple ranking lists by id.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from tracking.memory.embed import cosine, from_blob

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Row helpers
# --------------------------------------------------------------------------- #
_MEMORY_COLS = (
    "id, user_id, memory_type, content, summary, ticker, ticker_name, "
    "market_type, importance_score, compression_layer, created_at, "
    "last_accessed_at, command_source, message_id, tags"
)


def _row_to_memory(row: Sequence[Any]) -> Dict[str, Any]:
    return {
        "id": row[0],
        "user_id": row[1],
        "memory_type": row[2],
        "content": json.loads(row[3]) if row[3] else {},
        "summary": row[4],
        "ticker": row[5],
        "ticker_name": row[6],
        "market_type": row[7],
        "importance_score": row[8],
        "compression_layer": row[9],
        "created_at": row[10],
        "last_accessed_at": row[11],
        "command_source": row[12],
        "message_id": row[13],
        "tags": json.loads(row[14]) if row[14] else [],
    }


# --------------------------------------------------------------------------- #
# BM25 (FTS5) search
# --------------------------------------------------------------------------- #
def _sanitize_fts_query(query: str) -> str:
    """
    FTS5 MATCH expects column terms and operators. We treat the user query as a
    bag-of-words OR query — escape problematic chars and quote each token.
    """
    if not query:
        return ""
    # Drop FTS5 operator characters that break parsing.
    bad = '"():*-^~'
    cleaned = "".join(" " if c in bad else c for c in query)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return ""
    # Quote each token; FTS5 handles unicode61 tokens via prefix match.
    return " OR ".join(f'"{t}"' for t in tokens)


def bm25_search(
    conn: sqlite3.Connection,
    user_id: int,
    query: str,
    k: int = 10,
) -> List[Dict[str, Any]]:
    """FTS5 MATCH against user_memories_fts; join back for full row dicts."""
    fts_q = _sanitize_fts_query(query)
    if not fts_q:
        return []
    try:
        cur = conn.execute(
            f"""
            SELECT {_MEMORY_COLS}
            FROM user_memories
            WHERE user_id = ?
              AND id IN (
                  SELECT rowid FROM user_memories_fts
                  WHERE user_memories_fts MATCH ?
                  ORDER BY rank
                  LIMIT ?
              )
            ORDER BY (
                SELECT rank FROM user_memories_fts
                WHERE rowid = user_memories.id
                  AND user_memories_fts MATCH ?
            ) ASC
            """,
            (user_id, fts_q, k * 4, fts_q),
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("memory.retrieve.bm25.failed err=%s query=%r", e, fts_q)
        return []
    return [_row_to_memory(r) for r in rows[:k]]


# --------------------------------------------------------------------------- #
# Vector cosine search (with per-(user_id, db_mtime) LRU cache)
# --------------------------------------------------------------------------- #
_VEC_CACHE: Dict[Tuple[str, int, float], Tuple[List[int], np.ndarray]] = {}
_VEC_CACHE_LOCK = threading.Lock()
_VEC_CACHE_MAX = 64


def _db_mtime(conn: sqlite3.Connection) -> float:
    """Fingerprint of the DB. For file DBs use mtime; for :memory: use sentinel 0."""
    try:
        path = None
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main":
                path = row[2]
                break
        if path and path != "" and os.path.exists(path):
            return os.path.getmtime(path)
    except Exception:
        pass
    return 0.0


def _cache_key(conn: sqlite3.Connection, user_id: int) -> Tuple[str, int, float]:
    db_path = ""
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main":
                db_path = row[2] or ""
                break
    except Exception:
        pass
    return (db_path, user_id, _db_mtime(conn))


def _load_user_vectors(
    conn: sqlite3.Connection, user_id: int
) -> Tuple[List[int], np.ndarray]:
    key = _cache_key(conn, user_id)
    with _VEC_CACHE_LOCK:
        cached = _VEC_CACHE.get(key)
        if cached is not None:
            return cached

    cur = conn.execute(
        "SELECT id, embedding, embedding_model, created_at FROM user_memories "
        "WHERE user_id = ? AND embedding IS NOT NULL",
        (user_id,),
    )
    rows_all = cur.fetchall()

    # --- Provider-mismatch guard ---
    # Group by embedding_model; pick the model with the most-recent created_at.
    model_max_ts: Dict[str, str] = {}
    for row in rows_all:
        model = row[2] or ""
        ts = row[3] or ""
        if model not in model_max_ts or ts > model_max_ts[model]:
            model_max_ts[model] = ts

    if model_max_ts:
        current_model = max(model_max_ts, key=lambda m: model_max_ts[m])
        skipped_models = [m for m in model_max_ts if m != current_model]
        if skipped_models:
            skipped_rows = sum(1 for r in rows_all if (r[2] or "") != current_model)
            logger.warning(
                "memory.embed.mixed_providers user_id=%d current=%s "
                "skipped_models=%s skipped_rows=%d",
                user_id, current_model, skipped_models, skipped_rows,
            )
        rows_all = [r for r in rows_all if (r[2] or "") == current_model]

    ids: List[int] = []
    vecs: List[np.ndarray] = []
    for row in rows_all:
        try:
            v = from_blob(row[1])
        except Exception:
            continue
        if v.size == 0:
            continue
        ids.append(int(row[0]))
        vecs.append(v)

    if vecs:
        # Pad/truncate all to the same dim (use first vector's dim).
        target_dim = vecs[0].size
        normalized: List[np.ndarray] = []
        for v in vecs:
            if v.size == target_dim:
                normalized.append(v)
            elif v.size > target_dim:
                normalized.append(v[:target_dim])
            else:
                normalized.append(
                    np.concatenate([v, np.zeros(target_dim - v.size, dtype=np.float32)])
                )
        matrix = np.vstack(normalized).astype(np.float32)
    else:
        matrix = np.zeros((0, 0), dtype=np.float32)

    with _VEC_CACHE_LOCK:
        if len(_VEC_CACHE) >= _VEC_CACHE_MAX:
            _VEC_CACHE.clear()  # crude eviction; fine for our scale
        _VEC_CACHE[key] = (ids, matrix)
    return ids, matrix


def reset_vector_cache() -> None:
    with _VEC_CACHE_LOCK:
        _VEC_CACHE.clear()


def vector_search(
    conn: sqlite3.Connection,
    user_id: int,
    query_vec: Optional[np.ndarray],
    k: int = 10,
) -> List[Dict[str, Any]]:
    """Numpy cosine over cached user vectors. Returns up to k memory dicts."""
    if query_vec is None:
        return []
    q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return []

    ids, matrix = _load_user_vectors(conn, user_id)
    if not ids or matrix.size == 0:
        return []

    # Align dims.
    if q.size != matrix.shape[1]:
        if q.size > matrix.shape[1]:
            q = q[: matrix.shape[1]]
        else:
            q = np.concatenate(
                [q, np.zeros(matrix.shape[1] - q.size, dtype=np.float32)]
            )
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []

    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    sims = (matrix @ q) / (norms * qn)
    top_idx = np.argsort(-sims)[:k]
    top_ids = [ids[i] for i in top_idx if i < len(ids)]

    if not top_ids:
        return []

    # Bulk fetch + preserve cosine order.
    placeholders = ",".join(["?"] * len(top_ids))
    cur = conn.execute(
        f"SELECT {_MEMORY_COLS} FROM user_memories WHERE id IN ({placeholders})",
        top_ids,
    )
    by_id: Dict[int, Dict[str, Any]] = {}
    for row in cur.fetchall():
        d = _row_to_memory(row)
        by_id[d["id"]] = d
    return [by_id[i] for i in top_ids if i in by_id]


# --------------------------------------------------------------------------- #
# Recent memories (V1-equivalent ordering)
# --------------------------------------------------------------------------- #
def recent_memories(
    conn: sqlite3.Connection,
    user_id: int,
    ticker: Optional[str] = None,
    k: int = 10,
    memory_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = f"SELECT {_MEMORY_COLS} FROM user_memories WHERE user_id = ?"
    params: List[Any] = [user_id]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker)
    if memory_type:
        sql += " AND memory_type = ?"
        params.append(memory_type)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(k)
    cur = conn.execute(sql, params)
    return [_row_to_memory(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Reciprocal Rank Fusion
# --------------------------------------------------------------------------- #
def rrf_combine(rankings: List[List[int]], k: int = 60) -> List[int]:
    """
    Pure function: combine multiple ranked id lists into one ordered unique list
    via Σ 1/(k + rank_i(d)). Stable on ties (preserves first-seen order).
    """
    scores: Dict[int, float] = {}
    first_seen: Dict[int, int] = {}
    counter = 0
    for ranking in rankings:
        for rank, item_id in enumerate(ranking):
            if item_id is None:
                continue
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
            if item_id not in first_seen:
                first_seen[item_id] = counter
                counter += 1

    if not scores:
        return []
    # Sort by score desc, tie-break by first-seen order.
    return sorted(
        scores.keys(),
        key=lambda i: (-scores[i], first_seen.get(i, 1 << 30)),
    )


# --------------------------------------------------------------------------- #
# Hybrid retrieval entry point used by the manager facade
# --------------------------------------------------------------------------- #
def hybrid_search(
    conn: sqlite3.Connection,
    user_id: int,
    query: str,
    query_vec: Optional[np.ndarray],
    ticker: Optional[str] = None,
    k: int = 10,
) -> List[Dict[str, Any]]:
    """
    BM25 + vector + recent → RRF.
    Each retriever fetches up to ``k * 2`` candidates so RRF has enough overlap
    to differentiate.
    """
    fan_k = max(k * 2, 10)
    bm25 = bm25_search(conn, user_id, query, k=fan_k) if query else []
    vec = vector_search(conn, user_id, query_vec, k=fan_k) if query_vec is not None else []
    rec = recent_memories(conn, user_id, ticker=ticker, k=fan_k)

    rankings = [
        [m["id"] for m in bm25],
        [m["id"] for m in vec],
        [m["id"] for m in rec],
    ]
    fused_ids = rrf_combine(rankings, k=60)[:k]
    by_id: Dict[int, Dict[str, Any]] = {}
    for m in bm25 + vec + rec:
        by_id.setdefault(m["id"], m)
    return [by_id[i] for i in fused_ids if i in by_id]
