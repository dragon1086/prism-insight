"""Private verified BTC database/Git archive over existing host-verified SSH."""
import argparse
from contextlib import closing
import fcntl
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess  # nosec B404 - fixed git/ssh executables, validated argv, no local shell
import sys
import tarfile
import tempfile
import time
import uuid
import zlib

RESERVE = 5 * 1024**3
CAP = 10 * 1024**3
RAW_CAP = 20 * 1024**3
REMOTE_ROOT = Path('/root/prism-btc-archive')
FILES = ('snapshot.sqlite.gz', 'snapshot.manifest.json', 'history.bundle', 'archive.json')
COMMIT_KEYS = {'git_revision', 'source_commit', 'code_commit', 'source_reference'}
EVIDENCE_QUERIES = {
    'llm_scenario_audit_manifests': {'body': 'SELECT body FROM llm_scenario_audit_manifests'},
    'llm_scenario_audit_events': {'body': 'SELECT body FROM llm_scenario_audit_events'},
    'llm_scenario_recovery_events': {'body': 'SELECT body FROM llm_scenario_recovery_events'},
    'llm_scenario_review_events': {'body': 'SELECT body FROM llm_scenario_review_events'},
    'llm_scenario_decisions': {'context': 'SELECT context FROM llm_scenario_decisions',
                               'snapshot': 'SELECT snapshot FROM llm_scenario_decisions'},
    'llm_scenario_review_tapes': {'body': 'SELECT body FROM llm_scenario_review_tapes'},
}


def review_text(value):
    """Keep the receiver self-contained while accepting tagged review records."""
    magic = b'PRISM_REVIEW_GZIP_V1\x00'
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        raise ValueError('review_body_type_invalid')
    if not value.startswith(magic):
        return value.decode('utf-8')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = decoder.decompress(value[len(magic):], 8_000_001)
    except zlib.error as exc:
        raise ValueError('review_body_compression_invalid') from exc
    if len(raw) > 8_000_000 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('review_body_compression_invalid')
    return raw.decode('utf-8')


def run(args, *, cwd=None, timeout=600):
    if not isinstance(args, list) or not all(isinstance(x, str) and '\x00' not in x for x in args):
        raise ValueError('invalid_command_argv')
    if not args or args[0] != 'git':
        raise ValueError('invalid_command_executable')
    tail = args[1:]
    def absolute(path):
        return Path(path).is_absolute()
    def commit(value):
        return re.fullmatch('[0-9a-f]{40}', value) is not None
    valid = (tail in [['fsck', '--full'], ['rev-parse', 'HEAD'], ['rev-parse', '--git-path', 'objects']]
             or len(tail) == 3 and tail[:2] == ['cat-file', '-e'] and tail[2].endswith('^{commit}') and commit(tail[2][:-9])
             or len(tail) == 3 and tail[0] == 'update-ref' and commit(tail[2]) and tail[1] == 'refs/archive-evidence/' + tail[2]
             or len(tail) == 4 and tail[:2] == ['bundle', 'create'] and absolute(tail[2]) and tail[3] == '--all'
             or len(tail) == 3 and tail[:2] == ['bundle', 'verify'] and absolute(tail[2])
             or len(tail) == 6 and tail[:4] == ['clone', '--mirror', '--shared', '--no-hardlinks'] and all(map(absolute, tail[4:]))
             or len(tail) == 4 and tail[:2] == ['clone', '--bare'] and all(map(absolute, tail[2:])))
    if not valid or cwd is not None and not Path(cwd).is_absolute():
        raise ValueError('invalid_git_command')
    return subprocess.run(['/usr/bin/git', *tail], cwd=cwd, check=True, capture_output=True, shell=False,  # nosec B603  # nosemgrep
                          text=True, timeout=timeout).stdout


def validate_host(host):
    if not isinstance(host, str) or len(host) > 253 or any(
            not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in host.split('.')):
        raise ValueError('invalid_backend_host')
    if re.fullmatch(r'[0-9.]+', host):
        ipaddress.IPv4Address(host)


