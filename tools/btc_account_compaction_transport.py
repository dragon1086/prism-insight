"""Bounded manifest transport; caller verifies PR and backup independently."""
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess  # nosec B404 - validated arguments and fixed commands

REMOTE = r'''
import os, subprocess, sys
root='/root/prism-insight'; expected,seal,proof=sys.argv[1:]
head=subprocess.check_output(['/usr/bin/git','-C',root,'rev-parse','HEAD'],text=True,timeout=10).strip()
status=subprocess.check_output(['/usr/bin/git','-C',root,'status','--porcelain'],text=True,timeout=10)
if head!=expected or status.strip(): raise SystemExit('server_checkout_changed')
sys.path[:0]=[root+'/prism-btc',root]
from tools.btc_account_compaction import main
sys.argv=['btc_account_compaction','--apply',seal,proof]
main()
'''


def apply_remote(manifest, seal, proof_sha, *, commit):
    if (not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit)
            or not isinstance(seal, str) or not re.fullmatch('[a-f0-9]{64}', seal)
            or not isinstance(proof_sha, str) or not re.fullmatch('[a-f0-9]{64}', proof_sha)):
        raise ValueError('transport_identity_invalid')
    fields = {'source_identity', 'cutoff', 'after_rowid', 'rows', 'backup_proof_sha256'}
    if (not isinstance(manifest, dict) or set(manifest) != fields or manifest.get('backup_proof_sha256') != proof_sha
            or not isinstance(manifest.get('source_identity'), str)
            or not re.fullmatch('[a-f0-9]{64}', manifest['source_identity'])
            or type(manifest.get('cutoff')) not in (int, float) or not math.isfinite(manifest['cutoff'])
            or type(manifest.get('after_rowid')) is not int or manifest['after_rowid'] < 0
            or not isinstance(manifest.get('rows'), list) or not 1 <= len(manifest['rows']) <= 1000
            or any(not isinstance(row, dict) or set(row) != {'rowid', 'captured_at', 'sha256'}
                   or type(row['rowid']) is not int or row['rowid'] <= 0
                   or type(row['captured_at']) not in (int, float) or not math.isfinite(row['captured_at'])
                   or not isinstance(row['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])
                   for row in manifest['rows'])):
        raise ValueError('transport_manifest_invalid')
    payload = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if hashlib.sha256(payload).hexdigest() != seal:
        raise ValueError('transport_seal_mismatch')
    if len(payload) > 512 * 1024:
        raise ValueError('manifest_size_limit')
    args = ['env', 'PYTHONDONTWRITEBYTECODE=1', 'PRISM_DISABLE_SIGNAL_PUBLISH=1',
            'PRISM_OBSERVABILITY_SPOOL=/dev/null', '/root/.pyenv/versions/3.11.11/bin/python',
            '-c', REMOTE, commit, seal, proof_sha]
    result = subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'LogLevel=ERROR', 'prism-db', shlex.join(args)], input=payload, capture_output=True, timeout=25, check=True)  # nosec B603 # nosemgrep
    if len(result.stdout) > 8192:
        raise ValueError('transport_response_limit')
    answer = json.loads(result.stdout)
    if not isinstance(answer, dict) or answer.get('manifest_sha256') != seal:
        raise ValueError('transport_response_invalid')
    return answer


def verified_merge(pr_number):
    """Require the exact clean local PR head and every required CI success."""
    if type(pr_number) is not int or pr_number <= 0:
        raise ValueError('invalid_pr_number')
    fields = 'state,headRefOid,mergeCommit,statusCheckRollup'
    gh = shutil.which('gh')
    if not gh or not os.path.isabs(gh):
        raise ValueError('gh_unavailable')
    proc = subprocess.run([gh, 'pr', 'view', str(pr_number), '--repo', 'dragon1086/prism-insight', '--json', fields], capture_output=True, text=True, timeout=15, check=True)  # nosec B603 # nosemgrep
    pr = json.loads(proc.stdout)
    head = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'], text=True, timeout=10).strip()  # nosec B603 # nosemgrep
    dirty = subprocess.check_output(['/usr/bin/git', 'status', '--porcelain'], text=True, timeout=10)  # nosec B603 # nosemgrep
    checks = pr.get('statusCheckRollup') or []
    required = {'Third-party license compliance', 'CLA', 'report-data', 'Dashboard production build',
                'Test (Python 3.10)', 'Test (Python 3.11)', 'Test (Python 3.12)', 'Codacy Static Code Analysis'}
    merge = (pr.get('mergeCommit') or {}).get('oid')
    if (pr.get('state') != 'MERGED' or pr.get('headRefOid') != head or dirty.strip()
            or not isinstance(merge, str) or not re.fullmatch('[a-f0-9]{40}', merge)
            or not required.issubset({item.get('name') for item in checks})
            or any(item.get('status') != 'COMPLETED' or item.get('conclusion') != 'SUCCESS' for item in checks)):
        raise ValueError('pr_verification_failed')
    return merge


def run_verified_remote_batches(proof_path, cutoff, pr_number, *, after_rowid=0, max_batches=10):
    from tools.btc_account_compaction import plan_attested_accounts, run_bounded_batches
    commit = verified_merge(pr_number)
    def apply(manifest, seal, proof_sha):
        return apply_remote(manifest, seal, proof_sha, commit=commit)
    result = run_bounded_batches(proof_path, cutoff, plan_attested_accounts, apply,
                                 after_rowid=after_rowid, max_batches=max_batches)
    if after_rowid > 0 and result['status'] == 'COMPLETE':
        result['status'] = 'RANGE_COMPLETE'
    return result
