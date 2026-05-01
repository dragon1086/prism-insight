"""
user_facts CRUD + conflict resolution helpers.

Tier 2 of the 3-tier memory architecture. Each fact carries:
    fact, category, confidence, evidence_memory_ids (json), embedding (BLOB),
    embedding_model, created_at, updated_at, superseded_by, active

Categories are a fixed enum: style|risk|holdings|aversion|goal|event.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from tracking.memory.embed import cosine, from_blob, to_blob

logger = logging.getLogger(__name__)


CATEGORIES = ("style", "risk", "holdings", "aversion", "goal", "event")


def _now() -> str:
    return datetime.now().isoformat()


def _row_to_dict(row: Sequence[Any]) -> Dict[str, Any]:
    return {
        "id": row[0],
        "user_id": row[1],
        "fact": row[2],
        "category": row[3],
        "confidence": row[4],
        "evidence_memory_ids": json.loads(row[5]) if row[5] else [],
        "embedding": row[6],  # raw BLOB
        "embedding_model": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "superseded_by": row[10],
        "active": row[11],
    }


def save_fact(
    conn: sqlite3.Connection,
    user_id: int,
    fact: str,
    category: str,
    confidence: float = 0.5,
    evidence_memory_ids: Optional[List[int]] = None,
    embedding: Optional[np.ndarray] = None,
    embedding_model: Optional[str] = None,
) -> int:
    """Insert a new active fact. Returns inserted id."""
    if category not in CATEGORIES:
        # Don't reject — log + clamp to 'event' so caller can iterate.
        logger.warning("memory.facts.unknown_category category=%s -> event", category)
        category = "event"

    now = _now()
    blob = to_blob(embedding) if embedding is not None else None
    evidence_json = json.dumps(evidence_memory_ids or [], ensure_ascii=False)

    cur = conn.execute(
        """
        INSERT INTO user_facts (
            user_id, fact, category, confidence, evidence_memory_ids,
            embedding, embedding_model, created_at, updated_at,
            superseded_by, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
        """,
        (
            user_id, fact, category, float(confidence), evidence_json,
            blob, embedding_model, now, now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def get_facts(
    conn: sqlite3.Connection,
    user_id: int,
    category: Optional[str] = None,
    active: int = 1,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List facts for a user. By default returns active facts only."""
    sql = """
        SELECT id, user_id, fact, category, confidence, evidence_memory_ids,
               embedding, embedding_model, created_at, updated_at,
               superseded_by, active
        FROM user_facts
        WHERE user_id = ?
    """
    params: List[Any] = [user_id]

    if active is not None:
        sql += " AND active = ?"
        params.append(active)
    if category:
        sql += " AND category = ?"
        params.append(category)

    sql += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
    params.append(limit)

    cur = conn.execute(sql, params)
    return [_row_to_dict(r) for r in cur.fetchall()]


def supersede(
    conn: sqlite3.Connection,
    old_id: int,
    new_id: int,
) -> bool:
    """Mark `old_id` as superseded by `new_id`; deactivate old."""
    cur = conn.execute(
        """
        UPDATE user_facts
        SET active = 0, superseded_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_id, _now(), old_id),
    )
    conn.commit()
    return cur.rowcount > 0


def find_similar(
    conn: sqlite3.Connection,
    user_id: int,
    category: str,
    embedding: Optional[np.ndarray],
    threshold: float = 0.85,
) -> List[Dict[str, Any]]:
    """
    Return active facts in the same category whose embedding cosine ≥ threshold,
    sorted by similarity descending. Empty list if embedding is None.
    """
    if embedding is None:
        return []

    facts = get_facts(conn, user_id, category=category, active=1, limit=200)
    out: List[Dict[str, Any]] = []
    for f in facts:
        blob = f.get("embedding")
        if not blob:
            continue
        try:
            other = from_blob(blob)
        except Exception:
            continue
        sim = cosine(embedding, other)
        if sim >= threshold:
            f2 = dict(f)
            f2["similarity"] = sim
            out.append(f2)
    out.sort(key=lambda x: x.get("similarity", 0.0), reverse=True)
    return out


def deactivate(conn: sqlite3.Connection, fact_id: int) -> bool:
    cur = conn.execute(
        "UPDATE user_facts SET active = 0, updated_at = ? WHERE id = ?",
        (_now(), fact_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_top_by_category(
    conn: sqlite3.Connection,
    user_id: int,
    k: int = 5,
    min_confidence: float = 0.6,
) -> List[Dict[str, Any]]:
    """Return up to k highest-confidence facts across categories for prompt building."""
    sql = """
        SELECT id, user_id, fact, category, confidence, evidence_memory_ids,
               embedding, embedding_model, created_at, updated_at,
               superseded_by, active
        FROM user_facts
        WHERE user_id = ? AND active = 1 AND confidence >= ?
        ORDER BY confidence DESC, updated_at DESC
        LIMIT ?
    """
    cur = conn.execute(sql, (user_id, float(min_confidence), int(k)))
    return [_row_to_dict(r) for r in cur.fetchall()]
