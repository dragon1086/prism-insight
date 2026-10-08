"""Reversible model-only factoring of exact duplicates; never truncate evidence."""
from copy import deepcopy
import hashlib
import json


def _json(value, *, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=sort_keys)


def _definition_rows(payload, name):
    snapshot = payload.get("market_snapshot")
    frames = snapshot.get("timeframes", {}) if isinstance(snapshot, dict) else None
    if not isinstance(frames, dict):
        return []
    return [(f"market_snapshot.timeframes.{tf}.{kind}.{name}", feature)
            for tf, frame in frames.items() if isinstance(frame, dict)
            for kind in ("confirmed", "forming")
            if isinstance(feature := frame.get(kind), dict) and name in feature]


def project_model_input(payload):
    """Return independent projected payload and value-free byte/hash/path audit."""
    original = _json(payload)
    projected = deepcopy(payload)
    if "model_input_projection" in payload:
        raise ValueError("reserved_projection_field")
    references, definitions, paths = [], {}, []
    recovery = projected.get("contract_context", {}).get("recovery")
    if isinstance(recovery, dict) and isinstance(recovery.get("current"), dict):
        from live.scenario_recovery import numeric_market
        try:
            current = numeric_market(projected["market_snapshot"])
        except (AttributeError, KeyError, TypeError, ValueError):
            current = None
        # JSON comparison is type-sensitive (True != 1, 1 != 1.0), unlike dict equality.
        if current is not None and _json(current, sort_keys=True) == _json(recovery["current"], sort_keys=True):
            recovery["current"] = {"$ref": "market_snapshot", "projection": "numeric_market-v1"}
            references.append("contract_context.recovery.current")
            paths.append("contract_context.recovery.current")
    for name in ("compression_definition", "convergence_definition"):
        rows = _definition_rows(projected, name)
        values = [row[name] for _, row in rows]
        if len(values) > 1 and all(isinstance(v, str) and v == values[0] for v in values):
            definitions[name] = values[0]
            for path, row in rows:
                row[name] = {"$feature_definition": name}
                paths.append(path)
    if paths:
        projected["model_input_projection"] = {
            "version": 1, "numeric_market_references": references, "feature_definitions": definitions,
            "meaning": "Lossless references only. Resolve numeric_market-v1 as the supplied snapshot numeric paths; feature-definition references use this shared dictionary. All original values are retained or exactly reproducible."}
    encoded = _json(projected)
    audit = {"version": 1, "original_utf8_bytes": len(original.encode()),
             "projected_utf8_bytes": len(encoded.encode()),
             "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
             "projected_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
             "wire_hash_basis": "compact_utf8_json_insertion_order",
             "semantic_hash_basis": "compact_utf8_json_sorted_keys_preserves_numeric_types",
             "original_semantic_sha256": hashlib.sha256(_json(payload, sort_keys=True).encode()).hexdigest(),
             "projected_semantic_sha256": hashlib.sha256(_json(projected, sort_keys=True).encode()).hexdigest(),
             "factored_paths": paths}
    return projected, audit


def restore_model_input(payload):
    """Deterministic reconstruction for audit/tests; no network or model calls."""
    original = deepcopy(payload)
    metadata = original.pop("model_input_projection", None)
    if metadata is None:
        return original
    if metadata.get("version") != 1:
        raise ValueError("unknown_projection_version")
    for name, definition in metadata["feature_definitions"].items():
        for _, row in _definition_rows(original, name):
            if row[name] == {"$feature_definition": name}:
                row[name] = definition
    if "contract_context.recovery.current" in metadata["numeric_market_references"]:
        from live.scenario_recovery import numeric_market
        original["contract_context"]["recovery"]["current"] = numeric_market(original["market_snapshot"])
    return original
