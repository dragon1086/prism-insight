"""Offline, evidence-bound checks; never order authority or an economic gate.

Hashes establish internal binding, not authenticity of the supplied originals.
``core_validated`` is an evidenced premise, not economic validation by this module.
VERIFIED covers only the declared checkpoint transition or annotated MA claims;
it never certifies unannotated prose, a strategy, or future profitability.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math


SCOPE = "OFFLINE_INTERNAL_BINDING_NOT_SOURCE_AUTHENTICITY_OR_TRADE_AUTHORITY"
RECEIPT_SCOPE = "ADVISORY_CHECKPOINT_MEMORY_NOT_ORDER"


def content_hash(value):
    """Type-sensitive canonical JSON content hash (not original wire bytes)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _same(left, right):
    return content_hash(left) == content_hash(right)


def _result(packet, status, reasons, **extra):
    try:
        digest = content_hash(packet)
    except (TypeError, ValueError, OverflowError):
        digest = None
    return {"status": status, "reasons": reasons, "packet_sha256": digest,
            "scope": SCOPE, **extra}


def _identifier(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 80


def _number(value, positive=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0)
    except OverflowError:
        return False


def _condition(row):
    return (isinstance(row, dict) and set(row) == {"source", "operator", "price"}
            and row["source"] == "MARK_PRICE" and row["operator"] in ("ge", "le")
            and _number(row["price"], positive=True))


def _conditions(row):
    return {key: row.get(key) for key in ("source", "operator", "price")}


def _rows(rows):
    return (isinstance(rows, list) and len(rows) <= 3
            and all(isinstance(row, dict) and _identifier(row.get("id"))
                    and _condition(_conditions(row)) and "reached_at" in row
                    and (row["reached_at"] is None or _number(row["reached_at"]))
                    and _number(row.get("created_at", 0)) for row in rows)
            and len({row["id"] for row in rows}) == len(rows))


def _metadata(value):
    if not isinstance(value, dict) or set(value) != {"conditions", "acknowledgements"}:
        return False
    conditions, acks = value["conditions"], value["acknowledgements"]
    return (isinstance(conditions, list) and isinstance(acks, list)
            and len(conditions) <= 3 and len(acks) <= 3
            and all(_condition(row) for row in conditions)
            and len({(row["operator"], row["price"]) for row in conditions}) == len(conditions)
            and all(isinstance(row, dict) and set(row) == {"id", "disposition", "reason"}
                    and _identifier(row["id"]) and row["disposition"] in ("hold", "replace")
                    and isinstance(row["reason"], str) and 1 <= len(row["reason"].strip()) <= 240
                    for row in acks)
            and len({row["id"] for row in acks}) == len(acks))


def _binding(packet, source_keys, proposal=False):
    """Return a failure or validated input context. No live imports/oracles."""
    if not isinstance(packet, dict) or type(packet.get("version")) is not int or packet["version"] != 1:
        return "INSUFFICIENT", "packet_schema", None
    binding, model_input = packet.get("binding"), packet.get("input")
    if not isinstance(binding, dict) or not isinstance(model_input, dict):
        return "INSUFFICIENT", "missing_binding_or_input", None
    sources = binding.get("source_ids")
    if (not isinstance(sources, dict) or any(not isinstance(sources.get(key), str)
            or not sources[key].strip() for key in source_keys)
            or not isinstance(binding.get("source_revision"), str) or not binding["source_revision"].strip()):
        return "INSUFFICIENT", "missing_source_identifiers", None
    ctx = model_input.get("contract_context")
    if not isinstance(ctx, dict) or not _identifier(ctx.get("input_id")):
        return "INSUFFICIENT", "missing_input_identity", None
    if not binding.get("input_sha256") or not binding.get("input_id"):
        return "INSUFFICIENT", "missing_input_binding", None
    if binding["input_sha256"] != content_hash(model_input) or binding["input_id"] != ctx["input_id"]:
        return "MISMATCH", "input_binding", None
    if proposal:
        proposed = packet.get("proposal")
        if not isinstance(proposed, dict) or not binding.get("proposal_sha256"):
            return "INSUFFICIENT", "missing_proposal_binding", None
        if binding["proposal_sha256"] != content_hash(proposed):
            return "MISMATCH", "proposal_hash", None
        suffix = hashlib.sha256(ctx["input_id"].encode()).hexdigest()[:24]
        scenario = ctx.get("scenario_id") if ctx.get("scenario_id") is not None else "s_" + suffix
        action = "a_" + suffix
        if any(binding.get(key) != expected or proposed.get(key) != expected for key, expected in
               (("input_id", ctx["input_id"]), ("scenario_id", scenario), ("action_id", action))):
            return "MISMATCH", "action_scenario_binding", None
        if proposed.get("action") not in ("OPEN", "WAIT", "ADJUST", "EXIT"):
            return "INSUFFICIENT", "unsupported_action", None
    return None, None, ctx


def verify_checkpoint_transition(packet):
    """Check the bound immediate apply-review boundary, not whole runtime state.

    Required: version, input, proposal, binding (input/proposal hashes, IDs,
    source_revision and source_ids for input/proposal/before/after/receipt/
    core_validation), core_validated, before/presented/after row arrays,
    applied_at, receipt. OPEN derives its scenario from input_id when flat.
    Missing/corrupt metadata lies outside the VERIFIED domain, even if a no-op.
    """
    try:
        return _checkpoint(packet)
    except (KeyError, TypeError, ValueError, OverflowError):
        return _result(packet, "INSUFFICIENT", ["corrupt_or_non_json_evidence"])


def _checkpoint(packet):
    failure, reason, ctx = _binding(packet, ("input", "proposal", "before", "after", "receipt", "core_validation"), True)
    if failure:
        return _result(packet, failure, [reason])
    if any(key not in packet for key in ("before", "presented", "after", "receipt", "applied_at", "core_validated")):
        return _result(packet, "INSUFFICIENT", ["missing_transition_boundary"])
    before, after, presented = packet["before"], packet["after"], packet["presented"]
    if packet["core_validated"] is not True:
        mutated = not _same(before, after) or packet["receipt"] is not None
        return _result(packet, "MISMATCH" if mutated else "INSUFFICIENT", ["core_validation_not_established"])
    if not all(_rows(rows) for rows in (before, presented)) or not _number(packet["applied_at"]):
        return _result(packet, "INSUFFICIENT", ["corrupt_boundary"])
    if "review_memory" not in ctx:
        return _result(packet, "INSUFFICIENT", ["missing_presented_input"])
    if not _same(presented, ctx["review_memory"]):
        return _result(packet, "MISMATCH", ["presented_input_binding"])
    old = {row["id"]: row for row in before}
    shown = {row["id"]: row for row in presented}
    if any(key in old and not _same(_conditions(row), _conditions(old[key])) for key, row in shown.items()):
        return _result(packet, "MISMATCH", ["same_id_condition_changed"])
    metadata = packet["proposal"].get("review")
    if not _metadata(metadata):
        return _result(packet, "INSUFFICIENT" if _same(before, after) else "MISMATCH",
                       ["metadata_outside_verified_domain"])
    binding = packet["binding"]
    acks = metadata["acknowledgements"]
    rejection = next(("unknown_ack_id" if ack["id"] not in old else "new_hit_not_presented"
                      for ack in acks if ack["id"] not in old or ack["id"] not in shown
                      or not _same(old[ack["id"]]["reached_at"], shown[ack["id"]]["reached_at"])), None)
    retained = copy.deepcopy(before)
    condition_receipts = [{**row, "status": "NOT_APPLIED"} for row in metadata["conditions"]]
    ack_receipts = [{"id": row["id"], "disposition": row["disposition"], "status": "NOT_APPLIED"} for row in acks]
    status = rejection or "accepted"
    if rejection is None:
        removed = {ack["id"] for ack in acks if ack["disposition"] == "replace"}
        held = {ack["id"]: ack for ack in acks if ack["disposition"] == "hold"}
        retained = [row for row in retained if row["id"] not in removed]
        for row in retained:
            if row["id"] in held:
                row["acknowledgement"] = {**held[row["id"]], "action_id": binding["action_id"]}
        for receipt in ack_receipts:
            receipt["status"] = "REPLACED" if receipt["id"] in removed else "HELD"
        for index, condition in enumerate(metadata["conditions"]):
            # Numeric equality follows the host condition contract; retained
            # content is nevertheless compared type-sensitively below.
            existing = next((row for row in retained if _conditions(row) == condition), None)
            receipt = condition_receipts[index]
            if existing is not None:
                receipt.update(status="ALREADY_RETAINED", id=existing["id"])
            elif len(retained) == 3:
                receipt["status"] = "NOT_ADDED_CAPACITY"
                status = "capacity_retained"
            else:
                raw_id = f'{binding["scenario_id"]}:{binding["action_id"]}:{index}'
                identifier = "review_" + hashlib.sha256(raw_id.encode()).hexdigest()[:24]
                retained.append({**condition, "id": identifier, "created_at": packet["applied_at"],
                                 "reached_at": None, "reached_price": None, "acknowledgement": None})
                receipt.update(status="ADDED", id=identifier)
    expected = {"version": 1, "scope": RECEIPT_SCOPE, "action_id": binding["action_id"], "status": status,
                "conditions": condition_receipts, "acknowledgements": ack_receipts,
                "retained_ids": [row["id"] for row in retained], "free_capacity": 3 - len(retained)}
    errors = []
    if not _same(retained, after):
        errors.append("checkpoint_state_transition")
    if not _same(expected, packet["receipt"]):
        errors.append("checkpoint_receipt_transition")
    return _result(packet, "MISMATCH" if errors else "VERIFIED", errors,
                   verified_domain="BOUND_VALID_METADATA_IMMEDIATE_CHECKPOINT_TRANSITION",
                   core_validation="SUPPLIED_EVIDENCE_PREMISE_NOT_REVALIDATED",
                   expected_status=status)


def verify_ma_claims(packet):
    """Verify explicitly annotated references, never extract/certify prose.

    In addition to input/proposal/action binding, require source_text equal to
    proposal.rationale, source_text_sha256, source_ids.text, and claims.
    Claims contain level_id, timeframe, ma, phase,
    price, as_of_ms, basis, input_sha256, origin (manual_annotation or
    model_structured), excerpt. Excerpts must occur in the bound original text.
    Annotation interpretation itself is not certified.
    """
    try:
        return _ma(packet)
    except (KeyError, TypeError, ValueError, OverflowError):
        return _result(packet, "INSUFFICIENT", ["corrupt_or_non_json_evidence"])


def _ma(packet):
    failure, reason, ctx = _binding(packet, ("input", "proposal", "text"), True)
    if failure:
        return _result(packet, failure, [reason])
    text, claims = packet.get("source_text"), packet.get("claims")
    if not isinstance(text, str) or not isinstance(claims, list) or not packet.get("source_text_sha256"):
        return _result(packet, "INSUFFICIENT", ["missing_text_or_claims"])
    if packet["source_text_sha256"] != content_hash(text) or packet["proposal"].get("rationale") != text:
        return _result(packet, "MISMATCH", ["source_text_binding"])
    structure = ctx.get("ma_structure")
    if not isinstance(structure, dict) or not isinstance(structure.get("levels"), dict):
        return _result(packet, "INSUFFICIENT", ["missing_canonical_levels"])
    results = []
    for index, claim in enumerate(claims):
        status, reasons = "VERIFIED", []
        if not isinstance(claim, dict) or not claim.get("level_id") or claim["level_id"] not in structure["levels"]:
            status, reasons = "MISSING_REFERENCE", ["exact_level_id_required"]
        elif (claim.get("origin") not in ("manual_annotation", "model_structured")
              or not isinstance(claim.get("excerpt"), str) or not claim["excerpt"].strip()):
            status, reasons = "INSUFFICIENT", ["missing_claim_origin_or_excerpt"]
        elif claim["excerpt"] not in text or claim.get("input_sha256") != packet["binding"]["input_sha256"]:
            status, reasons = "MISMATCH", ["claim_source_binding"]
        else:
            source = structure["levels"][claim["level_id"]]
            required = ("timeframe", "ma", "price", "as_of_ms", "is_confirmed")
            if (not isinstance(source, dict) or any(key not in source for key in required)
                    or type(source["is_confirmed"]) is not bool or not structure.get("levels_price_basis")
                    or not _number(source["price"], positive=True) or not _number(source["as_of_ms"])):
                status, reasons = "INSUFFICIENT", ["incomplete_canonical_source"]
            else:
                expected = {key: source[key] for key in ("timeframe", "ma", "price", "as_of_ms")}
                expected.update(phase="confirmed" if source["is_confirmed"] else "forming",
                                basis=structure["levels_price_basis"])
                canonical_id = f'{source["timeframe"]}.{source["ma"]}.{expected["phase"]}'
                if (source["timeframe"] not in ("4h", "12h", "1d", "1w")
                        or source["ma"] not in ("ma10", "ma35") or canonical_id != claim["level_id"]):
                    results.append({"index": index, "status": "INSUFFICIENT",
                                    "reasons": ["noncanonical_source_identity"], "origin": claim["origin"]})
                    continue
                missing = [key for key in expected if key not in claim]
                wrong = [key for key in expected if key in claim and not _same(claim[key], expected[key])]
                if missing:
                    status, reasons = "INSUFFICIENT", ["missing_claim_fields:" + ",".join(missing)]
                elif wrong:
                    status, reasons = "MISMATCH", wrong
        results.append({"index": index, "status": status, "reasons": reasons,
                        "origin": claim.get("origin") if isinstance(claim, dict) else None})
    statuses = {row["status"] for row in results}
    overall = next((state for state in ("MISMATCH", "INSUFFICIENT", "MISSING_REFERENCE") if state in statuses),
                   "VERIFIED" if results else "UNASSESSED_PROSE")
    return _result(packet, overall, [], claims=results, assessed_claims=len(results),
                   verified_claims=sum(row["status"] == "VERIFIED" for row in results),
                   prose_status="UNASSESSED_PROSE", annotation_semantics="NOT_CERTIFIED",
                   verified_domain="EXPLICIT_ANNOTATED_CANONICAL_REFERENCES_ONLY")
