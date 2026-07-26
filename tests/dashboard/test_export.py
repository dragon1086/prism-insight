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
from prism_core.storage.migrations import DatabaseKind, migrate_database


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
                f"score-{suffix}", "v1", json.dumps([f"e-{suffix}"]), "{}",
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
    assert payload["research"]["trend_v1_proposals"][0]["strategy_id"] == "TREND_V1"
    assert payload["research"]["scenario_evidence_falsifiers"][0]["falsifiers"]
    assert payload["research"]["research_oos"]["status"] == "UNAVAILABLE"
    assert [item["lesson_id"] for item in payload["research"]["shadow_feedback"]] == ["lesson-1"]
    assert payload["paper"]["environment"] == "INTERNAL_PAPER"
    assert payload["paper"]["books"][0]["strategy_id"] == "SWING_V1"
    assert payload["ops"]["jobs"][0]["job_key"] == "daily_pipeline"
    assert _walk_keys(payload).isdisjoint(
        {"account", "account_summary", "real_portfolio", "real_trading", "broker", "order_intent"}
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
