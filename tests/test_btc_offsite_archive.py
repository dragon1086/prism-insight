import gzip
import fcntl
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from utils import btc_offsite_archive as archive


def test_commit_scan_rejects_expired_outer_deadline(tmp_path):
    raw = tmp_path / 'snapshot.sqlite'
    sqlite3.connect(raw).close()
    with pytest.raises(TimeoutError, match='commit_scan_deadline'):
        archive.referenced_commits(raw, deadline=0)


def test_scan_does_not_classify_deadline_interruption_as_malformed(tmp_path, monkeypatch):
    raw = tmp_path / 'snapshot.sqlite'
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_review_events(body TEXT)')
        db.execute("INSERT INTO llm_scenario_review_events VALUES ('{}')")
    now = [10]
    monkeypatch.setattr(archive.time, 'monotonic', lambda: now[0])
    def interrupted(_):
        now[0] = 30
        raise sqlite3.OperationalError('interrupted')
    monkeypatch.setattr(archive, 'review_text', interrupted)
    with pytest.raises(TimeoutError, match='commit_scan_deadline'):
        archive.referenced_commits(raw, deadline=20)


def git(repo, *args):
    return subprocess.run(['/usr/bin/git', *args], cwd=repo, capture_output=True,
                          check=True, text=True, timeout=30).stdout.strip()


@pytest.fixture
def payload(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init')
    git(repo, 'config', 'user.name', 'test')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    (repo / 'source.py').write_text('print(1)\n')
    git(repo, 'add', 'source.py')
    git(repo, 'commit', '-m', 'initial')
    first = git(repo, 'rev-parse', 'HEAD')
    # Keep an actual commit that no source ref reaches; the bundle must include it.
    tree = git(repo, 'rev-parse', 'HEAD^{tree}')
    orphan = git(repo, 'commit-tree', tree, '-m', 'historical detached revision')
    git(repo, 'update-ref', 'refs/notes/archive-test', first)
    raw = tmp_path / 'source.sqlite'
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_audit_manifests(body TEXT)')
        db.execute('INSERT INTO llm_scenario_audit_manifests VALUES(?)',
                   (json.dumps({'git_revision': orphan}),))
    zipped = tmp_path / archive.FILES[0]
    with gzip.open(zipped, 'wb') as f:
        f.write(raw.read_bytes())
    manifest = dict(sqlite_quick_check='ok', decompression_verified=True, source_writes=0,
                    raw_bytes=raw.stat().st_size, raw_sha256=archive.digest(raw),
                    compressed_bytes=zipped.stat().st_size, compressed_sha256=archive.digest(zipped))
    mp = tmp_path / archive.FILES[1]
    mp.write_text(json.dumps(manifest))
    refs_before = git(repo, 'show-ref')
    bundle = tmp_path / archive.FILES[2]
    archive.make_bundle(repo, bundle, [orphan], tmp_path / 'temporary.git')
    assert git(repo, 'show-ref') == refs_before
    assert 'refs/notes/archive-test' in git(repo, 'bundle', 'list-heads', str(bundle))
    needed = raw.stat().st_size + zipped.stat().st_size + 4 * bundle.stat().st_size + 1024**2
    _, coverage = archive.referenced_commits(raw)
    metadata = dict(required_space=needed, source_head=first, referenced_commits=[orphan], code_reference_coverage=coverage,
                    sha256={name: archive.digest(tmp_path / name) for name in archive.FILES[:-1]})
    (tmp_path / archive.FILES[3]).write_text(json.dumps(metadata))
    return tmp_path, needed, manifest, repo


def transfer(path, names=archive.FILES):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name in names:
            tar.add(path / name, arcname=name, recursive=False)
    stream.seek(0)
    return stream


def test_verified_restore_and_exact_commit_no_settings_upload(payload, monkeypatch):
    path, needed, manifest, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    (path / '.env').write_text('private-do-not-copy')
    root = path / 'remote'
    ack = archive.receive(transfer(path), root, needed)
    assert ack['status'] == 'VERIFIED' and ack['raw_sha256'] == manifest['raw_sha256']
    saved = root / ack['archive']
    assert {p.name for p in saved.iterdir()} == set(archive.FILES) | {'receipt.json'}
    assert all(p.stat().st_mode & 0o077 == 0 for p in saved.iterdir())
    assert not list(root.glob('.incoming-*'))
    assert (root / '.receive.lock').stat().st_mode & 0o077 == 0


def test_retained_archive_restores_database_and_exact_code(payload, monkeypatch):
    path, needed, manifest, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    root, output = path / 'remote', path / 'replays'
    ack = archive.receive(transfer(path), root, needed)
    with pytest.raises(ValueError, match='restore_root_overlap'):
        archive.restore_archive(root, ack['archive'], root / 'unsafe-replay')
    assert not (root / 'unsafe-replay').exists()
    result = archive.restore_archive(root, ack['archive'], output)
    restored = Path(result['directory'])
    assert result['status'] == 'VERIFIED' and result['raw_sha256'] == manifest['raw_sha256']
    assert restored.stat().st_mode & 0o077 == 0
    assert (restored / 'snapshot.sqlite').stat().st_mode & 0o077 == 0
    with sqlite3.connect(restored / 'snapshot.sqlite') as conn:
        assert conn.execute('SELECT COUNT(*) FROM llm_scenario_audit_manifests').fetchone()[0] == 1
    assert git(restored / 'history.git', 'cat-file', '-e', result['source_head'] + '^{commit}') == ''
    assert (root / ack['archive'] / 'receipt.json').is_file()
    with pytest.raises(ValueError, match='restore_already_present'):
        archive.restore_archive(root, ack['archive'], output)
    (root / ack['archive'] / 'snapshot.sqlite.gz').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='restore_archive_checksum_mismatch'):
        archive.restore_archive(root, ack['archive'], path / 'another-replay')
    assert (root / ack['archive'] / 'receipt.json').is_file()


