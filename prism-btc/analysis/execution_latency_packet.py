"""Build a secret-minimized packet from BTC exchange latency observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PACKET_SCHEMA_VERSION = 1
ANALYSIS_CONTRACT_VERSION = "btc-execution-latency-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


def build_latency_packet(samples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    rows = []
    for raw in samples:
        try:
            latency = max(0.0, float(raw.get("latency_ms") or 0.0))
        except (TypeError, ValueError):
            continue
        row = {
            "mode": str(raw.get("mode") or "unknown"),
            "operation": str(raw.get("operation") or "unknown"),
            "phase": str(raw.get("phase") or "unknown"),
            "order_ref": str(raw.get("order_ref") or "") or None,
            "request_at": raw.get("request_at"),
            "completed_at": raw.get("completed_at"),
            "latency_ms": latency,
            "success": bool(raw.get("success")),
            "retry_count": max(0, int(raw.get("retry_count") or 0)),
            "ret_code": raw.get("ret_code"),
        }
        rows.append(row)
        grouped.setdefault(
            (row["mode"], row["operation"], row["phase"]), []
        ).append(row)
    cohorts = []
    for (mode, operation, phase), members in sorted(grouped.items()):
        latencies = [member["latency_ms"] for member in members]
        ret_codes = sorted(
            {member["ret_code"] for member in members}, key=lambda value: str(value)
        )
        cohorts.append(
            {
                "mode": mode,
                "operation": operation,
                "phase": phase,
                "n": len(members),
                "p50_ms": _percentile(latencies, 0.50),
                "p90_ms": _percentile(latencies, 0.90),
                "p95_ms": _percentile(latencies, 0.95),
                "p99_ms": _percentile(latencies, 0.99),
                "max_ms": round(max(latencies), 3),
                "success_rate": round(
                    sum(member["success"] for member in members) / len(members), 6
                ),
                "retry_rate": round(
                    sum(member["retry_count"] > 0 for member in members)
                    / len(members),
                    6,
                ),
                "ret_code_distribution": {
                    str(code): sum(member["ret_code"] == code for member in members)
                    for code in ret_codes
                },
            }
        )
    packet = {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "sample_count": len(rows),
        "cohorts": cohorts,
        "interpretation": {
            "submit_latency": "client send to exchange acknowledgement",
            "fill_latency": (
                "reconcile detection upper bound, not exchange execution timestamp"
            ),
        },
        "readiness": {
            "automatic_shadow_forbidden": True,
            "automatic_live_forbidden": True,
            "execution_model_change_requires": (
                "sufficient samples, stable percentiles, replay, user approval"
            ),
        },
    }
    packet["packet_id"] = hashlib.sha256(_canonical(packet).encode("utf-8")).hexdigest()[:24]
    return packet


def _read_samples(path: Path, mode: str | None) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query = "SELECT * FROM btc_execution_samples"
    params: tuple[Any, ...] = ()
    if mode:
        query += " WHERE mode=?"
        params = (mode,)
    query += " ORDER BY id"
    try:
        rows = [dict(row) for row in connection.execute(query, params).fetchall()]
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error).lower():
            raise
        rows = []
    connection.close()
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-db", type=Path, required=True)
    parser.add_argument("--mode", choices=("demo", "live"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    packet = build_latency_packet(_read_samples(args.root_db, args.mode))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"packet_id": packet["packet_id"], "sample_count": packet["sample_count"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
