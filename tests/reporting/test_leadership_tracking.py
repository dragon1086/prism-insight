import json
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from prism_core.reporting.leadership_tracking import (
    LeadershipChange,
    LeadershipConflictError,
    LeadershipRepository,
    MarketTrackingSnapshot,
    canonical_snapshot_json,
    render_leadership_report,
)
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database


def valid_snapshot_payload(*, market: str = "KR", slot: int = 13) -> dict:
    stage_by_market_slot = {
        ("KR", 1): ("PRIOR_CLOSE_CONTEXT", "CONFIRMED"),
        ("KR", 7): ("PREOPEN_OBSERVATIONS", "PROVISIONAL"),
        ("KR", 13): ("INTRADAY", "PROVISIONAL"),
        ("KR", 19): ("CLOSE", "CONFIRMED"),
        ("US", 1): ("INTRADAY", "PROVISIONAL"),
        ("US", 7): ("CLOSE", "CONFIRMED"),
        ("US", 13): ("POST_CLOSE_EVENTS", "CONFIRMED"),
        ("US", 19): ("PREMARKET_OBSERVATIONS", "PROVISIONAL"),
    }
    stage, confirmation = stage_by_market_slot[(market, slot)]
    symbol = "005930" if market == "KR" else "NVDA"
    currency = "KRW" if market == "KR" else "USD"
    return {
        "schema_version": "market_tracking_v1",
        "run_id": f"{market.lower()}-20260723-{slot:02d}",
        "revision": 0,
        "market": market,
        "kst_slot": slot,
        "stage": stage,
        "confirmation_state": confirmation,
        "as_of": "2026-07-23T04:00:00+00:00",
        "observed_at": "2026-07-23T03:55:00+00:00",
        "available_at": "2026-07-23T03:56:00+00:00",
        "ingested_at": "2026-07-23T04:01:00+00:00",
        "source": {
            "report_path": f"reports/{market.lower()}-{slot:02d}.md",
            "report_sha256": "a" * 64,
            "source_urls": ["https://example.com/evidence"],
            "evidence_refs": ["evidence-market-1"],
        },
        "quality": "FRESH",
        "quality_reasons": [],
        "core_evidence_usable": True,
        "leader_universe_complete": True,
        "market_state": {
            "regime": "MODERATE_BULL",
            "summary": "Broad leadership remains constructive.",
            "evidence_refs": ["evidence-market-1"],
        },
        "events": [
            {
                "event_id": "event-1",
                "event_type": "MACRO",
                "title": "Scheduled release",
                "summary": "A scheduled release remains a tracked risk.",
                "evidence_refs": ["evidence-event-1"],
            }
        ],
        "leaders": [
            {
                "symbol": symbol,
                "name": "Example Leader",
                "relative_strength": {
                    "rs_5d": 91.5,
                    "rs_20d": None,
                    "rs_60d": 88,
                },
                "high_52_week": {"state": "NEAR_HIGH", "distance_pct": 2.5},
                "liquidity": {
                    "currency": currency,
                    "latest_trading_value": 1000000,
                    "average_20d_trading_value": 900000,
                    "latest_volume": 120000,
                },
                "flow": None
                if market == "US"
                else {
                    "unit": "SHARES",
                    "foreign_net": 1000,
                    "institutional_net": None,
                    "retail_net": -1000,
                },
                "momentum": {"state": "ADVANCING", "score": 72.5},
                "peak": {"state": "APPROACHING", "score": 64},
                "strategies": ["SWING_V1", "TREND_V1"],
                "decision_status": "LEADER",
                "evidence_refs": ["evidence-leader-1"],
            }
        ],
    }


@pytest.mark.parametrize(("market", "slot"), [("KR", 13), ("US", 7)])
def test_kr_and_us_snapshots_accept_and_serialize_canonically(
    market: str, slot: int
):
    payload = valid_snapshot_payload(market=market, slot=slot)

    snapshot = MarketTrackingSnapshot.model_validate_json(json.dumps(payload))
    first = canonical_snapshot_json(snapshot)
    second = canonical_snapshot_json(
        MarketTrackingSnapshot.model_validate_json(json.dumps(deepcopy(payload)))
    )

    assert first == second
    assert json.loads(first)["market"] == market
    assert json.loads(first)["schema_version"] == "market_tracking_v1"
    assert snapshot.available_at.tzinfo is not None
    assert snapshot.source.report_sha256 == "a" * 64


