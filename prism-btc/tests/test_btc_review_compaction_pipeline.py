import hashlib
import io
import json
import shutil
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from live.scenario_review_storage import review_text
from tools import btc_review_backup_manifest as backup_manifest
from tools import btc_review_event_compaction as server
from tools import btc_review_manifest_batch as batch_selector


def write_private(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def pipeline(tmp_path):
    live = tmp_path / 'live.sqlite'
    with sqlite3.connect(live) as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT)')
        conn.executemany('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                         [(7,0,'first'*2000),(7,1,'second'*2000)])
    identity=server.source_identity(live)
    directory=tmp_path / 'backup';directory.mkdir(mode=0o700)
    restored=directory / 'restored.sqlite';shutil.copyfile(live,restored);restored.chmod(0o600)
    archive=directory / 'backup.typed.gz';write_private(archive,b'synthetic backup')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    verified=directory / 'verified-manifest.json'
    write_private(verified,json.dumps({'source_identity':identity,'archive_bytes':archive.stat().st_size,
        'archive_sha256':sha(archive),'restored_bytes':restored.stat().st_size,'restored_sha256':sha(restored)}).encode())
    write_private(tmp_path / 'backup.receipt.json',json.dumps({'status':'VERIFIED','directory':str(directory),
        'source_identity':identity,'manifest_sha256':sha(verified)}).encode())
    fingerprints=directory / 'event-fingerprints.json'
    assert backup_manifest.create_manifest(directory,fingerprints)==2
    batch=batch_selector.select_batch(fingerprints,sha(fingerprints),identity,0)
    return live, restored, batch


def call_server(monkeypatch, capsys, source, batch):
    monkeypatch.setattr(server,'SOURCE',source)
    monkeypatch.setattr(server,'resource_guard',lambda *_:True)
    monkeypatch.setenv('PRISM_BTC_STORAGE_APPROVED','1')
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(batch).encode())))
    server.main()
    return json.loads(capsys.readouterr().out)


def test_pipeline_preserves_backup_and_can_resume(tmp_path,monkeypatch,capsys):
    live,backup,batch=pipeline(tmp_path)
    original=backup.read_bytes()
    first=call_server(monkeypatch,capsys,live,batch)
    assert first['status']=='COMPLETE' and first['counts']['UPDATED']==2
    with sqlite3.connect(live) as conn:
        assert [review_text(r[0]) for r in conn.execute('SELECT body FROM llm_scenario_review_events ORDER BY rowid')]==['first'*2000,'second'*2000]
    second=call_server(monkeypatch,capsys,live,batch)
    assert second['status']=='COMPLETE' and second['counts']['UNCHANGED']==2
    assert backup.read_bytes()==original


def test_pipeline_refuses_replaced_source(tmp_path,monkeypatch,capsys):
    live,backup,batch=pipeline(tmp_path)
    replacement=tmp_path / 'replacement.sqlite';shutil.copyfile(backup,replacement);replacement.replace(live)
    before=live.read_bytes()
    with pytest.raises(ValueError,match='source_identity_changed'):
        call_server(monkeypatch,capsys,live,batch)
    assert live.read_bytes()==before
