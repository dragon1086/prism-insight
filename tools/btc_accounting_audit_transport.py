"""Transport sealed backup-derived accounting compaction batches to the server."""
import hashlib
import json
import re
import shlex
import subprocess  # nosec B404 - fixed SSH command and validated arguments

REMOTE = r'''
import subprocess,sys
root='/root/prism-insight'; expected,seal,proof=sys.argv[1:]
head=subprocess.check_output(['/usr/bin/git','-C',root,'rev-parse','HEAD'],text=True,timeout=10).strip()
status=subprocess.check_output(['/usr/bin/git','-C',root,'status','--porcelain'],text=True,timeout=10)
if head!=expected or status.strip(): raise SystemExit('server_checkout_changed')
sys.path[:0]=[root+'/prism-btc',root]
from tools.btc_accounting_audit_compaction import main
sys.argv=['btc_accounting_audit_compaction','--apply',seal,proof]
main()
'''


def apply_remote(manifest, seal, proof_sha, *, commit):
    if (not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit)
            or not isinstance(seal, str) or not re.fullmatch('[a-f0-9]{64}', seal)
            or not isinstance(proof_sha, str) or not re.fullmatch('[a-f0-9]{64}', proof_sha)
            or not isinstance(manifest, dict) or manifest.get('backup_proof_sha256') != proof_sha
            or not isinstance(manifest.get('rows'), list) or not 1 <= len(manifest['rows']) <= 100):
        raise ValueError('transport_manifest_invalid')
    payload = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if hashlib.sha256(payload).hexdigest() != seal or len(payload) > 512*1024:
        raise ValueError('transport_seal_invalid')
    args = ['env', 'PYTHONDONTWRITEBYTECODE=1', 'PRISM_DISABLE_SIGNAL_PUBLISH=1',
            'PRISM_OBSERVABILITY_SPOOL=/dev/null', '/root/.pyenv/versions/3.11.11/bin/python',
            '-c', REMOTE, commit, seal, proof_sha]
    result = subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             '-o', 'LogLevel=ERROR', 'prism-db', shlex.join(args)], input=payload,
                            capture_output=True, timeout=25, check=True)  # nosec B603 # nosemgrep
    if len(result.stdout) > 8192:
        raise ValueError('transport_response_limit')
    answer = json.loads(result.stdout)
    if not isinstance(answer, dict) or answer.get('manifest_sha256') != seal:
        raise ValueError('transport_response_invalid')
    return answer


def run_verified_controller(state_path, proof_path, cutoff, pr_number):
    from tools.btc_account_compaction_controller import run_once
    from tools.btc_account_compaction_transport import verified_merge
    from tools.btc_accounting_audit_compaction import plan_attested_audits, run_bounded_batches
    return run_once(state_path, proof_path, cutoff, pr_number, verify=verified_merge,
                    plan=plan_attested_audits, apply=apply_remote, run_batches=run_bounded_batches)
