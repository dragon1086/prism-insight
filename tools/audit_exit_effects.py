#!/usr/bin/env python3
"""Read-only audit for durable exit effect delivery state."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prism_core.positions import account_fingerprint  # noqa: E402

_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,119}$")


def _safe_error_type(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized if _SAFE_ERROR_TYPE.fullmatch(normalized) else "REDACTED"


def _readonly_connection(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.expanduser().resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def audit_database(db_path: str | Path, *, limit: int = 50) -> dict[str, Any]:
    """Report unresolved effects without mutating SQLite or exposing payloads."""

    if not isinstance(limit, int) or limit < 1:
        raise ValueError("audit limit must be a positive integer")
    try:
        with _readonly_connection(Path(db_path)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='exit_effect_outbox'"
            ).fetchone()
            if table is None:
                return {
                    "status": "unknown",
                    "error_type": "MissingExitEffectOutbox",
                }
            status_counts = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT status, COUNT(*) FROM exit_effect_outbox "
                    "GROUP BY status ORDER BY status"
                )
            }
            effect_counts = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT effect_type, COUNT(*) FROM exit_effect_outbox "
                    "WHERE status != 'DELIVERED' "
                    "GROUP BY effect_type ORDER BY effect_type"
                )
            }
            rows = connection.execute(
                """
                SELECT id, effect_type, market, account_id, symbol, source,
                       status, attempt_count, next_attempt_at,
                       lease_expires_at, last_error, created_at
                FROM exit_effect_outbox
                WHERE status != 'DELIVERED'
                ORDER BY created_at, id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            unresolved = [
                {
                    "effect_id": str(row["id"]),
                    "effect_type": str(row["effect_type"]),
                    "market": str(row["market"]),
                    "account_ref": account_fingerprint(row["account_id"]),
                    "symbol": str(row["symbol"]),
                    "source": str(row["source"]),
                    "status": str(row["status"]),
                    "attempt_count": int(row["attempt_count"]),
                    "next_attempt_at": row["next_attempt_at"],
                    "lease_expires_at": row["lease_expires_at"],
                    "last_error": _safe_error_type(row["last_error"]),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            ]
            unresolved_count = sum(effect_counts.values())
            return {
                "status": "blocked" if unresolved_count else "ready",
                "status_counts": status_counts,
                "effect_counts": effect_counts,
                "unresolved_count": unresolved_count,
                "returned_count": len(unresolved),
                "truncated": unresolved_count > len(unresolved),
                "unresolved": unresolved,
            }
    except (OSError, sqlite3.Error) as error:
        return {"status": "unknown", "error_type": type(error).__name__}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = audit_database(args.db_path, limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return {"ready": 0, "blocked": 1}.get(report["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