def test_embedded_remote_restore_preserves_verified_archive(payload, monkeypatch):
    import shlex
    path, needed, manifest, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    root, output = path / 'remote', path / 'replays'
    ack = archive.receive(transfer(path), root, needed)
    real_run = subprocess.run
    def local_ssh(args, **kwargs):
        if args[0] != '/usr/bin/ssh':
            return real_run(args, **kwargs)
        command = shlex.split(args[-1])
        code = command[2].replace('/root/prism-btc-archive', str(root)).replace('/root/prism-btc-replays', str(output))
        return real_run([os.sys.executable, '-I', '-c', code], **kwargs)
    monkeypatch.setattr(archive.subprocess, 'run', local_ssh)
    result = json.loads(archive.ssh_archive('fixture.local', 0, restore=ack['archive']).stdout)
    assert result['status'] == 'VERIFIED' and result['raw_sha256'] == manifest['raw_sha256']
    assert (Path(result['directory']) / 'snapshot.sqlite').is_file()
    with pytest.raises(ValueError, match='invalid_restore_request'):
        archive.ssh_archive('fixture.local', 0, restore='../bad')


def test_replay_restore_cleans_staging_when_disk_reserve_is_lost(payload, monkeypatch):
    path, needed, _, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    root, output = path / 'remote', path / 'replays'
    ack = archive.receive(transfer(path), root, needed)
    calls = iter((100 * archive.CAP, archive.RESERVE - 1))
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=next(calls)))
    with pytest.raises(OSError, match='snapshot_restore_space_reserve_lost'):
        archive.restore_archive(root, ack['archive'], output)
    assert not list(output.glob('.restoring-*')) and not list(output.glob('restore-*'))
    assert (root / ack['archive'] / 'receipt.json').is_file()


def test_replay_restore_command_reports_status_without_private_errors(monkeypatch, capsys):
    from tools import restore_btc_replay_archive as runner
    name = 'archive-' + 'a' * 32
    monkeypatch.setattr(runner, 'backend_host', lambda _: 'fixture.local')
    monkeypatch.setattr(runner, 'ssh_archive', lambda host, needed, restore: SimpleNamespace(
        stdout=json.dumps({'status': 'VERIFIED', 'directory': '/fixture', 'source_head': 'b' * 40}).encode()))
    assert runner.main([name]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'VERIFIED'
    def fail(*args, **kwargs):
        raise ValueError('private fixture data')
    monkeypatch.setattr(runner, 'ssh_archive', fail)
    assert runner.main([name]) == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'ERROR', 'error_type': 'ValueError'}


def test_captured_references_avoid_sender_restore_but_receiver_verifies(payload, monkeypatch):
    from utils.sqlite_snapshot import backup_database

    path, _, _, repo = payload
    snapshot = path / 'fresh.sqlite.gz'
    scan = archive.referenced_commits
    received_deadlines = []
    def checked_scan(raw, *, deadline=None):
        received_deadlines.append(deadline)
        return scan(raw, deadline=deadline)
    monkeypatch.setattr(archive, 'referenced_commits', checked_scan)
    manifest = backup_database(path / 'source.sqlite', snapshot, reserve_bytes=0, capture_references=True)
    assert received_deadlines and received_deadlines[0] is not None
    assert manifest['code_reference_scan']['commits']
    monkeypatch.setattr(archive, 'backend_host', lambda _: 'fixture')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    root = path / 'remote-captured'
    restored = []
    real_restore = archive.restore_snapshot
    def record_restore(zipped, metadata, raw):
        restored.append(raw)
        return real_restore(zipped, metadata, raw)
    monkeypatch.setattr(archive, 'restore_snapshot', record_restore)
    def ssh(_host, needed, *, stream=None):
        if stream is None:
            return SimpleNamespace(stdout=b'READY')
        return SimpleNamespace(stdout=json.dumps(archive.receive(stream, root, needed)).encode())
    monkeypatch.setattr(archive, 'ssh_archive', ssh)
    ack = archive.archive(repo, snapshot, path / 'unused-config')
    assert ack['status'] == 'VERIFIED'
    assert len(restored) == 1 and root in restored[0].parents
    manifest_path = Path(str(snapshot) + '.manifest.json')
    damaged = json.loads(manifest_path.read_text())
    damaged['code_reference_scan']['coverage']['malformed_evidence_rows'] += 1
    manifest_path.write_text(json.dumps(damaged))
    with pytest.raises(ValueError, match='archive_reference_coverage_mismatch'):
        archive.archive(repo, snapshot, path / 'unused-config')


