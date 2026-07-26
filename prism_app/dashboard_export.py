"""Read-only export contract for the localhost Phase 1 dashboard.

The exporter reads the authoritative research, internal-paper, and operations stores
through separate SQLite connections.  It never imports a broker/account adapter and
never exposes account-shaped fields.  Missing persisted research/OOS data is shown as
unavailable rather than fabricated.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import quote

SCHEMA_VERSION = "prism_dashboard_v1"
LOCAL_BIND_HOST = "127.0.0.1"
_STRATEGIES = ("SWING_V1", "TREND_V1")
_REQUIRED_TABLES = {
    "research": frozenset(
        {
            "market_snapshots",
            "observations",
            "decision_snapshots",
            "trade_plan_proposals",
            "proposal_outcomes",
            "lesson_candidates",
        }
    ),
    "paper": frozenset(
        {"strategy_books", "positions", "nav_snapshots", "paper_orders"}
    ),
    "ops": frozenset({"job_runs", "heartbeats"}),
}


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _json_object(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("persisted dashboard JSON must be an object")
    return parsed


def _tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    )


def _validate_boundary(connection: sqlite3.Connection, kind: str) -> None:
    missing = _REQUIRED_TABLES[kind] - _tables(connection)
    if missing:
        raise RuntimeError(f"{kind} dashboard store is missing tables: {sorted(missing)}")
    connection.execute("PRAGMA query_only = ON")


@contextmanager
def open_read_only_database(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open an existing SQLite file without creating, migrating, or mutating it."""

    resolved = Path(path).expanduser().resolve(strict=True)
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    finally:
        connection.close()


