"""One-attempt localhost OAuth research calls with durable, exact-wire receipts.

``record(event)`` must synchronously persist the local research event. It must
never send raw inputs/responses to public telemetry. Request headers are omitted.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
import os
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

from tools.btc_research_call_guard import CallGuardError


MAX_BYTES = 1_048_576
_KWARGS = {"system_prompt", "user_prompt", "model", "reasoning_effort", "fast_tier",
           "timeout", "response_schema", "mcp_profile", "retry_transport"}


class RuntimeReadinessError(ValueError):
    """A bounded reason for denying a real research model request."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


_REMOTE_READINESS = r"""
import hashlib,json,shlex,subprocess,sys,time
from pathlib import Path
head,cron_hash=sys.argv[1:3]
def idle(proc_root=Path('/proc')):
    try:
        for p in proc_root.iterdir():
            if not p.name.isdecimal():
                continue
            try:
                args=(p/'cmdline').read_bytes().split(b'\0')
            except FileNotFoundError:
                continue
            matches=[i for i,v in enumerate(args) if v==b'live.scenario_runner' or v.endswith(b'/scenario_runner.py')]
            if not matches:
                continue
            if len(matches)!=1 or not args[0] or b'python' not in Path(args[0].decode(errors='replace')).name.encode():
                return False
            n=matches[0]
            if (args[n]==b'live.scenario_runner' and (n<2 or args[n-1]!=b'-m')) or (args[n]!=b'live.scenario_runner' and n<1):
                return False
            if [v for v in args[n+1:] if v]!=[b'--protect-only']:
                return False
        return True
    except OSError:
        return False

def schedule_ok(raw):
    try:
        lines=raw.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return False
    decisions=[]
    for line in lines:
        stripped=line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if 'live.scenario_runner' not in line and 'scenario_runner.py' not in line:
            continue
        fields=line.split(None,5)
        if len(fields)!=6:
            return False
        try:
            command=shlex.split(fields[5],comments=True)
        except ValueError:
            return False
        markers=[token for token in command if token=='live.scenario_runner' or token.endswith('/scenario_runner.py')]
        if len(markers)!=1:
            return False
        if '--protect-only' in command:
            if '--execute' in command:
                return False
            continue
        decisions.append(fields[:5])
    return bool(decisions) and all(fields==['*/5','*','*','*','*'] for fields in decisions)
try:
    actual_head=subprocess.check_output(['git','-C','/root/prism-insight','rev-parse','HEAD'],stderr=subprocess.DEVNULL,timeout=5).decode().strip()
    cron=subprocess.check_output(['crontab','-l'],stderr=subprocess.DEVNULL,timeout=5)
    print(json.dumps({'head_ok':actual_head==head,'cron_ok':hashlib.sha256(cron).hexdigest()==cron_hash,
                      'schedule_ok':schedule_ok(cron),'idle':idle(),'checked_at':time.time()}))
except Exception:
    raise SystemExit(1)
"""


def _required_window_seconds(phase, timeout):
    if phase not in {'DRAFT', 'SELECT', 'SINGLE'} or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeReadinessError('invalid_phase_or_timeout')
    minimum = math.ceil(2 * timeout + 40 if phase == 'DRAFT' else timeout + 25)
    if minimum > 225:
        raise RuntimeReadinessError('timeout_exceeds_window')
    return minimum


