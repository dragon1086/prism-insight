"""Bounded advisory checkpoints, never order authority or risk validation."""
from __future__ import annotations

import hashlib
import math


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def valid_review(value):
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {"conditions", "acknowledgements"}:
        return False
    conditions, acknowledgements = value["conditions"], value["acknowledgements"]
    if not isinstance(conditions, list) or not isinstance(acknowledgements, list):
        return False
    if len(conditions) > 3 or len(acknowledgements) > 3:
        return False
    for row in conditions:
        if (not isinstance(row, dict) or set(row) != {"source", "operator", "price"}
                or row["source"] != "MARK_PRICE" or row["operator"] not in ("ge", "le")
                or not _number(row["price"]) or row["price"] <= 0):
            return False
    if len({(row["operator"], row["price"]) for row in conditions}) != len(conditions):
        return False
    ids = []
    for row in acknowledgements:
        if (not isinstance(row, dict) or set(row) != {"id", "disposition", "reason"}
                or not isinstance(row["id"], str) or not 1 <= len(row["id"]) <= 80
                or row["disposition"] not in ("hold", "replace")
                or not isinstance(row["reason"], str) or not 1 <= len(row["reason"].strip()) <= 240):
            return False
        ids.append(row["id"])
    return len(ids) == len(set(ids))


def _saved_rows_valid(rows):
    if not isinstance(rows, list) or len(rows) > 3:
        return False
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not valid_review({"conditions": [{key: row.get(key) for key in
                    ("source", "operator", "price")}], "acknowledgements": []})
                or "reached_at" not in row
                or not (row["reached_at"] is None or _number(row["reached_at"]))
                or not _number(row.get("created_at", 0))):
            return False
    return len({row["id"] for row in rows}) == len(rows)


def observe_review(active, context, now):
    """Only sampled fresh account MarkPrice can hit a threshold; hits are sticky."""
    if not active:
        return
    price, stamp = context.get("mark_price"), context.get("account_captured_at")
    if not (_number(price) and price > 0 and _number(stamp) and 0 <= now - stamp <= 120):
        return
    rows = active.get("review_memory", [])
    if not _saved_rows_valid(rows):
        active["review_status"] = "invalid_saved_memory"
        return
    for row in rows:
        if stamp < row.get("created_at", 0):
            continue
        if row["reached_at"] is None and (
                price >= row["price"] if row["operator"] == "ge" else price <= row["price"]):
            row.update(reached_at=stamp, reached_price=price, acknowledgement=None)


def apply_review(active, metadata, action_id, *, now=0, presented=None):
    """Only called after fresh core validation. Missing/bad metadata is a no-op.

    Empty lists never clear memory. Replacement requires exact host ID plus
    rationale; unresolved old checkpoints occupy capacity instead of vanishing.
    Full history remains in the original audited decision records.
    """
    if not active:
        return
    if metadata is None or not valid_review(metadata):
        active["review_status"] = "missing_metadata" if metadata is None else "invalid_metadata"
        return
    rows = active.setdefault("review_memory", [])
    if not _saved_rows_valid(rows):
        active["review_status"] = "invalid_saved_memory"
        return
    active["review_status"] = "accepted"
    by_id = {row["id"]: row for row in rows}
    presented_ids = {row["id"]: row for row in presented or []} if _saved_rows_valid(presented or []) else {}
    for ack in metadata["acknowledgements"]:
        if ack["id"] not in by_id:
            active["review_status"] = "unknown_ack_id"
            return
        if presented is not None and (ack["id"] not in presented_ids or
                by_id[ack["id"]]["reached_at"] != presented_ids[ack["id"]].get("reached_at")):
            active["review_status"] = "new_hit_not_presented"
            return
    for ack in metadata["acknowledgements"]:
        old = by_id[ack["id"]]
        if ack["disposition"] == "replace":
            rows.remove(old)
        else:
            old["acknowledgement"] = {**ack, "action_id": action_id}
    for index, condition in enumerate(metadata["conditions"]):
        if any(all(row[key] == condition[key] for key in condition) for row in rows):
            continue
        if len(rows) >= 3:
            active["review_status"] = "capacity_retained"
            break
        identity = f'{active["scenario_id"]}:{action_id}:{index}'
        rows.append({**condition, "id": "review_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                     "created_at": now, "reached_at": None, "reached_price": None, "acknowledgement": None})
