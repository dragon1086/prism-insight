import pytest
from tools.btc_account_checkpoint import load, save


def test_checkpoint_rejects_changed_backup_cutoff_and_tampering(tmp_path):
    path = tmp_path/'state.json'
    value = {'schema':1,'commit':'a'*40,'proof_sha':'b'*64,'identity':'c'*64,'cutoff':3,'after_rowid':1000,'status':'LIMIT_REACHED','updated_total':0,'unchanged_total':0,'saved_bytes_total':0,'batches_total':0}
    save(path,value)
    assert load(path,commit='a'*40,proof_sha='b'*64,identity='c'*64,cutoff=3)['after_rowid'] == 1000
    with pytest.raises(ValueError,match='checkpoint_binding_changed'):
        load(path,commit='a'*40,proof_sha='d'*64,identity='c'*64,cutoff=3)
    path.write_text(path.read_text().replace('1000','2000'))
    with pytest.raises(ValueError,match='checkpoint_binding_changed'):
        load(path,commit='a'*40,proof_sha='b'*64,identity='c'*64,cutoff=3)


def test_checkpoint_rejects_symlink_and_non_private_parent(tmp_path):
    value = {'schema':1,'commit':'a'*40,'proof_sha':'b'*64,'identity':'c'*64,'cutoff':3,'after_rowid':0,'status':'READY','updated_total':0,'unchanged_total':0,'saved_bytes_total':0,'batches_total':0}
    parent = tmp_path/'shared';parent.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='unsafe_checkpoint_directory'):
        save(parent/'state',value)
    target = tmp_path/'target';target.write_text('{}')
    link = tmp_path/'link';link.symlink_to(target)
    with pytest.raises(ValueError,match='unsafe_checkpoint'):
        load(link,commit='a'*40,proof_sha='b'*64,identity='c'*64,cutoff=3)


def test_checkpoint_will_not_replace_shared_or_hardlinked_file(tmp_path):
    import os
    value = {'schema':1,'commit':'a'*40,'proof_sha':'b'*64,'identity':'c'*64,'cutoff':3,'after_rowid':0,'status':'READY','updated_total':0,'unchanged_total':0,'saved_bytes_total':0,'batches_total':0}
    path=tmp_path/'state';path.write_text('existing');path.chmod(0o644)
    with pytest.raises(ValueError,match='unsafe_checkpoint'):
        save(path,value)
    assert path.read_text()=='existing'
    path.chmod(0o600);os.link(path,tmp_path/'hardlink')
    with pytest.raises(ValueError,match='unsafe_checkpoint'):
        save(path,value)
    assert path.read_text()=='existing'


from pathlib import Path
import pytest
from tools.btc_account_compaction_controller import run_once


def test_controller_persists_cursor_and_refuses_changed_backup(tmp_path):
    state = tmp_path/'state.json';ident = {'source':'c'*64,'proof':'b'*64};calls=[]
    def verify(pr):return 'a'*40
    def plan(proof, cutoff, *, after_rowid=0, limit=1000):
        return {'backup_proof_sha256':ident['proof'],'source_identity':ident['source']}
    def run(proof, cutoff, planner, apply, *, after_rowid, max_batches):
        calls.append(after_rowid)
        return {'status':'LIMIT_REACHED','after_rowid':after_rowid+10,'updated':10,'unchanged':0,'saved_bytes':100,'batches':1}
    args=dict(verify=verify,plan=plan,apply=lambda *a,**k:None,run_batches=run)
    first = run_once(state,'proof',3,1254,**args)
    second = run_once(state,'proof',3,1254,**args)
    assert first['after_rowid']==10 and first['updated_total']==10
    assert second['after_rowid']==20 and second['updated_total']==20 and second['saved_bytes_total']==200
    assert calls==[0,10]
    ident['proof']='d'*64
    with pytest.raises(ValueError,match='checkpoint_binding_changed'):
        run_once(state,'proof',3,1254,**args)
    assert calls==[0,10]


