"""One bounded run; state contains metadata only, never account data."""
import fcntl
import math
import os
import time
from pathlib import Path
import stat

from tools.btc_account_checkpoint import load, save


def run_once(state_path, proof_path, cutoff, pr_number, *, verify, plan, apply, run_batches):
    if type(cutoff) not in (int, float) or not math.isfinite(cutoff) or not 0 < cutoff <= time.time() - 7 * 86400:
        raise ValueError('invalid_retention_cutoff')
    state_path = Path(state_path)
    parent = state_path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
        raise ValueError('unsafe_state_directory')
    lock_path = parent/'.account-compaction.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('unsafe_state_lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        commit = verify(pr_number)
        first = plan(proof_path, cutoff, after_rowid=0, limit=1)
        proof_sha, identity = first['backup_proof_sha256'], first['source_identity']
        state = load(state_path, commit=commit, proof_sha=proof_sha, identity=identity, cutoff=cutoff)
        totals = {k:state[k] for k in ('updated_total','unchanged_total','saved_bytes_total','batches_total')}
        if state['status'] in ('ERROR', 'STARTED'):
            return {'status':'INSPECTION_REQUIRED', 'after_rowid':state['after_rowid'], **totals}
        if state['status'] in ('COMPLETE','RANGE_COMPLETE'):
            return {'status':state['status'], 'after_rowid':state['after_rowid'], **totals}
        before = state['after_rowid']
        initial = {'schema':1,'commit':commit,'proof_sha':proof_sha,'identity':identity,
                   'cutoff':cutoff,'after_rowid':before,'status':'STARTED',
                   **{k:state[k] for k in ('updated_total','unchanged_total','saved_bytes_total','batches_total')}}
        save(state_path, initial)
        def bound_plan(proof, pinned_cutoff, *, after_rowid):
            manifest = plan(proof, pinned_cutoff, after_rowid=after_rowid)
            if (manifest.get('backup_proof_sha256') != proof_sha
                    or manifest.get('source_identity') != identity or pinned_cutoff != cutoff):
                raise ValueError('backup_binding_changed')
            return manifest
        def transport(manifest, seal, backup_sha):
            return apply(manifest, seal, backup_sha, commit=commit)
        result = run_batches(proof_path, cutoff, bound_plan, transport, after_rowid=before, max_batches=10)
        status = result.get('status')
        if status not in ('COMPLETE','LIMIT_REACHED','DEADLINE_REACHED','DEFERRED','ERROR'):
            raise ValueError('invalid_run_result')
        after = result.get('after_rowid')
        if type(after) is not int or after < before:
            raise ValueError('invalid_run_cursor')
        for key in ('updated','unchanged','saved_bytes','batches'):
            if type(result.get(key)) is not int or result[key] < 0:
                raise ValueError('invalid_run_totals')
        if before > 0 and status == 'COMPLETE':
            status = 'RANGE_COMPLETE'
        totals = {name+'_total':initial[name+'_total'] + result[name] for name in ('updated','unchanged','saved_bytes','batches')}
        save(state_path, dict(initial, status=status, after_rowid=after, **totals))
        return dict(result, status=status, **totals)
    finally:
        os.close(fd)


def main():
    import argparse
    import json
    from tools.btc_account_compaction import plan_attested_accounts, run_bounded_batches
    from tools.btc_account_compaction_transport import apply_remote, verified_merge
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--state', required=True)
    parser.add_argument('--proof', required=True)
    parser.add_argument('--cutoff', required=True, type=float)
    parser.add_argument('--pr', required=True, type=int)
    args = parser.parse_args()
    if not args.apply:
        raise ValueError('explicit_apply_required')
    result = run_once(args.state, args.proof, args.cutoff, args.pr, verify=verified_merge,
                      plan=plan_attested_accounts, apply=apply_remote, run_batches=run_bounded_batches)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
