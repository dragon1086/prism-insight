"""Create private review-event fingerprints only after verifying a full backup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def private_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('backup_artifact_not_private')


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
