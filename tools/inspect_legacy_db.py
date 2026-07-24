#!/usr/bin/env python3
"""Inspect a legacy PRISM SQLite database without exposing row content."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core.storage.legacy_manifest import inspect_legacy  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Produce a deterministic metadata-only legacy DB inspection report."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect_legacy(args.source)
    except Exception:
        print(json.dumps({"error": "INSPECTION_FAILED"}, sort_keys=True))
        return 2
    if args.pretty:
        print(json.dumps(json.loads(report.to_json()), indent=2, sort_keys=True))
    else:
        print(report.to_json())
    return 0 if report.migration_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
