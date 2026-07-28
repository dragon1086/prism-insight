from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from prism_app.daily_pipeline import PersistedDailyAnalysis, SQLiteAppRunRepository
from prism_app.user_surface_uat import export_existing_user_surfaces
from prism_core.data.quality import QualityDecision, QualityDisposition
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market


NOW = datetime(2026, 7, 27, 6, 30, tzinfo=timezone.utc)
SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000777")


def _stores(
    tmp_path: Path,
    *,
    report_snapshot_id: UUID = SNAPSHOT_ID,
) -> tuple[Path, Path, Path, str]:
    research = tmp_path / "research.sqlite"
    paper = tmp_path / "paper.sqlite"
    ops = tmp_path / "ops.sqlite"
    job_key = "daily:KR:2026-07-27:daily-close:surface"
    with open_database(research) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        connection.execute(
            "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (str(SNAPSHOT_ID), "KR", NOW.isoformat(), "hash", "FRESH", NOW.isoformat()),
        )
        if report_snapshot_id != SNAPSHOT_ID:
            connection.execute(
                "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(report_snapshot_id),
                    "KR",
                    NOW.isoformat(),
                    "other-hash",
                    "FRESH",
                    NOW.isoformat(),
                ),
            )
        connection.execute(
            "INSERT INTO reports VALUES (?, ?, ?, ?, ?)",
            (
                "existing-report",
                str(report_snapshot_id),
                "market_leadership_v1",
                "# 기존 PRISM 통합 시황\n\n기존 후보·시장 내용입니다.\n",
                NOW.isoformat(),
            ),
        )
    with open_database(paper) as connection:
        migrate_database(connection, DatabaseKind.PAPER)
    with open_database(ops) as connection:
        migrate_database(connection, DatabaseKind.OPS)
        SQLiteAppRunRepository(connection).save(
            PersistedDailyAnalysis(
                job_key=job_key,
                run_id=SQLiteAppRunRepository.run_id_for(job_key),
                market=Market.KR,
                as_of_date=date(2026, 7, 27),
                run_type="daily-close",
                evaluated_at=NOW,
                data_snapshot_id=SNAPSHOT_ID,
                leadership_snapshot_id=str(SNAPSHOT_ID),
                leadership_report_id="existing-report",
                quality_decision=QualityDecision(
                    disposition=QualityDisposition.ACCEPT,
                    reasons=(),
                    missing_fields=(),
                    stale_fields=(),
                ),
                quality_skip=None,
                source_payload={"provider": "KIS", "evidence_level": "LIVE_READ_ONLY"},
                strategies=(),
            )
        )
    return research, paper, ops, job_key


def test_exports_existing_report_and_dashboard_from_same_persisted_run(tmp_path: Path) -> None:
    research, paper, ops, job_key = _stores(tmp_path)
    report = tmp_path / "integrated.md"
    dashboard = tmp_path / "dashboard_data.json"

    result = export_existing_user_surfaces(
        research_db=research,
        paper_db=paper,
        ops_db=ops,
        job_key=job_key,
        report_output=report,
        dashboard_output=dashboard,
        generated_at=NOW,
    )

    markdown = report.read_text(encoding="utf-8")
    payload = json.loads(dashboard.read_text(encoding="utf-8"))
    assert markdown.startswith("# 기존 PRISM 통합 시황")
    assert markdown.count("<!-- PRISM_PHASE1_SHADOW_START -->") == 1
    assert "주문 또는 계좌 작업을 수행하지 않습니다" in markdown
    assert payload["schema_version"] == "prism_dashboard_v1"
    assert payload["research"]["data_freshness"][0]["snapshot_id"] == str(SNAPSHOT_ID)
    assert result.report_id == "existing-report"
    assert result.data_snapshot_id == str(SNAPSHOT_ID)
    assert result.broker_called is False
    assert result.schedule_activated is False


def test_rejects_report_from_a_different_snapshot(tmp_path: Path) -> None:
    research, paper, ops, job_key = _stores(
        tmp_path,
        report_snapshot_id=UUID("00000000-0000-0000-0000-000000000778"),
    )

    try:
        export_existing_user_surfaces(
            research_db=research,
            paper_db=paper,
            ops_db=ops,
            job_key=job_key,
            report_output=tmp_path / "integrated.md",
            dashboard_output=tmp_path / "dashboard.json",
            generated_at=NOW,
        )
    except LookupError as exc:
        assert "same persisted snapshot" in str(exc)
    else:
        raise AssertionError("snapshot mismatch must fail closed")
