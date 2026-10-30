import gzip
import json
import random
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from utils import btc_review_event_references as refs
from observability.review_storage import MAGIC as LEGACY_MAGIC, review_text


def canonical(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def sample():
    large = random.Random(17).randbytes(16000).hex()
    request = {'kind': 'MODEL_REQUEST', 'phase': 'single', 'request': {
        'system_prompt': large, 'user_prompt': large[::-1], 'timeout': 50.0, 'model': 'fixture'}}
    actual = dict(request['request'], timeout=49.75)
    raw = {'kind': 'MODEL_RAW', 'phase': 'single', 'actual_request': actual,
           'raw': '{"action":"WAIT"}', 'actual_request_sha256': 'a' * 64}
    return canonical(request), canonical(raw)


def test_unique_matching_request_restores_exact_original_bytes():
    request, raw = sample()
    encoded = refs.compact(raw, [(2, request)], 100, 3)
    assert isinstance(encoded, bytes) and encoded.startswith(refs.MAGIC)
    assert len(encoded) < len(gzip.compress(raw.encode(), compresslevel=1, mtime=0))
    assert refs.restore(encoded, request, 100, 3) == raw


def test_ambiguous_missing_or_noncanonical_evidence_keeps_original():
    request, raw = sample()
    assert refs.compact(raw, [], 100, 3) == raw
    assert refs.compact(raw, [(1, request), (2, request)], 100, 3) == raw
    assert refs.compact(raw, [(3, request)], 100, 3) == raw
    assert refs.compact(raw + ' ', [(2, request)], 100, 3) == raw + ' '
    changed = json.loads(request)
    changed['request']['model'] = 'different'
    assert refs.compact(raw, [(2, canonical(changed))], 100, 3) == raw
    assert refs.compact('[]', [(2, request)], 100, 3) == '[]'
    assert refs.compact(raw, [(2, '[]')], 100, 3) == raw


def test_bool_and_number_are_not_treated_as_identical_json():
    request, raw = sample()
    left, right = json.loads(request), json.loads(raw)
    left['request']['flag'] = 1
    right['actual_request']['flag'] = True
    assert refs.compact(canonical(right), [(2, canonical(left))], 100, 3) == canonical(right)


def test_reference_rejects_corrupt_or_changed_sources():
    request, raw = sample()
    encoded = refs.compact(raw, [(2, request)], 100, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded, request + ' ', 100, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded, request, 101, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded, request, 100, 4)
    with pytest.raises(ValueError):
        refs.restore(encoded[:-4], request, 100, 3)
    with pytest.raises(ValueError):
        refs.restore(encoded + b'extra', request, 100, 3)


def test_database_reader_preserves_legacy_and_fails_closed_on_source_loss():
    request, raw = sample()
    encoded = refs.compact(raw, [(2, request)], 100, 3)
    compressed = LEGACY_MAGIC + gzip.compress(request.encode(), mtime=0)
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body BLOB,PRIMARY KEY(slot,sequence))')
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (100, 2, compressed))
        assert refs.read_event(compressed, conn, 100, 2, decode_legacy=review_text) == request
        assert refs.read_event(encoded, conn, 100, 3, decode_legacy=review_text) == raw
        conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=?', (request + ' ', 100))
        with pytest.raises(ValueError):
            refs.read_event(encoded, conn, 100, 3, decode_legacy=review_text)
        conn.execute('DELETE FROM llm_scenario_review_events')
        with pytest.raises(ValueError, match='source_missing'):
            refs.read_event(encoded, conn, 100, 3, decode_legacy=review_text)
    with pytest.raises(ValueError, match='context_missing'):
        refs.read_event(encoded, None, 100, 3, decode_legacy=review_text)


