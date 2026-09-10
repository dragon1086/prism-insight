import sqlite3
import hashlib
import json
from types import SimpleNamespace

import pytest

from tools.isolated_trading_case_supervisor import CaseLane, CaseRejected
from tools.isolated_case_disposition import migrate_v1_to_v2
from tools import isolated_case_disposition as disposition
from tools import isolated_trading_case_supervisor as supervisor


def unknown(root):
    with CaseLane(root) as lane:
        lane.claim("original", "a" * 64)
        lane.finish("original", "a" * 64, {"status": "UNKNOWN"}, cleanup_confirmed=False)


def test_opening_lane_never_migrates_or_releases(tmp_path):
    unknown(tmp_path)
    with CaseLane(tmp_path) as lane:
        assert lane.db.execute("SELECT version FROM metadata").fetchall() == [(1,)]
        with pytest.raises(CaseRejected, match="lane_uncertain"):
            lane.claim("fresh", "b" * 64)
    with sqlite3.connect(tmp_path / "case-claims.sqlite") as db:
        assert db.execute("SELECT version FROM metadata").fetchall() == [(1,)]


def test_explicit_migration_preserves_unknown_and_blocks_claims(tmp_path):
    unknown(tmp_path)
    with CaseLane(tmp_path) as lane:
        before = lane.db.execute("SELECT * FROM cases").fetchall()
        migrate_v1_to_v2(lane)
        assert lane.db.execute("SELECT version FROM metadata").fetchall() == [(2,)]
        assert lane.db.execute("SELECT * FROM cases").fetchall() == before
        assert lane.db.execute("SELECT * FROM dispositions").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            lane.db.execute("UPDATE cases SET state='FINISHED' WHERE case_id='original'")
    with CaseLane(tmp_path) as lane:
        with pytest.raises(CaseRejected, match="lane_uncertain"):
            lane.claim("fresh", "b" * 64)


def test_migration_failure_rolls_back_every_schema_change(tmp_path):
    unknown(tmp_path)
    with CaseLane(tmp_path) as lane:
        def authorize(action, arg1, arg2, db, source):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK
        lane.db.set_authorizer(authorize)
        with pytest.raises(Exception):
            migrate_v1_to_v2(lane)
        lane.db.set_authorizer(None)
        assert lane.db.execute("SELECT version FROM metadata").fetchall() == [(1,)]
        assert lane.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("metadata",), ("cases",)]


