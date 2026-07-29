from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from prism_app.dashboard_export import (
    DashboardExporter,
    export_dashboard,
    open_read_only_database,
)
from prism_core.feedback.repository import canonical_json
from prism_core.llm.trade_plan import (
    MissingDataStatus,
    MissingOrStaleData,
    ProposedDecision,
    TradePlanProposal,
)
from prism_core.storage.migrations import DatabaseKind, migrate_database
from tests.llm.test_trade_plan_schema import valid_proposal_payload


AS_OF = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _research_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE market_snapshots (
            snapshot_id TEXT PRIMARY KEY, market TEXT, as_of_date TEXT,
            content_hash TEXT, quality TEXT, created_at TEXT
        );
        CREATE TABLE observations (
            observation_id TEXT PRIMARY KEY, snapshot_id TEXT, security_id TEXT,
            observation_kind TEXT, provider TEXT, provider_symbol TEXT,
            source_record_id TEXT, revision INTEGER, observed_at TEXT,
            available_at TEXT, ingested_at TEXT, payload_json TEXT, created_at TEXT
        );
        CREATE TABLE decision_snapshots (
            decision_snapshot_id TEXT PRIMARY KEY, feedback_run_id TEXT,
            strategy_id TEXT, strategy_version TEXT, market TEXT, security_id TEXT,
            data_snapshot_id TEXT, feature_snapshot_id TEXT, feature_version TEXT,
            quant_score_id TEXT, quant_score_version TEXT, evidence_refs_json TEXT,
            snapshot_json TEXT, data_quality TEXT, quality_disposition TEXT,
            observed_at TEXT, available_at TEXT, ingested_at TEXT, as_of_at TEXT,
            content_hash TEXT
        );
        CREATE TABLE trade_plan_proposals (
            proposal_record_id TEXT PRIMARY KEY, proposal_key TEXT, proposal_id TEXT,
            revision INTEGER, decision_snapshot_id TEXT, strategy_id TEXT,
            strategy_version TEXT, parse_status TEXT, validation_status TEXT,
            proposed_decision TEXT, raw_output_ref TEXT, raw_output TEXT,
            normalized_proposal_json TEXT, model_provider TEXT, model_id TEXT,
            model_version TEXT, prompt_version TEXT, sampling_version TEXT,
            sampling_json TEXT, validator_version TEXT, policy_version TEXT,
            observed_at TEXT, available_at TEXT, ingested_at TEXT, as_of_at TEXT,
            content_hash TEXT
        );
        CREATE TABLE proposal_disposition_events (
            disposition_event_id TEXT PRIMARY KEY, proposal_record_id TEXT,
            strategy_id TEXT, strategy_version TEXT, sequence_no INTEGER,
            field_path TEXT, action TEXT, reason TEXT, proposed_value_json TEXT,
            resolved_value_json TEXT, evidence_refs_json TEXT,
            validator_version TEXT, policy_version TEXT, observed_at TEXT,
            available_at TEXT, ingested_at TEXT, as_of_at TEXT, content_hash TEXT
        );
        CREATE TABLE proposal_outcomes (
            outcome_event_id TEXT PRIMARY KEY, proposal_record_id TEXT,
            strategy_id TEXT, strategy_version TEXT, horizon_sessions INTEGER,
            revision INTEGER, outcome_state TEXT, quality TEXT, outcome_json TEXT,
            observed_at TEXT, available_at TEXT, ingested_at TEXT, as_of_at TEXT,
            content_hash TEXT, config_version TEXT, code_version TEXT,
            schema_version TEXT
        );
        CREATE TABLE lesson_candidates (
            lesson_candidate_event_id TEXT PRIMARY KEY, lesson_id TEXT,
            strategy_id TEXT, strategy_version TEXT, revision INTEGER, status TEXT,
            candidate_json TEXT, observed_at TEXT, available_at TEXT,
            ingested_at TEXT, as_of_at TEXT, content_hash TEXT
        );
        """
    )
    timing = "2026-07-26T10:00:00+00:00"
    connection.execute(
        "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        ("snap-kr", "KR", timing, "hash", "FRESH", timing),
    )
    old_timing = "2026-07-25T10:00:00+00:00"
    connection.execute(
        "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        ("snap-kr-old", "KR", old_timing, "old-hash", "FRESH", old_timing),
    )
    leader = {
        "symbol": "005930",
        "name": "Samsung Electronics",
        "decision_status": "LEADER",
        "strategies": ["SWING_V1"],
        "relative_strength": {"rs_20d": "91"},
        "high_52_week": {"state": "NEAR_HIGH", "distance_pct": "2.1"},
        "momentum": {"state": "ADVANCING", "score": "80"},
        "peak": {"state": "NOT_PEAKED", "score": "20"},
        "evidence_refs": ["e-leader"],
    }
    connection.execute(
        "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "obs-leader", "snap-kr", "security-kr", "leadership_security_state",
            "hermes_agent_report", "005930", "leader:kr", 0, timing, timing,
            timing, json.dumps(leader), timing,
        ),
    )
    old_leader = dict(leader, symbol="000660", name="SK Hynix")
    connection.execute(
        "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "obs-leader-old", "snap-kr-old", "security-old",
            "leadership_security_state", "hermes_agent_report", "000660",
            "leader:kr:old", 0, old_timing, old_timing, old_timing,
            json.dumps(old_leader), old_timing,
        ),
    )
    for strategy, suffix in (("SWING_V1", "swing"), ("TREND_V1", "trend")):
        snapshot_id = f"decision-{suffix}"
        connection.execute(
            "INSERT INTO decision_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot_id, f"run-{suffix}", strategy, "1.0.0", "KR",
                "security-kr", "snap-kr", f"feature-{suffix}", "v1",
                f"score-{suffix}", f"SHADOW_SCORE_V1.{strategy}",
                json.dumps([f"e-{suffix}"]),
                json.dumps(
                    {
                        "quant_score": {
                            "total_score": "67.500000",
                            "components": {f"{suffix}_component": "67.500000"},
                        }
                    }
                ),
                "FRESH", "ACCEPT", timing, timing, timing, timing, f"h-{suffix}",
            ),
        )
        proposal = {
            "proposal_id": f"proposal-{suffix}",
            "decision": "WATCH",
            "regime": {
                "probabilities": {"sideways": "1"},
                "confidence": "0.7",
                "drivers": ["driver"],
                "falsifiers": [f"falsifier-{suffix}"],
            },
            "bull_evidence_ids": [f"bull-{suffix}"],
            "bear_evidence_ids": [f"bear-{suffix}"],
            "missing_or_stale_data": [],
            "uncertainty": {"level": "0.3", "known_unknowns": ["unknown"]},
        }
        connection.execute(
            "INSERT INTO trade_plan_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"record-{suffix}", f"key-{suffix}", f"proposal-{suffix}", 0,
                snapshot_id, strategy, "1.0.0", "PARSED", "ACCEPTED", "WATCH",
                f"raw-{suffix}", "not exported", json.dumps(proposal), "provider",
                "model", "model-v1", "prompt-v1", "sampling-v1", "{}",
                "validator-v1", "policy-v1", timing, timing, timing, timing,
                f"proposal-hash-{suffix}",
            ),
        )
        connection.execute(
            "INSERT INTO proposal_disposition_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"disposition-{suffix}", f"record-{suffix}", strategy, "1.0.0", 0,
                "regime.probabilities", "ACCEPT", "regime validated", None,
                "1", json.dumps([f"e-{suffix}"]), "validator-v1", "policy-v1",
                timing, timing, timing, timing, f"disposition-hash-{suffix}",
            ),
        )
    connection.execute(
        "INSERT INTO lesson_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "lesson-event", "lesson-1", "SWING_V1", "1.0.0", 0, "SHADOW",
            json.dumps({"condition": "condition", "tentative_action": "observe"}),
            timing, timing, timing, timing, "lesson-hash",
        ),
    )
    future = "2026-07-27T10:00:00+00:00"
    connection.execute(
        "INSERT INTO lesson_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "future-event", "future-lesson", "TREND_V1", "1.0.0", 0, "SHADOW",
            "{}", future, future, future, future, "future-hash",
        ),
    )
    connection.commit()
    return connection


def _paper_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE strategy_books (book_id TEXT, strategy_id TEXT, market TEXT, currency TEXT, created_at TEXT);
        CREATE TABLE positions (position_snapshot_id TEXT, book_id TEXT, security_id TEXT, quantity TEXT, average_cost TEXT, as_of_at TEXT, created_at TEXT);
        CREATE TABLE nav_snapshots (nav_snapshot_id TEXT, book_id TEXT, nav TEXT, cash TEXT, as_of_at TEXT, created_at TEXT);
        CREATE TABLE paper_orders (order_id TEXT, book_id TEXT, proposal_id TEXT, security_id TEXT, order_state TEXT, payload_json TEXT, occurred_at TEXT, created_at TEXT);
        """
    )
    timing = "2026-07-26T10:00:00+00:00"
    connection.execute("INSERT INTO strategy_books VALUES (?,?,?,?,?)", ("book-swing-kr", "SWING_V1", "KR", "KRW", timing))
    connection.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?)", ("position-1", "book-swing-kr", "security-kr", "3", "70000", timing, timing))
    connection.execute("INSERT INTO nav_snapshots VALUES (?,?,?,?,?,?)", ("nav-1", "book-swing-kr", "1000000", "790000", timing, timing))
    connection.commit()
    return connection