def _runtime_readiness(phase, timeout, *, _clock=time.time, _run=subprocess.run, _environ=os.environ):
    """Check real clock and the pinned server; injected dependencies are for unit tests only."""
    head = _environ.get('PRISM_BTC_RESEARCH_EXPECTED_HEAD', '')
    cron = _environ.get('PRISM_BTC_RESEARCH_EXPECTED_CRONTAB_SHA256', '')
    if not isinstance(head, str) or not re.fullmatch(r'[0-9a-f]{40}', head):
        raise RuntimeReadinessError('expected_head_missing_or_invalid')
    if not isinstance(cron, str) or not re.fullmatch(r'[0-9a-f]{64}', cron):
        raise RuntimeReadinessError('expected_crontab_hash_missing_or_invalid')
    minimum = _required_window_seconds(phase, timeout)
    before = _clock()
    if type(before) not in (int, float) or not math.isfinite(before):
        raise RuntimeReadinessError('invalid_local_clock')
    if 300 - before % 300 < minimum:
        raise RuntimeReadinessError('scheduled_tick_near')
    try:
        result = _run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'LogLevel=ERROR',
                       'prism-db', '/usr/bin/python3', '-', head, cron], input=_REMOTE_READINESS,
                      capture_output=True, text=True, timeout=12)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise RuntimeReadinessError('remote_check_failed') from None
    if getattr(result, 'returncode', None) != 0:
        raise RuntimeReadinessError('remote_check_failed')
    try:
        raw = result.stdout
        if not isinstance(raw, str) or len(raw.encode()) > 2048:
            raise ValueError('invalid')
        facts = json.loads(raw)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        raise RuntimeReadinessError('remote_response_invalid') from None
    if (not isinstance(facts, dict) or set(facts) != {'head_ok', 'cron_ok', 'schedule_ok', 'idle', 'checked_at'}
            or any(type(facts[k]) is not bool for k in ('head_ok', 'cron_ok', 'schedule_ok', 'idle'))
            or type(facts['checked_at']) not in (int, float) or not math.isfinite(facts['checked_at'])):
        raise RuntimeReadinessError('remote_response_invalid')
    after = _clock()
    if type(after) not in (int, float) or not math.isfinite(after) or after < before or int(after // 300) != int(before // 300):
        raise RuntimeReadinessError('local_clock_changed')
    if not before - 2 <= facts['checked_at'] <= after + 2:
        raise RuntimeReadinessError('remote_clock_skew')
    if not facts['head_ok']:
        raise RuntimeReadinessError('server_head_changed')
    if not facts['cron_ok']:
        raise RuntimeReadinessError('crontab_changed')
    if not facts['schedule_ok']:
        raise RuntimeReadinessError('unsupported_decision_schedule')
    if not facts['idle']:
        raise RuntimeReadinessError('decision_running')
    if 300 - after % 300 < minimum or 300 - facts['checked_at'] % 300 < minimum:
        raise RuntimeReadinessError('scheduled_tick_near')
    return dict(status='PASSED', phase=phase, expected_head=head, crontab_sha256=cron,
                checked_at=after, remote_checked_at=facts['checked_at'], minimum_seconds=minimum,
                local_remaining_seconds=300 - after % 300, remote_remaining_seconds=300 - facts['checked_at'] % 300,
                head_ok=True, cron_ok=True, schedule_ok=True, idle=True)


def _readiness_still_safe(proof, phase, timeout, *, _clock=time.time):
    if (not isinstance(proof, dict) or proof.get('status') != 'PASSED'
            or any(proof.get(key) is not True for key in ('head_ok', 'cron_ok', 'schedule_ok', 'idle'))
            or proof.get('phase') != phase
            or not isinstance(proof.get('expected_head'), str) or not re.fullmatch(r'[0-9a-f]{40}', proof['expected_head'])
            or not isinstance(proof.get('crontab_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', proof['crontab_sha256'])
            or any(type(proof.get(key)) not in (int, float) or not math.isfinite(proof[key])
                   for key in ('checked_at', 'remote_checked_at', 'local_remaining_seconds', 'remote_remaining_seconds'))
            or type(proof.get('minimum_seconds')) is not int
            or not _required_window_seconds(phase, timeout) <= proof['minimum_seconds'] <= 225):
        raise RuntimeReadinessError('invalid_readiness_proof')
    if (int(proof['checked_at'] // 300) != int(proof['remote_checked_at'] // 300)
            or abs(proof['checked_at'] - proof['remote_checked_at']) > 14
            or proof['local_remaining_seconds'] != 300 - proof['checked_at'] % 300
            or proof['remote_remaining_seconds'] != 300 - proof['remote_checked_at'] % 300
            or min(proof['local_remaining_seconds'], proof['remote_remaining_seconds']) < proof['minimum_seconds']):
        raise RuntimeReadinessError('invalid_readiness_proof')
    now = _clock()
    if (type(now) not in (int, float) or not math.isfinite(now) or now < proof['checked_at']
            or int(now // 300) != int(proof['checked_at'] // 300)
            or 300 - now % 300 < proof['minimum_seconds']):
        raise RuntimeReadinessError('schedule_changed_after_check')


class GuardedModelCall:
    def __init__(self, guard, arm, record, *, open_url=None, monotonic=time.monotonic):
        from live import scenario_oauth
        self.guard, self.arm, self.record = guard, arm, record
        self.open_url = scenario_oauth._local_open if open_url is None else open_url
        self.monotonic = monotonic
        self.actual_http_calls = 0

    def on_failure(self, *args, **kwargs):
        """External schema, clock, accounting, or other technical failure latch."""
        self.guard.stop("technical_failure")

    def _now(self):
        value = self.monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid_research_clock")
        return value

    def __call__(self, **kwargs):
        from live import scenario_oauth
        attempt_id, request_sha256, actual_calls, succeeded = None, None, 0, False
        opener_entries, http_status = 0, None
        runtime_guard_reason = None
        phase = "SINGLE"

        def event(kind, **fields):
            self.record(dict(kind=kind, arm=self.arm, phase=phase,
                             attempt_id=attempt_id, request_sha256=request_sha256, **fields))

        def capture(raw, status):
            if not isinstance(raw, bytes):
                raise ValueError("invalid_http_response_bytes")
            bounded = raw[:MAX_BYTES + 1]
            event("HTTP_RESPONSE", http_status=status, raw_response=bounded.decode("utf-8", errors="replace"),
                  raw_sha256=hashlib.sha256(bounded).hexdigest(), truncated=len(raw) > MAX_BYTES)
            try:
                body = json.loads(bounded)
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(body, dict):
                detail = body.get("error")
                code = detail.get("code") if isinstance(detail, dict) else None
                if (body.get("status") in ("failed", "cancelled")
                        or code in ("terminal_error", "authentication_error", "invalid_token", "invalid_grant")):
                    raise ValueError("explicit_terminal_response")

        def guarded_open(request, timeout):
            nonlocal attempt_id, request_sha256, actual_calls, opener_entries, http_status, runtime_guard_reason
            opener_entries += 1
            if opener_entries != 1:
                raise CallGuardError("research retry forbidden")
            started = self._now()
            url = urlsplit(request.full_url)
            if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
                    or url.path != "/v1/responses" or url.username or url.password or url.query or url.fragment
                    or request.get_method() != "POST"):
                raise ValueError("local_oauth_proxy_required")
            raw = request.data
            if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
                raise ValueError("invalid_http_request_bytes")
            try:
                readiness = _runtime_readiness(phase, timeout)
            except RuntimeReadinessError as error:
                runtime_guard_reason = error.reason
                raise
            request_sha256 = hashlib.sha256(raw).hexdigest()
            readiness_sha256 = hashlib.sha256(json.dumps(readiness, sort_keys=True, separators=(',', ':'),
                                                         ensure_ascii=True, allow_nan=False).encode()).hexdigest()
            attempt_id = self.guard.reserve(self.arm, phase, request_sha256,
                                            runtime_readiness_sha256=readiness_sha256)
            event("HTTP_REQUEST", request_body=raw.decode("utf-8"), generate_kwargs=deepcopy(kwargs),
                  runtime_readiness=deepcopy(readiness))
            if self.guard.snapshot()["stopped"]:
                raise CallGuardError("research stopped before HTTP")
            try:
                _readiness_still_safe(readiness, phase, timeout)
            except RuntimeReadinessError as error:
                runtime_guard_reason = error.reason
                raise
            elapsed = self._now() - started
            if elapsed < 0 or not math.isfinite(timeout) or timeout - elapsed <= 0:
                raise ValueError("research_pre_http_deadline")
            # Counts entry to the real opener, including ambiguous transport errors.
            actual_calls += 1
            self.actual_http_calls += 1
            try:
                response = self.open_url(request, timeout=timeout - elapsed)
            except HTTPError as error:
                http_status = error.code
                if error.fp is not None:
                    try:
                        error_raw = error.read(MAX_BYTES + 1)
                    finally:
                        error.close()
                    capture(error_raw, error.code)
                    raise HTTPError(error.geturl(), error.code, error.msg, error.hdrs,
                                    BytesIO(error_raw)) from error
                raise

            class Response:
                def __enter__(self):
                    response.__enter__()
                    return self

                def __exit__(self, *args):
                    return response.__exit__(*args)

                @property
                def status(self):
                    nonlocal http_status
                    http_status = response.status
                    return http_status

                def geturl(self):
                    return response.geturl()

                def read(self, n):
                    raw_response = response.read(min(n, MAX_BYTES + 1))
                    capture(raw_response, response.status)
                    return raw_response

            return Response()

        try:
            if set(kwargs) - _KWARGS or kwargs.get("retry_transport", False) is not False:
                raise ValueError("research_retry_or_kwargs_policy")
            kwargs = deepcopy(kwargs)
            kwargs["retry_transport"] = False
            properties = kwargs.get("response_schema", {}).get("properties", {})
            phase = "SELECT" if "selection_token" in properties else "DRAFT" if "wait_proposal" in properties else "SINGLE"
            if self.guard.snapshot()["stopped"]:
                raise CallGuardError("research already stopped")
            result = scenario_oauth.generate_scenario(**kwargs, open_url=guarded_open)
            if opener_entries != 1 or actual_calls != 1:
                raise CallGuardError("research HTTP count mismatch")
            event("MODEL_RESULT", text=result.text, actual_http_calls=actual_calls)
            succeeded = True
            return result
        except BaseException as error:
            self.on_failure()
            event("HTTP_ERROR", error_type=type(error).__name__, http_status=http_status,
                  authentication_error=http_status in (401, 403), actual_http_calls=actual_calls,
                  runtime_guard_reason=runtime_guard_reason)
            raise
        finally:
            if attempt_id is not None:
                self.guard.finish(attempt_id, "ok" if succeeded else "error",
                                  actual_http_calls=actual_calls if actual_calls in (0, 1) else None)