def test_standalone_receiver_recomputes_retention_index(payload, monkeypatch):
    import shlex
    from utils.sqlite_snapshot import backup_database

    path, _, _, repo = payload
    source = path / 'retention.sqlite'
    ctx = {'scenario_id': None, 'positions': [], 'pending_entries': [], 'unknown_intents': False,
           'historical_unsettled': [], 'incident_epoch_blocked': False, 'recovery_observing': False}
    with sqlite3.connect(source) as conn:
        conn.executescript('CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY,body TEXT);'
                           'CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,context TEXT,proposal TEXT,outcome TEXT,snapshot TEXT DEFAULT "{}");')
        conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (1, '{"kind":"WAIT"}'))
        conn.execute('INSERT INTO llm_scenario_decisions(slot,context,proposal,outcome) VALUES(?,?,?,?)',
                     (1, json.dumps(ctx), json.dumps({'action': 'WAIT'}), json.dumps({'status': 'wait'})))
    snapshot = path / 'retention.sqlite.gz'
    manifest = backup_database(source, snapshot, reserve_bytes=0, capture_references=True)
    assert len(manifest['btc_retention_index']['candidates']) == 1
    root = path / 'embedded-remote'
    original_run = archive.subprocess.run
    def local_ssh(args, **kwargs):
        if args[0] != '/usr/bin/ssh':
            return original_run(args, **kwargs)
        command = shlex.split(args[-1])
        assert command[:2] == ['/usr/bin/python3', '-c']
        code = command[2].replace('/root/prism-btc-archive', str(root))
        return original_run([os.sys.executable, '-I', '-c', code], **kwargs)
    monkeypatch.setattr(archive.subprocess, 'run', local_ssh)
    monkeypatch.setattr(archive, 'backend_host', lambda _: 'fixture')
    ack = archive.archive(repo, snapshot, path / 'unused-config')
    assert ack['retention_index_verified'] is True
    manifest_path = Path(str(snapshot) + '.manifest.json')
    altered = json.loads(manifest_path.read_text())
    altered['btc_retention_index']['candidates'][0]['tape_sha256'] = '0' * 64
    manifest_path.write_text(json.dumps(altered))
    with pytest.raises(subprocess.CalledProcessError):
        archive.archive(repo, snapshot, path / 'unused-config')
    assert (root / ack['archive'] / 'receipt.json').is_file()


@pytest.mark.parametrize('fault', ['checksum', 'partial', 'env', 'raw_hash', 'understated_space', 'missing_ref'])
def test_failed_receive_preserves_old_archive_and_removes_only_staging(payload, monkeypatch, fault):
    path, needed, _, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    root = path / 'remote'
    root.mkdir(mode=0o700)
    old = root / 'older-verified'
    old.write_bytes(b'old proof')
    names = archive.FILES
    if fault == 'checksum':
        (path / archive.FILES[0]).write_bytes(b'corrupt')
    if fault == 'partial':
        names = archive.FILES[:-1]
    if fault == 'env':
        (path / '.env').write_text('not allowed')
        names = (*archive.FILES, '.env')
    if fault in {'raw_hash', 'missing_ref', 'understated_space'}:
        mp = path / archive.FILES[3]
        meta = json.loads(mp.read_text())
        if fault == 'raw_hash':
            sp = path / archive.FILES[1]
            snapshot = json.loads(sp.read_text())
            snapshot['raw_sha256'] = '0' * 64
            sp.write_text(json.dumps(snapshot))
            meta['sha256'][archive.FILES[1]] = archive.digest(sp)
        elif fault == 'missing_ref':
            meta['referenced_commits'] = []
        else:
            needed = sum((path / name).stat().st_size for name in archive.FILES) + 4096
            meta['required_space'] = needed
        mp.write_text(json.dumps(meta))
    with pytest.raises((ValueError, tarfile.TarError, EOFError)):
        archive.receive(transfer(path, names), root, needed)
    assert old.read_bytes() == b'old proof'
    assert sorted(p.name for p in root.iterdir()) == ['.receive.lock', 'older-verified']