def _set_nested(payload: dict, path: tuple[str | int, ...], value: object) -> None:
    target: object = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("schema_version",), "market_tracking_v2", "schema_version"),
        (("market",), "CA", "market"),
        (("kst_slot",), 2, "kst_slot"),
        (("stage",), "CLOSE", "inconsistent"),
        (("available_at",), "2026-07-23T03:56:00", "timezone"),
        (("leaders", 0, "momentum", "score"), float("nan"), "finite"),
        (("leaders", 0, "peak", "state"), "TOPPING", "state"),
        (("leaders", 0, "strategies"), [], "strategy"),
    ],
)
def test_invalid_schema_values_fail_closed(
    path: tuple[str | int, ...], value: object, message: str
):
    payload = valid_snapshot_payload()
    _set_nested(payload, path, value)

    with pytest.raises(ValidationError, match=message):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("market", "slot", "symbol"),
    [("KR", 13, "5930"), ("US", 7, "$AAPL")],
)
def test_market_specific_symbol_formats_fail_closed(
    market: str, slot: int, symbol: str
):
    payload = valid_snapshot_payload(market=market, slot=slot)
    payload["leaders"][0]["symbol"] = symbol

    with pytest.raises(ValidationError, match="KR symbols|US symbols"):
        _snapshot(payload)


def test_future_availability_fails_closed():
    payload = valid_snapshot_payload()
    payload.update(
        {
            "observed_at": "2099-01-01T00:00:00+00:00",
            "available_at": "2099-01-01T00:01:00+00:00",
            "as_of": "2099-01-01T00:02:00+00:00",
            "ingested_at": "2099-01-01T00:03:00+00:00",
        }
    )

    with pytest.raises(ValidationError, match="future"):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


def test_timestamp_ordering_fails_closed():
    payload = valid_snapshot_payload()
    payload["observed_at"] = "2026-07-23T03:57:00+00:00"

    with pytest.raises(ValidationError, match="observed_at"):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


def test_duplicate_symbols_fail_closed_after_normalization():
    payload = valid_snapshot_payload(market="US", slot=7)
    duplicate = deepcopy(payload["leaders"][0])
    duplicate["symbol"] = "nvda"
    payload["leaders"].append(duplicate)

    with pytest.raises(ValidationError, match="unique"):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "mutation",
    [
        {"quality": "STALE", "quality_reasons": ["stale"], "core_evidence_usable": True},
        {"core_evidence_usable": False, "leader_universe_complete": True},
        {"quality": "PARTIAL", "quality_reasons": []},
    ],
)
def test_inconsistent_quality_and_completeness_fail_closed(mutation: dict):
    payload = valid_snapshot_payload()
    payload.update(mutation)

    with pytest.raises(ValidationError, match="quality|complete|usable|reason"):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


def test_duplicate_strategy_labels_fail_closed():
    payload = valid_snapshot_payload()
    payload["leaders"][0]["strategies"] = ["SWING_V1", "SWING_V1"]

    with pytest.raises(ValidationError, match="unique"):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("state", "distance"),
    [("NEW_HIGH", 1), ("UNKNOWN", 1), ("NEAR_HIGH", None)],
)
def test_inconsistent_52_week_high_state_fails_closed(
    state: str, distance: float | None
):
    payload = valid_snapshot_payload()
    payload["leaders"][0]["high_52_week"] = {
        "state": state,
        "distance_pct": distance,
    }

    with pytest.raises(ValidationError, match="52-week-high|distance"):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field", ["order", "quantity", "account_id", "entry_price"])
def test_executable_fields_are_rejected_at_every_schema_boundary(field: str):
    payload = valid_snapshot_payload()
    payload["leaders"][0][field] = "forbidden"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


def _snapshot(payload: dict) -> MarketTrackingSnapshot:
    return MarketTrackingSnapshot.model_validate_json(json.dumps(payload))


