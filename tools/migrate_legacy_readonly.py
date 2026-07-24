#!/usr/bin/env python3
"""Copy supported legacy records into a new PRISM database bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core.storage.legacy_manifest import (  # noqa: E402
    inspect_legacy,
    migrate_legacy,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed copy-only migration from a read-only legacy source."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination_directory", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            report = inspect_legacy(args.source)
            output = report.to_json()
            exit_code = 0 if report.migration_ready else 2
        else:
            result = migrate_legacy(args.source, args.destination_directory)
            output = result.to_json()
            exit_code = 0
    except Exception:
        print(json.dumps({"error": "MIGRATION_FAILED"}, sort_keys=True))
        return 2

    if args.pretty:
        print(json.dumps(json.loads(output), indent=2, sort_keys=True))
    else:
        print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