class DashboardExporter:
    """Build one provenance-preserving Phase 1 dashboard read model."""

    def __init__(
        self,
        research_connection: sqlite3.Connection,
        paper_connection: sqlite3.Connection,
        ops_connection: sqlite3.Connection,
    ) -> None:
        self._research = research_connection
        self._paper = paper_connection
        self._ops = ops_connection
        for connection, kind in (
            (research_connection, "research"),
            (paper_connection, "paper"),
            (ops_connection, "ops"),
        ):
            if not isinstance(connection, sqlite3.Connection):
                raise TypeError(f"{kind}_connection must be sqlite3.Connection")
            _validate_boundary(connection, kind)

    def build(self, *, as_of: datetime, generated_at: datetime | None = None) -> dict[str, Any]:
        boundary = _aware_utc(as_of, "as_of")
        generated = _aware_utc(
            generated_at or datetime.now(timezone.utc), "generated_at"
        )
        boundary_text = boundary.isoformat()
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated.isoformat(),
            "as_of": boundary_text,
            "local_only": True,
            "bind_host": LOCAL_BIND_HOST,
            "research": self._research_contract(boundary_text),
            "paper": self._paper_contract(boundary_text),
            "ops": self._ops_contract(boundary_text),
        }

    def _research_contract(self, boundary: str) -> dict[str, Any]:
        proposals = {
            strategy: self._proposals(strategy, boundary) for strategy in _STRATEGIES
        }
        return {
            "data_freshness": self._freshness(boundary),
            "daily_leaders": self._leaders(boundary),
            "swing_v1_proposals": proposals["SWING_V1"],
            "trend_v1_proposals": proposals["TREND_V1"],
            "scenario_evidence_falsifiers": [
                {
                    "proposal_record_id": item["proposal_record_id"],
                    "strategy_id": item["strategy_id"],
                    "market": item["market"],
                    "security_id": item["security_id"],
                    "scenario": item["scenario"],
                    "bull_evidence_ids": item["bull_evidence_ids"],
                    "bear_evidence_ids": item["bear_evidence_ids"],
                    "falsifiers": item["falsifiers"],
                    "uncertainty": item["uncertainty"],
                }
                for strategy in _STRATEGIES
                for item in proposals[strategy]
            ],
            "research_oos": {
                "status": "UNAVAILABLE",
                "reason": (
                    "No persistent experiment/OOS dashboard read contract exists; "
                    "results are not inferred from in-memory research state."
                ),
                "experiments": [],
            },
            "shadow_feedback": self._shadow_feedback(boundary),
        }

    def _freshness(self, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT s.market, s.snapshot_id, s.as_of_date, s.quality,
                   MAX(o.observed_at), MAX(o.available_at), MAX(o.ingested_at)
            FROM market_snapshots AS s
            LEFT JOIN observations AS o
              ON o.snapshot_id = s.snapshot_id AND o.available_at <= ?
            WHERE s.as_of_date <= ?
              AND NOT EXISTS (
                SELECT 1 FROM market_snapshots AS newer
                WHERE newer.market = s.market
                  AND newer.as_of_date <= ?
                  AND (newer.as_of_date > s.as_of_date OR
                       (newer.as_of_date = s.as_of_date AND newer.snapshot_id > s.snapshot_id))
              )
            GROUP BY s.market, s.snapshot_id, s.as_of_date, s.quality
            ORDER BY s.market
            """,
            (boundary, boundary, boundary),
        ).fetchall()
        return [
            {
                "market": row[0],
                "snapshot_id": row[1],
                "as_of": row[2],
                "quality": row[3],
                "observed_at": row[4],
                "available_at": row[5],
                "ingested_at": row[6],
            }
            for row in rows
        ]

    def _leaders(self, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT s.market, o.snapshot_id, o.security_id, o.provider,
                   o.provider_symbol, o.observed_at, o.available_at, o.ingested_at,
                   s.quality, o.payload_json
            FROM observations AS o
            JOIN market_snapshots AS s USING (snapshot_id)
            WHERE o.observation_kind = 'leadership_security_state'
              AND o.available_at <= ? AND s.as_of_date <= ?
              AND NOT EXISTS (
                SELECT 1 FROM market_snapshots AS later_snapshot
                WHERE later_snapshot.market = s.market
                  AND later_snapshot.as_of_date <= ?
                  AND (later_snapshot.as_of_date > s.as_of_date OR
                       (later_snapshot.as_of_date = s.as_of_date
                        AND later_snapshot.snapshot_id > s.snapshot_id))
              )
              AND NOT EXISTS (
                SELECT 1 FROM observations AS newer
                WHERE newer.provider = o.provider
                  AND newer.source_record_id = o.source_record_id
                  AND newer.available_at <= ?
                  AND (newer.revision > o.revision OR
                       (newer.revision = o.revision AND newer.observation_id > o.observation_id))
              )
            ORDER BY s.market, o.provider_symbol, o.security_id
            """,
            (boundary, boundary, boundary, boundary),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_object(row[9])
            result.append(
                {
                    "market": row[0],
                    "snapshot_id": row[1],
                    "security_id": row[2],
                    "provider": row[3],
                    "provider_symbol": row[4],
                    "observed_at": row[5],
                    "available_at": row[6],
                    "ingested_at": row[7],
                    "quality": row[8],
                    "symbol": payload.get("symbol"),
                    "name": payload.get("name"),
                    "decision_status": payload.get("decision_status"),
                    "strategies": payload.get("strategies", []),
                    "relative_strength": payload.get("relative_strength", {}),
                    "high_52_week": payload.get("high_52_week", {}),
                    "momentum": payload.get("momentum", {}),
                    "peak": payload.get("peak", {}),
                    "evidence_refs": payload.get("evidence_refs", []),
                }
            )
        return result

    def _proposals(self, strategy: str, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT p.proposal_record_id, p.proposal_id, p.revision,
                   p.strategy_id, p.strategy_version, d.market, d.security_id,
                   d.data_snapshot_id, d.evidence_refs_json, d.data_quality,
                   d.quality_disposition, p.proposed_decision,
                   p.normalized_proposal_json, p.model_provider, p.model_id,
                   p.model_version, p.prompt_version, p.available_at
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS d USING (decision_snapshot_id)
            WHERE p.strategy_id = ?
              AND p.available_at <= ? AND p.as_of_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM trade_plan_proposals AS newer
                WHERE newer.proposal_key = p.proposal_key
                  AND newer.strategy_id = p.strategy_id
                  AND newer.strategy_version = p.strategy_version
                  AND newer.available_at <= ? AND newer.as_of_at <= ?
                  AND newer.revision > p.revision
              )
            ORDER BY p.proposal_key
            """,
            (strategy, boundary, boundary, boundary, boundary),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            normalized = _json_object(row[12])
            regime = normalized.get("regime", {})
            if not isinstance(regime, Mapping):
                regime = {}
            falsifiers = regime.get("falsifiers", [])
            result.append(
                {
                    "proposal_record_id": row[0],
                    "proposal_id": row[1],
                    "revision": row[2],
                    "strategy_id": row[3],
                    "strategy_version": row[4],
                    "market": row[5],
                    "security_id": row[6],
                    "snapshot_id": row[7],
                    "evidence_refs": json.loads(row[8]),
                    "data_quality": row[9],
                    "quality_disposition": row[10],
                    "proposed_decision": row[11],
                    "scenario": dict(regime),
                    "bull_evidence_ids": normalized.get("bull_evidence_ids", []),
                    "bear_evidence_ids": normalized.get("bear_evidence_ids", []),
                    "falsifiers": falsifiers if isinstance(falsifiers, list) else [],
                    "missing_or_stale_data": normalized.get("missing_or_stale_data", []),
                    "uncertainty": normalized.get("uncertainty", {}),
                    "model": {
                        "provider": row[13],
                        "model_id": row[14],
                        "model_version": row[15],
                        "prompt_version": row[16],
                    },
                    "available_at": row[17],
                }
            )
        return result

    def _shadow_feedback(self, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT c.lesson_id, c.strategy_id, c.strategy_version, c.revision,
                   c.status, c.candidate_json, c.observed_at, c.available_at,
                   c.ingested_at, c.as_of_at
            FROM lesson_candidates AS c
            WHERE c.available_at <= ? AND c.as_of_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM lesson_candidates AS newer
                WHERE newer.lesson_id = c.lesson_id
                  AND newer.strategy_id = c.strategy_id
                  AND newer.strategy_version = c.strategy_version
                  AND newer.available_at <= ? AND newer.as_of_at <= ?
                  AND newer.revision > c.revision
              )
            ORDER BY c.strategy_id, c.lesson_id
            """,
            (boundary, boundary, boundary, boundary),
        ).fetchall()
        return [
            {
                "lesson_id": row[0],
                "strategy_id": row[1],
                "strategy_version": row[2],
                "revision": row[3],
                "status": row[4],
                "candidate": _json_object(row[5]),
                "observed_at": row[6],
                "available_at": row[7],
                "ingested_at": row[8],
                "as_of": row[9],
            }
            for row in rows
        ]

    def _paper_contract(self, boundary: str) -> dict[str, Any]:
        books = self._paper.execute(
            "SELECT book_id, strategy_id, market, currency, created_at "
            "FROM strategy_books WHERE created_at <= ? ORDER BY strategy_id, market",
            (boundary,),
        ).fetchall()
        result = []
        for book in books:
            position_rows = self._paper.execute(
                """
                SELECT p.security_id, p.quantity, p.average_cost, p.as_of_at
                FROM positions AS p
                WHERE p.book_id = ? AND p.as_of_at <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM positions AS newer
                    WHERE newer.book_id = p.book_id
                      AND newer.security_id = p.security_id
                      AND newer.as_of_at <= ? AND newer.as_of_at > p.as_of_at
                  )
                ORDER BY p.security_id
                """,
                (book[0], boundary, boundary),
            ).fetchall()
            nav = self._paper.execute(
                "SELECT nav, cash, as_of_at FROM nav_snapshots "
                "WHERE book_id = ? AND as_of_at <= ? "
                "ORDER BY as_of_at DESC, nav_snapshot_id DESC LIMIT 1",
                (book[0], boundary),
            ).fetchone()
            states = self._paper.execute(
                "SELECT order_state, COUNT(*) FROM paper_orders "
                "WHERE book_id = ? AND occurred_at <= ? GROUP BY order_state "
                "ORDER BY order_state",
                (book[0], boundary),
            ).fetchall()
            result.append(
                {
                    "book_id": book[0],
                    "strategy_id": book[1],
                    "market": book[2],
                    "currency": book[3],
                    "created_at": book[4],
                    "positions": [
                        {
                            "security_id": row[0],
                            "quantity": row[1],
                            "average_cost": row[2],
                            "as_of": row[3],
                        }
                        for row in position_rows
                    ],
                    "nav": None
                    if nav is None
                    else {"value": nav[0], "cash": nav[1], "as_of": nav[2]},
                    "order_state_counts": {row[0]: row[1] for row in states},
                }
            )
        return {
            "environment": "INTERNAL_PAPER",
            "external_broker": False,
            "readiness": (
                "FOUNDATION_ONLY" if not result else "PERSISTED_LEDGER_READ_ONLY"
            ),
            "books": result,
        }

    def _ops_contract(self, boundary: str) -> dict[str, Any]:
        rows = self._ops.execute(
            """
            SELECT r.run_id, r.job_key, r.status, r.started_at, r.finished_at,
                   (SELECT MAX(h.observed_at) FROM heartbeats AS h
                    WHERE h.run_id = r.run_id) AS last_heartbeat
            FROM job_runs AS r
            WHERE r.started_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM job_runs AS newer
                WHERE newer.job_key = r.job_key AND newer.started_at <= ?
                  AND (newer.started_at > r.started_at OR
                       (newer.started_at = r.started_at AND newer.run_id > r.run_id))
              )
            ORDER BY r.job_key
            """,
            (boundary, boundary),
        ).fetchall()
        return {
            "jobs": [
                {
                    "run_id": row[0],
                    "job_key": row[1],
                    "status": row[2],
                    "started_at": row[3],
                    "finished_at": row[4],
                    "last_heartbeat": row[5],
                }
                for row in rows
            ]
        }


