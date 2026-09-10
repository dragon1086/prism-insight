"""Host-operator-only additive disposition, never an automatic retry/reset.

Opening a lane does not migrate it. No approval is created by this module.
An UNKNOWN row and its original case ID remain permanently unusable.
One original case permits exactly one immutable future source/runtime scope;
later revisions need a separately reviewed authorization design, not a new lane.
Operator attestations are not cryptographic authentication. No invocation has
been approved by this module; collection never substitutes for operator consent.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

DECISION = "ACCEPT_DISTINCT_NO_ORDER_DIAGNOSTICS"
_BASE_SQL = {
    "metadata": "CREATE TABLE metadata(version INTEGER NOT NULL)",
    "cases": "CREATE TABLE cases(case_id TEXT PRIMARY KEY,payload_sha256 TEXT NOT NULL,state TEXT NOT NULL,claimed_at REAL NOT NULL,finished_at REAL,summary TEXT)",
}
_TABLE_SQL = (
    "CREATE TABLE dispositions(case_id TEXT PRIMARY KEY,payload_sha256 TEXT NOT NULL,"
    "evidence_sha256 TEXT NOT NULL,operator_authorization_ref TEXT NOT NULL,"
    "approval_sha256 TEXT NOT NULL,scope_sha256 TEXT NOT NULL,quiescence_json TEXT NOT NULL,"
    "decision TEXT NOT NULL CHECK(decision='ACCEPT_DISTINCT_NO_ORDER_DIAGNOSTICS'))"
)
_TRIGGERS = {
    "unknown_case_no_update": "CREATE TRIGGER unknown_case_no_update BEFORE UPDATE ON cases WHEN OLD.state='UNKNOWN' BEGIN SELECT RAISE(ABORT,'immutable_unknown'); END",
    "unknown_case_no_delete": "CREATE TRIGGER unknown_case_no_delete BEFORE DELETE ON cases WHEN OLD.state='UNKNOWN' BEGIN SELECT RAISE(ABORT,'immutable_unknown'); END",
    "disposition_no_update": "CREATE TRIGGER disposition_no_update BEFORE UPDATE ON dispositions BEGIN SELECT RAISE(ABORT,'immutable_disposition'); END",
    "disposition_no_delete": "CREATE TRIGGER disposition_no_delete BEFORE DELETE ON dispositions BEGIN SELECT RAISE(ABORT,'immutable_disposition'); END",
}


class DispositionRejected(ValueError):
    pass


def validate_lane_schema(db):
    """Read-only schema check; reject unknown additions and changed triggers."""
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = db.execute("SELECT version FROM metadata").fetchall()
    if version not in ([(1,)], [(2,)]):
        raise DispositionRejected("unknown_claim_database")
    version = version[0][0]
    if tables != ({"metadata", "cases"} if version == 1 else {"metadata", "cases", "dispositions"}):
        raise DispositionRejected("unknown_claim_database")
    for name, sql in _BASE_SQL.items():
        actual = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if actual != (sql,):
            raise DispositionRejected("unknown_claim_database")
    expected_indexes = {("sqlite_autoindex_cases_1", "cases", None)}
    if version == 2:
        expected_indexes.add(("sqlite_autoindex_dispositions_1", "dispositions", None))
    indexes = set(db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index'"))
    if indexes != expected_indexes or db.execute("SELECT 1 FROM sqlite_master WHERE type NOT IN ('table','index','trigger') LIMIT 1").fetchone():
        raise DispositionRejected("unknown_claim_database")
    metadata = db.execute("PRAGMA table_info(metadata)").fetchall()
    cases = db.execute("PRAGMA table_info(cases)").fetchall()
    if ([row[1:4] for row in metadata] != [("version", "INTEGER", 1)]
            or [row[1:4] for row in cases] != [("case_id", "TEXT", 0), ("payload_sha256", "TEXT", 1),
                ("state", "TEXT", 1), ("claimed_at", "REAL", 1), ("finished_at", "REAL", 0), ("summary", "TEXT", 0)]
            or cases[0][5] != 1):
        raise DispositionRejected("unknown_claim_database")
    triggers = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
    if triggers != ({} if version == 1 else _TRIGGERS):
        raise DispositionRejected("unknown_claim_database")
    if version == 2:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='dispositions'").fetchone()[0]
        if sql != _TABLE_SQL:
            raise DispositionRejected("unknown_claim_database")
    return version


def migrate_v1_to_v2(lane):
    """Explicit operator API under the existing lane lock. Never called by claim."""
    from tools.isolated_trading_case_supervisor import CaseLane
    if not isinstance(lane, CaseLane) or lane.fd is None or lane.db is None or lane.db.in_transaction:
        raise DispositionRejected("locked_lane_required")
    db = lane.db
    db.execute("BEGIN IMMEDIATE")
    try:
        if validate_lane_schema(db) != 1:
            raise DispositionRejected("explicit_v1_migration_required")
        db.execute(_TABLE_SQL)
        for sql in _TRIGGERS.values():
            db.execute(sql)
        db.execute("UPDATE metadata SET version=2")
        validate_lane_schema(db)
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _artifact(root, filename, expected_hash):
    if not _hash(expected_hash) or filename not in {"operator-disposition-approval.json", "operator-disposition-evidence.json"}:
        raise DispositionRejected("invalid_operator_artifact")
    path = Path(root) / filename
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 1 <= info.st_size <= 65536):
            raise DispositionRejected("invalid_operator_artifact")
        raw = os.read(fd, 65537)
        if len(raw) != info.st_size or hashlib.sha256(raw).hexdigest() != expected_hash:
            raise DispositionRejected("operator_artifact_hash_mismatch")
        value = json.loads(raw)
        if type(value) is not dict:
            raise DispositionRejected("invalid_operator_artifact")
        return value, raw
    except (OSError, ValueError):
        raise DispositionRejected("invalid_operator_artifact") from None
    finally:
        if fd is not None:
                os.close(fd)


def _archive_bytes(root, raw):
    """Store exact bounded bytes by hash, exclusive and read-only; never repair."""
    from tools.isolated_trading_case_supervisor import _private_root
    root = _private_root(root)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 65536:
        raise DispositionRejected("invalid_archive_bytes")
    digest = hashlib.sha256(raw).hexdigest()
    directory = root / "operator-artifacts"
    fd = parent = None
    try:
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _private_root(directory)
        parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        path = directory / (digest + ".json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o400 or info.st_size != len(raw)
                    or os.read(fd, 65537) != raw):
                raise DispositionRejected("archive_conflict")
        else:
            cursor = 0
            while cursor < len(raw):
                count = os.write(fd, raw[cursor:])
                if count <= 0:
                    raise OSError()
                cursor += count
            os.fchmod(fd, 0o400)
        os.fsync(fd)
        os.fsync(parent)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        return digest
    except (OSError, ValueError):
        raise DispositionRejected("archive_failed") from None
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def _host_probe(original_registration, state_root):
    """Mandatory fixed host collector, never a caller boolean/callback."""
    from tools.isolated_host_quiescence import collect, QuiescenceRejected
    try:
        return collect(original_registration, state_root)
    except QuiescenceRejected:
        raise DispositionRejected("host_quiescence_unavailable") from None


def _validated_registration(registration, root):
    from tools.isolated_trading_case_supervisor import _registration, namespace
    inputs, payload = _registration(registration, root)
    namespace.validate_python_source(inputs["source_root"], inputs["script_hash"], inputs["source_files"])
    namespace.validate_tree(inputs["runtime_root"], inputs["runtime_files"])
    return inputs, payload


def _original_artifact_hash(registration):
    from tools.isolated_trading_case_supervisor import _private_file
    path = Path(registration.runtime.root) / "case-result.json"
    _private_file(path)
    if path.resolve() != path or path.stat().st_size > 256 * 1024:
        raise DispositionRejected("original_artifact_unverified")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _claim_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def record_operator_disposition(lane, *, expected_approval_sha256, original_registration):
    """Trusted host operator API only; cannot succeed without fresh host probe.

    An expected hash/reference is NOT authentication. Authority is operator-owned
    approval/evidence files in the structurally unmounted private state root.
    This API does not create approval, migrate, repair UNKNOWN, or launch work.
    """
    from tools.isolated_trading_case_supervisor import CaseLane
    if not isinstance(lane, CaseLane) or lane.db is None or lane.fd is None or lane.db.in_transaction:
        raise DispositionRejected("locked_lane_required")
    approval, approval_bytes = _artifact(lane.root, "operator-disposition-approval.json", expected_approval_sha256)
    fields = {"schema_version", "case_id", "payload_sha256", "evidence_sha256", "operator_authorization_ref",
              "decision", "scope_sha256", "historical_receipt", "risk_acceptance", "scope", "original_outcome",
              "same_case_retry", "approved_at"}
    if (set(approval) != fields or type(approval["schema_version"]) is not int or approval["schema_version"] != 1
            or approval["decision"] != DECISION or approval["historical_receipt"] != "NOT_CAPTURED"
            or approval["risk_acceptance"] != "ACCEPT_MISSING_HISTORICAL_RECEIPT_NO_ORDER_ONLY"
            or approval["scope"] != "ISOLATED_NO_ORDER_DIAGNOSTICS_ONLY" or approval["original_outcome"] != "UNKNOWN"
            or approval["same_case_retry"] != "FORBIDDEN" or type(approval["approved_at"]) not in {int, float}
            or not math.isfinite(approval["approved_at"]) or not 0 <= time.time() - approval["approved_at"] <= 3600
            or any(not _hash(approval[k]) for k in ("payload_sha256", "evidence_sha256", "scope_sha256"))
            or not isinstance(approval["operator_authorization_ref"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", approval["operator_authorization_ref"]) is None
            or approval["case_id"] != original_registration.case_id):
        raise DispositionRejected("invalid_operator_approval")
    evidence, evidence_bytes = _artifact(lane.root, "operator-disposition-evidence.json", approval["evidence_sha256"])
    if (set(evidence) != {"schema_version", "case_id", "payload_sha256", "historical_start_identity", "historical_cleanup_receipt",
                         "original_claim_sha256", "original_artifact_sha256", "original_deadline_seconds"}
            or type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1
            or evidence["case_id"] != approval["case_id"] or evidence["payload_sha256"] != approval["payload_sha256"]
            or evidence["historical_start_identity"] != "NOT_CAPTURED"
            or evidence["historical_cleanup_receipt"] != "NOT_CAPTURED"
            or not _hash(evidence["original_claim_sha256"]) or not _hash(evidence["original_artifact_sha256"])
            or evidence["original_deadline_seconds"] != original_registration.case_deadline):
        raise DispositionRejected("invalid_operator_evidence")
    _, reconstructed_hash = _validated_registration(original_registration, lane.root)
    if reconstructed_hash != approval["payload_sha256"]:
        raise DispositionRejected("original_registration_mismatch")
    if _original_artifact_hash(original_registration) != evidence["original_artifact_sha256"]:
        raise DispositionRejected("original_artifact_mismatch")
    db = lane.db
    db.execute("BEGIN IMMEDIATE")
    try:
        if validate_lane_schema(db) != 2:
            raise DispositionRejected("explicit_v2_migration_required")
        row = db.execute("SELECT payload_sha256,state,claimed_at FROM cases WHERE case_id=?", (approval["case_id"],)).fetchone()
        if row is None or row[:2] != (approval["payload_sha256"], "UNKNOWN"):
            raise DispositionRejected("only_original_unknown_disposable")
        original = db.execute("SELECT case_id,payload_sha256,state,claimed_at,finished_at,summary FROM cases WHERE case_id=?", (approval["case_id"],)).fetchone()
        if _claim_hash(original) != evidence["original_claim_sha256"]:
            raise DispositionRejected("original_claim_mismatch")
        if not time.time() > row[2] + original_registration.case_deadline:
            raise DispositionRejected("original_window_not_expired")
        # Current absence never reconstructs historical cleanup or completion.
        observation = _host_probe(original_registration, lane.root)
        from tools.isolated_host_quiescence import validate_receipt
        if (not validate_receipt(observation)
                or not 0 <= time.time() - observation["observed_at"] <= 5
                or observation["current_matches"] != 0 or observation["relevant_socket_count"] != 0):
            raise DispositionRejected("current_quiescence_unverified")
        _archive_bytes(lane.root, approval_bytes)
        _archive_bytes(lane.root, evidence_bytes)
        probe_bytes = json.dumps(observation, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        probe_sha256 = _archive_bytes(lane.root, probe_bytes)
        if not 0 <= time.time() - observation["observed_at"] <= 5:
            raise DispositionRejected("current_quiescence_unverified")
        db.execute("INSERT INTO dispositions(case_id,payload_sha256,evidence_sha256,operator_authorization_ref,approval_sha256,scope_sha256,quiescence_json,decision) VALUES(?,?,?,?,?,?,?,?)",
                   (approval["case_id"], approval["payload_sha256"], approval["evidence_sha256"],
                    approval["operator_authorization_ref"], expected_approval_sha256, approval["scope_sha256"],
                    json.dumps({**observation, "probe_sha256": probe_sha256}, sort_keys=True, separators=(",", ":"), allow_nan=False), DECISION))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


_SCOPES = {}


def diagnostic_scope(registration, state_root):
    """Process-local, nonserializable capability for a validated analysis case.

    This is not user authentication: only trusted host registration may call it.
    It cannot be supplied over model RPC or reconstructed from a caller string.
    Current run_case intentionally does not request/use this capability.
    """
    from tools.isolated_trading_case_supervisor import _private_root
    root = _private_root(state_root)
    inputs, payload = _validated_registration(registration, root)
    if registration.mode not in {"VALIDATE_ONLY", "EXECUTE_REGISTERED"}:
        raise DispositionRejected("analysis_only_scope_required")
    pin = {"source_files": inputs["source_files"], "runtime_files": inputs["runtime_files"],
           "script_hash": inputs["script_hash"], "helper_source": json.loads(registration.helper_source_files_json),
           "helper_python": registration.helper_python_sha256, "wrapper": registration.binding.wrapper_sha256}
    scope_hash = hashlib.sha256(json.dumps(pin, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    now = time.monotonic()
    for token in list(_SCOPES):
        if now - _SCOPES[token][-1] > 60:
            del _SCOPES[token]
    if len(_SCOPES) >= 64:
        raise DispositionRejected("scope_capacity")
    token = object()
    _SCOPES[token] = (str(root), registration.case_id, payload, scope_hash, now)
    return token


def scope_hash_for_claim(token, root, case_id, payload):
    """Only CaseLane consults a capability; caller strings/booleans never work."""
    try:
        value = _SCOPES.get(token)
    except TypeError:
        return None
    if value is None or value[:3] != (str(root), case_id, payload) or time.monotonic() - value[-1] > 60:
        return None
    return value[3]
