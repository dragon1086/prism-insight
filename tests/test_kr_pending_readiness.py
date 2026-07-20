"""Read-only activation preflight tests for issue #412 Phase 4-b2b-3."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from prism_core.order_intents import IntentStore, OrderIntent
from prism_core.exit_effects import ExitEffectStore
from prism_core.positions import PositionStore, account_fingerprint
from tools import check_kr_pending_readiness as readiness
from tracking.db_schema import TABLE_STOCK_HOLDINGS


ACCOUNT = "vps:12345678:01"


def _seed_clean_db(path: Path, *, rows: int = 1) -> None:
    IntentStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute(TABLE_STOCK_HOLDINGS)
        position_store = PositionStore(connection)
        position_store.ensure_schema()
        ExitEffectStore(connection).ensure_schema()
        for index in range(rows):
            holding_id = connection.execute(
                """INSERT INTO stock_holdings (
                       account_key, account_name, ticker, company_name,
                       buy_price, buy_date
                   ) VALUES (?, 'primary', '005930', 'Samsung', ?, '2026-07-19')""",
                (ACCOUNT, 70000 - index * 1000),
            ).lastrowid
            assert position_store.open_legacy_position(
                market="KR",
                legacy_holding_id=holding_id,
                account_id=ACCOUNT,
                account_name="primary",
                symbol="005930",
                entry_price=70000 - index * 1000,
                opened_at="2026-07-19",
            )


def _kis_ok(*orders: dict) -> dict[str, dict]:
    return {ACCOUNT: {"authoritative": True, "orders": list(orders)}}


def _accepted_sell(path: Path, *, order_no: str | None = "KR-1") -> None:
    intent_store = IntentStore(path)
    intent = OrderIntent.create(
        market="KR",
        account_id=ACCOUNT,
        symbol="005930",
        side="SELL",
        order_style="market",
        source="test",
        source_position_id="legacy:KR:1",
        quantity=1,
    )
    created, _ = intent_store.reserve(intent)
    assert created
    intent_store.mark_submitting(intent.id)
    response = {"success": True, "quantity": 1}
    if order_no is not None:
        response["order_no"] = order_no
    intent_store.record_result(
        intent,
        status="SUBMITTED",
        accepted=True,
        response=response,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE positions SET exit_intent_id=? WHERE id='legacy:KR:1'",
            (intent.id,),
        )


def test_clean_readiness_is_ready_and_does_not_mutate_database(tmp_path):
    db_path = tmp_path / "clean.sqlite"
    _seed_clean_db(db_path)
    before = db_path.read_bytes()

    report = readiness.audit_database(db_path, _kis_ok())

    assert report["status"] == "ready"
    assert report["blockers"] == []
    assert report["position_status_counts"] == {
        "PENDING_ENTRY": 0,
        "PENDING_EXIT": 0,
        "EXIT_UNKNOWN": 0,
        "ENTRY_FAILED": 0,
    }
    assert report["comparator"]["matches"] is True
    assert report["pyramided_holdings"] == []
    assert report["exit_effect_outbox"] == {
        "status_counts": {},
        "effect_counts": {},
        "unresolved_count": 0,
        "unresolved": [],
    }
    assert report["kis_inquiries"] == [
        {
            "account_ref": account_fingerprint(ACCOUNT),
            "authoritative": True,
            "open_order_count": 0,
            "error_type": None,
        }
    ]
    assert before == db_path.read_bytes()


def test_missing_exit_effect_outbox_schema_is_unknown(tmp_path):
    db_path = tmp_path / "missing-outbox.sqlite"
    _seed_clean_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE exit_effect_outbox")

    report = readiness.audit_database(db_path, _kis_ok())

    assert report["status"] == "unknown"
    assert report["exit_effect_outbox"] is None
    assert "exit_effect_outbox_schema" in report["unknowns"]


@pytest.mark.parametrize("status", ["PENDING", "IN_PROGRESS", "DEAD"])
def test_each_unresolved_exit_effect_status_blocks_with_redacted_account(
    tmp_path, status
):
    db_path = tmp_path / f"outbox-{status.lower()}.sqlite"
    _seed_clean_db(db_path)
    intent_id = f"intent-outbox-{status.lower()}"
    with sqlite3.connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        ExitEffectStore(connection).enqueue_exit_effects(
            intent_id=intent_id,
            market="KR",
            account_id=ACCOUNT,
            symbol="005930",
            source="test",
            payload={
                "event_id": intent_id,
                "message": "sensitive sell detail",
            },
        )
        connection.execute(
            "UPDATE exit_effect_outbox SET status=? WHERE effect_type='REDIS'",
            (status,),
        )
        connection.execute(
            "UPDATE exit_effect_outbox SET status='DELIVERED' "
            "WHERE effect_type!='REDIS'"
        )
        connection.commit()

    report = readiness.audit_database(db_path, _kis_ok())
    serialized = json.dumps(report, sort_keys=True)

    assert report["status"] == "blocked"
    assert "unresolved_exit_effects" in report["blockers"]
    assert report["exit_effect_outbox"]["unresolved_count"] == 1
    assert report["exit_effect_outbox"]["unresolved"][0]["account_ref"] == (
        account_fingerprint(ACCOUNT)
    )
    assert ACCOUNT not in serialized
    assert "sensitive sell detail" not in serialized


@pytest.mark.parametrize(
    "status",
    ["PENDING_ENTRY", "PENDING_EXIT", "EXIT_UNKNOWN", "ENTRY_FAILED"],
)
def test_each_unresolved_position_state_is_a_blocker(tmp_path, status):
    db_path = tmp_path / f"{status.lower()}.sqlite"
    _seed_clean_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE positions SET status=? WHERE id='legacy:KR:1'",
            (status,),
        )

    report = readiness.audit_database(db_path, _kis_ok())

    assert report["status"] == "blocked"
    assert report["position_status_counts"][status] == 1
    assert "blocking_position_states" in report["blockers"]


def test_pyramiding_is_a_blocker_with_masked_account(tmp_path):
    db_path = tmp_path / "blocked.sqlite"
    _seed_clean_db(db_path, rows=2)

    report = readiness.audit_database(db_path, _kis_ok())

    assert report["status"] == "blocked"
    assert report["pyramided_holdings"] == [
        {
            "account_ref": account_fingerprint(ACCOUNT),
            "symbol": "005930",
            "row_count": 2,
        }
    ]
    serialized = json.dumps(report, sort_keys=True)
    assert ACCOUNT not in serialized


def test_failed_exit_linked_open_position_is_an_explicit_blocker(tmp_path):
    db_path = tmp_path / "failed-exit.sqlite"
    _seed_clean_db(db_path)
    _accepted_sell(db_path, order_no="KR-FAILED")
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE order_intents SET status='FAILED' WHERE side='SELL'")

    report = readiness.audit_database(db_path, _kis_ok())

    assert report["comparator"]["counts"]["failed_exit_linked_open_positions"] == 1
    assert "failed_exit_linked_open_positions" in report["blockers"]


def test_missing_position_mirror_blocks_on_comparator_mismatch(tmp_path):
    db_path = tmp_path / "comparator.sqlite"
    _seed_clean_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM positions")

    report = readiness.audit_database(db_path, _kis_ok())

    assert report["comparator"]["matches"] is False
    assert "legacy_position_comparator_mismatch" in report["blockers"]


def test_accepted_sell_open_order_linkage_is_reported_without_state_change(tmp_path):
    db_path = tmp_path / "open.sqlite"
    _seed_clean_db(db_path)
    _accepted_sell(db_path, order_no="KR-1")
    before = db_path.read_bytes()

    report = readiness.audit_database(
        db_path,
        _kis_ok(
            {
                "order_no": "KR-1",
                "ticker": "005930",
                "side": "SELL",
                "unfilled_qty": 1,
            }
        ),
    )

    assert report["status"] == "blocked"
    assert report["broker_order_audit"]["matched"] == 1
    assert report["broker_order_audit"]["accepted_without_broker_order_id"] == []
    assert report["broker_order_audit"]["accepted_not_currently_open"] == []
    assert report["broker_order_audit"]["open_sell_without_accepted_ledger"] == []
    assert report["broker_order_audit"]["current_open_sell_count"] == 1
    assert before == db_path.read_bytes()


def test_broker_order_missing_id_and_bidirectional_mismatch_block(tmp_path):
    db_path = tmp_path / "mismatch.sqlite"
    _seed_clean_db(db_path)
    _accepted_sell(db_path, order_no=None)

    report = readiness.audit_database(
        db_path,
        _kis_ok(
            {
                "order_no": "UNTRACKED-1",
                "ticker": "005930",
                "side": "SELL",
                "unfilled_qty": 1,
            }
        ),
    )

    broker = report["broker_order_audit"]
    assert report["status"] == "blocked"
    assert len(broker["accepted_without_broker_order_id"]) == 1
    assert len(broker["open_sell_without_accepted_ledger"]) == 1


def test_same_order_number_with_different_symbol_is_not_a_match(tmp_path):
    db_path = tmp_path / "symbol-mismatch.sqlite"
    _seed_clean_db(db_path)
    _accepted_sell(db_path, order_no="KR-1")

    report = readiness.audit_database(
        db_path,
        _kis_ok(
            {
                "order_no": "KR-1",
                "ticker": "000660",
                "side": "SELL",
                "unfilled_qty": 1,
            }
        ),
    )

    broker = report["broker_order_audit"]
    assert broker["matched"] == 0
    assert len(broker["accepted_not_currently_open"]) == 1
    assert len(broker["open_sell_without_accepted_ledger"]) == 1


def test_non_authoritative_kis_result_is_unknown(tmp_path):
    db_path = tmp_path / "unknown.sqlite"
    _seed_clean_db(db_path)

    report = readiness.audit_database(
        db_path,
        {ACCOUNT: {"authoritative": False, "orders": [], "error_type": "TimeoutError"}},
    )

    assert report["status"] == "unknown"
    assert report["unknowns"] == ["kis_open_orders"]
    assert report["broker_order_audit"] is None
    assert report["kis_inquiries"] == [
        {
            "account_ref": account_fingerprint(ACCOUNT),
            "authoritative": False,
            "open_order_count": 0,
            "error_type": "TimeoutError",
        }
    ]
    assert ACCOUNT not in json.dumps(report, sort_keys=True)


def test_gate_sources_check_process_dotenv_and_every_active_cron_line():
    cron = "\n".join(
        [
            "# * * * * POSITION_PENDING_KR_ENABLED=true ignored.py",
            "* * * * * POSITION_PENDING_KR_ENABLED=false first.py",
            "* * * * * POSITION_PENDING_KR_ENABLED=true second.py",
        ]
    )

    report = readiness.evaluate_gate_sources(
        process_value="false",
        dotenv_value="false",
        crontab_text=cron,
    )

    assert report["status"] == "blocked"
    assert report["cron_values"] == ["false", "true"]


def test_invalid_gate_value_is_blocked_not_silently_off():
    report = readiness.evaluate_gate_sources(
        process_value=None,
        dotenv_value="maybe",
        crontab_text="",
    )

    assert report["status"] == "blocked"
    assert report["invalid_values"] == [{"source": "dotenv", "value": "maybe"}]


def test_combine_reports_prioritizes_unknown_over_blocked():
    combined = readiness.combine_reports(
        {"status": "blocked", "blockers": ["position_pending_kr_gate"]},
        {"status": "unknown", "unknowns": ["kis_open_orders"]},
    )

    assert combined["status"] == "unknown"
    assert readiness.exit_code(combined) == 2
    assert readiness.exit_code({"status": "blocked"}) == 1
    assert readiness.exit_code({"status": "ready"}) == 0


def test_cli_emits_json_and_ready_exit_code(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli.sqlite"
    _seed_clean_db(db_path)
    monkeypatch.delenv(readiness.GATE, raising=False)
    monkeypatch.setattr(
        readiness,
        "_read_dotenv_gate",
        lambda _path: (True, "false", None),
    )
    monkeypatch.setattr(
        readiness,
        "_read_crontab",
        lambda: (True, "", None),
    )

    async def fake_inquiry():
        return _kis_ok()

    monkeypatch.setattr(readiness, "inquire_kis_open_sells", fake_inquiry)

    result = readiness.main(["--db-path", str(db_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["status"] == "ready"
    assert payload["gate"]["status"] == "ready"
    assert payload["database"]["status"] == "ready"