def ssh_archive(host, needed, *, stream=None):
    validate_host(host)
    if type(needed) is not int or not 0 <= needed <= 4 * RAW_CAP:
        raise ValueError('invalid_space_requirement')
    source = Path(__file__).read_text()
    refs_source = Path(__file__).with_name('btc_review_references.py').read_text()
    prelude = ("import sys,types\n"
               "_btc_refs=types.ModuleType('btc_review_refs_embedded')\n"
               "exec(compile(" + repr(refs_source) + ",'<btc_review_refs>','exec'),_btc_refs.__dict__)\n"
               "sys.modules['btc_review_refs_embedded']=_btc_refs\n")
    suffix = ('\nprivate_root(REMOTE_ROOT); space(REMOTE_ROOT, ' + str(needed) + '); print("READY")\n'
              if stream is None else '\nprint(json.dumps(receive(sys.stdin.buffer, REMOTE_ROOT, ' + str(needed) + ')))\n')
    command = '/usr/bin/python3 -c ' + shlex.quote(
        "exec(compile(" + repr(prelude + source + suffix) + ", '<archive>', 'exec'), {'__name__':'offsite_receiver'})")
    # Only this repository's receiver source and a bounded integer reach the remote command.
    return subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',  # nosec B603  # nosemgrep
                           '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15',
                           '-o', 'ServerAliveCountMax=3', 'root@' + host, command],
                          stdin=stream, capture_output=True, check=True, shell=False,
                          timeout=60 if stream is None else 1800)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_root(root):
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('unsafe_archive_root')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('archive_root_not_private')


def space(root, needed):
    if not isinstance(needed, int) or not 0 <= needed <= 4 * RAW_CAP:
        raise ValueError('invalid_space_requirement')
    usage = 0
    for p in root.rglob('*'):
        if p.is_symlink():
            raise ValueError('archive_symlink')
        if p.is_file():
            usage += p.stat().st_size
    if usage + needed > CAP or shutil.disk_usage(root).free < RESERVE + needed:
        raise OSError('archive_space_limit')


