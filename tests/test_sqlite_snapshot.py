import gzip
import json
import logging
import os
from pathlib import Path
import sqlite3

import pytest

from utils.sqlite_snapshot import backup_database


def test_optional_event_proof_failure_preserves_verified_backup(tmp_path, monkeypatch, caplog):
    source, destination = tmp_path / 'source.sqlite', tmp_path / 'snapshot.gz'
    context = {'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
               'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False}
    with sqlite3.connect(source) as conn:
        conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT);'
                           'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT);'
                           'CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT);'
                           'CREATE TABLE llm_scenario_recovery_events(event_id TEXT,slot INTEGER,kind TEXT,body TEXT);')
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (1, '{}'))
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)',
                     (1, '{}', json.dumps(context), json.dumps({'schema_version': 1, 'action': 'WAIT', 'entries': [],
                                                               'take_profits': [], 'partial_stops': [], 'cancel_entry_ids': []}),
                      '{"status":"wait"}'))
    result = backup_database(source, destination, reserve_bytes=0, capture_references=True,
                             capture_review_event_proof=True)
    assert result['decompression_verified'] is True
    assert result['btc_review_event_proof_status'] == 'UNAVAILABLE'
    assert 'btc_review_event_proof_index' not in result and destination.is_file()
    assert len(result['btc_retention_index']['candidates']) == 1
    from utils import btc_review_event_proof
    def expired(*_args, **_kwargs):
        raise TimeoutError('event_proof_deadline')
    monkeypatch.setattr(btc_review_event_proof, 'snapshot_event_index', expired)
    timed = backup_database(source, tmp_path / 'timed.gz', reserve_bytes=0, capture_references=True,
                            capture_review_event_proof=True)
    assert timed['decompression_verified'] is True and timed['btc_review_event_proof_status'] == 'UNAVAILABLE'
    assert 'btc_review_event_proof_index' not in timed
    def unexpected(*_args, **_kwargs):
        raise RuntimeError('private fixture content')
    monkeypatch.setattr(btc_review_event_proof, 'snapshot_event_index', unexpected)
    fallback = backup_database(source, tmp_path / 'unexpected.gz', reserve_bytes=0, capture_references=True,
                               capture_review_event_proof=True)
    assert fallback['decompression_verified'] is True and fallback['btc_review_event_proof_status'] == 'UNAVAILABLE'
    assert 'btc_review_event_proof_index' not in fallback and 'private fixture content' not in caplog.text
    import builtins
    real_import = builtins.__import__
    def missing_proof(name, *args, **kwargs):
        if name == 'utils.btc_review_event_proof':
            raise ImportError('private fixture content')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing_proof)
    missing = backup_database(source, tmp_path / 'missing.gz', reserve_bytes=0, capture_references=True,
                              capture_review_event_proof=True)
    assert missing['decompression_verified'] is True and missing['btc_review_event_proof_status'] == 'UNAVAILABLE'
    assert 'btc_review_event_proof_index' not in missing and 'private fixture content' not in caplog.text


