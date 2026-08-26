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
import re
import sqlite3
import subprocess
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
    table = "trading_history" if market == "KR" else "us_trading_history"
    columns = (
        "id, ticker, company_name, buy_price, buy_date, sell_price, sell_date, "
        "profit_rate, holding_days, scenario, trigger_type, trigger_mode, "
        "sector, exit_kind"
    )
    connection.row_factory = sqlite3.Row
    for row in connection.execute(f"SELECT {columns} FROM {table} ORDER BY id"):
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
    if market == "KR":
        table = "analysis_performance_tracker"
        query = f"""
            SELECT id, ticker, company_name, trigger_type, trigger_mode,
                   analyzed_date AS analysis_date, decision, was_traded,
                   skip_reason, buy_score, min_score, target_price, stop_loss,
                   risk_reward_ratio, tracked_7d_return AS return_7d,
                   tracked_14d_return AS return_14d,
                   tracked_30d_return AS return_30d,
                   tracked_30d_date AS outcome_observed_at,
                   NULL AS hit_target, NULL AS hit_stop_loss, NULL AS sector
            FROM {table}
            WHERE tracked_30d_return IS NOT NULL
            ORDER BY id
        """
    else:
        table = "us_analysis_performance_tracker"
        query = f"""
            SELECT id, ticker, company_name, trigger_type, trigger_mode,
                   analysis_date, decision, was_traded, skip_reason, buy_score,
                   NULL AS min_score, target_price, stop_loss,
                   risk_reward_ratio, return_7d, return_14d, return_30d,
                   last_updated AS outcome_observed_at,
                   hit_target, hit_stop_loss, sector
            FROM {table}
            WHERE return_30d IS NOT NULL
            ORDER BY id
        """

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
            "decision_id": f"legacy-candidate:{market}:{row['id']}",
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
    subjects_result = subprocess.run(
        ["git", "log", "--all", "--format=%H%x1f%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subjects = {}
    for row in subjects_result.stdout.splitlines():
        sha, separator, subject = row.partition("\x1f")
        if separator:
            subjects[sha] = subject
    result = subprocess.run(
        [
            "git",
            "reflog",
            "--date=iso",
            "--format=%gd%x1f%H%x1f%gs",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    for raw in result.stdout.splitlines():
        parts = raw.split("\x1f", 2)
        if len(parts) != 3:
            continue
        selector, git_sha, message = parts
        if "pull" not in message and "merge origin/main" not in message:
            continue
        if "@{" not in selector or not selector.endswith("}"):
            continue
        event_time = _parse_time(selector.split("@{", 1)[1][:-1])
        if event_time is None or event_time < since:
            continue
        source_key = f"reflog:{selector}:{git_sha}:v{BACKFILL_VERSION}"
        subject = subjects.get(git_sha, "")
        prs = sorted(
            {
                int(match)
                for match in re.findall(
                    r"(?:pull request |PR\s*)#(\d+)",
                    subject,
                    re.IGNORECASE,
                )
            }
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
                "commit_subject": subject,
                "prs": prs,
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
