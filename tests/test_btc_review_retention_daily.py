import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools import run_btc_review_retention_daily as daily


@pytest.fixture
def dated_backup(tmp_path, monkeypatch):
    root=tmp_path/'backups';root.mkdir(mode=0o700)
    backup=root/'20261031_020001';backup.mkdir(mode=0o700)
    marker=backup/'.btc-retention-mode';marker.write_bytes(b'1\n');marker.chmod(0o600)
    monkeypatch.setattr(daily.retention,'BACKUP_ROOT',root)
    now=datetime.datetime(2026,10,31,22,2,40,tzinfo=ZoneInfo('Asia/Seoul'))
    return root,backup,marker,now


def test_daily_never_falls_back_after_newer_backup_failed(dated_backup):
    root,_,_,now=dated_backup
    (root/'20261031_030001').mkdir(mode=0o700)
    with pytest.raises(FileNotFoundError):
        daily.select_today(root,now)
    with pytest.raises(ValueError,match='today_backup_missing'):
        daily.select_today(root,now+datetime.timedelta(days=1))


def test_daily_rejects_unsafe_marker(dated_backup):
    root,backup,marker,now=dated_backup
    marker.chmod(0o644)
    with pytest.raises(ValueError,match='retention_private_file_invalid'):
        daily.select_today(root,now)
    marker.unlink();marker.symlink_to(backup/'missing')
    with pytest.raises(ValueError,match='mode_invalid'):
        daily.select_today(root,now)


@pytest.mark.parametrize('mode',['0','1'])
def test_daily_uses_matching_verified_mode_only(dated_backup,monkeypatch,mode):
    _,backup,marker,now=dated_backup;marker.write_text(mode+'\n');marker.chmod(0o600)
    manifest={'btc_review_event_proof_status':'CAPTURED'} if mode=='1' else {}
    ack={'review_event_proof_index_verified':True} if mode=='1' else {}
    monkeypatch.setattr(daily.retention,'_backup_evidence',lambda p:(p,'id',json.dumps(manifest).encode(),ack,{}))
    calls=[]
    monkeypatch.setattr(daily,'cleanup_events',lambda p:calls.append(('events',p)) or {'status':'COMPLETE'})
    monkeypatch.setattr(daily.retention,'cleanup',lambda p:calls.append(('tape',p)) or {'status':'COMPLETE'})
    assert daily.run(now)=={'status':'COMPLETE'}
    assert calls==[('events' if mode=='1' else 'tape',backup)]


def test_daily_defers_missing_event_proof_without_deletion(dated_backup,monkeypatch):
    _,_,_,now=dated_backup
    monkeypatch.setattr(daily.retention,'_backup_evidence',lambda p:(p,'id',b'{"btc_review_event_proof_status":"UNAVAILABLE"}',{},{}))
    monkeypatch.setattr(daily,'cleanup_events',lambda _:pytest.fail('must not delete'))
    assert daily.run(now)=={'status':'DEFERRED_EVENT_PROOF'}


@pytest.mark.parametrize('removed',[0,400])
def test_daily_tape_mode_preserves_more_to_scan_status(dated_backup,monkeypatch,removed):
    _,_,marker,now=dated_backup;marker.write_text('0\n');marker.chmod(0o600)
    monkeypatch.setattr(daily.retention,'_backup_evidence',lambda p:(p,'id',b'{}',{}, {}))
    monkeypatch.setattr(daily.retention,'cleanup',lambda _: {'status':'MORE_TO_SCAN','removed':removed})
    assert daily.run(now)=={'status':'MORE_TO_SCAN','removed':removed}


def test_persistent_catchup_never_reads_backups_outside_quiet_window(dated_backup,monkeypatch):
    _,_,_,now=dated_backup
    monkeypatch.setattr(daily,'select_today',lambda *_:pytest.fail('must not read backup'))
    assert daily.run(now+datetime.timedelta(hours=1))=={'status':'DEFERRED_SCHEDULE_WINDOW'}


@pytest.mark.parametrize(('status','exit_code'),[('COMPLETE',0),('NO_CANDIDATES',0),
    ('DEFERRED',1),('DEFERRED_EVENT_PROOF',1),('DEFERRED_SCHEDULE_WINDOW',1),('MORE_TO_SCAN',1),('BUDGET_LIMIT',1),('PARTIAL',1)])
def test_daily_result_never_reports_deferred_or_incomplete_as_success(monkeypatch,status,exit_code):
    monkeypatch.setattr(daily,'run',lambda _: {'status':status})
    assert daily.main()==exit_code


def test_daily_timer_is_persistent_and_uses_b3_safe_window(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'prism-btc'))
    from tools.btc_account_compaction import _outside_b3_live_window
    root=Path(__file__).resolve().parents[1]
    timer=(root/'deploy/systemd/prism-btc-daily-retention.timer').read_text()
    assert 'OnCalendar=*-*-* 22:02:40 Asia/Seoul' in timer and 'Persistent=true' in timer
    for month in (1,7):
        at=datetime.datetime(2026,month,15,22,2,40,tzinfo=ZoneInfo('Asia/Seoul'))
        assert _outside_b3_live_window(at.timestamp())