def test_error_state_requires_inspection_without_auto_retry(tmp_path):
    state=tmp_path/'state.json';calls=[]
    def plan(*a,**k):return {'backup_proof_sha256':'b'*64,'source_identity':'c'*64}
    def run(*a,**k):calls.append(1);return {'status':'ERROR','after_rowid':0,'updated':0,'unchanged':0,'saved_bytes':0,'batches':0}
    args=dict(verify=lambda _: 'a'*40,plan=plan,apply=lambda *a,**k:None,run_batches=run)
    assert run_once(state,'proof',3,1254,**args)['status']=='ERROR'
    assert run_once(state,'proof',3,1254,**args)['status']=='INSPECTION_REQUIRED'
    assert calls==[1]


def test_controller_does_not_run_concurrently(tmp_path):
    import fcntl, os
    state=tmp_path/'state.json';lock=tmp_path/'.account-compaction.lock';fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            run_once(state,'proof',3,1254,verify=lambda _: (_ for _ in ()).throw(AssertionError()),
                     plan=None,apply=None,run_batches=None)
        assert not state.exists()
    finally:
        os.close(fd)


def test_interrupted_run_requires_inspection_without_automatic_retry(tmp_path):
    state=tmp_path/'state.json';calls=[]
    def plan(*a,**k):return {'backup_proof_sha256':'b'*64,'source_identity':'c'*64}
    def run(*a,**k):
        calls.append(1)
        raise RuntimeError('interrupted after starting remote work')
    args=dict(verify=lambda _: 'a'*40,plan=plan,apply=lambda *a,**k:None,run_batches=run)
    with pytest.raises(RuntimeError,match='interrupted'):
        run_once(state,'proof',3,1254,**args)
    assert run_once(state,'proof',3,1254,**args)['status']=='INSPECTION_REQUIRED'
    assert calls==[1]


def test_controller_rejects_recent_cutoff_before_any_side_effect(tmp_path):
    import time
    state=tmp_path/'state.json'
    with pytest.raises(ValueError,match='invalid_retention_cutoff'):
        run_once(state,'proof',time.time(),1254,verify=None,plan=None,apply=None,run_batches=None)
    assert not list(tmp_path.iterdir())


def test_controller_rejects_backup_swap_before_first_apply(tmp_path):
    state = tmp_path/'state.json'; calls=[]
    def plan(proof, cutoff, *, after_rowid=0, limit=1000):
        calls.append(1)
        return {'backup_proof_sha256':('b' if len(calls)==1 else 'd')*64,'source_identity':'c'*64}
    def run(proof, cutoff, planner, apply, *, after_rowid, max_batches):
        planner(proof, cutoff, after_rowid=after_rowid)
        raise AssertionError('unexpected apply')
    args=dict(verify=lambda _: 'a'*40,plan=plan,apply=lambda *a,**k: (_ for _ in ()).throw(AssertionError()),run_batches=run)
    with pytest.raises(ValueError,match='backup_binding_changed'):
        run_once(state,'proof',3,1254,**args)
    with pytest.raises(ValueError,match='checkpoint_binding_changed'):
        run_once(state,'proof',3,1254,**args)
    assert len(calls)==3


def test_controller_integrates_with_bounded_runner_and_does_not_repeat_completion(tmp_path):
    from tools.btc_account_compaction import run_bounded_batches
    state=tmp_path/'state.json';applied=[]
    def plan(proof, cutoff, *, after_rowid=0, limit=1000):
        return {'source_identity':'c'*64,'backup_proof_sha256':'b'*64,'cutoff':cutoff,
                'after_rowid':after_rowid,'rows':[] if after_rowid>=2 else [{'rowid':after_rowid+1}]}
    def apply(manifest, seal, backup_sha, *, commit):
        assert backup_sha=='b'*64 and commit=='a'*40
        applied.append(manifest['after_rowid'])
        return {'status':'COMMITTED','manifest_sha256':seal,'updated':1,'unchanged':0,'saved_bytes':100}
    args=dict(verify=lambda _: 'a'*40,plan=plan,apply=apply,run_batches=run_bounded_batches)
    result=run_once(state,'proof',3,1254,**args)
    assert result['status']=='COMPLETE' and result['after_rowid']==2 and result['updated_total']==2
    assert run_once(state,'proof',3,1254,**args)['status']=='COMPLETE'
    assert applied==[0,1]