def restore_snapshot(zipped, manifest, raw):
    if (manifest.get('sqlite_quick_check') != 'ok' or
            manifest.get('decompression_verified') is not True or
            manifest.get('source_writes') != 0 or
            type(manifest.get('raw_bytes')) is not int or
            not 0 < manifest['raw_bytes'] <= RAW_CAP or
            zipped.stat().st_size != manifest.get('compressed_bytes') or
            digest(zipped) != manifest.get('compressed_sha256')):
        raise ValueError('snapshot_manifest_mismatch')
    total = 0
    deadline = time.monotonic() + 600
    with gzip.open(zipped, 'rb') as src, raw.open('xb') as dst:
        while chunk := src.read(1024 * 1024):
            total += len(chunk)
            if total > manifest['raw_bytes'] or time.monotonic() > deadline:
                raise ValueError('snapshot_expansion_bound')
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    if total != manifest['raw_bytes'] or digest(raw) != manifest.get('raw_sha256'):
        raise ValueError('snapshot_raw_mismatch')
    with closing(sqlite3.connect(raw.as_uri() + '?mode=ro', uri=True)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('snapshot_quick_check')


def referenced_commits(raw):
    """Find exact observed revisions in retained JSON; never manufacture missing refs."""
    found = set()
    coverage = dict(scope='WELL_FORMED_STORED_REVISIONS_IN_KNOWN_EVIDENCE_COLUMNS_ONLY',
                    wellformed_revision_occurrences=0, unique_known_revision_count=0,
                    invalid_or_unknown_revision_fields=0, missing_manifest_git_revision=0,
                    malformed_evidence_rows=0, missing_evidence_values=0,
                    manifest_table_present=False)
    def walk(x):
        if isinstance(x, dict):
            for key, value in x.items():
                if key in COMMIT_KEYS:
                    if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value):
                        found.add(value)
                        coverage['wellformed_revision_occurrences'] += 1
                    else:
                        coverage['invalid_or_unknown_revision_fields'] += 1
                walk(value)
        elif isinstance(x, list):
            for value in x:
                walk(value)
    deadline = time.monotonic() + 600
    with closing(sqlite3.connect(raw.as_uri() + '?mode=ro', uri=True)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        # Only known evidence columns, not unrelated application secrets/settings.
        for table, queries in EVIDENCE_QUERIES.items():
            cols = [r[0] for r in db.execute('SELECT name FROM pragma_table_info(?)', (table,))]
            if table == 'llm_scenario_audit_manifests':
                coverage['manifest_table_present'] = bool(cols)
            for col, query in queries.items():
                if col not in cols:
                    continue
                with_slot = table == 'llm_scenario_review_tapes' and 'slot' in cols
                selected_query = ('SELECT slot,body FROM llm_scenario_review_tapes' if with_slot else query)
                for row in db.execute(selected_query):
                    slot, value = row if with_slot else (None, row[0])
                    is_reference = (table == 'llm_scenario_review_tapes' and isinstance(value, bytes)
                                    and value.startswith(b'PRISM_REVIEW_REFS_V1\x00'))
                    if time.monotonic() > deadline:
                        raise TimeoutError('commit_scan_deadline')
                    if value is None or value == '':
                        parsed = None
                        coverage['missing_evidence_values'] += 1
                    else:
                        try:
                            if is_reference:
                                if 'btc_review_refs_embedded' in sys.modules:
                                    from btc_review_refs_embedded import read_tape
                                elif __package__:
                                    from .btc_review_references import read_tape
                                else:
                                    from btc_review_references import read_tape
                                value = read_tape(value, db, slot, decode_legacy=review_text)
                            elif table in ('llm_scenario_review_events', 'llm_scenario_review_tapes', 'llm_scenario_recovery_events'):
                                value = review_text(value)
                            parsed = json.loads(value)
                        except (TypeError, ValueError) as exc:
                            if is_reference:
                                raise ValueError('reference_tape_unresolved') from exc
                            parsed = None
                            coverage['malformed_evidence_rows'] += 1
                    if table == 'llm_scenario_audit_manifests' and (
                            not isinstance(parsed, dict) or 'git_revision' not in parsed):
                        coverage['missing_manifest_git_revision'] += 1
                    walk(parsed)
    coverage['unique_known_revision_count'] = len(found)
    return sorted(found), coverage


def make_bundle(repo, bundle, commits, bare):
    run(['git', 'clone', '--mirror', '--shared', '--no-hardlinks', str(repo), str(bare)])
    for commit in commits:
        if not re.fullmatch('[0-9a-f]{40}', commit):
            raise ValueError('invalid_commit')
        try:
            run(['git', 'cat-file', '-e', commit + '^{commit}'], cwd=repo)
        except subprocess.CalledProcessError:
            raise ValueError('referenced_commit_unavailable:' + commit) from None
        run(['git', 'update-ref', 'refs/archive-evidence/' + commit, commit], cwd=bare)
    run(['git', 'bundle', 'create', str(bundle), '--all'], cwd=bare)
    run(['git', 'bundle', 'verify', str(bundle)], cwd=bare)


def receive(stream, root, needed):
    """Receive only selected artifacts; publish after independent restore checks."""
    os.umask(0o077)
    private_root(root)
    lock = root / '.receive.lock'
    if lock.is_symlink():
        raise ValueError('archive_lock_symlink')
    lock_fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    stage = None
    try:
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('unsafe_archive_lock')
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        space(root, needed)
        stage = Path(tempfile.mkdtemp(prefix='.incoming-', dir=root))
        seen, transferred = set(), 0
        with tarfile.open(fileobj=stream, mode='r|') as tar:
            for member in tar:
                if member.name not in FILES or member.name in seen or not member.isfile():
                    raise ValueError('archive_member_rejected')
                if member.size < 0 or member.size > CAP or transferred + member.size > needed:
                    raise ValueError('archive_transfer_bound')
                if member.name.endswith('.json') and member.size > 16 * 1024**2:
                    raise ValueError('archive_metadata_bound')
                seen.add(member.name)
                transferred += member.size
                with closing(tar.extractfile(member)) as src, (stage / member.name).open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                os.chmod(stage / member.name, 0o600)
        if seen != set(FILES):
            raise ValueError('archive_partial_transfer')
        metadata = json.loads((stage / 'archive.json').read_text())
        manifest = json.loads((stage / 'snapshot.manifest.json').read_text())
        for name in FILES[:-1]:
            if digest(stage / name) != metadata['sha256'][name]:
                raise ValueError('archive_checksum_mismatch')
        if metadata['required_space'] != needed:
            raise ValueError('archive_space_binding')
        if needed < (transferred + int(manifest['raw_bytes']) + 3 * (stage / 'history.bundle').stat().st_size):
            raise ValueError('archive_restore_space_bound')
        space(root, needed - transferred)
        raw = stage / 'restored.sqlite'
        restore_snapshot(stage / FILES[0], manifest, raw)
        actual, coverage = referenced_commits(raw)
        if actual != metadata['referenced_commits']:
            raise ValueError('archive_commit_manifest_mismatch')
        if coverage != metadata.get('code_reference_coverage'):
            raise ValueError('archive_reference_coverage_mismatch')
        bare = stage / 'restored.git'
        run(['git', 'clone', '--bare', str(stage / 'history.bundle'), str(bare)])
        source_head = metadata.get('source_head')
        if not isinstance(source_head, str) or not re.fullmatch('[0-9a-f]{40}', source_head):
            raise ValueError('archive_source_head')
        for commit in [*actual, source_head]:
            run(['git', 'cat-file', '-e', commit + '^{commit}'], cwd=bare)
        run(['git', 'fsck', '--full'], cwd=bare)
        # These are verification-only expansions, never retained private application copies.
        raw.unlink()
        shutil.rmtree(bare)
        ack = dict(status='VERIFIED', raw_sha256=manifest['raw_sha256'],
                   compressed_sha256=manifest['compressed_sha256'],
                   manifest_sha256=metadata['sha256'][FILES[1]],
                   bundle_sha256=metadata['sha256'][FILES[2]],
                   archive_metadata_sha256=digest(stage / 'archive.json'),
                   referenced_commit_count=len(actual), code_reference_coverage=coverage)
        with (stage / 'receipt.json').open('x') as dst:
            json.dump(ack, dst)
            dst.flush()
            os.fsync(dst.fileno())
        sync_dir(stage)
        destination = root / ('archive-' + uuid.uuid4().hex)
        os.rename(stage, destination)
        sync_dir(root)
        return dict(ack, archive=destination.name)
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
        os.close(lock_fd)


def backend_host(config):
    if not config.exists():
        return None
    values = []
    for line in config.read_text().splitlines():
        if line.strip().startswith('PRISM_BACKEND_HOST='):
            values.append(line.strip().split('=', 1)[1].strip().strip('\"\''))
    if len(values) != 1 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', values[0]):
        raise ValueError('invalid_backend_host')
    validate_host(values[0])
    return values[0]


def archive(repo, snapshot, config):
    host = backend_host(config)
    if host is None:
        return dict(status='DISABLED', reason='backend_configuration_absent')
    manifest_path = Path(str(snapshot) + '.manifest.json')
    manifest = json.loads(manifest_path.read_text())
    private = snapshot.parent
    if snapshot.is_symlink() or manifest_path.is_symlink():
        raise ValueError('snapshot_symlink')
    # Local workspace must accommodate raw restore and complete Git packing before upload.
    git_objects = Path(run(['git', 'rev-parse', '--git-path', 'objects'], cwd=repo).strip())
    if not git_objects.is_absolute():
        git_objects = repo / git_objects
    objects_size = sum(p.stat().st_size for p in git_objects.rglob('*') if p.is_file())
    local_need = int(manifest['raw_bytes']) + 4 * objects_size + 2 * snapshot.stat().st_size + 1024**2
    if shutil.disk_usage(private).free < RESERVE + local_need:
        raise OSError('local_archive_space_limit')
    with tempfile.TemporaryDirectory(prefix='.btc-offsite-', dir=private) as tmp:
        stage = Path(tmp)
        raw = stage / 'snapshot.sqlite'
        restore_snapshot(snapshot, manifest, raw)
        commits, coverage = referenced_commits(raw)
        source_head = run(['git', 'rev-parse', 'HEAD'], cwd=repo).strip()
        bundle = stage / 'history.bundle'
        make_bundle(repo, bundle, sorted(set(commits) | {source_head}), stage / 'source.git')
        hashes = {FILES[0]: digest(snapshot), FILES[1]: digest(manifest_path), FILES[2]: digest(bundle)}
        # Includes gz+manifest+bundle, raw SQLite, bare Git expansion and conservative slack.
        needed = snapshot.stat().st_size + manifest_path.stat().st_size + int(manifest['raw_bytes']) + 4 * bundle.stat().st_size + 1024**2
        metadata = dict(schema_version=1, source_head=source_head, referenced_commits=commits,
                        code_reference_coverage=coverage, sha256=hashes, required_space=needed)
        meta = stage / 'archive.json'
        meta.write_text(json.dumps(metadata, sort_keys=True))
        if ssh_archive(host, needed).stdout.decode().strip() != 'READY':
            raise ValueError('archive_preflight')
        # Selected files only; never recurse over the backup directory or copy .env.
        tar_path = stage / 'transfer.tar'
        with tarfile.open(tar_path, 'w') as tar:
            for path, name in ((snapshot, FILES[0]), (manifest_path, FILES[1]), (bundle, FILES[2]), (meta, FILES[3])):
                tar.add(path, arcname=name, recursive=False)
        with tar_path.open('rb') as stream:
            completed = ssh_archive(host, needed, stream=stream)
        ack = json.loads(completed.stdout)
        if (ack.get('status') != 'VERIFIED' or ack.get('raw_sha256') != manifest['raw_sha256'] or
                ack.get('compressed_sha256') != hashes[FILES[0]] or ack.get('manifest_sha256') != hashes[FILES[1]] or
                ack.get('bundle_sha256') != hashes[FILES[2]] or ack.get('archive_metadata_sha256') != digest(meta) or
                ack.get('referenced_commit_count') != len(commits) or ack.get('code_reference_coverage') != coverage):
            raise ValueError('archive_ack_mismatch')
        return ack


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--config', type=Path, default=Path('/etc/prism-observability/tunnel.env'))
    args = parser.parse_args()
    try:
        result = archive(args.repo.resolve(), args.snapshot.absolute(), args.config)
        print(json.dumps(result))
        return 0 if result['status'] == 'VERIFIED' else 2
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[a-z0-9_:]{1,100}', str(exc)) else 'archive_failed'
        print(json.dumps(dict(status='ERROR', error_type=type(exc).__name__, reason=reason)))
        return 1


if __name__ == '__main__':
    sys.exit(main())
