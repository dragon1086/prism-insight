"""Build a sanitized dashboard snapshot from PRISM ClickHouse events."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
EVENT_TYPES = {
    "candidate.outcome",
    "deployment.applied",
    "market.regime_snapshot",
    "trade.outcome",
    "trigger.performance_feedback",
}
_CLICKHOUSE_EVENTS_QUERY = """
    SELECT Body
    FROM otel_logs
    WHERE TimestampTime >= now() - INTERVAL {days:UInt16} DAY
      AND LogAttributes['event.name'] IN (
        'candidate.outcome',
        'deployment.applied',
        'market.regime_snapshot',
        'trade.outcome',
        'trigger.performance_feedback'
      )
    FORMAT JSONEachRow
"""


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


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _rounded(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    return wins / gross_loss if gross_loss else None


def _trade_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [
        float(event["attributes"]["profit_rate_pct"])
        for event in events
        if event.get("attributes", {}).get("profit_rate_pct") is not None
    ]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    stop_count = sum(
        any(
            marker in str(event.get("attributes", {}).get("exit_kind") or "").lower()
            for marker in ("stop", "hard", "risk", "손절")
        )
        for event in events
    )
    return {
        "count": len(returns),
        "win_rate": _rounded(len(wins) / len(returns) if returns else None),
        "avg_return_pct": _rounded(_mean(returns)),
        "median_return_pct": _rounded(_median(returns)),
        "avg_win_pct": _rounded(_mean(wins)),
        "avg_loss_pct": _rounded(_mean(losses)),
        "profit_factor": _rounded(_profit_factor(returns)),
        "stop_rate": _rounded(stop_count / len(events) if events else None),
        "sample_sufficient": len(returns) >= 5,
    }


def _candidate_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    watched = [
        event
        for event in events
        if int(event.get("attributes", {}).get("was_traded") or 0) == 0
    ]

    def values(key: str) -> list[float]:
        return [
            float(event["attributes"][key])
            for event in watched
            if event.get("attributes", {}).get(key) is not None
        ]

    returns_7 = values("return_7d_pct")
    returns_14 = values("return_14d_pct")
    returns_30 = values("return_30d_pct")
    return {
        "count": len(returns_30),
        "positive_rate_30d": _rounded(
            sum(value > 0 for value in returns_30) / len(returns_30)
            if returns_30
            else None
        ),
        "avg_7d_pct": _rounded(_mean(returns_7)),
        "median_7d_pct": _rounded(_median(returns_7)),
        "avg_14d_pct": _rounded(_mean(returns_14)),
        "median_14d_pct": _rounded(_median(returns_14)),
        "avg_30d_pct": _rounded(_mean(returns_30)),
        "median_30d_pct": _rounded(_median(returns_30)),
        "sample_sufficient": len(returns_30) >= 5,
    }


def _deduplicate(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in events:
        event = dict(raw)
        event_id = str(event.get("event_id") or "")
        if not event_id or event.get("event_type") not in EVENT_TYPES:
            continue
        existing = by_id.get(event_id)
        if existing is None or str(event.get("timestamp") or "") > str(
            existing.get("timestamp") or ""
        ):
            by_id[event_id] = event
    return list(by_id.values())


def _deployment_records(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") != "deployment.applied":
            continue
        attributes = event.get("attributes") or {}
        git_sha = str(attributes.get("git_sha") or event.get("git_sha") or "")
        target = str(attributes.get("target") or "unknown")
        timestamp = _parse_time(event.get("timestamp"))
        if not git_sha or timestamp is None:
            continue
        record = {
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "git_sha": git_sha,
            "target": target,
            "prs": attributes.get("prs") or [],
            "subject": attributes.get("commit_subject") or attributes.get("subject"),
            "ingestion_mode": attributes.get("ingestion_mode", "live"),
            "verified_actual_deployment": bool(
                attributes.get("verified_actual_deployment")
                or attributes.get("ingestion_mode") != "backfill"
            ),
        }
        key = (git_sha, target)
        existing = records.get(key)
        if existing is None:
            records[key] = record
            continue
        if existing["ingestion_mode"] == "backfill" and record["ingestion_mode"] != "backfill":
            records[key] = record
    return sorted(records.values(), key=lambda item: item["timestamp"])


def _market_snapshot(
    events: list[dict[str, Any]],
    market: str,
) -> dict[str, Any]:
    actual = [
        event
        for event in events
        if event.get("event_type") == "trade.outcome" and event.get("market") == market
    ]
    candidates = [
        event
        for event in events
        if event.get("event_type") == "candidate.outcome"
        and event.get("market") == market
    ]
    regimes = [
        event
        for event in events
        if event.get("event_type") == "market.regime_snapshot"
        and event.get("market") == market
    ]
    triggers = sorted(
        {
            str(event.get("attributes", {}).get("trigger_type"))
            for event in actual + candidates
            if event.get("attributes", {}).get("trigger_type")
        }
    )
    trigger_rows = []
    for trigger in triggers:
        trigger_actual = [
            event
            for event in actual
            if event.get("attributes", {}).get("trigger_type") == trigger
        ]
        trigger_candidates = [
            event
            for event in candidates
            if event.get("attributes", {}).get("trigger_type") == trigger
        ]
        trigger_rows.append(
            {
                "trigger_type": trigger,
                "actual": _trade_metrics(trigger_actual),
                "candidate": _candidate_metrics(trigger_candidates),
            }
        )
    trigger_rows.sort(
        key=lambda row: (
            -(row["actual"]["count"] + row["candidate"]["count"]),
            row["trigger_type"],
        )
    )

    regime_counts = Counter(
        str(event.get("attributes", {}).get("regime") or "unknown")
        for event in regimes
    )
    latest_regime = None
    if regimes:
        latest = max(regimes, key=lambda event: str(event.get("timestamp") or ""))
        latest_regime = {
            "regime": latest.get("attributes", {}).get("regime"),
            "confidence": latest.get("attributes", {}).get("confidence"),
            "observed_at": latest.get("timestamp"),
        }

    return {
        "actual": _trade_metrics(actual),
        "candidate": _candidate_metrics(candidates),
        "triggers": trigger_rows,
        "latest_regime": latest_regime,
        "regime_distribution": [
            {"regime": regime, "count": count}
            for regime, count in regime_counts.most_common()
        ],
    }


def _deployment_impacts(
    events: list[dict[str, Any]],
    deployments: list[dict[str, Any]],
    *,
    now: datetime,
    window_days: int = 14,
) -> list[dict[str, Any]]:
    actual_by_market = {
        market: [
            event
            for event in events
            if event.get("event_type") == "trade.outcome"
            and event.get("market") == market
        ]
        for market in ("KR", "US")
    }
    window = timedelta(days=window_days)
    impacts = []
    for deployment in deployments[-20:]:
        deployed_at = _parse_time(deployment["timestamp"])
        if deployed_at is None:
            continue
        markets = {}
        for market, trades in actual_by_market.items():
            pre = []
            post = []
            for trade in trades:
                buy_at = _parse_time(trade.get("attributes", {}).get("buy_date"))
                if buy_at is None:
                    continue
                if deployed_at - window <= buy_at < deployed_at:
                    pre.append(trade)
                elif deployed_at <= buy_at < deployed_at + window:
                    post.append(trade)
            markets[market] = {
                "pre": _trade_metrics(pre),
                "post": _trade_metrics(post),
            }
        impacts.append(
            {
                **deployment,
                "window_days": window_days,
                "post_window_complete": now >= deployed_at + window,
                "markets": markets,
            }
        )
    return impacts


def build_snapshot(
    raw_events: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    retention_days: int = 180,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    events = _deduplicate(raw_events)
    timestamps = [
        parsed
        for parsed in (_parse_time(event.get("timestamp")) for event in events)
        if parsed is not None
    ]
    deployments = _deployment_records(events)
    backfill_count = sum(
        event.get("attributes", {}).get("ingestion_mode") == "backfill"
        for event in events
    )
    return {
        "schema_version": 1,
        "generated_at": current.isoformat().replace("+00:00", "Z"),
        "retention_days": retention_days,
        "data_quality": {
            "total_events": len(events),
            "backfill_events": backfill_count,
            "live_events": len(events) - backfill_count,
            "coverage_start": min(timestamps).isoformat().replace("+00:00", "Z")
            if timestamps
            else None,
            "last_event_at": max(timestamps).isoformat().replace("+00:00", "Z")
            if timestamps
            else None,
        },
        "markets": {
            market: _market_snapshot(events, market)
            for market in ("KR", "US")
        },
        "deployments": deployments[-50:],
        "deployment_impacts": _deployment_impacts(
            events,
            deployments,
            now=current,
        ),
    }


def load_clickhouse_events(
    endpoint: str,
    *,
    user: str,
    password: str,
    days: int,
) -> list[dict[str, Any]]:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("ClickHouse dashboard endpoint must be local HTTP")
    query = urllib.parse.urlencode({"param_days": max(1, days)})
    url = endpoint.rstrip("/") + "/?" + query
    request = urllib.request.Request(
        url,
        data=_CLICKHOUSE_EVENTS_QUERY.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "X-ClickHouse-User": user,
            "X-ClickHouse-Key": password,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        output = response.read().decode("utf-8")
    events = []
    for line in output.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        body = json.loads(row["Body"])
        if isinstance(body, dict):
            events.append(body)
    return events


def write_snapshot(path: Path, snapshot: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.chmod(0o644)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default=os.getenv("CLICKHOUSE_HTTP_ENDPOINT", "http://127.0.0.1:18123"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/var/lib/prism-observability/exports/observability_insights.json"
        ),
    )
    parser.add_argument("--days", type=int, default=180)
    args = parser.parse_args(argv)

    events = load_clickhouse_events(
        args.endpoint,
        user=os.getenv("CLICKHOUSE_USER", "prism_otel"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        days=args.days,
    )
    snapshot = build_snapshot(events, retention_days=max(1, args.days))
    write_snapshot(args.output, snapshot)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "events": snapshot["data_quality"]["total_events"],
                "generated_at": snapshot["generated_at"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