def test_space_reserve_and_capacity_are_fail_closed(tmp_path, monkeypatch):
    root = tmp_path / 'remote'
    archive.private_root(root)
    old = root / 'old'
    old.write_bytes(b'x')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=archive.RESERVE + 9))
    with pytest.raises(OSError, match='space'):
        archive.receive(io.BytesIO(), root, 10)
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    with pytest.raises(OSError, match='space'):
        archive.space(root, archive.CAP)
    assert old.read_bytes() == b'x'


def test_temporary_restore_can_exceed_retained_cap_but_final_archive_cannot(payload, monkeypatch):
    path, needed, _, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))
    monkeypatch.setattr(archive, 'CAP', needed - 1)
    root = path / 'remote-staging'
    ack = archive.receive(transfer(path), root, needed)
    assert ack['status'] == 'VERIFIED'
    monkeypatch.setattr(archive, 'CAP', 1)
    with pytest.raises(OSError, match='archive_space_limit'):
        archive.receive(transfer(path), root, needed)
    assert (root / ack['archive'] / 'receipt.json').exists()


def test_root_and_host_safety(tmp_path):
    with pytest.raises(ValueError):
        archive.private_root(Path('relative'))
    link = tmp_path / 'linked'
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        archive.private_root(link / 'archive')
    config = tmp_path / 'tunnel.env'
    assert archive.backend_host(config) is None
    for value in ['-oProxyCommand=bad', 'host; touch /tmp/bad', 'a..b', 'a.-b', '999.1.2.3', '$(whoami)', '']:
        config.write_text('PRISM_BACKEND_HOST=' + value)
        with pytest.raises(ValueError):
            archive.backend_host(config)
    config.write_text('SECRET=not-read-as-host\nPRISM_BACKEND_HOST=158.247.205.250\n')
    assert archive.backend_host(config) == '158.247.205.250'


def test_missing_source_commit_cannot_create_valid_bundle(payload):
    path, _, _, repo = payload
    with pytest.raises(ValueError, match='referenced_commit_unavailable'):
        archive.make_bundle(repo, path / 'missing.bundle', ['0' * 40], path / 'missing.git')


def test_expansion_limit_and_concurrent_receiver_preserve_existing(payload, monkeypatch):
    path, needed, manifest, _ = payload
    bad = dict(manifest, raw_bytes=1)
    with pytest.raises(ValueError, match='expansion_bound'):
        archive.restore_snapshot(path / archive.FILES[0], bad, path / 'oversize.sqlite')
    root = path / 'remote'
    root.mkdir(mode=0o700)
    lock = root / '.receive.lock'
    lock.write_text('another receiver')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    with lock.open('r+') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            archive.receive(transfer(path), root, needed)
    assert lock.read_text() == 'another receiver'


