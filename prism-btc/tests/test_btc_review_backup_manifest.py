import hashlib
import json
import os
import sqlite3

import pytest

from tools import btc_review_backup_manifest as subject


def write_private(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def fixture(tmp_path):
    directory = tmp_path / 'backup'
    directory.mkdir(mode=0o700)
    archive, restored = directory / 'backup.typed.gz', directory / 'restored.sqlite'
    write_private(archive, b'backup archive')
    with sqlite3.connect(restored) as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT)')
        conn.executemany('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', [(7, 0, 'one'), (7, 1, 'two')])
    restored.chmod(0o600)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    detail = {'source_identity':'a' * 64, 'archive_bytes':archive.stat().st_size,
              'archive_sha256':digest(archive), 'restored_bytes':restored.stat().st_size,
              'restored_sha256':digest(restored)}
    manifest = directory / 'verified-manifest.json'
    write_private(manifest, json.dumps(detail).encode())
    receipt = {'status':'VERIFIED','directory':str(directory),'source_identity':'a' * 64,
               'manifest_sha256':digest(manifest)}
    write_private(tmp_path / 'backup.receipt.json', json.dumps(receipt).encode())
    return directory


def test_verified_backup_creates_private_fingerprints_without_mutating_backup(tmp_path):
    directory = fixture(tmp_path)
    restored = directory / 'restored.sqlite'
    before = restored.read_bytes()
    output = directory / 'event-fingerprints.json'
    assert subject.create_manifest(directory, output) == 2
    data = json.loads(output.read_text())
    assert data['source_identity'] == 'a' * 64
    assert [row['rowid'] for row in data['rows']] == [1, 2]
    assert data['rows'][0]['expected_sha256'] == hashlib.sha256(b'one').hexdigest()
    assert 'one' not in output.read_text() and os.stat(output).st_mode & 0o077 == 0
    assert restored.read_bytes() == before
    with pytest.raises(FileExistsError):
        subject.create_manifest(directory, output)


def test_changed_backup_is_rejected_without_output(tmp_path):
    directory = fixture(tmp_path)
    (directory / 'backup.typed.gz').write_bytes(b'changed')
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        subject.create_manifest(directory, directory / 'event-fingerprints.json')
    assert not (directory / 'event-fingerprints.json').exists()


def test_archive_change_during_scan_is_rejected(tmp_path, monkeypatch):
    directory = fixture(tmp_path)
    original_connect = sqlite3.connect
    class Connection:
        def __enter__(self):
            self.conn = original_connect((directory / 'restored.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
            return self.conn
        def __exit__(self, *args):
            self.conn.close()
            (directory / 'backup.typed.gz').write_bytes(b'changed during scan')
    monkeypatch.setattr(subject.sqlite3, 'connect', lambda *a, **kw: Connection())
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        subject.create_manifest(directory, directory / 'event-fingerprints.json')
    assert not (directory / 'event-fingerprints.json').exists()


def attested_pair_fixture(tmp_path):
    import random
    from live.scenario_review_storage import review_storage
    directory = tmp_path / 'backed-pair'
    directory.mkdir(mode=0o700)
    archive, restored = directory/'backup.typed.gz', directory/'restored.sqlite'
    write_private(archive,b'verified-archive-fixture')
    request = {'kind':'MODEL_REQUEST','phase':'single','request':{
        'prompt':random.Random(11).randbytes(12000).hex(),'timeout':50}}
    raw = {'kind':'MODEL_RAW','phase':'single','actual_request':dict(request['request'],timeout=49),'raw':'x'}
    with sqlite3.connect(restored) as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body BLOB,PRIMARY KEY(slot,sequence))')
        for seq, event in enumerate((request,raw)):
            conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                         (7,seq,review_storage(json.dumps(event,sort_keys=True,allow_nan=False))))
    restored.chmod(0o600)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    identity = 'a'*64
    detail = {'source_identity':identity,'archive_bytes':archive.stat().st_size,'archive_sha256':digest(archive),
              'restored_bytes':restored.stat().st_size,'restored_sha256':digest(restored)}
    manifest = directory/'verified-manifest.json';write_private(manifest,json.dumps(detail).encode())
    receipt = {'status':'VERIFIED','directory':str(directory),'source_identity':identity,'manifest_sha256':digest(manifest)}
    write_private(tmp_path/'backed-pair.receipt.json',json.dumps(receipt).encode())
    proof_path=tmp_path/'proof.json'
    subject.create_backup_attestation(directory,proof_path)
    return directory, proof_path


