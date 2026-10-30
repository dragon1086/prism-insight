"""Consistent, private, compressed SQLite backup; never restore into the source."""
from __future__ import annotations

import argparse
from contextlib import closing
import gzip
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import zlib


def _hash_stream(stream, deadline):
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        if time.monotonic() > deadline:
            raise TimeoutError("snapshot_deadline")
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _unlink_owned(path, identity):
    try:
        stat = path.lstat()
        if (stat.st_dev, stat.st_ino) == identity:
            path.unlink()
    except FileNotFoundError:
        pass


def _compress_in_place(path, raw_bytes, progress):
    """Compress a disposable snapshot without overwriting unread input."""
    consumed = written = 0
    compressor = zlib.compressobj(level=3, wbits=31)
    with path.open('r+b', buffering=0) as stream:
        def write(data):
            nonlocal written
            if consumed < raw_bytes and written + len(data) > consumed:
                raise OSError('snapshot_compression_overtook_input')
            while data:
                count = os.pwrite(stream.fileno(), data, written)
                if count <= 0:
                    raise OSError('snapshot_compression_short_write')
                written += count
                data = data[count:]

        while consumed < raw_bytes:
            progress(None, None, None)
            chunk = os.pread(stream.fileno(), min(1024 * 1024, raw_bytes - consumed), consumed)
            if not chunk:
                raise OSError('snapshot_compression_short_read')
            consumed += len(chunk)
            write(compressor.compress(chunk))
        write(compressor.flush())
        progress(None, None, None)
        os.ftruncate(stream.fileno(), written)
        os.fsync(stream.fileno())


def backup_database(source, destination, *, timeout=180, reserve_bytes=4 * 1024**3,
                    capture_references=False, retention_cursor=None):
    source, destination = Path(source), Path(destination)
    if not source.is_file() or source.is_symlink() or destination.suffix != ".gz":
        raise ValueError("invalid_snapshot_paths")
    if source.resolve() == destination.resolve() or destination.exists():
        raise ValueError("snapshot_destination_exists_or_is_source")
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or type(reserve_bytes) is not int or reserve_bytes < 0):
        raise ValueError("invalid_snapshot_limits")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.parent.is_symlink():
        raise ValueError("snapshot_directory_symlink")
    receipt = destination.with_name(destination.name + ".manifest.json")
    if receipt.exists():
        raise ValueError("snapshot_receipt_exists")
    source_bytes = source.stat().st_size
    source_stat = source.stat()
    source_identity = hashlib.sha256(
        f'{source.resolve()}:{source_stat.st_dev}:{source_stat.st_ino}'.encode()).hexdigest()
    # Compression reuses only the disposable snapshot and never touches the source.
    wal = Path(str(source) + "-wal")
    wal_bytes = wal.stat().st_size if wal.is_file() else 0
    if shutil.disk_usage(destination.parent).free < source_bytes + wal_bytes + reserve_bytes:
        raise OSError("insufficient_snapshot_space")
    lock = destination.with_name(destination.name + ".lock")
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    lock_stat = os.fstat(lock_fd)
    os.close(lock_fd)
    temporary = []
    published = []
    deadline = time.monotonic() + timeout
    started = time.time()
    try:
        def temp(suffix):
            fd, name = tempfile.mkstemp(prefix=".sqlite-snapshot-", suffix=suffix, dir=destination.parent)
            os.close(fd)
            path = Path(name)
            temporary.append(path)
            return path

        raw = temp(".sqlite")
        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError("snapshot_deadline")
            if shutil.disk_usage(destination.parent).free < reserve_bytes:
                raise OSError("snapshot_space_reserve_lost")

        logging.getLogger(__name__).info('snapshot_stage=copy')
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as src:
            src.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(raw)) as dst:
                src.backup(dst, pages=256, progress=progress, sleep=0.05)
                progress(None, None, None)
                logging.getLogger(__name__).info('snapshot_stage=integrity')
                dst.execute("PRAGMA journal_mode=DELETE")
                dst.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                if dst.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("snapshot_integrity_failed")
                progress(None, None, None)
                dst.commit()
        logging.getLogger(__name__).info('snapshot_stage=hash')
        with raw.open("rb") as stream:
            raw_hash, raw_bytes = _hash_stream(stream, deadline)
        reference_scan = None
        if capture_references:
            logging.getLogger(__name__).info('snapshot_stage=reference_scan')
            if __package__:
                from .btc_offsite_archive import referenced_commits
                from .btc_review_retention import read_cursor, snapshot_index
            else:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
                from utils.btc_offsite_archive import referenced_commits
                from utils.btc_review_retention import read_cursor, snapshot_index
            commits, coverage = referenced_commits(raw, deadline=deadline)
            after_slot = read_cursor(retention_cursor, source_identity) if retention_cursor is not None else -1
            with closing(sqlite3.connect(raw.resolve().as_uri() + '?mode=ro', uri=True)) as index_conn:
                retention_index = snapshot_index(index_conn, int(started), after_slot=after_slot,
                                                 guard=lambda: progress(None, None, None))
            progress(None, None, None)
            reference_scan = {'commits': commits, 'coverage': coverage}
        logging.getLogger(__name__).info('snapshot_stage=compress')
        _compress_in_place(raw, raw_bytes, progress)
        compressed = raw
        logging.getLogger(__name__).info('snapshot_stage=verify')
        with gzip.open(compressed, "rb") as stream:
            restored_hash, restored_bytes = _hash_stream(stream, deadline)
        if (raw_hash, raw_bytes) != (restored_hash, restored_bytes):
            raise ValueError("snapshot_roundtrip_failed")
        with compressed.open("rb") as stream:
            compressed_hash, compressed_bytes = _hash_stream(stream, deadline)
        result = dict(schema_version=1, started_at=started, completed_at=time.time(),
                      sqlite_quick_check="ok", compression="gzip", source_opened_readonly=True,
                      source_basename=source.name, raw_sha256=raw_hash, raw_bytes=raw_bytes,
                      source_identity_sha256=source_identity,
                      compressed_sha256=compressed_hash, compressed_bytes=compressed_bytes,
                      decompression_verified=True, source_writes=0)
        if reference_scan is not None:
            result['code_reference_scan'] = reference_scan
            result['btc_retention_index'] = retention_index
        manifest = temp(".json")
        with manifest.open("w", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publishing is atomic and refuses overwriting a competing file.
        logging.getLogger(__name__).info('snapshot_stage=publish')
        os.link(compressed, destination)
        stat = compressed.stat()
        published.append((destination, (stat.st_dev, stat.st_ino)))
        os.link(manifest, receipt)
        stat = manifest.stat()
        published.append((receipt, (stat.st_dev, stat.st_ino)))
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return result
    except BaseException:
        for path, identity in published:
            _unlink_owned(path, identity)
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)
        _unlink_owned(lock, (lock_stat.st_dev, lock_stat.st_ino))


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--capture-references", action="store_true")
    parser.add_argument("--retention-cursor", type=Path)
    args = parser.parse_args()
    result = backup_database(args.source, args.destination, timeout=args.timeout,
                             capture_references=args.capture_references, retention_cursor=args.retention_cursor)
    print(json.dumps(dict(status="ok", destination=str(args.destination), **result)))


if __name__ == "__main__":
    main()
