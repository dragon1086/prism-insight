"""Private resume metadata; never stores account bodies or initiates a migration."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def seal(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load(path, *, commit, proof_sha, identity, cutoff):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return {'status':'READY', 'after_rowid':0, 'updated_total':0, 'unchanged_total':0, 'saved_bytes_total':0, 'batches_total':0}
    stat = path.lstat()
    if path.is_symlink() or not path.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_nlink != 1 or stat.st_size > 8192:
        raise ValueError('unsafe_checkpoint')
    value = json.loads(path.read_bytes())
    fields = {'schema','commit','proof_sha','identity','cutoff','after_rowid','status','updated_total','unchanged_total','saved_bytes_total','batches_total','seal'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('invalid_checkpoint')
    digest = value.pop('seal')
    if (digest != seal(value) or value['schema'] != 1 or value['commit'] != commit
            or value['proof_sha'] != proof_sha or value['identity'] != identity or value['cutoff'] != cutoff
            or type(value['after_rowid']) is not int or value['after_rowid'] < 0
            or any(type(value[k]) is not int or value[k] < 0 for k in ('updated_total','unchanged_total','saved_bytes_total','batches_total'))
            or value['status'] not in ('READY','STARTED','LIMIT_REACHED','DEADLINE_REACHED','DEFERRED','RANGE_COMPLETE','COMPLETE','ERROR')):
        raise ValueError('checkpoint_binding_changed')
    return value


def save(path, value):
    path = Path(path);parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077 or path.is_symlink():
        raise ValueError('unsafe_checkpoint_directory')
    if path.exists():
        current = path.lstat()
        if not path.is_file() or current.st_uid != os.getuid() or current.st_mode & 0o077 or current.st_nlink != 1:
            raise ValueError('unsafe_checkpoint')
    payload = dict(value, seal=seal(value))
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > 8192:
        raise ValueError('checkpoint_too_large')
    fd, name = tempfile.mkstemp(prefix='.btc-account-checkpoint-', dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(parent, os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)
