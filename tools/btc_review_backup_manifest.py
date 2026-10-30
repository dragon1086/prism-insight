"""Create private review-event fingerprints only after verifying a full backup."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def private_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('backup_artifact_not_private')


def create_backup_attestation(directory, output):
    """Hash existing backup files once and pin their identities for bounded planning."""
    directory, output = Path(directory), Path(output)
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValueError('backup_directory_not_private')
    receipt_path = directory.parent / (directory.name + '.receipt.json')
    manifest_path = directory / 'verified-manifest.json'
    archive, restored = directory / 'backup.typed.gz', directory / 'restored.sqlite'
    for path in (receipt_path, manifest_path, archive, restored):
        private_file(path)
    if receipt_path.stat().st_size > 32768 or manifest_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('backup_proof_invalid')
    receipt_bytes, manifest_bytes = receipt_path.read_bytes(), manifest_path.read_bytes()
    receipt, manifest = json.loads(receipt_bytes), json.loads(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    identity = receipt.get('source_identity') if isinstance(receipt,dict) else None
    if (not isinstance(receipt,dict) or not isinstance(manifest,dict) or receipt.get('status') != 'VERIFIED'
            or receipt.get('directory') != str(directory) or receipt.get('manifest_sha256') != manifest_sha
            or not isinstance(identity,str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity)
            or identity != manifest.get('source_identity')
            or ('archive_sha256' in receipt and receipt['archive_sha256'] != manifest.get('archive_sha256'))
            or ('archive_bytes' in receipt and receipt['archive_bytes'] != manifest.get('archive_bytes'))):
        raise ValueError('backup_receipt_mismatch')
    files = {}
    for label, path in (('archive',archive),('restored',restored)):
        before = path.stat()
        digest = file_sha256(path)
        after = path.stat()
        if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
                != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
                or before.st_size != manifest.get(label+'_bytes') or digest != manifest.get(label+'_sha256')):
            raise ValueError('backup_artifact_changed')
        files[label] = {'path':str(path),'dev':before.st_dev,'ino':before.st_ino,'size':before.st_size,
                        'mtime_ns':before.st_mtime_ns,'sha256':digest}
    for label, path in (('archive',archive),('restored',restored)):
        private_file(path)
        current, record = path.stat(), files[label]
        if ((current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)
                != (record['dev'],record['ino'],record['size'],record['mtime_ns'])):
            raise ValueError('backup_artifact_changed')
    if receipt_path.read_bytes() != receipt_bytes or manifest_path.read_bytes() != manifest_bytes:
        raise ValueError('backup_artifact_changed')
    proof = {'status':'VERIFIED','checked_unix':time.time(),'source_identity':receipt['source_identity'],
             'manifest_sha256':manifest_sha,'files':files}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0), 0o600)
    created = os.fstat(fd)
    try:
        with os.fdopen(fd,'w') as target:
            json.dump(proof,target,sort_keys=True,indent=2)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        try:
            current = output.lstat()
            if (current.st_dev,current.st_ino) == (created.st_dev,created.st_ino):
                output.unlink()
        except FileNotFoundError:
            pass
        raise
    return proof


def plan_pairs_from_attestation(proof_path, *, after_rowid=0):
    """Plan a bounded batch only from files matching a previously hashed backup."""
    proof_path = Path(proof_path)
    private_file(proof_path)
    if proof_path.stat().st_size > 32768:
        raise ValueError('backup_proof_invalid')
    proof_bytes = proof_path.read_bytes()
    proof = json.loads(proof_bytes)
    if not isinstance(proof, dict) or proof.get('status') != 'VERIFIED' or not isinstance(proof.get('files'), dict):
        raise ValueError('backup_proof_invalid')
    files = proof['files']
    def valid_record(record):
        fields = {'path','dev','ino','size','mtime_ns','sha256'}
        return (isinstance(record,dict) and set(record) == fields and isinstance(record['path'],str)
                and all(type(record[k]) is int and record[k] >= 0 for k in ('dev','ino','size','mtime_ns'))
                and isinstance(record['sha256'],str) and len(record['sha256']) == 64
                and all(c in '0123456789abcdef' for c in record['sha256']))
    if set(files) != {'archive','restored'} or not all(valid_record(files[k]) for k in files):
        raise ValueError('backup_proof_invalid')
    archive, restored = (Path(files[k].get('path','')) for k in ('archive','restored'))
    if (not archive.is_absolute() or not restored.is_absolute() or archive.parent != restored.parent
            or archive.name != 'backup.typed.gz' or restored.name != 'restored.sqlite'):
        raise ValueError('backup_proof_invalid')
    directory = restored.parent
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValueError('backup_directory_not_private')
    receipt_path = directory.parent / (directory.name + '.receipt.json')
    manifest_path = directory / 'verified-manifest.json'
    for path in (archive, restored, receipt_path, manifest_path):
        private_file(path)
    if receipt_path.stat().st_size > 32768 or manifest_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('backup_proof_invalid')
    receipt_bytes, manifest_bytes = receipt_path.read_bytes(), manifest_path.read_bytes()
    if len(receipt_bytes) > 32768 or len(manifest_bytes) > 4 * 1024 * 1024:
        raise ValueError('backup_proof_invalid')
    receipt, detail = json.loads(receipt_bytes), json.loads(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if (not isinstance(receipt,dict) or not isinstance(detail,dict)
            or receipt.get('status') != 'VERIFIED' or receipt.get('directory') != str(directory)
            or manifest_sha != proof.get('manifest_sha256') or manifest_sha != receipt.get('manifest_sha256')
            or proof.get('source_identity') != receipt.get('source_identity')
            or proof.get('source_identity') != detail.get('source_identity')
            or ('archive_sha256' in receipt and receipt['archive_sha256'] != detail.get('archive_sha256'))
            or ('archive_bytes' in receipt and receipt['archive_bytes'] != detail.get('archive_bytes'))):
        raise ValueError('backup_receipt_mismatch')
    def check_files():
        for label, path in (('archive', archive),('restored', restored)):
            private_file(path)
            record, current = files[label], path.stat()
            if ((current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)
                    != (record.get('dev'),record.get('ino'),record.get('size'),record.get('mtime_ns'))
                    or record.get('size') != detail.get(label+'_bytes')
                    or record.get('sha256') != detail.get(label+'_sha256')):
                raise ValueError('backup_artifact_changed')
    check_files()
    from tools.btc_review_event_compaction import plan_backed_pairs
    with closing(sqlite3.connect(restored.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        result = plan_backed_pairs(conn, proof['source_identity'], after_rowid=after_rowid)
    check_files()
    if (proof_path.read_bytes() != proof_bytes or receipt_path.read_bytes() != receipt_bytes
            or manifest_path.read_bytes() != manifest_bytes):
        raise ValueError('backup_artifact_changed')
    result['backup_proof_sha256'] = hashlib.sha256(proof_bytes).hexdigest()
    return result


def create_manifest(directory, output):
    directory, output = Path(directory), Path(output)
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValueError('backup_directory_not_private')
    receipt = directory.parent / (directory.name + '.receipt.json')
    manifest, archive, restored = (directory / name for name in
                                   ('verified-manifest.json', 'backup.typed.gz', 'restored.sqlite'))
    for path in (receipt, manifest, archive, restored):
        private_file(path)
    proof = json.loads(receipt.read_text())
    if (proof.get('status') != 'VERIFIED' or proof.get('directory') != str(directory)
            or file_sha256(manifest) != proof.get('manifest_sha256')):
        raise ValueError('backup_receipt_mismatch')
    detail = json.loads(manifest.read_text())
    identity = proof.get('source_identity')
    if identity != detail.get('source_identity') or not isinstance(identity, str) or len(identity) != 64:
        raise ValueError('backup_identity_mismatch')
    originals = {}
    for label, path in (('archive', archive), ('restored', restored)):
        before = path.stat()
        if before.st_size != detail.get(label + '_bytes') or file_sha256(path) != detail.get(label + '_sha256'):
            raise ValueError('backup_artifact_changed')
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('backup_artifact_changed')
        originals[path] = after
    rows = []
    with sqlite3.connect(restored.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        for rowid, slot, sequence, body in conn.execute(
                'SELECT rowid,slot,sequence,body FROM llm_scenario_review_events ORDER BY rowid'):
            if (any(type(x) is not int or x < 0 for x in (rowid, slot, sequence)) or rowid == 0
                    or not isinstance(body, (str, bytes))):
                raise ValueError('backup_row_invalid')
            raw = body.encode('utf-8') if isinstance(body, str) else body
            rows.append({'rowid':rowid, 'slot':slot, 'sequence':sequence,
                         'expected_type':'text' if isinstance(body,str) else 'blob',
                         'expected_sha256':hashlib.sha256(raw).hexdigest()})
    for path, before in originals.items():
        current = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
            raise ValueError('backup_artifact_changed')
    if output.parent.resolve() != directory.resolve():
        raise ValueError('manifest_must_be_in_backup_directory')
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as target:
            json.dump({'source_identity':identity, 'rows':rows}, target, separators=(',', ':'))
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('backup_directory')
    parser.add_argument('output')
    args = parser.parse_args()
    count = create_manifest(args.backup_directory, args.output)
    print(json.dumps({'status':'COMPLETE','rows':count}))


if __name__ == '__main__':
    main()
