import fcntl
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import btc_offsite_archive as archive


def published(root,number):
    path=root/('archive-'+format(number,'032x'));path.mkdir(mode=0o700)
    raw=hashlib.sha256(b'source').hexdigest()
    data=b'verified historical snapshot '+str(number).encode()
    (path/archive.FILES[0]).write_bytes(data)
    manifest=dict(raw_sha256=raw,compressed_sha256=hashlib.sha256(data).hexdigest(),compressed_bytes=len(data))
    (path/archive.FILES[1]).write_text(json.dumps(manifest))
    (path/archive.FILES[2]).write_bytes(b'verified git bundle')
    hashes={name:archive.digest(path/name)for name in archive.FILES[:-1]}
    (path/archive.FILES[3]).write_text(json.dumps(dict(sha256=hashes)))
    receipt=dict(status='VERIFIED',raw_sha256=raw,compressed_sha256=hashes[archive.FILES[0]],
        manifest_sha256=hashes[archive.FILES[1]],bundle_sha256=hashes[archive.FILES[2]],
        archive_metadata_sha256=archive.digest(path/archive.FILES[3]))
    (path/'receipt.json').write_text(json.dumps(receipt))
    for p in path.iterdir():p.chmod(0o600)
    os.utime(path/'receipt.json',(100+number,100+number))
    return path


def test_preflight_releases_accumulated_copies_but_preserves_latest_before_replacement(tmp_path,monkeypatch):
    root=tmp_path/'archives';root.mkdir(mode=0o700)
    paths=[published(root,n)for n in range(4)]
    latest={p.name:p.read_bytes()for p in paths[-1].iterdir()}
    def disk(_):
        count=sum(p.is_dir()for p in root.iterdir())
        return SimpleNamespace(free=archive.RESERVE+(50 if count==1 else 0))
    monkeypatch.setattr(archive.shutil,'disk_usage',disk)
    archive.prepare_archive(root,50)
    assert all(not p.exists()for p in paths[:-1])
    assert {p.name:p.read_bytes()for p in paths[-1].iterdir()}==latest
    # A later failed upload cannot remove the last known good archive.
    with pytest.raises(OSError,match='archive_space_limit'):archive.prepare_archive(root,51)
    assert paths[-1].exists()


@pytest.mark.parametrize('damage',['latest_payload','old_receipt','symlink','unexpected_file'])
def test_unverified_or_unsafe_archives_abort_before_any_deletion(tmp_path,monkeypatch,damage):
    root=tmp_path/'archives';root.mkdir(mode=0o700)
    old,latest=published(root,1),published(root,2)
    if damage=='latest_payload':
        p=latest/archive.FILES[0];data=p.read_bytes();p.write_bytes(b'X'+data[1:])
    if damage=='old_receipt':(old/'receipt.json').write_text('{}')
    if damage=='symlink':(old/'receipt.json').unlink();(old/'receipt.json').symlink_to(latest/'receipt.json')
    if damage=='unexpected_file':(old/'user-data.txt').write_text('preserve')
    with pytest.raises(ValueError):archive.prepare_archive(root,1)
    assert old.exists()and latest.exists()


def test_active_archive_lease_blocks_gc_and_zero_needed_is_read_only(tmp_path):
    root=tmp_path/'archives';root.mkdir(mode=0o700)
    a,b=published(root,1),published(root,2)
    archive.prepare_archive(root,0)
    assert a.exists()and b.exists()
    with(root/'.receive.lock').open('rb')as lease:
        fcntl.flock(lease,fcntl.LOCK_SH|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):archive.prepare_archive(root,1)
    assert a.exists()and b.exists()
