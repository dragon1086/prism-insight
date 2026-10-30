"""Lossless reference encoding for already durable BTC review evidence."""

import copy
import gzip
import hashlib
import json
import zlib

MAGIC = b"PRISM_REVIEW_REFS_V1\x00"
MAX_BYTES = 8_000_000
MAX_REFERENCES = 130


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def order_recipe(value):
    changes = []

    def visit(node, path):
        if isinstance(node, dict):
            keys = list(node)
            if keys != sorted(keys):
                changes.append([path, keys])
            for key, child in node.items():
                visit(child, path + [key])
        elif isinstance(node, list):
            for i, child in enumerate(node):
                visit(child, path + [i])

    visit(value, [])
    return changes


def restore_order(value, recipe):
    if not isinstance(recipe, list):
        raise ValueError("invalid_order_recipe")
    mapping = {}
    for entry in recipe:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not isinstance(entry[0], list)
            or not isinstance(entry[1], list)
            or any(
                not isinstance(x, str) and not (type(x) is int and x >= 0)
                for x in entry[0]
            )
            or any(not isinstance(x, str) for x in entry[1])
        ):
            raise ValueError("invalid_order_recipe")
        path, keys = entry
        if tuple(path) in mapping:
            raise ValueError("duplicate_order_path")
        mapping[tuple(path)] = keys
    if len(mapping) != len(recipe):
        raise ValueError("duplicate_order_path")
    visited = set()

    def visit(node, path):
        key = tuple(path)
        if isinstance(node, dict):
            keys = mapping.get(key, list(node))
            if set(keys) != set(node) or len(keys) != len(node):
                raise ValueError("invalid_order_keys")
            if key in mapping:
                visited.add(key)
            return {k: visit(node[k], path + [k]) for k in keys}
        if isinstance(node, list):
            return [visit(x, path + [i]) for i, x in enumerate(node)]
        return node

    out = visit(value, [])
    if visited != set(mapping):
        raise ValueError("unknown_order_path")
    return out


def compact(raw_tape, snapshot, context, event_bodies):
    if not isinstance(raw_tape, str) or len(raw_tape.encode("utf-8")) > MAX_BYTES:
        raise ValueError("invalid_tape")
    tape = json.loads(raw_tape)
    if json.dumps(tape, allow_nan=False) != raw_tape:
        raise ValueError("noncanonical_tape")
    refs = []

    def make_ref(kind, index, value, source):
        if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_BYTES:
            raise ValueError("invalid_reference_source")
        if len(refs) >= MAX_REFERENCES:
            raise ValueError("too_many_references")
        if canonical(value) != source:
            raise ValueError("duplicate_source_mismatch")
        meta = {
            "kind": kind,
            "index": index,
            "sha256": sha(source),
            "order": order_recipe(value),
        }
        refs.append(meta)
        return {"$btc_ref": len(refs) - 1}

    for name, source in (("snapshot", snapshot), ("context", context)):
        tape["inputs"][name] = make_ref(name, None, tape["inputs"][name], source)
    sequence = 0
    for op in tape["operations"]:
        if op.get("kind") == "record":
            if sequence >= len(event_bodies):
                raise ValueError("missing_event")
            op["event"] = make_ref(
                "event", sequence, op["event"], event_bodies[sequence]
            )
            sequence += 1
    return {
        "version": 1,
        "tape": tape,
        "references": refs,
        "tape_sha256": sha(raw_tape),
    }


