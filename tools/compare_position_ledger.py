#!/usr/bin/env python3
"""Read-only legacy holdings versus Phase 4-a positions comparison."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prism_core.positions import PositionStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="stock_tracking_db.sqlite")
    parser.add_argument(
        "--market",
        choices=("kr", "us", "both"),
        default="both",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    markets = ("KR", "US") if args.market == "both" else (args.market.upper(),)
    try:
        uri = Path(args.db_path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            store = PositionStore(connection)
            results = [store.compare_legacy_positions(market) for market in markets]
        finally:
            connection.close()
    except Exception as error:
        print(
            json.dumps(
                {"status": "error", "error_type": type(error).__name__},
                sort_keys=True,
            )
        )
        return 2

    matches = all(result["matches"] for result in results)
    print(
        json.dumps(
            {"status": "ok" if matches else "mismatch", "results": results},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