def _ops_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE job_runs (run_id TEXT, job_key TEXT, status TEXT, started_at TEXT, finished_at TEXT, payload_json TEXT, created_at TEXT);
        CREATE TABLE heartbeats (heartbeat_id TEXT, run_id TEXT, observed_at TEXT, payload_json TEXT, created_at TEXT);
        """
    )
    connection.execute(
        "INSERT INTO job_runs VALUES (?,?,?,?,?,?,?)",
        ("run-1", "daily_pipeline", "SUCCESS", "2026-07-26T09:00:00+00:00", "2026-07-26T09:05:00+00:00", "{}", "2026-07-26T09:00:00+00:00"),
    )
    connection.commit()
    return connection


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_walk_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_walk_keys(item) for item in value), set())
    return set()


def test_export_separates_authoritative_phase1_sections_and_pit_boundary() -> None:
    exporter = DashboardExporter(
        _research_connection(), _paper_connection(), _ops_connection()
    )

    payload = exporter.build(as_of=AS_OF, generated_at=AS_OF)

    assert payload["schema_version"] == "prism_dashboard_v1"
    assert payload["as_of"] == AS_OF.isoformat()
    assert payload["local_only"] is True
    assert payload["bind_host"] == "127.0.0.1"
    assert set(payload) == {
        "schema_version", "generated_at", "as_of", "local_only", "bind_host",
        "research", "paper", "ops",
    }
    assert [item["symbol"] for item in payload["research"]["daily_leaders"]] == ["005930"]
    assert payload["research"]["swing_v1_proposals"][0]["strategy_id"] == "SWING_V1"
    assert payload["research"]["swing_v1_proposals"][0]["quant_score"] == {
        "score_version": "SHADOW_SCORE_V1.SWING_V1",
        "total_score": "67.500000",
        "components": {"swing_component": "67.500000"},
    }
    assert payload["research"]["trend_v1_proposals"][0]["strategy_id"] == "TREND_V1"
    assert payload["research"]["swing_v1_proposals"][0]["scenario_state"] == "INVALID_PROPOSAL"
    assert payload["research"]["scenario_evidence_falsifiers"] == []
    assert payload["research"]["research_oos"]["status"] == "UNAVAILABLE"
    user_daily = payload["research"]["kr_daily"]
    assert user_daily["market"] == "KR"
    assert user_daily["section_order"] == [
        "source_quality", "market_context", "candidate_table", "strategy_cards",
        "conditional_scenarios", "data_gaps", "changes", "next_review", "audit",
    ]
    assert user_daily["candidate_table"][0]["security_id"] == "security-kr"
    assert user_daily["candidate_table"][0]["swing"]["state"] == "INVALID_PROPOSAL"
    assert user_daily["candidate_table"][0]["trend"]["state"] == "INVALID_PROPOSAL"
    assert user_daily["candidate_table"][0]["change"] == "NEW"
    assert user_daily["market_context"]["breadth"] == []
    assert user_daily["market_context"]["investor_flows"] == []
    assert user_daily["market_context"]["leading_groups"] == []
    assert user_daily["market_context"]["weak_groups"] == []
    assert [item["lesson_id"] for item in payload["research"]["shadow_feedback"]] == ["lesson-1"]
    assert payload["paper"]["environment"] == "INTERNAL_PAPER"
    assert payload["paper"]["books"][0]["strategy_id"] == "SWING_V1"
    assert payload["ops"]["jobs"][0]["job_key"] == "daily_pipeline"
    assert _walk_keys(payload).isdisjoint(
        {"account", "account_summary", "real_portfolio", "real_trading", "broker", "order_intent"}
    )


def test_export_reads_legacy_score_version_and_values_without_reinterpretation() -> None:
    research = _research_connection()
    research.execute(
        "UPDATE decision_snapshots SET quant_score_version = ?, snapshot_json = ? "
        "WHERE strategy_id = 'SWING_V1'",
        (
            "swing-score.shadow.v1",
            json.dumps(
                {
                    "quant_score": {
                        "total_score": "42.125000",
                        "components": {
                            "swing_v1.price_momentum_5d": "35.000000",
                            "swing_v1.regime_compatibility": "52.812500",
                        },
                    }
                }
            ),
        ),
    )

    score = DashboardExporter(
        research, _paper_connection(), _ops_connection()
    ).build(as_of=AS_OF, generated_at=AS_OF)["research"]["swing_v1_proposals"][0][
        "quant_score"
    ]

    assert score == {
        "score_version": "swing-score.shadow.v1",
        "total_score": "42.125000",
        "components": {
            "swing_v1.price_momentum_5d": "35.000000",
            "swing_v1.regime_compatibility": "52.812500",
        },
    }


def test_rejected_proposal_is_exported_as_invalid_without_inventing_no_entry() -> None:
    research = _research_connection()
    research.execute(
        "UPDATE trade_plan_proposals SET parse_status = 'REJECTED', "
        "validation_status = 'REJECTED', proposed_decision = 'ENTRY_CANDIDATE', "
        "normalized_proposal_json = NULL WHERE strategy_id = 'SWING_V1'"
    )
    exporter = DashboardExporter(research, _paper_connection(), _ops_connection())

    proposal = exporter.build(as_of=AS_OF, generated_at=AS_OF)["research"][
        "swing_v1_proposals"
    ][0]

    assert proposal["status"] == "REJECTED"
    assert proposal["scenario_state"] == "INVALID_PROPOSAL"
    assert proposal["scenario_complete"] is False
    assert proposal["proposed_decision"] is None
    assert proposal["scenario_reasons"]
    assert _walk_keys(proposal).isdisjoint(
        {"entry_price", "stop_price", "target_price", "quantity", "account"}
    )


def test_degraded_quality_suppresses_actionable_price_level_collections() -> None:
    research = _research_connection()
    normalized = valid_proposal_payload()
    normalized["stop_candidates"] = [{"price": "65000", "basis": "STRUCTURE"}]
    normalized["target_candidates"] = [{"price": "74000", "basis": "ATR"}]
    normalized["entry_predicates"][0]["comparison_value"] = "68123"
    normalized["entry_predicates"][0]["observed_value"] = "67999"
    research.execute(
        "UPDATE decision_snapshots SET data_quality = 'STALE', "
        "quality_disposition = 'REPORT_ONLY' WHERE strategy_id = 'SWING_V1'"
    )
    research.execute(
        "UPDATE trade_plan_proposals SET normalized_proposal_json = ? "
        "WHERE strategy_id = 'SWING_V1'",
        (canonical_json(normalized),),
    )

    payload = DashboardExporter(
        research, _paper_connection(), _ops_connection()
    ).build(as_of=AS_OF, generated_at=AS_OF)
    proposal = payload["research"]["swing_v1_proposals"][0]
    serialized = json.dumps(proposal, ensure_ascii=False)

    assert "65000" not in serialized
    assert "74000" not in serialized
    assert "68123" not in serialized
    assert "67999" not in serialized
    assert proposal["actionable_levels_suppressed"] is True
    assert proposal["level_suppression_reason"] == "STALE / REPORT_ONLY"


def test_accepted_proposal_preserves_bound_identity_and_observed_trigger() -> None:
    research = _research_connection()
    normalized = TradePlanProposal.model_validate(valid_proposal_payload()).model_copy(
        update={
            "decision": ProposedDecision.NO_ENTRY,
            "bull_case": TradePlanProposal.model_validate(
                valid_proposal_payload()
            ).bull_case.model_copy(update={"evidence_ids": ("ev-risk-1",)}),
            "missing_or_stale_data": (
                MissingOrStaleData(
                    field="supplemental_news",
                    status=MissingDataStatus.STALE,
                    critical=False,
                    detail="supplemental context is old",
                ),
            ),
        }
    )
    timing = "2026-07-26T10:00:00+00:00"
    research.execute(
        "UPDATE decision_snapshots SET strategy_version = ?, market = ?, security_id = ?, "
        "data_snapshot_id = ?, feature_snapshot_id = ?, evidence_refs_json = ? "
        "WHERE strategy_id = 'SWING_V1'",
        (
            normalized.strategy_version.value,
            normalized.market.value,
            str(normalized.security_id.value),
            str(normalized.feature_provenance.data_snapshot_id),
            str(normalized.feature_provenance.feature_snapshot_id),
            json.dumps(["ev-price-1", "ev-risk-1"]),
        ),
    )
    research.execute(
        "UPDATE trade_plan_proposals SET strategy_version = ?, proposal_id = ?, "
        "proposed_decision = ?, normalized_proposal_json = ? "
        "WHERE proposal_record_id = 'record-swing'",
        (
            normalized.strategy_version.value,
            str(normalized.proposal_id),
            normalized.decision.value,
            canonical_json(normalized),
        ),
    )
    research.execute(
        "DELETE FROM proposal_disposition_events WHERE proposal_record_id = 'record-swing'"
    )
    disposition_rows = (
        ("evidence.ev-price-1", "ACCEPT", "evidence exists", "ev-price-1", ["ev-price-1"]),
        ("evidence.ev-risk-1", "ACCEPT", "evidence exists", "ev-risk-1", ["ev-risk-1"]),
        (
            "entry_predicates[0].observed_value",
            "RECALCULATE",
            "feature value read from bound snapshot",
            "0.10",
            ["ev-price-1"],
        ),
        (
            "entry_predicates[0].evaluation",
            "RECALCULATE",
            "predicate evaluated from feature snapshot",
            "false",
            ["ev-price-1"],
        ),
    )
    for sequence_no, (field_path, action, reason, resolved, evidence_ids) in enumerate(
        disposition_rows
    ):
        research.execute(
            "INSERT INTO proposal_disposition_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"accepted-disposition-{sequence_no}",
                "record-swing",
                "SWING_V1",
                normalized.strategy_version.value,
                sequence_no,
                field_path,
                action,
                reason,
                None,
                json.dumps(resolved),
                json.dumps(evidence_ids),
                "validator-v1",
                "policy-v1",
                timing,
                timing,
                timing,
                timing,
                f"accepted-disposition-hash-{sequence_no}",
            ),
        )

    proposal = DashboardExporter(
        research, _paper_connection(), _ops_connection()
    ).build(as_of=AS_OF, generated_at=AS_OF)["research"]["swing_v1_proposals"][0]

    assert proposal["scenario_state"] == "NO_ENTRY"
    assert proposal["scenario_complete"] is True
    assert proposal["proposed_decision"] == "NO_ENTRY"
    assert proposal["scenario"]["triggers"][0]["observed_value"] == "0.10"
    assert proposal["scenario"]["bull_path"]["summary"].startswith("Leadership broadens")
    assert proposal["scenario"]["bull_path"]["evidence_ids"] == ["ev-risk-1"]
    assert proposal["scenario"]["base_path"]["confirmations"]
    assert proposal["scenario"]["bear_path"]["evidence_ids"] == ["ev-risk-1"]
    assert proposal["bull_evidence_ids"] == ["ev-price-1"]
    assert proposal["bear_evidence_ids"] == ["ev-risk-1"]
    assert proposal["missing_or_stale_data"] == [
        {
            "field": "supplemental_news",
            "status": "STALE",
            "critical": False,
            "detail": "supplemental context is old",
        }
    ]


@pytest.mark.parametrize(
    ("market", "strategy", "record_id"),
    (
        ("KR", "SWING_V1", "record-swing"),
        ("KR", "TREND_V1", "record-trend"),
        ("US", "SWING_V1", "record-swing"),
        ("US", "TREND_V1", "record-trend"),
    ),
)
def test_four_malformed_watch_with_critical_missing_outputs_are_not_no_entry(
    market: str,
    strategy: str,
    record_id: str,
) -> None:
    research = _research_connection()
    research.execute(
        "UPDATE decision_snapshots SET market = ?, security_id = ? "
        "WHERE strategy_id = ?",
        (market, f"security-{market.lower()}", strategy),
    )
    research.execute(
        "UPDATE trade_plan_proposals SET parse_status = 'REJECTED', "
        "validation_status = 'REJECTED', proposed_decision = NULL, "
        "raw_output = ?, normalized_proposal_json = NULL WHERE proposal_record_id = ?",
        (
            '{"decision":"WATCH","missing_or_stale_data":[{"critical":true}]}',
            record_id,
        ),
    )
    research.execute(
        "UPDATE proposal_disposition_events SET action = 'REJECT', "
        "field_path = 'missing_or_stale_data', "
        "reason = 'critical data requires fail closed' WHERE proposal_record_id = ?",
        (record_id,),
    )

    proposals = DashboardExporter(
        research, _paper_connection(), _ops_connection()
    ).build(as_of=AS_OF, generated_at=AS_OF)["research"][
        "swing_v1_proposals" if strategy == "SWING_V1" else "trend_v1_proposals"
    ]
    proposal = next(item for item in proposals if item["proposal_record_id"] == record_id)

    assert proposal["scenario_state"] == "INVALID_PROPOSAL"
    assert proposal["scenario_complete"] is False
    assert proposal["proposed_decision"] is None
    assert proposal["scenario"] == {}
    assert proposal["scenario_reasons"] == ["critical data requires fail closed"]
    assert "raw_output" not in proposal
    assert '"critical":true' not in json.dumps(proposal, sort_keys=True)


def test_export_allowlists_nested_persisted_json_fields() -> None:
    research = _research_connection()
    malicious = {
        "regime": {
            "probabilities": {"sideways": "1", "order_intent": "forbidden"},
            "confidence": "0.7",
            "drivers": ["driver"],
            "falsifiers": ["falsifier"],
            "account": {"token": "forbidden"},
        },
        "bull_evidence_ids": ["bull"],
        "bear_evidence_ids": ["bear"],
        "missing_or_stale_data": [],
        "uncertainty": {
            "level": "0.3",
            "known_unknowns": ["unknown"],
            "quantity": 999,
        },
        "entry_price": "70000",
    }
    research.execute(
        "UPDATE trade_plan_proposals SET normalized_proposal_json = ? "
        "WHERE strategy_id = 'SWING_V1'",
        (json.dumps(malicious),),
    )
    research.execute(
        "UPDATE lesson_candidates SET candidate_json = ?",
        (json.dumps({"condition": "observe", "tentative_action": "wait", "account": "x"}),),
    )

    payload = DashboardExporter(
        research, _paper_connection(), _ops_connection()
    ).build(as_of=AS_OF, generated_at=AS_OF)

    assert _walk_keys(payload["research"]).isdisjoint(
        {"account", "token", "order_intent", "quantity", "entry_price"}
    )


def test_export_reads_source_databases_read_only_and_writes_atomic_json(tmp_path: Path) -> None:
    paths = {name: tmp_path / f"{name}.sqlite" for name in ("research", "paper", "ops")}
    for path, source in (
        (paths["research"], _research_connection()),
        (paths["paper"], _paper_connection()),
        (paths["ops"], _ops_connection()),
    ):
        destination = sqlite3.connect(path)
        source.backup(destination)
        destination.close()
        source.close()
    output = tmp_path / "dashboard.json"

    payload = export_dashboard(
        research_db=paths["research"], paper_db=paths["paper"],
        ops_db=paths["ops"], output_path=output, as_of=AS_OF,
        generated_at=AS_OF,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    with open_read_only_database(paths["research"]) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden (value TEXT)")
    assert not list(tmp_path.glob("*.tmp"))


def test_export_reads_populated_versioned_authoritative_stores(tmp_path: Path) -> None:
    paths = {kind.value: tmp_path / f"{kind.value}.sqlite" for kind in DatabaseKind}
    for kind in DatabaseKind:
        connection = sqlite3.connect(paths[kind.value], isolation_level=None)
        migrate_database(connection, kind)
        timing = "2026-07-26T10:00:00+00:00"
        if kind is DatabaseKind.RESEARCH:
            connection.execute(
                "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?)",
                ("migrated-snapshot", "US", timing, "hash", "FRESH", timing),
            )
            connection.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "migrated-leader", "migrated-snapshot", None,
                    "leadership_security_state", "hermes_agent_report", "AAPL",
                    "migrated-source", 0, timing, timing, timing,
                    json.dumps({"symbol": "AAPL", "name": "Apple"}), timing,
                ),
            )
        elif kind is DatabaseKind.PAPER:
            connection.execute(
                "INSERT INTO strategy_books VALUES (?,?,?,?,?)",
                ("migrated-book", "TREND_V1", "US", "USD", timing),
            )
            connection.execute(
                "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                ("migrated-position", "migrated-book", "security-us", "2", "150", timing, timing),
            )
            connection.execute(
                "INSERT INTO nav_snapshots VALUES (?,?,?,?,?,?)",
                ("migrated-nav", "migrated-book", "1000", "700", timing, timing),
            )
        else:
            connection.execute(
                "INSERT INTO job_runs VALUES (?,?,?,?,?,?,?)",
                ("migrated-run", "daily", "SUCCESS", timing, timing, "{}", timing),
            )
        connection.close()

    payload = export_dashboard(
        research_db=paths["research"], paper_db=paths["paper"],
        ops_db=paths["ops"], output_path=tmp_path / "empty.json",
        as_of=AS_OF, generated_at=AS_OF,
    )

    assert payload["research"]["daily_leaders"][0]["symbol"] == "AAPL"
    assert payload["paper"]["books"][0]["book_id"] == "migrated-book"
    assert payload["ops"]["jobs"][0]["run_id"] == "migrated-run"


def test_legacy_generators_are_safe_thin_local_export_wrappers() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = (
        "domestic_stock_trading", "us_stock_trading", "kis_auth", "get_portfolio(",
        "get_account_summary(", "load_dotenv", "kis_devlp.yaml", "yfinance",
    )
    for relative in (
        "examples/generate_dashboard_json.py",
        "examples/generate_us_dashboard_json.py",
    ):
        source = (root / relative).read_text(encoding="utf-8").lower()
        assert "prism_app.dashboard_export" in source
        assert all(token not in source for token in forbidden)

    package = json.loads((root / "examples/dashboard/package.json").read_text())
    assert package["scripts"]["dev"].endswith("--hostname 127.0.0.1")
    assert package["scripts"]["start"].endswith("--hostname 127.0.0.1")


def test_typescript_contract_has_no_real_account_surface() -> None:
    root = Path(__file__).resolve().parents[2] / "examples/dashboard/types"
    dashboard = (root / "dashboard.ts").read_text(encoding="utf-8")
    assert 'from "./research"' in dashboard
    assert 'from "./paper"' in dashboard
    assert 'from "./ops"' in dashboard
    combined = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.ts"))
    for forbidden in ("RealTradingSummary", "AccountSummary", "real_portfolio", "real_trading"):
        assert forbidden not in combined


def test_existing_dashboard_page_consumes_phase1_contract_only() -> None:
    root = Path(__file__).resolve().parents[2] / "examples/dashboard"
    page = (root / "app/page.tsx").read_text(encoding="utf-8")

    for required in (
        "data.research.data_freshness",
        "data.research.daily_leaders",
        "data.research.swing_v1_proposals",
        "data.research.trend_v1_proposals",
        "data.research.scenario_evidence_falsifiers",
        "data.research.shadow_feedback",
        "data.ops.jobs",
    ):
        assert required in page
    for forbidden in (
        "data.real_portfolio",
        "data.holdings",
        "data.trading_history",
        "MetricsCards",
        "HoldingsTable",
        "TradingHistoryPage",
        "StockEasy",
        'proposal.status === "REJECTED" ? "NO_ENTRY"',
        'proposal.proposed_decision || "NO_ENTRY"',
        "검증을 통과한 시나리오가 없습니다. NO_ENTRY 상태를 유지합니다.",
    ):
        assert forbidden not in page
    assert "proposal.scenario_state" in page
    assert "ANALYSIS_INCOMPLETE" in page
    for required_surface in (
        "원천·기준시점·호출 증거와 데이터 품질",
        "KR 시장 국면·수급·주도/약세 그룹",
        "후보 요약",
        "종목별 SWING/TREND 카드",
        "조건부 진입·회피와 무효화·축소/청산",
        "데이터 공백·충돌과 가격 수준 공개 여부",
        "이전 실행 대비 변화",
        "다음 이벤트와 검토 시각",
        "감사 상세",
        "현재 판정",
        "시장 판단",
        "섹터 판단",
        "종목 판단",
        "상승 경로",
        "기본 경로",
        "하락 경로",
        "진입 조건",
        "회피 조건",
        "무효화·실패 전환",
        "손절 후보",
        "목표 후보",
        "리스크 배수 후보",
        "재진입 후보",
        "피라미딩 후보",
        "반대 근거",
        "불확실성",
        "다음 검토",
        "필드 판정",
    ):
        assert required_surface in page
    assert "kr_daily?: KrDailyLayer" in page
    assert "kr_daily_composition" in page
    assert "<details" in page
    assert "actionable_levels_suppressed" in page
    positions = [page.index(label) for label in (
        "원천·기준시점·호출 증거와 데이터 품질",
        "KR 시장 국면·수급·주도/약세 그룹",
        "후보 요약",
        "종목별 SWING/TREND 카드",
        "조건부 진입·회피와 무효화·축소/청산",
        "데이터 공백·충돌과 가격 수준 공개 여부",
        "이전 실행 대비 변화",
        "다음 이벤트와 검토 시각",
        "감사 상세",
    )]
    assert positions == sorted(positions)


def test_dashboard_contract_tests_are_explicitly_discovered_by_ci() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Run separated localhost dashboard contract tests" in workflow
    assert "python -m pytest tests/dashboard -q" in workflow


def test_generators_require_explicit_separate_store_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "examples/generate_dashboard_json.py"), "--help"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--research-db" in result.stdout
    assert "--paper-db" in result.stdout
    assert "--ops-db" in result.stdout
    assert "stock_tracking_db.sqlite" not in result.stdout
