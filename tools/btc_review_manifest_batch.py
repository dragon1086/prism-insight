"""Select a bounded batch from a previously fingerprinted private backup."""
import argparse
import hashlib
import json
from pathlib import Path


def select_batch(path, expected_sha256, expected_identity, after):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('manifest_not_private')
    if (type(after) is not int or after < 0 or not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(c not in '0123456789abcdef' for c in expected_sha256)
            or not isinstance(expected_identity, str) or len(expected_identity) != 64
            or any(c not in '0123456789abcdef' for c in expected_identity)):
        raise ValueError('invalid_selection_context')
    before = path.stat()
    payload = path.read_bytes()
    after_stat = path.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after_stat.st_dev, after_stat.st_ino, after_stat.st_size, after_stat.st_mtime_ns)
            or hashlib.sha256(payload).hexdigest() != expected_sha256):
        raise ValueError('fingerprint_manifest_changed')
    data = json.loads(payload)
    if (not isinstance(data, dict) or set(data) != {'source_identity', 'rows'}
            or data['source_identity'] != expected_identity or not isinstance(data['rows'], list)):
        raise ValueError('fingerprint_manifest_invalid')
    ids = [row.get('rowid') if isinstance(row, dict) else None for row in data['rows']]
    if any(type(i) is not int or i < 1 for i in ids) or ids != sorted(set(ids)):
        raise ValueError('fingerprint_manifest_invalid')
    rows = [row for row in data['rows'] if row['rowid'] > after][:20]
    return {'source_identity':expected_identity, 'rows':rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('manifest_sha256')
    parser.add_argument('source_identity')
    parser.add_argument('--after', type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(select_batch(args.manifest, args.manifest_sha256, args.source_identity, args.after)))


if __name__ == '__main__':
    main()