def restore(bundle, snapshot, context, event_bodies):
    if not isinstance(bundle, dict):
        raise ValueError("reference_body_invalid")
    obj = copy.deepcopy(bundle)
    if type(obj.get("version")) is not int or obj["version"] not in (1, 2):
        raise ValueError("unknown_version")
    if set(obj) != {"version", "tape", "references", "tape_sha256"}:
        raise ValueError("reference_body_invalid")
    tape = obj["tape"]
    refs = obj["references"]
    used = set()
    if not isinstance(tape, dict) or not isinstance(event_bodies, list):
        raise ValueError("reference_body_invalid")
    if not isinstance(refs, list) or not 2 <= len(refs) <= MAX_REFERENCES:
        raise ValueError("invalid_reference_count")

    def deref(marker, kind, index, source):
        if not isinstance(marker, dict) or set(marker) != {"$btc_ref"}:
            raise ValueError("invalid_marker")
        n = marker["$btc_ref"]
        if type(n) is not int or not 0 <= n < len(refs) or n in used:
            raise ValueError("invalid_reference")
        ref = refs[n]
        if not isinstance(ref, dict) or set(ref) != {
            "kind",
            "index",
            "sha256",
            "order",
        }:
            raise ValueError("invalid_reference")
        if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_BYTES:
            raise ValueError("invalid_reference_source")
        if ref["kind"] != kind or ref["index"] != index or ref["sha256"] != sha(source):
            raise ValueError("reference_changed")
        used.add(n)
        return restore_order(json.loads(source), ref["order"])

    for name, source in (("snapshot", snapshot), ("context", context)):
        tape["inputs"][name] = deref(tape["inputs"][name], name, None, source)
    sequence = 0
    model_refs = 0
    for op in tape["operations"]:
        if op.get("kind") == "record":
            if sequence >= len(event_bodies):
                raise ValueError("missing_event")
            op["event"] = deref(op["event"], "event", sequence, event_bodies[sequence])
            sequence += 1
        elif obj['version'] == 2 and op.get('kind') == 'generate':
            request, response = op.get('request'), op.get('text')
            if isinstance(request, dict) and set(request) == {'$btc_model_ref'}:
                ref = request['$btc_model_ref']
                if (not isinstance(ref, dict) or set(ref) != {'sequence', 'order', 'request_sha256', 'response_sha256'}
                        or type(ref['sequence']) is not int or not 0 <= ref['sequence'] < len(event_bodies)
                        or not isinstance(response, dict) or set(response) != {'$btc_model_ref'}
                        or type(response['$btc_model_ref']) is not int or response['$btc_model_ref'] != ref['sequence']):
                    raise ValueError('invalid_model_reference')
                source = event_bodies[ref['sequence']]
                if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_BYTES:
                    raise ValueError('invalid_reference_source')
                event = json.loads(source)
                if (not isinstance(event, dict) or event.get('kind') != 'MODEL_RAW'
                        or not isinstance(event.get('actual_request'), dict) or not isinstance(event.get('raw'), str)
                        or sha(canonical(event['actual_request'])) != ref['request_sha256']
                        or sha(event['raw']) != ref['response_sha256']):
                    raise ValueError('model_reference_changed')
                op['request'] = restore_order(event['actual_request'], ref['order'])
                op['text'] = event['raw']
                model_refs += 1
            elif not isinstance(request, dict) or ('text' in op and not isinstance(response, str)):
                raise ValueError('invalid_model_reference')
    if obj['version'] == 2 and not model_refs:
        raise ValueError('missing_model_reference')
    if len(used) != len(refs):
        raise ValueError("unreferenced_metadata")
    result = json.dumps(tape, allow_nan=False)
    if len(result.encode("utf-8")) > MAX_BYTES:
        raise ValueError("restored_tape_too_large")
    if sha(result) != obj.get("tape_sha256"):
        raise ValueError("tape_changed")
    return result


def pack(bundle):
    """Encode a reference envelope with the same bounded-gzip discipline as tapes."""
    raw = json.dumps(bundle, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("reference_body_too_large")
    return MAGIC + gzip.compress(raw, compresslevel=1, mtime=0)


def unpack(value):
    if not isinstance(value, bytes) or not value.startswith(MAGIC):
        raise ValueError("reference_format_invalid")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = decoder.decompress(value[len(MAGIC) :], MAX_BYTES + 1)
        if (
            len(raw) > MAX_BYTES
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise ValueError("reference_compression_invalid")
        result = json.loads(raw.decode("utf-8"))
    except (zlib.error, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("reference_compression_invalid") from exc
    if not isinstance(result, dict):
        raise ValueError("reference_body_invalid")
    return result


def read_tape(value, conn, slot, *, decode_legacy):
    """Resolve referenced tapes against the same database; never repair missing data."""
    if not isinstance(value, bytes) or not value.startswith(MAGIC):
        return decode_legacy(value)
    if conn is None or type(slot) is not int or slot < 0:
        raise ValueError("reference_context_missing")
    bundle = unpack(value)
    references = bundle.get("references")
    if not isinstance(references, list) or not 2 <= len(references) <= MAX_REFERENCES:
        raise ValueError("invalid_reference_count")
    event_count = len(references) - 2
    decision = conn.execute(
        "SELECT snapshot,context FROM llm_scenario_decisions WHERE slot=?", (slot,)
    ).fetchone()
    if not decision or not all(isinstance(item, str) for item in decision):
        raise ValueError("reference_decision_missing")
    rows = conn.execute(
        "SELECT sequence,body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence LIMIT ?",
        (slot, event_count),
    )
    events = []
    total_bytes = 0
    for sequence, body in rows:
        if type(sequence) is not int or sequence != len(events):
            raise ValueError("reference_event_sequence_invalid")
        text = decode_legacy(body)
        if not isinstance(text, str):
            raise ValueError("invalid_reference_source")
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_BYTES:
            raise ValueError("reference_sources_too_large")
        events.append(text)
    if len(events) != event_count:
        raise ValueError("reference_event_sequence_invalid")
    try:
        return restore(bundle, decision[0], decision[1], events)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("reference_body_invalid") from exc
