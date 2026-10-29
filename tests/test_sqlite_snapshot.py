import gzip
import json
import os
from pathlib import Path
import sqlite3

import pytest

from utils.sqlite_snapshot import backup_database


def test_online_backup_includes_committed_wal_and_roundtrips(tmp_path):
    source = tmp_path / 'source.sqlite'
    live = sqlite3.connect(source)
    try:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute('PRAGMA wal_autocheckpoint=0')
        live.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY, body TEXT)')
        live.execute('INSERT INTO evidence(body) VALUES(?)', ('정확한 원본',))
        live.commit()
        assert Path(str(source)+'-wal').stat().st_size > 0
        target = tmp_path / 'private' / 'snapshot.sqlite.gz'
        result = backup_database(source, target, reserve_bytes=0)
        assert result['decompression_verified'] and result['source_writes'] == 0
        import hashlib
        stat = source.stat()
        assert result['source_identity_sha256'] == hashlib.sha256(
            f'{source.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
        assert target.stat().st_mode & 0o777 == 0o600
        assert Path(str(target)+'.manifest.json').stat().st_mode & 0o777 == 0o600
        restored = tmp_path / 'restore.sqlite'
        restored.write_bytes(gzip.decompress(target.read_bytes()))
        with sqlite3.connect(restored) as db:
            assert db.execute('SELECT body FROM evidence').fetchall() == [('정확한 원본',)]
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert json.loads(Path(str(target)+'.manifest.json').read_text())['raw_bytes'] == restored.stat().st_size
        assert live.execute('SELECT count(*) FROM evidence').fetchone()[0] == 1
    finally:
        live.close()


def test_existing_destination_never_overwritten(tmp_path):
    source = tmp_path / 'source.sqlite'
    sqlite3.connect(source).close()
    target = tmp_path / 'snapshot.gz'
    target.write_bytes(b'preserve')
    with pytest.raises(ValueError):
        backup_database(source, target, reserve_bytes=0)
    assert target.read_bytes() == b'preserve'


def test_invalid_source_leaves_no_partial_archive(tmp_path):
    source = tmp_path / 'source.sqlite'
    source.write_bytes(b'not a sqlite database')
    target = tmp_path / 'snapshot.gz'
    with pytest.raises(sqlite3.DatabaseError):
        backup_database(source, target, reserve_bytes=0)
    assert not target.exists()
    assert not list(tmp_path.glob('.sqlite-snapshot-*'))
    assert not list(tmp_path.glob('*.lock'))


def test_low_space_refuses_before_reading_source(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from utils import sqlite_snapshot
    source = tmp_path / 'source.sqlite'
    sqlite3.connect(source).close()
    monkeypatch.setattr(sqlite_snapshot.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(OSError, match='insufficient_snapshot_space'):
        backup_database(source, tmp_path/'snapshot.gz')
    assert list(tmp_path.iterdir()) == [source]


def test_snapshot_succeeds_with_space_for_only_one_copy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from utils import sqlite_snapshot

    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE samples(body BLOB)')
        payload = os.urandom(2 * 1024 * 1024)
        conn.execute('INSERT INTO samples VALUES(?)', (payload,))
    size = source.stat().st_size
    monkeypatch.setattr(sqlite_snapshot.shutil, 'disk_usage', lambda _: SimpleNamespace(free=size + 1024))
    target = tmp_path / 'snapshot.gz'
    backup_database(source, target, reserve_bytes=0)
    restored = tmp_path / 'restored.sqlite'
    restored.write_bytes(gzip.decompress(target.read_bytes()))
    with sqlite3.connect(restored) as conn:
        assert conn.execute('SELECT body FROM samples').fetchone()[0] == payload
    with sqlite3.connect(source) as conn:
        assert conn.execute('SELECT body FROM samples').fetchone()[0] == payload


def test_compression_never_overwrites_unread_snapshot(tmp_path, monkeypatch):
    from utils import sqlite_snapshot

    path = tmp_path / 'temporary.sqlite'
    original = b'x' * (2 * 1024 * 1024)
    path.write_bytes(original)
    class Expanding:
        def compress(self, chunk):
            return chunk + b'x'
        def flush(self):
            return b''
    monkeypatch.setattr(sqlite_snapshot.zlib, 'compressobj', lambda **_: Expanding())
    with pytest.raises(OSError, match='overtook_input'):
        sqlite_snapshot._compress_in_place(path, len(original), lambda *_: None)
    assert path.read_bytes() == original


def test_publish_failure_does_not_remove_competing_replacement(tmp_path, monkeypatch):
    from utils import sqlite_snapshot
    source = tmp_path / 'source.sqlite'
    sqlite3.connect(source).close()
    target = tmp_path / 'snapshot.gz'
    original = sqlite_snapshot.os.link
    calls = []
    def fail_second(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            target.unlink()
            target.write_bytes(b'other owner')
            raise OSError('manifest publish failed')
        return original(src, dst)
    monkeypatch.setattr(sqlite_snapshot.os, 'link', fail_second)
    with pytest.raises(OSError):
        backup_database(source, target, reserve_bytes=0)
    assert target.read_bytes() == b'other owner'
    assert not list(tmp_path.glob('.sqlite-snapshot-*'))


def test_lock_contention_preserves_other_lock(tmp_path):
    source = tmp_path / 'source.sqlite'
    sqlite3.connect(source).close()
    target = tmp_path / 'snapshot.gz'
    lock = Path(str(target)+'.lock')
    lock.write_text('other process')
    with pytest.raises(FileExistsError):
        backup_database(source, target, reserve_bytes=0)
    assert lock.read_text() == 'other process'


def test_timeout_closes_both_connections_and_cleans_owned_files(tmp_path, monkeypatch):
    from utils import sqlite_snapshot
    source = tmp_path/'source.sqlite'
    sqlite3.connect(source).close()
    opened = []
    original = sqlite3.connect
    class Tracked(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            super().close()
    def connect(*args, **kwargs):
        conn = original(*args, **kwargs, factory=Tracked)
        opened.append(conn)
        return conn
    monkeypatch.setattr(sqlite_snapshot.sqlite3, 'connect', connect)
    with pytest.raises(TimeoutError):
        backup_database(source, tmp_path/'snapshot.gz', timeout=1e-9, reserve_bytes=0)
    assert len(opened) == 2 and all(conn.closed for conn in opened)
    assert not list(tmp_path.glob('.sqlite-snapshot-*'))


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), 0, -1, True])
def test_nonfinite_or_invalid_deadline_rejected(tmp_path, timeout):
    source = tmp_path/'source.sqlite'
    sqlite3.connect(source).close()
    with pytest.raises(ValueError, match='invalid_snapshot_limits'):
        backup_database(source, tmp_path/'snapshot.gz', timeout=timeout, reserve_bytes=0)


def test_missing_main_db_stops_retention_of_older_backups(tmp_path):
    import os
    import subprocess
    home = tmp_path/'home'
    project = home/'prism-insight'
    project.mkdir(parents=True)
    older = home/'prism_backups'/'old'
    older.mkdir(parents=True)
    valuable = older/'stock_tracking_db.sqlite'
    valuable.write_bytes(b'keep older evidence')
    os.utime(older,(1,1))
    script = Path(__file__).resolve().parents[1]/'utils/backup_configs.sh'
    run = subprocess.run(['bash',str(script)],env={**os.environ,'HOME':str(home)},capture_output=True,text=True)
    assert run.returncode != 0
    assert valuable.read_bytes() == b'keep older evidence'
