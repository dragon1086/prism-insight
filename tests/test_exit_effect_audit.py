import json
import sqlite3

from prism_core.exit_effects import ExitEffectStore
from prism_core.positions import account_fingerprint
from tools import audit_exit_effects


ACCOUNT_ID = "vps:secret-account:01"
INTENT_ID = "intent-audit-1"


def _seed(path):
    with sqlite3.connect(path) as connection:
        store = ExitEffectStore(connection)
        store.ensure_schema()
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        store.enqueue_exit_effects(
            intent_id=INTENT_ID,
            market="KR",
            account_id=ACCOUNT_ID,
            symbol="005930",
            source="hardstop",
            payload={
                "version": 1,
                "event_id": INTENT_ID,
                "market": "KR",
                "source": "hardstop",
                "account_id": ACCOUNT_ID,
                "symbol": "005930",
                "message": "private sell message",
            },
        )
        connection.commit()


def test_audit_reports_unresolved_effects_without_mutating_or_leaking(tmp_path):
    db_path = tmp_path / "audit.sqlite"
    _seed(db_path)
    before = db_path.read_bytes()

    report = audit_exit_effects.audit_database(db_path, limit=10)
    serialized = json.dumps(report, sort_keys=True)

    assert report["status"] == "blocked"
    assert report["status_counts"] == {"PENDING": 4}
    assert report["effect_counts"] == {
        "GCP": 1,
        "JOURNAL": 1,
        "REDIS": 1,
        "TELEGRAM": 1,
    }
    assert report["unresolved"][0]["account_ref"] == account_fingerprint(ACCOUNT_ID)
    assert ACCOUNT_ID not in serialized
    assert "private sell message" not in serialized
    assert before == db_path.read_bytes()


def test_audit_redacts_non_type_error_text_and_reports_truncation(tmp_path):
    db_path = tmp_path / "redacted.sqlite"
    _seed(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE exit_effect_outbox SET last_error='token=private-value'"
        )

    report = audit_exit_effects.audit_database(db_path, limit=1)
    serialized = json.dumps(report, sort_keys=True)

    assert report["unresolved_count"] == 4
    assert report["returned_count"] == 1
    assert report["truncated"] is True
    assert report["unresolved"][0]["last_error"] == "REDACTED"
    assert "private-value" not in serialized


def test_missing_outbox_schema_is_unknown(tmp_path):
    db_path = tmp_path / "missing.sqlite"
    with sqlite3.connect(db_path):
        pass

    report = audit_exit_effects.audit_database(db_path)

    assert report == {
        "status": "unknown",
        "error_type": "MissingExitEffectOutbox",
    }


def test_cli_exit_codes_follow_ready_blocked_unknown(tmp_path, capsys):
    blocked_db = tmp_path / "blocked.sqlite"
    _seed(blocked_db)
    ready_db = tmp_path / "ready.sqlite"
    _seed(ready_db)
    with sqlite3.connect(ready_db) as connection:
        store = ExitEffectStore(connection)
        connection.execute("BEGIN IMMEDIATE")
        claimed = store.claim_ready_effects(owner="test", limit=4)
        for effect in claimed:
            remote_id = (
                f"{effect['effect_type'].lower()}-message-1"
                if effect["effect_type"] in {"REDIS", "GCP"}
                else None
            )
            store.mark_delivered(
                effect_id=effect["id"], owner="test", remote_id=remote_id
            )
        connection.commit()
    unknown_db = tmp_path / "unknown.sqlite"
    with sqlite3.connect(unknown_db):
        pass

    assert audit_exit_effects.main(["--db-path", str(blocked_db)]) == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "blocked"

    assert audit_exit_effects.main(["--db-path", str(ready_db)]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["status"] == "ready"
    assert ready["unresolved_count"] == 0

    assert audit_exit_effects.main(["--db-path", str(unknown_db)]) == 2
    unknown = json.loads(capsys.readouterr().out)
    assert unknown["status"] == "unknown"
