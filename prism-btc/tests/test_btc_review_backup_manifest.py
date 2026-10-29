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