def test_killed_lock_holder_does_not_strand_receiver(payload, monkeypatch):
    path, needed, _, _ = payload
    root = path / 'remote'
    root.mkdir(mode=0o700)
    lock = root / '.receive.lock'
    script = ('import fcntl,os,sys,time; '
              'fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600); '
              'fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); print("held",flush=True); time.sleep(60)')
    process = subprocess.Popen([os.sys.executable, '-c', script, str(lock)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'held'
        inode = lock.stat().st_ino
        with pytest.raises(BlockingIOError):
            archive.receive(io.BytesIO(), root, needed)
        process.kill()
        process.wait(timeout=10)
        monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
        assert archive.receive(transfer(path), root, needed)['status'] == 'VERIFIED'
        assert lock.stat().st_ino == inode
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()


def test_unknown_missing_and_malformed_revision_evidence_is_counted(tmp_path):
    raw = tmp_path / 'evidence.sqlite'
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_audit_manifests(body TEXT)')
        db.executemany('INSERT INTO llm_scenario_audit_manifests VALUES(?)', [
            (json.dumps({'git_revision': 'a' * 40}),),
            (json.dumps({'git_revision': 'UNKNOWN'}),),
            (json.dumps({'git_revision': None}),), ('{}',), ('broken JSON',),
        ])
    commits, coverage = archive.referenced_commits(raw)
    assert commits == ['a' * 40]
    assert coverage['unique_known_revision_count'] == 1
    assert coverage['invalid_or_unknown_revision_fields'] == 2
    assert coverage['missing_manifest_git_revision'] == 2
    assert coverage['malformed_evidence_rows'] == 1
    assert 'ONLY' in coverage['scope']


def test_uncollected_null_inputs_are_missing_not_corrupt(tmp_path):
    raw = tmp_path / 'uncollected.sqlite'
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_audit_manifests(body TEXT)')
        db.execute('INSERT INTO llm_scenario_audit_manifests VALUES(NULL)')
        db.execute('CREATE TABLE llm_scenario_decisions(context TEXT,snapshot TEXT)')
        db.execute("INSERT INTO llm_scenario_decisions VALUES(NULL,'')")
    commits, coverage = archive.referenced_commits(raw)
    assert commits == []
    assert coverage['missing_evidence_values'] == 3
    assert coverage['malformed_evidence_rows'] == 0
    assert coverage['missing_manifest_git_revision'] == 1


def test_receiver_recomputes_coverage_and_rejects_symlink_lock(payload, monkeypatch):
    path, needed, _, _ = payload
    root = path / 'remote'
    root.mkdir(mode=0o700)
    target = path / 'untouched'
    target.write_text('keep')
    (root / '.receive.lock').symlink_to(target)
    with pytest.raises(ValueError, match='lock_symlink'):
        archive.receive(io.BytesIO(), root, needed)
    assert target.read_text() == 'keep'
    (root / '.receive.lock').unlink()
    meta_path = path / 'archive.json'
    metadata = json.loads(meta_path.read_text())
    metadata['code_reference_coverage']['missing_manifest_git_revision'] = 100
    meta_path.write_text(json.dumps(metadata))
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    with pytest.raises(ValueError, match='reference_coverage_mismatch'):
        archive.receive(transfer(path), root, needed)


def test_cli_disabled_is_not_success_and_retention_follows_ack(tmp_path):
    result = subprocess.run([os.sys.executable, str(Path(archive.__file__)), '--repo', str(tmp_path),
                             '--snapshot', str(tmp_path / 'missing.gz'), '--config', str(tmp_path / 'absent')],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'DISABLED'
    script = Path(archive.__file__).with_name('backup_configs.sh').read_text()
    assert script.index('btc_offsite_archive.py') < script.index('-mtime +7')
    assert '기존 백업 보존, 보존기간 삭제 중단' in script


def test_local_preparation_upload_and_ack_binding_without_network(payload, monkeypatch):
    path, _, manifest, repo = payload
    snapshot = path / archive.FILES[0]
    Path(str(snapshot) + '.manifest.json').write_text(json.dumps(manifest))
    config = path / 'tunnel.env'
    config.write_text('PRISM_BACKEND_HOST=archive.example.invalid\nSECRET=not-sent\n')
    (path / '.env').write_text('not-sent')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    original = subprocess.run
    calls = []
    def transport(args, **kwargs):
        if args[0] != '/usr/bin/ssh':
            return original(args, **kwargs)
        calls.append(args)
        assert 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args
        assert 'root@archive.example.invalid' in args
        assert kwargs['shell'] is False
        if kwargs.get('stdin') is None:
            return subprocess.CompletedProcess(args, 0, stdout=b'READY\n', stderr=b'')
        stream = kwargs['stdin']
        with tarfile.open(fileobj=stream, mode='r:') as tar:
            assert set(tar.getnames()) == set(archive.FILES)
            meta = json.load(tar.extractfile('archive.json'))
        stream.seek(0)
        result = archive.receive(stream, path / 'remote', meta['required_space'])
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(result).encode(), stderr=b'')
    monkeypatch.setattr(archive.subprocess, 'run', transport)
    ack = archive.archive(repo, snapshot, config)
    assert ack['status'] == 'VERIFIED' and len(calls) == 2
    assert not list(path.glob('.btc-offsite-*'))


@pytest.mark.parametrize('args', [[], ['sh', '-c', 'id'], ['git', '-c', 'core.sshCommand=bad', 'fetch'],
    ['git', 'clone', '--bare', '-option', '/tmp/destination'], ['git', 'checkout', 'HEAD'],
    ['git', 'cat-file', '-e', 'HEAD'], ['git', 'update-ref', 'refs/heads/main', 'a' * 40],
    ['git', 'bundle', 'create', 'relative.bundle', '--all'], ['git', None]])
def test_command_boundary_rejects_unregistered_argv(args, monkeypatch):
    monkeypatch.setattr(archive.subprocess, 'run', lambda *a, **k: pytest.fail('must not execute'))
    with pytest.raises(ValueError):
        archive.run(args)


@pytest.mark.parametrize('host', ['host; id', '-oProxyCommand=bad', 'host\nother', 'a.-b', '999.1.2.3'])
def test_ssh_boundary_validates_host_before_process(host, monkeypatch):
    monkeypatch.setattr(archive.subprocess, 'run', lambda *a, **k: pytest.fail('must not execute'))
    with pytest.raises(ValueError):
        archive.ssh_archive(host, 100)


def test_git_boundary_uses_fixed_executable_without_shell(monkeypatch, tmp_path):
    calls = []
    def capture(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout='ok')
    monkeypatch.setattr(archive.subprocess, 'run', capture)
    assert archive.run(['git', 'rev-parse', 'HEAD'], cwd=tmp_path) == 'ok'
    assert calls[0][0] == ['/usr/bin/git', 'rev-parse', 'HEAD']
    assert calls[0][1]['shell'] is False


def test_embedded_receiver_resolves_recovery_references_and_rejects_missing(tmp_path):
    from utils.btc_recovery_references import MAGIC, canonical, compact, pack

    path = tmp_path / 'source.sqlite'
    event = {'kind': 'MODEL_RAW', 'raw': 'x' * 1000, 'git_revision': 'd' * 40}
    review = json.dumps(event, sort_keys=True)
    recovery = canonical(dict(event, calculated_review_version=1, input_id='fixture'))
    candidate = compact(review, recovery, 7, 0)
    assert candidate != recovery
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT);'
                           'CREATE TABLE llm_scenario_recovery_events(slot INTEGER,kind TEXT,body BLOB);')
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (7, 0, review))
        conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)',
                     (7, 'MODEL_RAW', pack(candidate)))
    helper = Path(archive.__file__).with_name('btc_recovery_references.py').read_text()
    source = Path(archive.__file__).read_text()
    child_script = ("import sys,types; m=types.ModuleType('btc_recovery_refs_embedded'); "
                    "exec(compile(%r,'<refs>','exec'),m.__dict__); "
                    "sys.modules['btc_recovery_refs_embedded']=m; ns={'__name__':'receiver'}; "
                    "exec(compile(%r,'<archive>','exec'),ns); "
                    "print(ns['referenced_commits'](ns['Path'](sys.argv[1]))[0][0])" % (helper, source))
    child = subprocess.run([os.sys.executable, '-I', '-c', child_script, str(path)], cwd=tmp_path,
                           capture_output=True, text=True, timeout=15, check=True)
    assert child.stdout.strip() == 'd' * 40
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE llm_scenario_recovery_events SET kind='MODEL_REQUEST'")
    with pytest.raises(ValueError, match='reference_recovery_unresolved'):
        archive.referenced_commits(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE llm_scenario_recovery_events SET kind='MODEL_RAW'")
        conn.execute('DELETE FROM llm_scenario_review_events')
    with pytest.raises(ValueError, match='reference_recovery_unresolved'):
        archive.referenced_commits(path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE llm_scenario_recovery_events SET body=?', (MAGIC + b'bad',))
    with pytest.raises(ValueError, match='reference_recovery_unresolved'):
        archive.referenced_commits(path)


def test_archive_resolves_recovery_input_reference(tmp_path):
    from utils.btc_recovery_references import canonical, compact_input, pack

    path = tmp_path / 'input.sqlite'
    recovery = {'baseline': 'x' * 1000, 'git_revision': 'e' * 40}
    context = canonical({'recovery': recovery})
    original = canonical({'decision_slot': 7, 'recovery': recovery})
    candidate = compact_input(context, original, 7)
    assert candidate != original
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE llm_scenario_decisions(slot INTEGER,context TEXT);'
                           'CREATE TABLE llm_scenario_recovery_events(slot INTEGER,kind TEXT,body BLOB);')
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?)', (7, context))
        conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)', (7, 'INPUT', pack(candidate)))
    assert 'e' * 40 in archive.referenced_commits(path)[0]
    helper = Path(archive.__file__).with_name('btc_recovery_references.py').read_text()
    source = Path(archive.__file__).read_text()
    child_script = ("import sys,types; m=types.ModuleType('btc_recovery_refs_embedded'); "
                    "exec(compile(%r,'<refs>','exec'),m.__dict__); "
                    "sys.modules['btc_recovery_refs_embedded']=m; ns={'__name__':'receiver'}; "
                    "exec(compile(%r,'<archive>','exec'),ns); "
                    "print(ns['referenced_commits'](ns['Path'](sys.argv[1]))[0][0])" % (helper, source))
    child = subprocess.run([os.sys.executable, '-I', '-c', child_script, str(path)], cwd=tmp_path,
                           capture_output=True, text=True, timeout=15, check=True)
    assert child.stdout.strip() == 'e' * 40
    with sqlite3.connect(path) as conn:
        conn.execute('DELETE FROM llm_scenario_decisions')
    with pytest.raises(ValueError, match='reference_recovery_unresolved'):
        archive.referenced_commits(path)