def fixture_approval(root, monkeypatch, *, probe=True):
    monkeypatch.setattr(disposition.time, "time", lambda: 2000)
    unknown(root)
    monkeypatch.setattr(disposition.time, "time", lambda: 3000)
    reg = SimpleNamespace(case_id="original", case_deadline=5, mode="EXECUTE_REGISTERED",
                          helper_source_files_json="{}", helper_python_sha256="c" * 64,
                          binding=SimpleNamespace(wrapper_sha256="d" * 64))
    inputs = {"source_files": {}, "runtime_files": {}, "script_hash": "e" * 64}
    monkeypatch.setattr(supervisor, "_registration", lambda registration, state: (inputs, "a" * 64 if registration.case_id == "original" else "b" * 64))
    monkeypatch.setattr(disposition, "_validated_registration", lambda *args: supervisor._registration(*args))
    monkeypatch.setattr(disposition, "_original_artifact_hash", lambda reg: "f" * 64)
    pin = {**inputs, "helper_source": {}, "helper_python": "c" * 64, "wrapper": "d" * 64}
    scope_hash = hashlib.sha256(json.dumps(pin, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with sqlite3.connect(root / "case-claims.sqlite") as db:
        row = db.execute("SELECT case_id,payload_sha256,state,claimed_at,finished_at,summary FROM cases").fetchone()
    evidence = {"schema_version": 1, "case_id": "original", "payload_sha256": "a" * 64,
                "historical_start_identity": "NOT_CAPTURED", "historical_cleanup_receipt": "NOT_CAPTURED",
                "original_claim_sha256": disposition._claim_hash(row), "original_artifact_sha256": "f" * 64,
                "original_deadline_seconds": 5}
    def artifact(filename, value):
        data = json.dumps(value).encode()
        path = root / filename
        path.write_bytes(data)
        path.chmod(0o600)
        return hashlib.sha256(data).hexdigest()
    evidence_hash = artifact("operator-disposition-evidence.json", evidence)
    approval = {"schema_version": 1, "case_id": "original", "payload_sha256": "a" * 64,
                "evidence_sha256": evidence_hash, "operator_authorization_ref": "fixture-not-real-authorization",
                "decision": disposition.DECISION, "scope_sha256": scope_hash,
                "historical_receipt": "NOT_CAPTURED", "risk_acceptance": "ACCEPT_MISSING_HISTORICAL_RECEIPT_NO_ORDER_ONLY",
                "scope": "ISOLATED_NO_ORDER_DIAGNOSTICS_ONLY", "original_outcome": "UNKNOWN",
                "same_case_retry": "FORBIDDEN", "approved_at": 3000}
    approval_hash = artifact("operator-disposition-approval.json", approval)
    if probe:
        # Unit-only stub. The production dependency is intentionally unavailable.
        monkeypatch.setattr(disposition, "_host_probe", lambda *_: {
            "observed_at": 3000, "current_matches": 0, "historical_start_identity": "NOT_CAPTURED"})
    return reg, approval_hash


def test_disposition_preserves_unknown_and_only_allows_distinct_scoped_case(tmp_path, monkeypatch):
    reg, approval_hash = fixture_approval(tmp_path, monkeypatch)
    with CaseLane(tmp_path) as lane:
        migrate_v1_to_v2(lane)
        before = lane.db.execute("SELECT * FROM cases").fetchall()
        disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        assert lane.db.execute("SELECT * FROM cases").fetchall() == before
        for payload in ("a" * 64, "b" * 64):
            with pytest.raises(CaseRejected, match="original_case_permanently_forbidden"):
                lane.claim("original", payload)
        for forged in (None, True, disposition.DECISION, {}, object()):
            with pytest.raises(CaseRejected, match="lane_uncertain"):
                lane.claim("fresh", "b" * 64, diagnostic_scope=forged)
        reg.case_id = "fresh"
        token = disposition.diagnostic_scope(reg, tmp_path)
        assert lane.claim("fresh", "b" * 64, diagnostic_scope=token) is None
        assert lane.db.execute("SELECT state FROM cases WHERE case_id='original'").fetchone() == ("UNKNOWN",)
        with pytest.raises(sqlite3.IntegrityError):
            lane.db.execute("DELETE FROM dispositions")


def test_missing_reviewed_probe_cannot_record_disposition(tmp_path, monkeypatch):
    reg, approval_hash = fixture_approval(tmp_path, monkeypatch, probe=False)
    with CaseLane(tmp_path) as lane:
        migrate_v1_to_v2(lane)
        with pytest.raises(disposition.DispositionRejected, match="reviewed_host_probe_unavailable"):
            disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        assert lane.db.execute("SELECT * FROM dispositions").fetchall() == []


@pytest.mark.parametrize("failure", ["hash", "claimed", "window", "reconstruction", "busy", "stale"])
def test_disposition_requires_all_host_evidence(tmp_path, monkeypatch, failure):
    reg, approval_hash = fixture_approval(tmp_path, monkeypatch)
    with CaseLane(tmp_path) as lane:
        if failure == "claimed":
            # Pre-migration fixture, never an operator recovery behavior.
            lane.db.execute("UPDATE cases SET state='CLAIMED'")
        migrate_v1_to_v2(lane)
        if failure == "hash":
            approval_hash = "f" * 64
        elif failure == "window":
            reg.case_deadline = 1500
        elif failure == "reconstruction":
            monkeypatch.setattr(supervisor, "_registration", lambda *_: ({}, "b" * 64))
        elif failure in {"busy", "stale"}:
            monkeypatch.setattr(disposition, "_host_probe", lambda *_: {
                "observed_at": 2000 if failure == "stale" else 3000,
                "current_matches": 1 if failure == "busy" else 0, "historical_start_identity": "NOT_CAPTURED"})
        with pytest.raises(disposition.DispositionRejected):
            disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        assert lane.db.execute("SELECT * FROM dispositions").fetchall() == []


def test_scope_wrong_case_payload_pin_or_live_mode_is_rejected(tmp_path, monkeypatch):
    reg, approval_hash = fixture_approval(tmp_path, monkeypatch)
    with CaseLane(tmp_path) as lane:
        migrate_v1_to_v2(lane)
        disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        reg.case_id = "fresh"
        token = disposition.diagnostic_scope(reg, tmp_path)
        for case, payload in (("different", "b" * 64), ("fresh", "c" * 64)):
            with pytest.raises(CaseRejected):
                lane.claim(case, payload, diagnostic_scope=token)
        reg.binding.wrapper_sha256 = "f" * 64
        wrong_pin = disposition.diagnostic_scope(reg, tmp_path)
        with pytest.raises(CaseRejected):
            lane.claim("fresh", "b" * 64, diagnostic_scope=wrong_pin)
        reg.mode = "LIVE"
        with pytest.raises(disposition.DispositionRejected):
            disposition.diagnostic_scope(reg, tmp_path)


def test_duplicate_disposition_is_not_update_or_second_authorization(tmp_path, monkeypatch):
    reg, approval_hash = fixture_approval(tmp_path, monkeypatch)
    with CaseLane(tmp_path) as lane:
        migrate_v1_to_v2(lane)
        disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        before = lane.db.execute("SELECT * FROM dispositions").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        assert lane.db.execute("SELECT * FROM dispositions").fetchall() == before


@pytest.mark.parametrize("bad", ["changed", "symlink", "hardlink", "scope"])
def test_replaced_or_unsafe_approval_is_rejected(tmp_path, monkeypatch, bad):
    import os
    reg, approval_hash = fixture_approval(tmp_path, monkeypatch)
    path = tmp_path / "operator-disposition-approval.json"
    if bad == "changed":
        path.write_text("{}")
    elif bad == "symlink":
        other = tmp_path / "other.json"
        path.rename(other)
        path.symlink_to(other)
    elif bad == "hardlink":
        os.link(path, tmp_path / "other.json")
    else:
        payload = json.loads(path.read_text())
        payload["scope"] = "LIVE"
        data = json.dumps(payload).encode()
        path.write_bytes(data)
        approval_hash = hashlib.sha256(data).hexdigest()
    with CaseLane(tmp_path) as lane:
        migrate_v1_to_v2(lane)
        with pytest.raises(disposition.DispositionRejected):
            disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        assert lane.db.execute("SELECT * FROM dispositions").fetchall() == []


def test_missing_history_disposition_cannot_release_new_claimed_case(tmp_path, monkeypatch):
    reg, approval_hash = fixture_approval(tmp_path, monkeypatch)
    with CaseLane(tmp_path) as lane:
        migrate_v1_to_v2(lane)
        disposition.record_operator_disposition(lane, expected_approval_sha256=approval_hash, original_registration=reg)
        reg.case_id = "fresh"
        lane.claim("fresh", "b" * 64, diagnostic_scope=disposition.diagnostic_scope(reg, tmp_path))
    with CaseLane(tmp_path) as lane:
        reg.case_id = "third"
        with pytest.raises(CaseRejected, match="lane_uncertain"):
            lane.claim("third", "b" * 64, diagnostic_scope=disposition.diagnostic_scope(reg, tmp_path))


@pytest.mark.parametrize("addition", ["view", "index", "default"])
def test_malformed_v1_never_migrated_or_adopted(tmp_path, addition):
    unknown(tmp_path)
    with CaseLane(tmp_path) as lane:
        if addition == "view":
            lane.db.execute("CREATE VIEW unexpected AS SELECT * FROM cases")
        elif addition == "index":
            lane.db.execute("CREATE INDEX unexpected ON cases(state)")
        else:
            lane.db.execute("DROP TABLE cases")
            lane.db.execute("CREATE TABLE cases(case_id TEXT PRIMARY KEY,payload_sha256 TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'FINISHED',claimed_at REAL NOT NULL,finished_at REAL,summary TEXT)")
        with pytest.raises(disposition.DispositionRejected, match="unknown_claim_database"):
            migrate_v1_to_v2(lane)
        assert lane.db.execute("SELECT version FROM metadata").fetchall() == [(1,)]
        assert lane.db.execute("SELECT name FROM sqlite_master WHERE name='dispositions'").fetchall() == []
    with pytest.raises(CaseRejected, match="unknown_claim_database"):
        with CaseLane(tmp_path):
            pass