def export_dashboard(
    *,
    research_db: str | Path,
    paper_db: str | Path,
    ops_db: str | Path,
    output_path: str | Path,
    as_of: datetime | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Read three existing stores and atomically replace one dashboard JSON file."""

    boundary = _aware_utc(as_of or datetime.now(timezone.utc), "as_of")
    with ExitStack() as stack:
        exporter = DashboardExporter(
            stack.enter_context(open_read_only_database(research_db)),
            stack.enter_context(open_read_only_database(paper_db)),
            stack.enter_context(open_read_only_database(ops_db)),
        )
        payload = exporter.build(as_of=boundary, generated_at=generated_at)

    destination = Path(output_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def dashboard_export_main(
    argv: list[str] | None = None, *, default_output: str | Path
) -> int:
    """CLI shared by the legacy KR/US script names during migration."""

    parser = argparse.ArgumentParser(
        description=(
            "Export the separate Phase 1 research, internal-paper, and ops stores "
            "for the localhost-only dashboard."
        )
    )
    parser.add_argument("--research-db", required=True, type=Path)
    parser.add_argument("--paper-db", required=True, type=Path)
    parser.add_argument("--ops-db", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path(default_output))
    parser.add_argument(
        "--as-of",
        type=datetime.fromisoformat,
        help="Timezone-aware ISO-8601 point-in-time boundary (default: current UTC)",
    )
    args = parser.parse_args(argv)
    export_dashboard(
        research_db=args.research_db,
        paper_db=args.paper_db,
        ops_db=args.ops_db,
        output_path=args.output,
        as_of=args.as_of,
    )
    return 0
