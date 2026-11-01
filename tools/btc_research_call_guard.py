"""Durable, fail-closed serial HTTP attempt budget for registered BTC research.

Reserve inside the opener boundary, before HTTP; finish once, even on failure.
No retries, refunds, or reset are supported. Unknown HTTP counts stay unknown.
The root is a private research directory, not a hostile-writer security boundary.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re


class CallGuardError(RuntimeError):
    """Research must not issue any further HTTP request."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value):
        raise ValueError("expected bounded symbolic identifier, not payload/error text")
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected lowercase SHA256")
    return value


class CallGuard:
    def __init__(self, root, registry_sha256, max_calls):
        self.root = Path(root)
        self.registry_sha256 = _sha(registry_sha256)
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        self.max_calls = max_calls
        self.root.mkdir(exist_ok=True)
        self.ledger = self.root / "attempts.jsonl"
        self.anchors = self.root / "anchors"
        with self._locked():
            if not self.ledger.exists():
                if self.anchors.exists():
                    raise CallGuardError("missing ledger")
                self.anchors.mkdir()
                with self.ledger.open("xb") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                self._append([], {"kind": "init", "registry_sha256": registry_sha256,
                                  "max_calls": max_calls, "version": 1})
                self._sync_dir(self.root.parent)
            records, state = self._read()
            # Reopening an interrupted run cannot authorize a retry or late success.
            if state["inflight"] and not state["stopped"]:
                self._append(records, {"kind": "stop", "reason": "unfinished_attempt"})

    @contextmanager
    def _locked(self):
        with (self.root / "guard.lock").open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _sync_dir(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _append(self, records, event):
        record = dict(event, seq=len(records), previous=records[-1]["sha256"] if records else "0" * 64)
        record["sha256"] = _hash(record)
        with self.ledger.open("ab") as stream:
            stream.write(_canonical(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Immutable anchors detect even removal of a complete valid chain suffix.
        with (self.anchors / f"{record['seq']:08d}.sha256").open("xb") as stream:
            stream.write(record["sha256"].encode())
            stream.flush()
            os.fsync(stream.fileno())
        self._sync_dir(self.anchors)
        self._sync_dir(self.root)

    def _read(self):
        try:
            raw = self.ledger.read_bytes()
            if not raw or not raw.endswith(b"\n"):
                raise ValueError("empty or truncated ledger")
            records = []
            state = {"reserved_attempts": 0, "actual_http_calls": 0,
                     "unknown_http_attempts": 0, "inflight": None,
                     "stopped": False, "stop_reason": None}
            for line in raw.splitlines():
                record = json.loads(line)
                sha = record.pop("sha256")
                if (sha != _hash(record) or record["seq"] != len(records)
                        or record["previous"] != (records[-1]["sha256"] if records else "0" * 64)
                        or self.anchors.joinpath(f"{len(records):08d}.sha256").read_text() != sha):
                    raise ValueError("invalid ledger chain or anchor")
                record["sha256"] = sha
                kind = record["kind"]
                fields = {"seq", "previous", "sha256", "kind"}
                if not records:
                    if (kind != "init" or record["version"] != 1
                            or record["registry_sha256"] != self.registry_sha256
                            or record["max_calls"] != self.max_calls
                            or type(record["max_calls"]) is not int):
                        raise ValueError("registry or cap mismatch")
                    fields |= {"version", "registry_sha256", "max_calls"}
                elif kind == "reserve":
                    fields |= {"attempt_id", "arm", "phase", "request_sha256"}
                    if 'runtime_readiness_sha256' in record:
                        fields.add('runtime_readiness_sha256')
                        _sha(record['runtime_readiness_sha256'])
                    if state["stopped"] or state["inflight"] or state["reserved_attempts"] >= self.max_calls:
                        raise ValueError("illegal reservation")
                    state["reserved_attempts"] += 1
                    if record["attempt_id"] != f"attempt-{state['reserved_attempts']:06d}":
                        raise ValueError("invalid attempt identifier")
                    _token(record["arm"])
                    _token(record["phase"])
                    _sha(record["request_sha256"])
                    state["inflight"] = record["attempt_id"]
                    state["unknown_http_attempts"] += 1
                elif kind == "finish":
                    fields |= {"attempt_id", "status", "actual_http_calls"}
                    count = record["actual_http_calls"]
                    if (record["attempt_id"] != state["inflight"]
                            or record["status"] not in ("ok", "error")
                            or (count is not None and (type(count) is not int or count not in (0, 1)))):
                        raise ValueError("invalid or duplicate finish")
                    state["inflight"] = None
                    if count is not None:
                        state["unknown_http_attempts"] -= 1
                        state["actual_http_calls"] += count
                    if record["status"] != "ok" or count is None:
                        if not state["stopped"]:
                            state["stop_reason"] = "attempt_error" if count is not None else "unknown_http_count"
                        state["stopped"] = True
                elif kind == "stop":
                    fields |= {"reason"}
                    _token(record["reason"])
                    if state["stopped"]:
                        raise ValueError("duplicate stop")
                    state["stopped"], state["stop_reason"] = True, record["reason"]
                else:
                    raise ValueError("unknown event")
                if set(record) != fields:
                    raise ValueError("unknown ledger fields")
                records.append(record)
            if {p.name for p in self.anchors.iterdir()} != {f"{i:08d}.sha256" for i in range(len(records))}:
                raise ValueError("ledger/anchor length mismatch")
            return records, state
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CallGuardError("invalid research call ledger; no HTTP permitted") from exc

    def reserve(self, arm, phase, request_sha256, *, runtime_readiness_sha256=None):
        arm, phase, request_sha256 = _token(arm), _token(phase), _sha(request_sha256)
        if runtime_readiness_sha256 is not None:
            runtime_readiness_sha256 = _sha(runtime_readiness_sha256)
        with self._locked():
            records, state = self._read()
            if state["stopped"] or state["inflight"]:
                raise CallGuardError("research stopped or attempt unfinished")
            if state["reserved_attempts"] >= self.max_calls:
                self._append(records, {"kind": "stop", "reason": "call_cap_reached"})
                raise CallGuardError("research HTTP attempt cap reached")
            attempt_id = f"attempt-{state['reserved_attempts'] + 1:06d}"
            reservation = {"kind": "reserve", "attempt_id": attempt_id,
                           "arm": arm, "phase": phase, "request_sha256": request_sha256}
            if runtime_readiness_sha256 is not None:
                reservation['runtime_readiness_sha256'] = runtime_readiness_sha256
            self._append(records, reservation)
            return attempt_id

    def finish(self, attempt_id, status, actual_http_calls=None):
        _token(attempt_id)
        if status not in ("ok", "error"):
            raise ValueError("status must be ok or error")
        if actual_http_calls is not None and (type(actual_http_calls) is not int or actual_http_calls not in (0, 1)):
            raise ValueError("actual_http_calls must be 0, 1, or None (unknown)")
        with self._locked():
            records, state = self._read()
            if state["inflight"] != attempt_id:
                if not state["stopped"]:
                    self._append(records, {"kind": "stop", "reason": "invalid_finish"})
                raise CallGuardError("unknown or already finished attempt")
            self._append(records, {"kind": "finish", "attempt_id": attempt_id,
                                  "status": status, "actual_http_calls": actual_http_calls})

    def stop(self, reason):
        reason = _token(reason)
        with self._locked():
            records, state = self._read()
            if not state["stopped"]:
                self._append(records, {"kind": "stop", "reason": reason})

    def snapshot(self):
        with self._locked():
            _, state = self._read()
            return dict(state, registry_sha256=self.registry_sha256, max_calls=self.max_calls)

    @classmethod
    def inspect_existing(cls, root, registry_sha256, max_calls):
        """Validate and inspect a live ledger without invoking restart recovery."""
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        guard = object.__new__(cls)
        guard.root = Path(root)
        guard.registry_sha256 = _sha(registry_sha256)
        guard.max_calls = max_calls
        guard.ledger = guard.root / "attempts.jsonl"
        guard.anchors = guard.root / "anchors"
        try:
            with (guard.root / "guard.lock").open("rb") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
                try:
                    _, state = guard._read()
                    return dict(state, registry_sha256=guard.registry_sha256, max_calls=max_calls)
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            raise CallGuardError("cannot inspect existing research call ledger") from exc
