"""Backfill trustworthy PRISM facts into the observability JSONL spool.

This intentionally does not reconstruct historical prompts, gates, or traces.
Only immutable SQLite outcomes, recorded regime snapshots, and server reflog
deployments are emitted. Every event is marked as a backfill and receives a
deterministic ID so repeated runs are safe with the local state file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from observability.events import emit_event

KST = ZoneInfo("Asia/Seoul")
BACKFILL_VERSION = 1
DEFAULT_DB = PROJECT_ROOT / "stock_tracking_db.sqlite"
DEFAULT_REGIME_LOG = PROJECT_ROOT / "logs" / "regime_history.jsonl"
DEFAULT_STATE = PROJECT_ROOT / "logs" / "observability_backfill_state.json"

_ACTUAL_TABLES = {
    "KR": "trading_history",
    "US": "us_trading_history",
}
_ACTUAL_QUERIES = {
    "KR": """
        SELECT id, ticker, company_name, buy_price, buy_date, sell_price,
               sell_date, profit_rate, holding_days, scenario, trigger_type,
               trigger_mode, sector, exit_kind
        FROM trading_history
        ORDER BY id
    """,
    "US": """
        SELECT id, ticker, company_name, buy_price, buy_date, sell_price,
               sell_date, profit_rate, holding_days, scenario, trigger_type,
               trigger_mode, sector, exit_kind
        FROM us_trading_history
        ORDER BY id
    """,
}
_CANDIDATE_TABLES = {
    "KR": "analysis_performance_tracker",
    "US": "us_analysis_performance_tracker",
}
_CANDIDATE_QUERIES = {
    "KR": """
        SELECT id, decision_id, ticker, company_name, trigger_type, trigger_mode,
               analyzed_date AS analysis_date, decision, was_traded,
               skip_reason, buy_score, min_score, target_price, stop_loss,
               risk_reward_ratio, tracked_7d_return AS return_7d,
               tracked_14d_return AS return_14d,
               tracked_30d_return AS return_30d,
               tracked_30d_date AS outcome_observed_at,
               NULL AS hit_target, NULL AS hit_stop_loss, NULL AS sector
        FROM analysis_performance_tracker
        WHERE tracked_30d_return IS NOT NULL
        ORDER BY id
    """,
    "US": """
        SELECT id, decision_id, ticker, company_name, trigger_type, trigger_mode,
               analysis_date, decision, was_traded, skip_reason, buy_score,
               NULL AS min_score, target_price, stop_loss,
               risk_reward_ratio, return_7d, return_14d, return_30d,
               last_updated AS outcome_observed_at,
               hit_target, hit_stop_loss, sector
        FROM us_analysis_performance_tracker
        WHERE return_30d IS NOT NULL
        ORDER BY id
    """,
}
_CANDIDATE_LEGACY_QUERIES = {
    market: query.replace("id, decision_id,", "id, NULL AS decision_id,")
    for market, query in _CANDIDATE_QUERIES.items()
}
_CANDIDATE_TABLE_INFO_QUERIES = {
    "KR": "PRAGMA table_info(analysis_performance_tracker)",
    "US": "PRAGMA table_info(us_analysis_performance_tracker)",
}


def _event_id(source_key: str) -> str:
    return hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:32]


def _parse_time(value: Any, *, default_timezone=KST) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed.astimezone(timezone.utc)


def _hash_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _base_attributes(source_table: str, source_id: Any) -> dict[str, Any]:
    return {
        "ingestion_mode": "backfill",
        "backfill_version": BACKFILL_VERSION,
        "source": "production_sqlite",
        "source_table": source_table,
        "source_id": str(source_id),
    }


def iter_actual_events(
    connection: sqlite3.Connection,
    market: str,
    *,
    since: datetime,
) -> Iterator[dict[str, Any]]:
    table = _ACTUAL_TABLES[market]
    connection.row_factory = sqlite3.Row
    for row in connection.execute(_ACTUAL_QUERIES[market]):
        event_time = _parse_time(row["sell_date"])
        if event_time is None or event_time < since:
            continue
        source_key = f"sqlite:{table}:{row['id']}:v{BACKFILL_VERSION}"
        attributes = _base_attributes(table, row["id"])
        attributes.update(
            {
                "company_name": row["company_name"],
                "buy_price": row["buy_price"],
                "buy_date": row["buy_date"],
                "sell_price": row["sell_price"],
                "sell_date": row["sell_date"],
                "profit_rate_pct": row["profit_rate"],
                "holding_days": row["holding_days"],
                "scenario_hash": _hash_text(row["scenario"]),
                "trigger_type": row["trigger_type"],
                "trigger_mode": row["trigger_mode"],
                "sector": row["sector"],
                "exit_kind": row["exit_kind"],
            }
        )
        yield {
            "event_type": "trade.outcome",
            "event_id": _event_id(source_key),
            "service": f"prism-{market.lower()}-history",
            "market": market,
            "ticker": row["ticker"],
            "position_id": f"legacy:{market}:{row['id']}",
            "event_time": event_time,
            "attributes": attributes,
        }


def iter_candidate_events(
    connection: sqlite3.Connection,
    market: str,
    *,
    since: datetime,
) -> Iterator[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    table = _CANDIDATE_TABLES[market]
    columns = {
        str(row[1])
        for row in connection.execute(
            _CANDIDATE_TABLE_INFO_QUERIES[market]
        ).fetchall()
    }
    query = (
        _CANDIDATE_QUERIES[market]
        if "decision_id" in columns
        else _CANDIDATE_LEGACY_QUERIES[market]
    )
    for row in connection.execute(query):
        event_time = _parse_time(row["analysis_date"])
        if event_time is None or event_time < since:
            continue
        source_key = f"sqlite:{table}:{row['id']}:v{BACKFILL_VERSION}"
        attributes = _base_attributes(table, row["id"])
        attributes.update(
            {
                "company_name": row["company_name"],
                "analysis_date": row["analysis_date"],
                "outcome_observed_at": row["outcome_observed_at"],
                "trigger_type": row["trigger_type"],
                "trigger_mode": row["trigger_mode"],
                "sector": row["sector"],
                "decision": row["decision"],
                "was_traded": int(row["was_traded"] or 0),
                "skip_reason": row["skip_reason"],
                "buy_score": row["buy_score"],
                "min_score": row["min_score"],
                "target_price": row["target_price"],
                "stop_loss": row["stop_loss"],
                "risk_reward_ratio": row["risk_reward_ratio"],
                "return_7d_pct": (
                    float(row["return_7d"]) * 100
                    if row["return_7d"] is not None
                    else None
                ),
                "return_14d_pct": (
                    float(row["return_14d"]) * 100
                    if row["return_14d"] is not None
                    else None
                ),
                "return_30d_pct": float(row["return_30d"]) * 100,
                "hit_target": row["hit_target"],
                "hit_stop_loss": row["hit_stop_loss"],
            }
        )
        yield {
            "event_type": "candidate.outcome",
            "event_id": _event_id(source_key),
            "service": f"prism-{market.lower()}-candidate-history",
            "market": market,
            "ticker": row["ticker"],
            "decision_id": row["decision_id"]
            or f"legacy-candidate:{market}:{row['id']}",
            "event_time": event_time,
            "attributes": attributes,
        }


def iter_regime_events(path: Path, *, since: datetime) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event_time = _parse_time(row.get("ts"))
        market = str(row.get("market") or "").upper()
        if event_time is None or event_time < since or market not in {"KR", "US"}:
            continue
        digest = hashlib.sha256(raw.encode()).hexdigest()
        source_key = f"regime:{digest}:v{BACKFILL_VERSION}"
        attributes = {
            "ingestion_mode": "backfill",
            "backfill_version": BACKFILL_VERSION,
            "source": "regime_history_jsonl",
            "source_line": line_number,
            **{
                key: value
                for key, value in row.items()
                if key not in {"ts", "market"}
            },
        }
        yield {
            "event_type": "market.regime_snapshot",
            "event_id": _event_id(source_key),
            "service": "prism-regime-history",
            "market": market,
            "event_time": event_time,
            "attributes": attributes,
        }


def iter_deployment_events(
    repo: Path,
    *,
    since: datetime,
) -> Iterator[dict[str, Any]]:
    dot_git = repo / ".git"
    if dot_git.is_dir():
        git_dir = dot_git
    elif dot_git.is_file():
        marker = dot_git.read_text(encoding="utf-8").strip()
        if not marker.startswith("gitdir:"):
            return
        git_dir = (repo / marker.split(":", 1)[1].strip()).resolve()
    else:
        return
    reflog = git_dir / "logs" / "HEAD"
    if not reflog.exists():
        return

    for raw in reflog.read_text(encoding="utf-8").splitlines():
        metadata, separator, message = raw.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) < 4:
            continue
        git_sha = fields[1]
        if "pull" not in message and "merge origin/main" not in message:
            continue
        try:
            epoch_seconds = int(fields[-2])
            offset_text = fields[-1]
            sign = 1 if offset_text.startswith("+") else -1
            offset = timedelta(
                hours=int(offset_text[1:3]),
                minutes=int(offset_text[3:5]),
            )
            reflog_timezone = timezone(sign * offset)
            event_time = datetime.fromtimestamp(
                epoch_seconds,
                tz=reflog_timezone,
            ).astimezone(timezone.utc)
        except (ValueError, IndexError):
            continue
        if event_time < since:
            continue
        source_key = (
            f"reflog:{epoch_seconds}:{git_sha}:{message}:"
            f"v{BACKFILL_VERSION}"
        )
        yield {
            "event_type": "deployment.applied",
            "event_id": _event_id(source_key),
            "service": "prism-deployment-history",
            "event_time": event_time,
            "attributes": {
                "ingestion_mode": "backfill",
                "backfill_version": BACKFILL_VERSION,
                "source": "git_reflog",
                "verified_actual_deployment": True,
                "target": "db-server",
                "git_sha": git_sha,
                "reflog_message": message,
            },
        }


def load_state(path: Path) -> set[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {str(item) for item in value.get("emitted_event_ids", [])}
    except (OSError, TypeError, json.JSONDecodeError):
        return set()


def save_state(path: Path, event_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "backfill_version": BACKFILL_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "emitted_event_ids": sorted(event_ids),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def emit_backfill(
    events: Iterable[Mapping[str, Any]],
    *,
    emitted_ids: set[str],
    dry_run: bool,
) -> dict[str, int]:
    counts = {"discovered": 0, "emitted": 0, "skipped": 0, "failed": 0}
    for event in events:
        counts["discovered"] += 1
        event_id = str(event["event_id"])
        if event_id in emitted_ids:
            counts["skipped"] += 1
            continue
        if dry_run:
            counts["emitted"] += 1
            continue
        result = emit_event(**event)
        if result is None:
            counts["failed"] += 1
            continue
        emitted_ids.add(event_id)
        counts["emitted"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--regime-log", type=Path, default=DEFAULT_REGIME_LOG)
    parser.add_argument("--repo", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    since = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    emitted_ids = load_state(args.state)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        groups = {
            "kr_actual": iter_actual_events(connection, "KR", since=since),
            "us_actual": iter_actual_events(connection, "US", since=since),
            "kr_candidate": iter_candidate_events(connection, "KR", since=since),
            "us_candidate": iter_candidate_events(connection, "US", since=since),
            "regime": iter_regime_events(args.regime_log, since=since),
            "deployment": iter_deployment_events(args.repo, since=since),
        }
        summary = {
            name: emit_backfill(
                events,
                emitted_ids=emitted_ids,
                dry_run=args.dry_run,
            )
            for name, events in groups.items()
        }
    finally:
        connection.close()
    if not args.dry_run:
        save_state(args.state, emitted_ids)
    print(
        json.dumps(
            {
                "since": since.isoformat(),
                "dry_run": args.dry_run,
                "groups": summary,
            },
            indent=2,
        )
    )
    return 1 if any(group["failed"] for group in summary.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
