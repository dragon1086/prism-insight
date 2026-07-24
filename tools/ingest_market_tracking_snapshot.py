#!/usr/bin/env python3
"""Ingest one explicit market_tracking_v1 document into research.sqlite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core.reporting.leadership_tracking import (  # noqa: E402
    SCHEMA_VERSION,
    LeadershipRepository,
    MarketTrackingSnapshot,
)
from prism_core.storage.database import open_database  # noqa: E402
from prism_core.storage.migrations import DatabaseKind, migrate_database  # noqa: E402


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and atomically store REPORT_ONLY leadership evidence in the "
            "explicit research database."
        )
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--input", default="-", help="JSON file path or '-' for stdin")
    parser.add_argument("--output", choices=("summary", "report"), default="summary")
    args = parser.parse_args(argv)

    try:
        snapshot = MarketTrackingSnapshot.model_validate_json(_read_input(args.input))
        with open_database(args.db) as connection:
            migrate_database(connection, DatabaseKind.RESEARCH)
            result = LeadershipRepository(connection).ingest(snapshot)
    except Exception as exc:
        error = {
            "error": "MARKET_TRACKING_INGEST_FAILED",
            "error_type": type(exc).__name__,
        }
        print(json.dumps(error, separators=(",", ":"), sort_keys=True), file=sys.stderr)
        return 2

    if args.output == "report":
        sys.stdout.write(result.rendered_markdown)
        return 0

    summary = {
        "changes": [
            {"state": item.state.value, "symbol": item.symbol} for item in result.changes
        ],
        "idempotent": result.idempotent,
        "market": snapshot.market.value,
        "report_id": result.report_id,
        "revision": snapshot.revision,
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": result.snapshot_id,
    }
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
