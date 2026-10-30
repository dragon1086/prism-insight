import copy
import gzip
import json

import pytest

from utils import btc_review_references as refs


def example():
    tape = {
        "inputs": {
            "snapshot": {"z": 1, "a": {"y": 2, "x": 3}},
            "context": {"q": "한글"},
        },
        "operations": [
            {"kind": "record", "event": {"z": [{"y": 2, "x": 1}], "a": "한글"}},
            {"kind": "clock", "value": 1},
        ],
    }
    raw = json.dumps(tape, allow_nan=False)
    snapshot = refs.canonical(tape["inputs"]["snapshot"])
    context = refs.canonical(tape["inputs"]["context"])
    events = [refs.canonical(tape["operations"][0]["event"])]
    return raw, snapshot, context, events


def test_reference_roundtrip_preserves_exact_text_and_key_order():
    raw, snapshot, context, events = example()
    bundle = refs.compact(raw, snapshot, context, events)
    packed = refs.pack(bundle)
    assert refs.restore(refs.unpack(packed), snapshot, context, events) == raw
    assert refs.pack(bundle) == packed


def test_v2_reader_restores_model_request_and_response_from_durable_event():
    request = {'z': {'b': 2, 'a': 1}, 'a': '한글'}
    text = '{"decision":"WAIT"}'
    event = {'kind': 'MODEL_RAW', 'actual_request': request, 'raw': text}
    tape = {'inputs': {'snapshot': {'v': 1}, 'context': {'v': 2}},
            'operations': [{'kind': 'generate', 'request': request, 'text': text},
                           {'kind': 'record', 'event': event}]}
    raw = json.dumps(tape, allow_nan=False)
    snapshot, context = (refs.canonical(tape['inputs'][key]) for key in ('snapshot', 'context'))
    events = [refs.canonical(event)]
    bundle = refs.compact(raw, snapshot, context, events)
    bundle['version'] = 2
    bundle['tape']['operations'][0]['request'] = {'$btc_model_ref': {
        'sequence': 0, 'order': refs.order_recipe(request),
        'request_sha256': refs.sha(refs.canonical(request)), 'response_sha256': refs.sha(text)}}
    bundle['tape']['operations'][0]['text'] = {'$btc_model_ref': 0}
    assert refs.restore(refs.unpack(refs.pack(bundle)), snapshot, context, events) == raw
    import sqlite3
    with sqlite3.connect(':memory:') as conn:
        conn.executescript('CREATE TABLE llm_scenario_decisions(slot INTEGER, snapshot TEXT, context TEXT);'
                           'CREATE TABLE llm_scenario_review_events(slot INTEGER, sequence INTEGER, body TEXT);')
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (1, snapshot, context))
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, events[0]))
        assert refs.read_tape(refs.pack(bundle), conn, 1, decode_legacy=lambda x: x) == raw
        conn.execute('DELETE FROM llm_scenario_review_events')
        with pytest.raises(ValueError, match='reference_event_sequence_invalid'):
            refs.read_tape(refs.pack(bundle), conn, 1, decode_legacy=lambda x: x)
    changed = copy.deepcopy(bundle)
    changed['tape']['operations'][0]['text'] = {'$btc_model_ref': 1}
    with pytest.raises(ValueError, match='invalid_model_reference'):
        refs.restore(changed, snapshot, context, events)
    changed = copy.deepcopy(bundle)
    changed['tape']['operations'][0]['request']['$btc_model_ref']['request_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='model_reference_changed'):
        refs.restore(changed, snapshot, context, events)
    changed = copy.deepcopy(bundle)
    changed['version'] = 1
    with pytest.raises(ValueError, match='tape_changed'):
        refs.restore(changed, snapshot, context, events)


def test_references_fail_closed_for_missing_or_changed_sources_and_order():
    raw, snapshot, context, events = example()
    bundle = refs.compact(raw, snapshot, context, events)
    with pytest.raises(ValueError, match="missing_event"):
        refs.restore(bundle, snapshot, context, [])
    with pytest.raises(ValueError, match="reference_changed"):
        refs.restore(bundle, snapshot + " ", context, events)
    changed = copy.deepcopy(bundle)
    changed["references"][0]["order"][0][1].reverse()
    with pytest.raises(ValueError, match="tape_changed"):
        refs.restore(changed, snapshot, context, events)
    changed = copy.deepcopy(bundle)
    changed["version"] = True
    with pytest.raises(ValueError, match="unknown_version"):
        refs.restore(changed, snapshot, context, events)


@pytest.mark.parametrize(
    "bad",
    [
        refs.MAGIC + b"invalid",
        refs.MAGIC + gzip.compress(b"{}")[:-1],
        refs.MAGIC + gzip.compress(b"{}") + b"trailing",
        refs.MAGIC + gzip.compress(b"X" * (refs.MAX_BYTES + 1)),
        b"not-a-reference",
    ],
)
def test_invalid_or_oversize_reference_never_decodes(bad):
    with pytest.raises(ValueError):
        refs.unpack(bad)


def test_noncanonical_or_mismatched_sources_are_rejected():
    raw, snapshot, context, events = example()
    with pytest.raises(ValueError, match="noncanonical_tape"):
        refs.compact(raw + " ", snapshot, context, events)
    with pytest.raises(ValueError, match="duplicate_source_mismatch"):
        refs.compact(raw, snapshot, context, ["{}"])
    with pytest.raises(ValueError, match="missing_event"):
        refs.compact(raw, snapshot, context, [])


def test_read_tape_requires_sources_and_contiguous_events():
    import sqlite3

    raw, snapshot, context, events = example()
    packed = refs.pack(refs.compact(raw, snapshot, context, events))
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE llm_scenario_decisions(slot INTEGER, snapshot TEXT, context TEXT);"
        "CREATE TABLE llm_scenario_review_events(slot INTEGER, sequence INTEGER, body TEXT);"
    )
    conn.execute(
        "INSERT INTO llm_scenario_decisions VALUES(?,?,?)", (1, snapshot, context)
    )
    conn.execute(
        "INSERT INTO llm_scenario_review_events VALUES(?,?,?)", (1, 0, events[0])
    )
    conn.execute(
        "INSERT INTO llm_scenario_review_events VALUES(?,?,?)",
        (1, 1, '{"later_receipt":true}'),
    )

    def legacy(value):
        return value if isinstance(value, str) else value.decode("utf-8")

    assert refs.read_tape(packed, conn, 1, decode_legacy=legacy) == raw
    assert refs.read_tape(raw, conn, 1, decode_legacy=legacy) == raw
    conn.execute("UPDATE llm_scenario_review_events SET sequence=2 WHERE sequence=0")
    with pytest.raises(
        ValueError, match="reference_changed|reference_event_sequence_invalid"
    ):
        refs.read_tape(packed, conn, 1, decode_legacy=legacy)
    with pytest.raises(ValueError, match="reference_decision_missing"):
        refs.read_tape(packed, conn, 2, decode_legacy=legacy)
