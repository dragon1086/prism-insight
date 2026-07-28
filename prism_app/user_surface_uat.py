"""Read-only UAT export for the existing PRISM report and dashboard surfaces."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

from prism_app.dashboard_export import export_dashboard
from prism_app.shadow_report import append_shadow_section, read_persisted_shadow


@dataclass(frozen=True)
class UserSurfaceExportResult:
    report_id: str
    data_snapshot_id: str
    report_output: Path
    dashboard_output: Path
    broker_called: bool = False
    schedule_activated: bool = False


def _read_existing_report(
    research_db: str | Path,
    *,
    report_id: str,
    snapshot_id: str,
) -> str:
    resolved = Path(research_db).expanduser().resolve(strict=True)
    connection = sqlite3.connect(
        f"file:{quote(str(resolved), safe='/')}?mode=ro",
        uri=True,
        isolation_level=None,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT content FROM reports WHERE report_id = ? AND snapshot_id = ?",
            (report_id, snapshot_id),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise LookupError("existing PRISM report is unavailable for the same persisted snapshot")
    return row[0]


def _write_atomic(path: str | Path, content: str) -> Path:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def export_existing_user_surfaces(
    *,
    research_db: str | Path,
    paper_db: str | Path,
    ops_db: str | Path,
    job_key: str,
    report_output: str | Path,
    dashboard_output: str | Path,
    generated_at: datetime | None = None,
) -> UserSurfaceExportResult:
    """Render one persisted run through the existing report and localhost dashboard seams."""

    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    readback = read_persisted_shadow(ops_db, job_key=job_key)
    analysis = readback.analysis
    snapshot_id = str(analysis.data_snapshot_id)
    base_report = _read_existing_report(
        research_db,
        report_id=analysis.leadership_report_id,
        snapshot_id=analysis.leadership_snapshot_id,
    )
    report_path = _write_atomic(
        report_output,
        append_shadow_section(base_report, readback.markdown),
    )
    export_dashboard(
        research_db=research_db,
        paper_db=paper_db,
        ops_db=ops_db,
        output_path=dashboard_output,
        as_of=generated,
        generated_at=generated,
    )
    return UserSurfaceExportResult(
        report_id=analysis.leadership_report_id,
        data_snapshot_id=snapshot_id,
        report_output=report_path,
        dashboard_output=Path(dashboard_output).expanduser(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export one persisted real-data SHADOW run through the existing PRISM "
            "report and localhost dashboard surfaces. No external call is made."
        )
    )
    parser.add_argument("--research-db", required=True, type=Path)
    parser.add_argument("--paper-db", required=True, type=Path)
    parser.add_argument("--ops-db", required=True, type=Path)
    parser.add_argument("--job-key", required=True)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument("--dashboard-output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = export_existing_user_surfaces(
            research_db=args.research_db,
            paper_db=args.paper_db,
            ops_db=args.ops_db,
            job_key=args.job_key,
            report_output=args.report_output,
            dashboard_output=args.dashboard_output,
        )
        payload = {
            "status": "EXISTING_USER_SURFACES_EXPORTED",
            "report_id": result.report_id,
            "data_snapshot_id": result.data_snapshot_id,
            "report_output": str(result.report_output),
            "dashboard_output": str(result.dashboard_output),
            "broker_called": result.broker_called,
            "schedule_activated": result.schedule_activated,
        }
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - surface only stable local error type
        payload = {
            "status": "USER_SURFACE_EXPORT_UNAVAILABLE",
            "failure_type": type(exc).__name__,
            "broker_called": False,
            "schedule_activated": False,
        }
        exit_code = 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