def test_archive_revision_scan_handles_compressed_review_and_corruption(tmp_path):
    import gzip
    from observability.review_storage import MAGIC
    raw = tmp_path / 'review.sqlite'
    original = json.dumps({'source_commit': 'a' * 40})
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_review_tapes(body TEXT)')
        db.execute('CREATE TABLE llm_scenario_review_events(body TEXT)')
        db.execute('INSERT INTO llm_scenario_review_tapes VALUES(?)',
                   (MAGIC + gzip.compress(original.encode(), mtime=0),))
        db.execute('INSERT INTO llm_scenario_review_events VALUES(?)', (MAGIC + b'broken',))
    commits, coverage = archive.referenced_commits(raw)
    assert commits == ['a' * 40]
    assert coverage['malformed_evidence_rows'] == 1
    assert archive.review_text(MAGIC + gzip.compress(original.encode(), mtime=0)) == original
    source = Path(archive.__file__).read_text()
    script = ("import sys; ns={'__name__':'offsite_receiver'}; "
              "exec(compile(%r, '<archive>', 'exec'), ns); "
              "print(ns['referenced_commits'](ns['Path'](sys.argv[1]))[0][0])" % source)
    child = subprocess.run([os.sys.executable, '-I', '-c', script, str(raw)], cwd=tmp_path,
                           capture_output=True, text=True, timeout=15, check=True)
    assert child.stdout.strip() == 'a' * 40

