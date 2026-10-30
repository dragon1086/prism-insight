"""Run one bounded retention pass for today's verified backup, never an old fallback."""
import datetime
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from utils import btc_review_retention as retention
from utils.btc_review_event_proof import cleanup_events


def select_today(root, now):
    root=Path(root)
    if (root.is_symlink() or not root.is_dir() or root.stat().st_uid!=os.getuid()
            or root.stat().st_mode&0o022 or now.tzinfo is None):
        raise ValueError('daily_retention_root_invalid')
    day=now.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')
    entries=sorted((p for p in root.iterdir() if re.fullmatch(day+r'_\d{6}',p.name)),reverse=True)
    if not entries:
        raise ValueError('daily_retention_today_backup_missing')
    directory=entries[0]
    if (directory.is_symlink() or not directory.is_dir() or directory.resolve().parent!=root.resolve()
            or directory.stat().st_uid!=os.getuid() or directory.stat().st_mode&0o077):
        raise ValueError('daily_retention_directory_invalid')
    marker=directory/'.btc-retention-mode'
    if marker.is_symlink() or marker.stat().st_nlink!=1:
        raise ValueError('daily_retention_mode_invalid')
    mode=retention._private_read(marker,8)
    if mode not in (b'0\n',b'1\n'):
        raise ValueError('daily_retention_mode_invalid')
    return directory,mode.decode().strip()


def run(now):
    if now.tzinfo is None:
        raise ValueError('daily_retention_time_invalid')
    local=now.astimezone(ZoneInfo('Asia/Seoul'))
    if local.hour!=22 or local.minute!=2 or not 25<=local.second<=49:
        return {'status':'DEFERRED_SCHEDULE_WINDOW'}
    directory,mode=select_today(retention.BACKUP_ROOT,now)
    _,_,manifest_bytes,ack,_=retention._backup_evidence(directory)
    manifest=json.loads(manifest_bytes)
    if mode=='1':
        if (manifest.get('btc_review_event_proof_status')!='CAPTURED'
                or ack.get('review_event_proof_index_verified') is not True):
            return {'status':'DEFERRED_EVENT_PROOF'}
        return cleanup_events(directory)
    if 'btc_review_event_proof_status' in manifest:
        raise ValueError('daily_retention_mode_mismatch')
    return retention.cleanup(directory)


def main():
    try:
        result=run(datetime.datetime.now(datetime.timezone.utc))
    except Exception as exc:
        reason=str(exc)
        result={'status':'ERROR','reason':reason if re.fullmatch(r'daily_retention_[a-z_]+',reason) else 'SOURCE_UNAVAILABLE'}
    print(json.dumps(result,sort_keys=True))
    return 0 if result.get('status') in {'COMPLETE','NO_CANDIDATES'} else 1


if __name__=='__main__':raise SystemExit(main())
