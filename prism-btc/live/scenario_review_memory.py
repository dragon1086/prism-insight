"""Bounded advisory checkpoints, never order authority or risk validation."""
from __future__ import annotations

import hashlib
import copy
import math


RECEIPT_SCOPE = "ADVISORY_CHECKPOINT_MEMORY_NOT_ORDER"


def _identifier(value):
    return isinstance(value, str) and 1 <= len(value) <= 80


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
        if (not isinstance(row, dict) or not _identifier(row.get("id"))
                or not valid_review({"conditions": [{key: row.get(key) for key in
                    ("source", "operator", "price")}], "acknowledgements": []})
                or "reached_at" not in row
                or not (row["reached_at"] is None or _number(row["reached_at"]))
                or not _number(row.get("created_at", 0))):
            return False
    return len({row["id"] for row in rows}) == len(rows)


def review_context(active):
    """Bounded host receipt, absent for legacy state; no proposal prose copied."""
    rows = active.get("review_memory", []) if active else []
    receipt = active.get("review_update_receipt") if active else None
    return {"review_free_capacity": 3 - len(rows) if _saved_rows_valid(rows) else None,
            "review_update_receipt": copy.deepcopy(receipt) if _receipt_valid(receipt) else None}


def _receipt_valid(value):
    if (not isinstance(value, dict) or set(value) != {"version", "scope", "action_id", "status",
            "conditions", "acknowledgements", "retained_ids", "free_capacity"}
            or type(value["version"]) is not int or value["version"] != 1 or value["scope"] != RECEIPT_SCOPE
            or not (value["action_id"] is None or _identifier(value["action_id"]))
            or value["status"] not in ("accepted", "capacity_retained", "missing_metadata",
                "invalid_metadata", "invalid_saved_memory", "unknown_ack_id", "new_hit_not_presented")
            or not (value["free_capacity"] is None or
                    type(value["free_capacity"]) is int and 0 <= value["free_capacity"] <= 3)):
        return False
    for key in ("conditions", "acknowledgements", "retained_ids"):
        if not isinstance(value[key], list) or len(value[key]) > 3:
            return False
    if not all(_identifier(ident) for ident in value["retained_ids"]):
        return False
    for row in value["conditions"]:
        if (not isinstance(row, dict) or set(row) not in (
                {"source", "operator", "price", "status"}, {"source", "operator", "price", "status", "id"})
                or row["status"] not in ("ADDED", "ALREADY_RETAINED", "NOT_ADDED_CAPACITY", "NOT_APPLIED")
                or "id" in row and not _identifier(row["id"])
                or not valid_review({"conditions": [{k: row[k] for k in ("source", "operator", "price")}],
                                     "acknowledgements": []})):
            return False
    return all(isinstance(row, dict) and set(row) == {"id", "disposition", "status"}
               and _identifier(row["id"]) and row["disposition"] in ("hold", "replace")
               and row["status"] in ("HELD", "REPLACED", "NOT_APPLIED")
               for row in value["acknowledgements"])


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
    receipt = {"version": 1, "scope": RECEIPT_SCOPE,
               "action_id": action_id if _identifier(action_id) else None,
               "conditions": [], "acknowledgements": []}

    def finish(status):
        saved = active.get("review_memory", [])
        valid = _saved_rows_valid(saved)
        active["review_status"] = status
        receipt.update(status=status, retained_ids=[row["id"] for row in saved] if valid else [],
                       free_capacity=3 - len(saved) if valid else None)
        active["review_update_receipt"] = receipt
        return copy.deepcopy(receipt)

    if metadata is None or not valid_review(metadata):
        return finish("missing_metadata" if metadata is None else "invalid_metadata")
    receipt["conditions"] = [{**row, "status": "NOT_APPLIED"} for row in metadata["conditions"]]
    receipt["acknowledgements"] = [{"id": row["id"], "disposition": row["disposition"],
                                    "status": "NOT_APPLIED"} for row in metadata["acknowledgements"]]
    rows = active.setdefault("review_memory", [])
    if not _saved_rows_valid(rows):
        return finish("invalid_saved_memory")
    status = "accepted"
    by_id = {row["id"]: row for row in rows}
    presented_ids = {row["id"]: row for row in presented or []} if _saved_rows_valid(presented or []) else {}
    for ack in metadata["acknowledgements"]:
        if ack["id"] not in by_id:
            return finish("unknown_ack_id")
        if presented is not None and (ack["id"] not in presented_ids or
                by_id[ack["id"]]["reached_at"] != presented_ids[ack["id"]].get("reached_at")):
            return finish("new_hit_not_presented")
    for index, ack in enumerate(metadata["acknowledgements"]):
        old = by_id[ack["id"]]
        if ack["disposition"] == "replace":
            rows.remove(old)
        else:
            old["acknowledgement"] = {**ack, "action_id": action_id}
        receipt["acknowledgements"][index]["status"] = "REPLACED" if ack["disposition"] == "replace" else "HELD"
    for index, condition in enumerate(metadata["conditions"]):
        existing = next((row for row in rows if all(row[key] == condition[key] for key in condition)), None)
        if existing is not None:
            receipt["conditions"][index].update(status="ALREADY_RETAINED", id=existing["id"])
            continue
        if len(rows) >= 3:
            status = "capacity_retained"
            receipt["conditions"][index]["status"] = "NOT_ADDED_CAPACITY"
            continue
        identity = f'{active["scenario_id"]}:{action_id}:{index}'
        rows.append({**condition, "id": "review_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                     "created_at": now, "reached_at": None, "reached_price": None, "acknowledgement": None})
        receipt["conditions"][index].update(status="ADDED", id=rows[-1]["id"])
    return finish(status)
