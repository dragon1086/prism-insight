#!/usr/bin/env python3
"""Track exact KR trading-day outcomes for third-slot SHADOW experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from observability.events import DEFAULT_SPOOL_PATH  # noqa: E402
from observability.third_slot_shadow import track_matured_outcomes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", default=str(DEFAULT_SPOOL_PATH))
    parser.add_argument("--as-of")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = track_matured_outcomes(
        spool_path=args.spool,
        as_of=args.as_of,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