def test_archive_scans_compressed_recovery_wire_and_marks_corruption(tmp_path):
    import gzip
    from observability.review_storage import MAGIC
    raw = tmp_path / 'recovery.sqlite'
    original = json.dumps({'source_commit': 'b' * 40})
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_recovery_events(body TEXT)')
        db.execute('INSERT INTO llm_scenario_recovery_events VALUES(?)',
                   (MAGIC+gzip.compress(original.encode(), mtime=0),))
        db.execute('INSERT INTO llm_scenario_recovery_events VALUES(?)', (MAGIC+b'bad',))
    commits, coverage = archive.referenced_commits(raw)
    assert commits == ['b' * 40]
    assert coverage['malformed_evidence_rows'] == 1


def test_archive_resolves_reference_tape_without_losing_revision_coverage(tmp_path):
    from utils.btc_review_references import canonical, compact, pack

    raw = tmp_path / 'references.sqlite'
    snapshot, context = {'z': 1, 'a': 2}, {'input_id': 'fixture'}
    request = {'system_prompt': 'fixture', 'schema': {'z': 1, 'a': 2}}
    event = {'kind': 'MODEL_RAW', 'source_commit': 'b' * 40, 'actual_request': request, 'raw': 'response'}
    tape = {'inputs': {'snapshot': snapshot, 'context': context},
            'operations': [{'kind': 'generate', 'request': request, 'text': 'response'},
                           {'kind': 'record', 'event': event}]}
    text = json.dumps(tape)
    s, c, e = map(canonical, (snapshot, context, event))
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT)')
        db.execute('CREATE TABLE llm_scenario_review_events(slot INTEGER,sequence INTEGER,body TEXT)')
        db.execute('CREATE TABLE llm_scenario_review_tapes(slot INTEGER,body TEXT)')
        db.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (1, s, c))
        db.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, e))
        db.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (1, text))
    original = archive.referenced_commits(raw)
    bundle = compact(text, s, c, [e])
    assert bundle['version'] == 2
    with sqlite3.connect(raw) as db:
        db.execute('UPDATE llm_scenario_review_tapes SET body=?', (pack(bundle),))
    assert archive.referenced_commits(raw) == original
    reference_source = Path(archive.__file__).with_name('btc_review_references.py').read_text()
    receiver_source = Path(archive.__file__).read_text()
    child_script = ("import sys,types; module=types.ModuleType('btc_review_refs_embedded'); "
                    "exec(compile(%r,'<refs>','exec'),module.__dict__); "
                    "sys.modules['btc_review_refs_embedded']=module; ns={'__name__':'receiver'}; "
                    "exec(compile(%r,'<archive>','exec'),ns); "
                    "print(ns['referenced_commits'](ns['Path'](sys.argv[1]))[0][0])" %
                    (reference_source, receiver_source))
    child = subprocess.run([os.sys.executable, '-I', '-c', child_script, str(raw)], cwd=tmp_path,
                           capture_output=True, text=True, timeout=15, check=True)
    assert child.stdout.strip() == 'b' * 40
    with sqlite3.connect(raw) as db:
        db.execute('DELETE FROM llm_scenario_review_events')
    with pytest.raises(ValueError, match='reference_tape_unresolved'):
        archive.referenced_commits(raw)