def test_sparse_scan_only_punches_complete_zero_blocks(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from utils import sqlite_snapshot
    path = tmp_path / 'snapshot.sqlite'
    original = b'x' * 4096 + bytes(8192) + b'y' * 4096 + bytes(100)
    path.write_bytes(original)
    calls = []
    class Punch:
        def __call__(self, fd, mode, offset, length):
            calls.append((mode, offset, length))
            return 0
    monkeypatch.setattr(sqlite_snapshot.sys, 'platform', 'linux')
    monkeypatch.setattr(sqlite_snapshot.os, 'statvfs', lambda _: SimpleNamespace(f_frsize=4096))
    monkeypatch.setattr(sqlite_snapshot.ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(fallocate=Punch()))
    sparse = sqlite_snapshot._SparseSnapshot(path)
    try:
        sparse.scan()
        sparse.scan()
    finally:
        sparse.close()
    assert calls == [(3, 4096, 8192)]
    assert path.read_bytes() == original


def test_sparse_scan_falls_back_only_for_unsupported_filesystems(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from utils import sqlite_snapshot
    path = tmp_path / 'snapshot.sqlite'
    path.write_bytes(bytes(4096))
    class Punch:
        def __call__(self, *args):
            return -1
    monkeypatch.setattr(sqlite_snapshot.sys, 'platform', 'linux')
    monkeypatch.setattr(sqlite_snapshot.os, 'statvfs', lambda _: SimpleNamespace(f_frsize=4096))
    monkeypatch.setattr(sqlite_snapshot.ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(fallocate=Punch()))
    monkeypatch.setattr(sqlite_snapshot.ctypes, 'get_errno', lambda: sqlite_snapshot.errno.EOPNOTSUPP)
    sparse = sqlite_snapshot._SparseSnapshot(path)
    sparse.scan()
    assert sparse.fd is None and path.read_bytes() == bytes(4096)
    monkeypatch.setattr(sqlite_snapshot.ctypes, 'get_errno', lambda: sqlite_snapshot.errno.EIO)
    sparse = sqlite_snapshot._SparseSnapshot(path)
    try:
        with pytest.raises(OSError, match='snapshot_sparse_punch_failed'):
            sparse.scan()
    finally:
        sparse.close()


def test_sparse_scan_failure_preserves_previous_backup(tmp_path, monkeypatch):
    from utils import sqlite_snapshot
    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE evidence(value TEXT)')
        db.execute("INSERT INTO evidence VALUES('preserved')")
    old = tmp_path / 'old.gz'
    old.write_bytes(b'previous backup')
    class Failure:
        def __init__(self, path): pass
        def scan(self): raise OSError('snapshot_sparse_punch_failed')
        def close(self): pass
    monkeypatch.setattr(sqlite_snapshot, '_SparseSnapshot', Failure)
    with pytest.raises(OSError, match='snapshot_sparse_punch_failed'):
        backup_database(source, tmp_path / 'new.gz', reserve_bytes=0)
    assert old.read_bytes() == b'previous backup'
    assert not (tmp_path / 'new.gz').exists()
    assert not list(tmp_path.glob('.sqlite-snapshot-*'))
    assert not list(tmp_path.glob('*.lock'))
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT value FROM evidence').fetchone()[0] == 'preserved'


def test_online_backup_includes_committed_wal_and_roundtrips(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='utils.sqlite_snapshot')
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
        for stage in ('copy', 'integrity', 'hash', 'compress', 'verify', 'publish'):
            assert 'snapshot_stage=' + stage in caplog.text
        assert '정확한 원본' not in caplog.text
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


def test_standalone_snapshot_resolves_accounting_references(tmp_path):
    import subprocess
    from utils.btc_accounting_audit_references import TAG, ORIGINAL_HASH, canonical, sha

    source = tmp_path / 'source.sqlite'
    account, financial = {'balance': 1}, {'fee': 2}
    original = canonical({'observation': account, 'financial_evidence': financial})
    marker = {TAG: 1, ORIGINAL_HASH: sha(original),
              'observation': {'rowid': 1, 'captured_at': 1, 'sha256': sha(canonical(account))},
              'financial_evidence': {'rowid': 2, 'captured_at': 2, 'sha256': sha(canonical(financial))}}
    with sqlite3.connect(source) as conn:
        conn.executescript('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT);'
                           'CREATE TABLE llm_scenario_audit_events(decision_slot INTEGER,kind TEXT,body TEXT);')
        conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                         [('account', 1, canonical(account)), ('financial', 2, canonical(financial))])
        conn.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?)',
                     (7, 'accounting_observation', canonical(marker)))
    script = Path(__file__).resolve().parents[1] / 'utils/sqlite_snapshot.py'
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    run = subprocess.run([os.sys.executable, str(script), '--source', str(source),
                          '--destination', str(tmp_path / 'snapshot.gz'), '--capture-references'],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr


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


@pytest.mark.parametrize('archive_success', [True, False])
@pytest.mark.parametrize('event_retention', ['unset', '0', '1', 'invalid'])
def test_backup_records_archive_ack_and_preserves_old_on_failure(tmp_path, archive_success, event_retention):
    import subprocess
    home = tmp_path / 'home'
    project = home / 'prism-insight'
    project.mkdir(parents=True)
    (project / 'stock_tracking_db.sqlite').write_bytes(b'fixture')
    old = home / 'prism_backups' / 'old'
    old.mkdir(parents=True)
    (old / 'evidence').write_bytes(b'keep')
    os.utime(old, (1, 1))
    tools = tmp_path / 'bin'
    tools.mkdir()
    fake = tools / 'python3'
    fake.write_text('''#!/bin/sh
case "$1" in
  */sqlite_snapshot.py)
    printf '%s\n' "$@" > "$SNAPSHOT_LOG"
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--destination" ]; then shift; target="$1"; fi
      shift
    done
    printf fixture > "$target"
    printf '{}' > "$target.manifest.json"
    ;;
  */btc_offsite_archive.py)
    if [ "$ARCHIVE_SUCCESS" = 1 ]; then
      printf '{"status":"VERIFIED","retention_index_verified":true}\\n'
    else
      printf '{"status":"ERROR"}\\n'
      exit 1
    fi
    ;;
  */remove_uploaded_btc_backup.py)
    printf called > "$REMOVE_LOG"
    printf '{"status":"REMOVED"}\\n'
    ;;
  *) exit 1 ;;
esac
''')
    fake.chmod(0o755)
    scheduler = tools / 'systemd-run'
    scheduler.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$SCHEDULE_LOG"\n')
    scheduler.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / 'utils/backup_configs.sh'
    env = {**os.environ, 'HOME': str(home), 'PATH': str(tools) + ':' + os.environ['PATH'],
           'ARCHIVE_SUCCESS': '1' if archive_success else '0', 'SCHEDULE_LOG': str(tmp_path / 'scheduled'),
           'REMOVE_LOG': str(tmp_path / 'removed'), 'SNAPSHOT_LOG': str(tmp_path / 'snapshot_args')}
    if event_retention == 'unset':
        env.pop('PRISM_BTC_EVENT_RETENTION', None)
    else:
        env['PRISM_BTC_EVENT_RETENTION'] = event_retention
    run = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True, timeout=30)
    assert (run.returncode == 0) is archive_success
    ack, = (home / 'prism_backups').glob('*/offsite_ack.json')
    assert json.loads(ack.read_text())['status'] == ('VERIFIED' if archive_success else 'ERROR')
    assert ack.stat().st_mode & 0o077 == 0
    schedule = tmp_path / 'scheduled'
    assert schedule.exists() is (archive_success and event_retention != 'invalid')
    assert (tmp_path / 'removed').exists() is archive_success
    assert ('--capture-review-event-proof' in (tmp_path / 'snapshot_args').read_text().splitlines()) == (event_retention in ('unset', '1'))
    if archive_success and event_retention != 'invalid':
        assert 'run_btc_review_retention.py' in schedule.read_text()
        arguments = schedule.read_text().splitlines()
        assert '--timer-property=AccuracySec=1s' in arguments
        if event_retention in ('unset', '1'):
            assert arguments.pop() == '--event-proof-only'
        assert arguments[-7:-3] == ['/usr/bin/bash', '-c', 'exec "$1" "$2" "$3" "${@:4}"', '--']
        assert arguments[-3] == str(fake)
    if not archive_success:
        assert (old / 'evidence').read_bytes() == b'keep'