def _set_run(
    payload: dict,
    *,
    run_id: str,
    slot: int,
    observed_at: str,
    available_at: str,
    as_of: str,
    ingested_at: str,
) -> dict:
    stage, confirmation = {
        1: ("PRIOR_CLOSE_CONTEXT", "CONFIRMED"),
        7: ("PREOPEN_OBSERVATIONS", "PROVISIONAL"),
        13: ("INTRADAY", "PROVISIONAL"),
        19: ("CLOSE", "CONFIRMED"),
    }[slot]
    payload.update(
        {
            "run_id": run_id,
            "kst_slot": slot,
            "stage": stage,
            "confirmation_state": confirmation,
            "observed_at": observed_at,
            "available_at": available_at,
            "as_of": as_of,
            "ingested_at": ingested_at,
        }
    )
    return payload


def test_exact_reingest_is_idempotent_and_readback_is_deterministic(tmp_path: Path):
    snapshot = _snapshot(valid_snapshot_payload())
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)

        first = repository.ingest(snapshot)
        second = repository.ingest(snapshot)
        stored = repository.read(first.snapshot_id)

        assert first.idempotent is False
        assert second.idempotent is True
        assert second.snapshot_id == first.snapshot_id
        assert stored.snapshot == snapshot
        assert stored.rendered_markdown == first.rendered_markdown
        assert [change.state for change in stored.changes] == [LeadershipChange.NEW]
        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1


def test_reingest_identity_excludes_processing_only_ingested_at(tmp_path: Path):
    first_payload = valid_snapshot_payload()
    replay_payload = deepcopy(first_payload)
    replay_payload["ingested_at"] = "2026-07-23T04:02:00+00:00"

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        first = repository.ingest(_snapshot(first_payload))
        replay = repository.ingest(_snapshot(replay_payload))

        assert replay.idempotent is True
        assert replay.snapshot_id == first.snapshot_id
        assert repository.read(first.snapshot_id).snapshot.ingested_at == _snapshot(
            first_payload
        ).ingested_at
        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1