def test_archive_resolves_audit_reference_in_standalone_receiver(tmp_path):
    from utils.btc_audit_references import canonical, compact, sha

    path = tmp_path / 'audit.sqlite'
    snapshot = {'source_commit': 'c' * 40, 'blob': 'x' * 5000}
    context = {'input_id': 'fixture', 'blob': 'y' * 5000}
    event = {'snapshot': snapshot, 'context': context, 'input_id': 'fixture',
             'snapshot_hash': sha(canonical(snapshot)), 'context_hash': sha(canonical(context))}
    s, c, original = json.dumps(snapshot, sort_keys=True), json.dumps(context, sort_keys=True), canonical(event)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER,snapshot TEXT,context TEXT)')
        conn.execute('CREATE TABLE llm_scenario_audit_events(kind TEXT,decision_slot INTEGER,body TEXT)')
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)', (1, s, c))
        conn.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?)', ('decision_input', 1, original))
    baseline = archive.referenced_commits(path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE llm_scenario_audit_events SET body=?', (compact(original, s, c),))
    assert archive.referenced_commits(path) == baseline
    helper = Path(archive.__file__).with_name('btc_audit_references.py').read_text()
    source = Path(archive.__file__).read_text()
    child_script = ("import sys,types; m=types.ModuleType('btc_audit_refs_embedded'); "
                    "exec(compile(%r,'<refs>','exec'),m.__dict__); "
                    "sys.modules['btc_audit_refs_embedded']=m; ns={'__name__':'receiver'}; "
                    "exec(compile(%r,'<archive>','exec'),ns); "
                    "print(ns['referenced_commits'](ns['Path'](sys.argv[1]))[0][0])" % (helper, source))
    child = subprocess.run([os.sys.executable, '-I', '-c', child_script, str(path)], cwd=tmp_path,
                           capture_output=True, text=True, timeout=15, check=True)
    assert child.stdout.strip() == 'c' * 40
    with sqlite3.connect(path) as conn:
        conn.execute('DELETE FROM llm_scenario_decisions')
    with pytest.raises(ValueError, match='reference_audit_unresolved'):
        archive.referenced_commits(path)


def test_archive_resolves_accounting_reference_in_standalone_receiver(tmp_path):
    from utils.btc_accounting_audit_references import TAG, ORIGINAL_HASH, canonical, sha

    path = tmp_path / 'accounting.sqlite'
    account = {'source_commit': 'e' * 40, 'wallet': {'detail': 'x' * 1000}}
    financial = {'trades': [], 'raw_executions': []}
    event = {'observation': account, 'financial_evidence': financial, 'result': {'status': 'confirmed'}}
    original = canonical(event)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind,captured_at,body)')
        conn.execute('CREATE TABLE llm_scenario_audit_events(kind,decision_slot,body)')
        conn.executemany('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                         [('account', 1, json.dumps(account, sort_keys=True)),
                          ('financial', 1, json.dumps(financial, sort_keys=True))])
        conn.execute("INSERT INTO llm_scenario_audit_events VALUES('accounting_observation',1,?)", (original,))
    baseline = archive.referenced_commits(path)
    encoded = dict(event, **{TAG: 1, ORIGINAL_HASH: sha(original)})
    encoded['observation'] = {'rowid': 1, 'captured_at': 1, 'sha256': sha(canonical(account))}
    encoded['financial_evidence'] = {'rowid': 2, 'captured_at': 1, 'sha256': sha(canonical(financial))}
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE llm_scenario_audit_events SET body=?', (canonical(encoded),))
    assert archive.referenced_commits(path) == baseline
    financial_source = Path(archive.__file__).with_name('btc_financial_references.py').read_text()
    accounting_source = Path(archive.__file__).with_name('btc_accounting_audit_references.py').read_text()
    source = Path(archive.__file__).read_text()
    child_script = ("import sys,types; f=types.ModuleType('btc_financial_refs_embedded'); "
                    "exec(compile(%r,'<financial>','exec'),f.__dict__); "
                    "sys.modules['btc_financial_refs_embedded']=f; "
                    "m=types.ModuleType('btc_accounting_refs_embedded'); "
                    "exec(compile(%r,'<accounting>','exec'),m.__dict__); "
                    "sys.modules['btc_accounting_refs_embedded']=m; ns={'__name__':'receiver'}; "
                    "exec(compile(%r,'<archive>','exec'),ns); "
                    "print(ns['referenced_commits'](ns['Path'](sys.argv[1]))[0][0])"
                    % (financial_source, accounting_source, source))
    child = subprocess.run([os.sys.executable, '-I', '-c', child_script, str(path)], cwd=tmp_path,
                           capture_output=True, text=True, timeout=15, check=True)
    assert child.stdout.strip() == 'e' * 40
    with sqlite3.connect(path) as conn:
        conn.execute('DELETE FROM llm_scenario_broker_evidence WHERE kind="account"')
    with pytest.raises(ValueError, match='reference_accounting_unresolved'):
        archive.referenced_commits(path)
