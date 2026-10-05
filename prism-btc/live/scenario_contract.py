"""Strict model wire protocol; economic validation remains in core.llm_scenario.

Responses requires every property to be required. WAIT/EXIT therefore carry
explicit null side/stop/chase placeholders. Only those validated nulls are
removed when converting to the existing economic-validator protocol.
"""
from __future__ import annotations

import hashlib
import math


def identity_fields(context):
    input_id = context["input_id"]
    if not isinstance(input_id, str) or not input_id.strip():
        raise ValueError("invalid_contract_input_id")
    revision = context["revision"]
    if type(revision) is not int or revision < 0:
        raise ValueError("invalid_contract_revision")
    suffix = hashlib.sha256(input_id.encode()).hexdigest()[:24]
    scenario_id = context.get("scenario_id")
    if scenario_id is not None and (not isinstance(scenario_id, str) or not scenario_id.strip()):
        raise ValueError("invalid_contract_scenario_id")
    return dict(schema_version=1, input_id=input_id, revision=revision + 1,
                scenario_id=scenario_id if scenario_id is not None else "s_" + suffix,
                action_id="a_" + suffix)


def _object(properties):
    return dict(type="object", properties=properties, required=list(properties),
                additionalProperties=False)


def response_schema(context):
    properties = {key: {"type": "integer" if type(value) is int else "string",
                        "enum": [value]} for key, value in identity_fields(context).items()}
    row = lambda quantity: _object({"id": {"type": "string"},
                                    "price": {"type": "number"},
                                    quantity: {"type": "number"}})
    cancellation_id = {"type": "string"}
    pending_ids = [row["id"] for row in context.get("pending_entries", [])]
    if pending_ids:
        cancellation_id["enum"] = pending_ids
    properties.update(action={"type": "string", "enum": ["WAIT", "ADJUST", "EXIT"]
                      if context.get("scenario_id") is not None else ["WAIT", "OPEN"]},
                      side={"type": ["string", "null"], "enum": ["LONG", "SHORT", None]},
                      confidence={"type": "number"}, expires_at={"type": "number"},
                      hard_stop={"type": ["number", "null"]},
                      entries={"type": "array", "items": row("quantity")},
                      take_profits={"type": "array", "items": row("fraction")},
                      partial_stops={"type": "array", "items": row("fraction")},
                      cancel_entry_ids={"type": "array", "items": cancellation_id,
                                        "maxItems": min(20, len(pending_ids))},
                      chase={"anyOf": [_object({"max_bps": {"type": "number"},
                                               "max_reprices": {"type": "integer"}}),
                                       {"type": "null"}]},
                      rationale={"type": "string"}, leverage={"type": "integer", "enum": [10]})
    if context.get("review_contract_version") == 1:
        properties["review"] = {"anyOf": [_object({
            "conditions": {"type": "array", "maxItems": 3, "items": _object({
                "source": {"type": "string", "enum": ["MARK_PRICE"]},
                "operator": {"type": "string", "enum": ["ge", "le"]},
                "price": {"type": "number"}})},
            "acknowledgements": {"type": "array", "maxItems": 3, "items": _object({
                "id": {"type": "string"},
                "disposition": {"type": "string", "enum": ["hold", "replace"]},
                "reason": {"type": "string"}})}}), {"type": "null"}]}
    return _object(properties)


def _matches(value, schema):
    """Validate exactly the small JSON-schema subset emitted above, fail closed."""
    if "anyOf" in schema:
        return any(_matches(value, candidate) for candidate in schema["anyOf"])
    kind = schema["type"]
    kinds = kind if isinstance(kind, list) else [kind]
    actual = ("null" if value is None else "boolean" if type(value) is bool else
              "integer" if type(value) is int else "number" if type(value) is float else
              "string" if isinstance(value, str) else "object" if isinstance(value, dict) else
              "array" if isinstance(value, list) else "invalid")
    if actual not in kinds and not (actual == "integer" and "number" in kinds):
        return False
    if actual in ("integer", "number"):
        try:
            if not math.isfinite(value):
                return False
        except OverflowError:
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if actual == "object":
        return (set(value) == set(schema["required"]) and
                all(_matches(value[key], child) for key, child in schema["properties"].items()))
    if actual == "array":
        return len(value) <= min(20, schema.get("maxItems",20)) and all(_matches(item, schema["items"]) for item in value)
    return True


def validate_wire_proposal(payload, context):
    schema = response_schema(context)
    if not isinstance(payload, dict):
        raise ValueError("response_contract_mismatch")
    # Advisory metadata must never veto otherwise valid protective actions.
    # Keep its original value for the decision audit; the runtime ignores invalid
    # metadata and strips it before all economic validation / execution.
    has_review = context.get("review_contract_version") == 1
    review = payload.get("review") if has_review else None
    if has_review:
        payload = {key: value for key, value in payload.items() if key != "review"}
        schema = dict(schema, properties={key: value for key, value in schema["properties"].items() if key != "review"},
                      required=[key for key in schema["required"] if key != "review"])
    if set(payload) - set(schema["properties"]):
        raise ValueError("response_unknown_fields")
    if payload.get("scenario_id") is None:
        raise ValueError("response_missing_scenario_id")
    expected = identity_fields(context)
    if type(payload.get("revision")) is not int or payload["revision"] != expected["revision"]:
        raise ValueError("response_revision_mismatch")
    if any(payload.get(key) != expected[key] for key in ("input_id", "scenario_id", "action_id")):
        raise ValueError("response_identity_mismatch")
    if not _matches(payload, schema):
        raise ValueError("response_contract_mismatch")
    normalized = dict(payload)
    if payload["action"] in ("WAIT", "EXIT"):
        if any(payload[key] is not None for key in ("side", "hard_stop", "chase")):
            raise ValueError("response_contract_inactive_protection")
        if any(payload[key] for key in ("entries", "take_profits", "partial_stops")):
            raise ValueError("response_contract_inactive_orders")
        for key in ("side", "hard_stop", "chase"):
            del normalized[key]
    elif any(payload[key] is None for key in ("side", "hard_stop", "chase")):
        raise ValueError("response_contract_missing_protection")
    if has_review:
        normalized["review"] = review
    return normalized