def test_canonical_identity_normalizes_equivalent_timezones_and_decimal_scales(
    tmp_path: Path,
):
    first_payload = valid_snapshot_payload()
    equivalent_payload = deepcopy(first_payload)
    equivalent_payload.update(
        {
            "observed_at": "2026-07-23T12:55:00+09:00",
            "available_at": "2026-07-23T12:56:00+09:00",
            "as_of": "2026-07-23T13:00:00+09:00",
            "ingested_at": "2026-07-23T13:01:00+09:00",
        }
    )
    equivalent_payload["leaders"][0]["relative_strength"]["rs_5d"] = "91.50"
    equivalent_payload["leaders"][0]["high_52_week"]["distance_pct"] = "2.50"

    first_snapshot = _snapshot(first_payload)
    equivalent_snapshot = _snapshot(equivalent_payload)
    assert canonical_snapshot_json(first_snapshot) == canonical_snapshot_json(
        equivalent_snapshot
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        first = repository.ingest(first_snapshot)
        equivalent = repository.ingest(equivalent_snapshot)

        assert equivalent.idempotent is True
        assert equivalent.snapshot_id == first.snapshot_id


def test_explicit_higher_revision_appends_without_rewriting_prior_evidence(
    tmp_path: Path,
):
    original_payload = valid_snapshot_payload()
    corrected_payload = deepcopy(original_payload)
    corrected_payload["revision"] = 1
    corrected_payload["source"]["report_sha256"] = "b" * 64
    corrected_payload["leaders"][0]["momentum"]["score"] = 80

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        original = repository.ingest(_snapshot(original_payload))
        before = connection.execute(
            "SELECT payload_json FROM observations WHERE snapshot_id = ? ORDER BY observation_id",
            (original.snapshot_id,),
        ).fetchall()

        correction = repository.ingest(_snapshot(corrected_payload))

        assert correction.snapshot_id != original.snapshot_id
        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 4
        assert connection.execute(
            "SELECT payload_json FROM observations WHERE snapshot_id = ? ORDER BY observation_id",
            (original.snapshot_id,),
        ).fetchall() == before


def test_same_run_and_revision_with_different_content_is_rejected_atomically(
    tmp_path: Path,
):
    original = valid_snapshot_payload()
    conflict = deepcopy(original)
    conflict["source"]["report_sha256"] = "b" * 64

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(_snapshot(original))

        with pytest.raises(LeadershipConflictError, match="same run and revision"):
            repository.ingest(_snapshot(conflict))

        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1


def test_mid_transaction_report_failure_rolls_back_every_evidence_row(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        connection.execute(
            "CREATE TRIGGER force_report_failure BEFORE INSERT ON reports "
            "BEGIN SELECT RAISE(ABORT, 'forced report failure'); END"
        )

        with pytest.raises(sqlite3.IntegrityError, match="forced report failure"):
            LeadershipRepository(connection).ingest(
                _snapshot(valid_snapshot_payload())
            )

        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_first_ingest_for_a_run_must_start_at_revision_zero(tmp_path: Path):
    payload = valid_snapshot_payload()
    payload["revision"] = 1

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)

        with pytest.raises(LeadershipConflictError, match="must be zero"):
            LeadershipRepository(connection).ingest(_snapshot(payload))

        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0


def test_prior_market_run_produces_new_maintained_and_exited_changes(tmp_path: Path):
    prior_payload = valid_snapshot_payload()
    current_payload = _set_run(
        valid_snapshot_payload(),
        run_id="kr-20260723-19",
        slot=19,
        observed_at="2026-07-23T09:55:00+00:00",
        available_at="2026-07-23T09:56:00+00:00",
        as_of="2026-07-23T10:00:00+00:00",
        ingested_at="2026-07-23T10:01:00+00:00",
    )
    maintained = deepcopy(current_payload["leaders"][0])
    new_leader = deepcopy(maintained)
    new_leader["symbol"] = "000660"
    new_leader["name"] = "New Leader"
    current_payload["leaders"] = [maintained, new_leader]

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(_snapshot(prior_payload))
        maintained_result = repository.ingest(_snapshot(current_payload))

        assert [(item.symbol, item.state) for item in maintained_result.changes] == [
            ("000660", LeadershipChange.NEW),
            ("005930", LeadershipChange.MAINTAINED),
        ]

        exit_payload = _set_run(
            valid_snapshot_payload(),
            run_id="kr-20260724-07",
            slot=7,
            observed_at="2026-07-23T21:55:00+00:00",
            available_at="2026-07-23T21:56:00+00:00",
            as_of="2026-07-23T22:00:00+00:00",
            ingested_at="2026-07-23T22:01:00+00:00",
        )
        exit_payload["leaders"][0]["symbol"] = "000660"
        exited_result = repository.ingest(_snapshot(exit_payload))

        assert [(item.symbol, item.state) for item in exited_result.changes] == [
            ("000660", LeadershipChange.MAINTAINED),
            ("005930", LeadershipChange.EXITED),
        ]


def test_prior_run_order_uses_available_then_ingested_time(tmp_path: Path):
    first_payload = valid_snapshot_payload()
    second_payload = deepcopy(first_payload)
    second_payload.update(
        {
            "run_id": "kr-same-available-later-ingest",
            "as_of": "2026-07-23T04:02:00+00:00",
            "ingested_at": "2026-07-23T04:02:00+00:00",
        }
    )
    second_payload["leaders"][0]["symbol"] = "000660"
    current_payload = deepcopy(first_payload)
    current_payload.update(
        {
            "run_id": "kr-after-tie",
            "observed_at": "2026-07-23T04:09:00+00:00",
            "available_at": "2026-07-23T04:10:00+00:00",
            "as_of": "2026-07-23T04:11:00+00:00",
            "ingested_at": "2026-07-23T04:12:00+00:00",
        }
    )
    current_payload["leaders"][0]["symbol"] = "000660"

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(_snapshot(first_payload))
        repository.ingest(_snapshot(second_payload))
        result = repository.ingest(_snapshot(current_payload))

        assert [(item.symbol, item.state) for item in result.changes] == [
            ("000660", LeadershipChange.MAINTAINED)
        ]


def test_prior_run_uses_highest_available_revision_of_each_run(tmp_path: Path):
    original_payload = valid_snapshot_payload()
    correction_payload = deepcopy(original_payload)
    correction_payload["revision"] = 1
    correction_payload["source"]["report_sha256"] = "b" * 64
    correction_payload["leaders"][0]["symbol"] = "000660"
    current_payload = deepcopy(original_payload)
    current_payload.update(
        {
            "run_id": "kr-after-corrected-run",
            "observed_at": "2026-07-23T04:09:00+00:00",
            "available_at": "2026-07-23T04:10:00+00:00",
            "as_of": "2026-07-23T04:11:00+00:00",
            "ingested_at": "2026-07-23T04:12:00+00:00",
        }
    )
    current_payload["leaders"][0]["symbol"] = "000660"

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(_snapshot(original_payload))
        repository.ingest(_snapshot(correction_payload))
        result = repository.ingest(_snapshot(current_payload))

        assert [(item.symbol, item.state) for item in result.changes] == [
            ("000660", LeadershipChange.MAINTAINED)
        ]


def test_prior_run_excludes_a_correction_not_available_before_current(tmp_path: Path):
    original_payload = valid_snapshot_payload()
    late_correction_payload = deepcopy(original_payload)
    late_correction_payload.update(
        {
            "revision": 1,
            "observed_at": "2026-07-23T04:19:00+00:00",
            "available_at": "2026-07-23T04:20:00+00:00",
            "as_of": "2026-07-23T04:21:00+00:00",
            "ingested_at": "2026-07-23T04:22:00+00:00",
        }
    )
    late_correction_payload["source"]["report_sha256"] = "b" * 64
    late_correction_payload["leaders"][0]["symbol"] = "000660"
    current_payload = deepcopy(original_payload)
    current_payload.update(
        {
            "run_id": "kr-before-late-correction",
            "observed_at": "2026-07-23T04:09:00+00:00",
            "available_at": "2026-07-23T04:10:00+00:00",
            "as_of": "2026-07-23T04:11:00+00:00",
            "ingested_at": "2026-07-23T04:12:00+00:00",
        }
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(_snapshot(original_payload))
        repository.ingest(_snapshot(late_correction_payload))
        result = repository.ingest(_snapshot(current_payload))

        assert [(item.symbol, item.state) for item in result.changes] == [
            ("005930", LeadershipChange.MAINTAINED)
        ]


def test_insufficient_quality_marks_absence_as_data_missing_not_exited(tmp_path: Path):
    prior_payload = valid_snapshot_payload()
    current_payload = _set_run(
        valid_snapshot_payload(),
        run_id="kr-20260723-19",
        slot=19,
        observed_at="2026-07-23T09:55:00+00:00",
        available_at="2026-07-23T09:56:00+00:00",
        as_of="2026-07-23T10:00:00+00:00",
        ingested_at="2026-07-23T10:01:00+00:00",
    )
    current_payload.update(
        {
            "quality": "STALE",
            "quality_reasons": ["leadership universe was stale"],
            "core_evidence_usable": False,
            "leader_universe_complete": False,
            "leaders": [],
        }
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(_snapshot(prior_payload))
        result = repository.ingest(_snapshot(current_payload))

        assert [(item.symbol, item.state) for item in result.changes] == [
            ("005930", LeadershipChange.DATA_MISSING)
        ]


def test_unusable_current_leaders_are_classified_as_data_missing(tmp_path: Path):
    payload = valid_snapshot_payload()
    payload.update(
        {
            "quality": "PARTIAL",
            "quality_reasons": ["core leadership evidence is incomplete"],
            "core_evidence_usable": False,
            "leader_universe_complete": False,
        }
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        result = LeadershipRepository(connection).ingest(_snapshot(payload))

        assert [(item.symbol, item.state) for item in result.changes] == [
            ("005930", LeadershipChange.DATA_MISSING)
        ]


def test_usable_current_symbol_is_new_when_prior_symbol_set_was_unusable(
    tmp_path: Path,
):
    prior_payload = valid_snapshot_payload()
    prior_payload.update(
        {
            "quality": "PARTIAL",
            "quality_reasons": ["prior core evidence was incomplete"],
            "core_evidence_usable": False,
            "leader_universe_complete": False,
        }
    )
    current_payload = deepcopy(valid_snapshot_payload())
    current_payload.update(
        {
            "run_id": "kr-after-unusable-prior",
            "observed_at": "2026-07-23T04:09:00+00:00",
            "available_at": "2026-07-23T04:10:00+00:00",
            "as_of": "2026-07-23T04:11:00+00:00",
            "ingested_at": "2026-07-23T04:12:00+00:00",
        }
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(_snapshot(prior_payload))
        result = repository.ingest(_snapshot(current_payload))

        assert [(item.symbol, item.state) for item in result.changes] == [
            ("005930", LeadershipChange.NEW)
        ]


def test_future_as_of_or_ingested_time_fails_closed():
    payload = valid_snapshot_payload()
    payload.update(
        {
            "as_of": "2099-01-01T00:02:00+00:00",
            "ingested_at": "2099-01-01T00:03:00+00:00",
        }
    )

    with pytest.raises(ValidationError, match="as_of|ingested_at|future"):
        _snapshot(payload)


def test_persisted_payload_marks_untrusted_report_only_provenance(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        result = LeadershipRepository(connection).ingest(
            _snapshot(valid_snapshot_payload())
        )

        rows = connection.execute(
            "SELECT observation_kind, provider, payload_json FROM observations "
            "WHERE snapshot_id = ? ORDER BY observation_kind",
            (result.snapshot_id,),
        ).fetchall()

        assert {row[0] for row in rows} == {
            "leadership_market_state",
            "leadership_security_state",
        }
        assert {row[1] for row in rows} == {"hermes_agent_report"}
        assert {json.loads(row[2])["policy_disposition"] for row in rows} == {
            "REPORT_ONLY"
        }


@pytest.mark.parametrize(
    "observation_kind", ["leadership_market_state", "leadership_security_state"]
)
def test_database_uniqueness_backs_same_source_revision_conflict(
    tmp_path: Path, observation_kind: str
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        result = LeadershipRepository(connection).ingest(
            _snapshot(valid_snapshot_payload())
        )

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                "INSERT INTO observations "
                "(observation_id, snapshot_id, security_id, observation_kind, provider, "
                "provider_symbol, source_record_id, revision, observed_at, available_at, "
                "ingested_at, payload_json, created_at) "
                "SELECT 'duplicate-observation', snapshot_id, security_id, "
                "observation_kind, provider, provider_symbol, source_record_id, revision, "
                "observed_at, available_at, ingested_at, payload_json, created_at "
                "FROM observations WHERE snapshot_id = ? AND observation_kind = ?",
                (result.snapshot_id, observation_kind),
            )


def test_unusable_security_observation_is_self_describing(tmp_path: Path):
    payload = valid_snapshot_payload()
    payload.update(
        {
            "quality": "PARTIAL",
            "quality_reasons": ["core evidence is incomplete"],
            "core_evidence_usable": False,
            "leader_universe_complete": False,
        }
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        result = LeadershipRepository(connection).ingest(_snapshot(payload))
        row = connection.execute(
            "SELECT payload_json FROM observations WHERE snapshot_id = ? "
            "AND observation_kind = ?",
            (result.snapshot_id, "leadership_security_state"),
        ).fetchone()

        security_payload = json.loads(row[0])
        assert security_payload["quality"] == "PARTIAL"
        assert security_payload["core_evidence_usable"] is False
        assert security_payload["leader_universe_complete"] is False


@pytest.mark.parametrize("table", ["market_snapshots", "observations", "reports"])
def test_leadership_evidence_tables_reject_update_and_delete(
    tmp_path: Path, table: str
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        LeadershipRepository(connection).ingest(_snapshot(valid_snapshot_payload()))

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(f"UPDATE {table} SET created_at = created_at")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize(
    ("market", "slot", "stage", "confirmation"),
    [
        ("KR", 1, "PRIOR_CLOSE_CONTEXT", "CONFIRMED"),
        ("KR", 7, "PREOPEN_OBSERVATIONS", "PROVISIONAL"),
        ("KR", 13, "INTRADAY", "PROVISIONAL"),
        ("KR", 19, "CLOSE", "CONFIRMED"),
        ("US", 1, "INTRADAY", "PROVISIONAL"),
        ("US", 7, "CLOSE", "CONFIRMED"),
        ("US", 13, "POST_CLOSE_EVENTS", "CONFIRMED"),
        ("US", 19, "PREMARKET_OBSERVATIONS", "PROVISIONAL"),
    ],
)
def test_renderer_exposes_each_time_slot_stage_without_cross_market_content(
    market: str, slot: int, stage: str, confirmation: str
):
    snapshot = _snapshot(valid_snapshot_payload(market=market, slot=slot))

    rendered = render_leadership_report(snapshot, ())

    assert f"Market stage: {stage}" in rendered
    assert f"Confirmation: {confirmation}" in rendered
    assert rendered.startswith(f"# {market} ")
    assert ("# US " if market == "KR" else "# KR ") not in rendered


def test_renderer_uses_generic_headings_and_preserves_leadership_metrics():
    payload = valid_snapshot_payload()
    payload["source"]["report_path"] = "StockEasy/Screener/NewHigh.md"
    snapshot = _snapshot(payload)

    rendered = render_leadership_report(snapshot, ())

    assert "## Security Evidence" in rendered
    assert "RS 5D: 91.5" in rendered
    assert "52-week high: NEAR_HIGH (2.5%)" in rendered
    assert "Liquidity latest: 1000000 KRW" in rendered
    assert "Momentum: ADVANCING (72.5)" in rendered
    assert "Peak: APPROACHING (64)" in rendered
    assert "SWING_V1, TREND_V1" in rendered
    assert "Decision: LEADER" in rendered
    for prohibited in ("StockEasy", "Screener", "NewHigh"):
        assert prohibited not in rendered


def test_renderer_visibly_fails_closed_when_core_evidence_is_unusable():
    payload = valid_snapshot_payload()
    payload.update(
        {
            "quality": "CONFLICT",
            "quality_reasons": ["core sources conflict"],
            "core_evidence_usable": False,
            "leader_universe_complete": False,
        }
    )
    snapshot = _snapshot(payload)

    rendered = render_leadership_report(snapshot, ())

    assert "FAIL-CLOSED" in rendered
    assert "core sources conflict" in rendered
    assert "No usable current leadership rows" in rendered
    assert "RS 5D" not in rendered
    for prohibited in ("entry price", "entry level", "target price", "stop price"):
        assert prohibited not in rendered.lower()


def test_renderer_keeps_security_detail_for_partial_but_usable_core_evidence():
    payload = valid_snapshot_payload()
    payload.update(
        {
            "quality": "PARTIAL",
            "quality_reasons": ["a non-core event source is missing"],
            "core_evidence_usable": True,
            "leader_universe_complete": True,
        }
    )

    rendered = render_leadership_report(_snapshot(payload), ())

    assert "Quality: PARTIAL" in rendered
    assert "RS 5D: 91.5" in rendered
    assert "FAIL-CLOSED" not in rendered


def test_cli_ingests_only_the_explicit_database_and_prints_canonical_summary(
    tmp_path: Path,
):
    root = Path(__file__).parents[2]
    input_path = tmp_path / "snapshot.json"
    input_path.write_text(json.dumps(valid_snapshot_payload()), encoding="utf-8")
    database_path = tmp_path / "approved-research.sqlite"

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "ingest_market_tracking_snapshot.py"),
            "--db",
            str(database_path),
            "--input",
            str(input_path),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["schema_version"] == "market_tracking_v1"
    assert summary["market"] == "KR"
    assert summary["idempotent"] is False
    assert summary["changes"] == [{"state": "NEW", "symbol": "005930"}]
    assert database_path.exists()
    assert not (tmp_path / "stock_tracking_db.sqlite").exists()


def test_cli_accepts_stdin_and_can_print_the_persisted_report(tmp_path: Path):
    root = Path(__file__).parents[2]
    database_path = tmp_path / "research.sqlite"

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "ingest_market_tracking_snapshot.py"),
            "--db",
            str(database_path),
            "--input",
            "-",
            "--output",
            "report",
        ],
        cwd=tmp_path,
        input=json.dumps(valid_snapshot_payload(market="US", slot=7)),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("# US Leadership Evidence")
    assert "Confirmation: CONFIRMED" in completed.stdout
    assert not (tmp_path / "stock_tracking_db.sqlite").exists()