def test_attested_pair_plan_reuses_verified_files_read_only(tmp_path):
    directory, proof = attested_pair_fixture(tmp_path)
    result = subject.plan_pairs_from_attestation(proof)
    assert result['scanned'] == 2 and result['unreadable'] == 0
    assert len(result['manifest']['pairs']) == 1
    assert result['manifest']['source_identity'] == 'a'*64
    assert result['backup_proof_sha256'] == hashlib.sha256(proof.read_bytes()).hexdigest()
    assert not subject.plan_pairs_from_attestation(proof,after_rowid=2)['manifest']['pairs']
    assert sorted(p.name for p in directory.iterdir()) == ['backup.typed.gz','restored.sqlite','verified-manifest.json']


def test_attested_pair_plan_closes_backup_connection(tmp_path, monkeypatch):
    _, proof = attested_pair_fixture(tmp_path)
    original = sqlite3.connect
    closed = []
    class Tracked(sqlite3.Connection):
        def close(self):
            closed.append(True)
            return super().close()
    monkeypatch.setattr(subject.sqlite3,'connect',lambda *args,**kwargs:original(*args,factory=Tracked,**kwargs))
    assert len(subject.plan_pairs_from_attestation(proof)['manifest']['pairs']) == 1
    assert closed == [True]


def test_attested_pair_plan_rejects_changed_backup_or_receipt(tmp_path):
    directory, proof = attested_pair_fixture(tmp_path)
    with (directory/'backup.typed.gz').open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        subject.plan_pairs_from_attestation(proof)


def test_attested_pair_plan_rejects_changed_receipt(tmp_path):
    _, proof = attested_pair_fixture(tmp_path)
    receipt = tmp_path/'backed-pair.receipt.json'
    value = json.loads(receipt.read_text());value['status'] = 'ERROR'
    write_private(receipt,json.dumps(value).encode())
    with pytest.raises(ValueError, match='backup_receipt_mismatch'):
        subject.plan_pairs_from_attestation(proof)


def test_attested_pair_plan_rejects_conflicting_archive_receipt(tmp_path):
    _, proof = attested_pair_fixture(tmp_path)
    receipt = tmp_path/'backed-pair.receipt.json'
    value = json.loads(receipt.read_text());value['archive_sha256'] = '0'*64
    write_private(receipt,json.dumps(value).encode())
    with pytest.raises(ValueError, match='backup_receipt_mismatch'):
        subject.plan_pairs_from_attestation(proof)


def test_attested_pair_plan_rechecks_backup_after_scan(tmp_path, monkeypatch):
    from tools import btc_review_event_compaction as compaction
    directory, proof = attested_pair_fixture(tmp_path)
    original = compaction.plan_backed_pairs
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        with (directory/'backup.typed.gz').open('ab') as stream:stream.write(b'changed')
        return result
    monkeypatch.setattr(compaction,'plan_backed_pairs',changed)
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        subject.plan_pairs_from_attestation(proof)


def test_backup_attestation_requires_matching_hashes_and_never_overwrites(tmp_path):
    directory = fixture(tmp_path)
    proof = tmp_path/'proof.json'
    result = subject.create_backup_attestation(directory,proof)
    assert result['status'] == 'VERIFIED' and proof.stat().st_mode & 0o077 == 0
    assert result['files']['restored']['sha256'] == subject.file_sha256(directory/'restored.sqlite')
    with pytest.raises(FileExistsError):
        subject.create_backup_attestation(directory,proof)
    (directory/'backup.typed.gz').write_bytes(b'changed')
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        subject.create_backup_attestation(directory,tmp_path/'second-proof.json')
    assert not (tmp_path/'second-proof.json').exists()


def test_backup_attestation_rejects_change_during_hash(tmp_path, monkeypatch):
    directory = fixture(tmp_path)
    original = subject.file_sha256
    def changed(path):
        digest = original(path)
        if path.name == 'backup.typed.gz':
            with path.open('ab') as stream:stream.write(b'changed')
        return digest
    monkeypatch.setattr(subject,'file_sha256',changed)
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        subject.create_backup_attestation(directory,tmp_path/'proof.json')
    assert not (tmp_path/'proof.json').exists()


def test_backup_attestation_rechecks_first_file_after_second_hash(tmp_path, monkeypatch):
    directory = fixture(tmp_path)
    original = subject.file_sha256
    def changed(path):
        digest = original(path)
        if path.name == 'restored.sqlite':
            with (directory/'backup.typed.gz').open('ab') as stream:stream.write(b'changed')
        return digest
    monkeypatch.setattr(subject,'file_sha256',changed)
    with pytest.raises(ValueError, match='backup_artifact_changed'):
        subject.create_backup_attestation(directory,tmp_path/'proof.json')
    assert not (tmp_path/'proof.json').exists()