def test_all_reference_readers_preserve_logical_evidence(tmp_path):
    from observability import btc_scenario_export as exporter
    from utils import btc_offsite_archive, btc_recovery_references, btc_review_references

    request, raw = sample()
    raw_object = json.loads(raw)
    raw_object['source_commit'] = 'a' * 40
    raw = canonical(raw_object)
    encoded = refs.compact(raw, [(0, request)], 100, 1)
    snapshot, context = canonical({'price': 10}), canonical({'mode': 'demo'})
    tape_obj = {'inputs': {'snapshot': {'price': 10}, 'context': {'mode': 'demo'}},
                'operations': [{'kind': 'record', 'event': json.loads(request)},
                               {'kind': 'generate', 'request': raw_object['actual_request'], 'text': raw_object['raw']},
                               {'kind': 'record', 'event': raw_object}]}
    tape_raw = json.dumps(tape_obj, allow_nan=False)
    bundle = btc_review_references.compact(tape_raw, snapshot, context, [request, raw])
    assert bundle['version'] == 2
    packed_tape = btc_review_references.pack(bundle)
    recovery_text = btc_recovery_references.canonical(dict(raw_object, calculated_review_version=1))
    recovery = btc_recovery_references.pack(btc_recovery_references.compact(raw, recovery_text, 100, 1))
    path = tmp_path / 'fixture.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE llm_scenario_review_events(slot INTEGER, sequence INTEGER, body BLOB, PRIMARY KEY(slot,sequence));
            CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body BLOB);
            CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT);
            CREATE TABLE llm_scenario_recovery_events(slot INTEGER,kind TEXT,body BLOB);
        ''')
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (100, snapshot, context))
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (100, 0, request))
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (100, 1, raw))
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (100, packed_tape))
        conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)', (100, 'MODEL_RAW', recovery))
        conn.commit()
        before = btc_offsite_archive.referenced_commits(path)
        plain = {'slot': 100, 'sequence': 1, 'body': raw}
        after = {'slot': 100, 'sequence': 1, 'body': encoded}
        assert exporter._anchor_hash('llm_scenario_review_events', plain, conn) == exporter._anchor_hash(
            'llm_scenario_review_events', after, conn)
        conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE slot=100 AND sequence=1', (encoded,))
        conn.commit()
        assert btc_review_references.read_tape(packed_tape, conn, 100, decode_legacy=review_text) == tape_raw
        assert btc_recovery_references.read_recovery(recovery, conn, 100, decode_legacy=review_text,
                                                     expected_kind='MODEL_RAW') == recovery_text
        assert btc_offsite_archive.referenced_commits(path) == before
        root = Path(__file__).resolve().parents[1]
        sources = [(name, (root / file).read_text()) for name, file in (
            ('btc_review_event_refs_embedded', 'utils/btc_review_event_references.py'),
            ('btc_review_refs_embedded', 'utils/btc_review_references.py'),
            ('btc_recovery_refs_embedded', 'utils/btc_recovery_references.py'))]
        child = tmp_path / 'isolated_archive_reader.py'
        child.write_text('import sys,types\n' + ''.join(
            f'm=types.ModuleType({name!r});exec(compile({source!r},"<module>","exec"),m.__dict__);'
            f'sys.modules[{name!r}]=m\n' for name, source in sources) +
            f'ns={{"__name__":"receiver"}};exec(compile({Path(btc_offsite_archive.__file__).read_text()!r},'
            '"<archive>","exec"),ns);print(ns["referenced_commits"](ns["Path"](sys.argv[1]))[0][0])\n')
        isolated = subprocess.run([sys.executable, '-I', str(child), str(path)], check=True,
                                  capture_output=True, text=True, timeout=15)
        assert isolated.stdout.strip() == 'a' * 40
        conn.execute('DELETE FROM llm_scenario_review_events WHERE slot=100 AND sequence=0')
        conn.commit()
        with pytest.raises(ValueError):
            btc_review_references.read_tape(packed_tape, conn, 100, decode_legacy=review_text)
        with pytest.raises(ValueError):
            btc_recovery_references.read_recovery(recovery, conn, 100, decode_legacy=review_text,
                                                  expected_kind='MODEL_RAW')
        with pytest.raises(ValueError, match='reference_.*unresolved'):
            btc_offsite_archive.referenced_commits(path)
