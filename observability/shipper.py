"""Ship PRISM JSONL events to an OTLP/HTTP logs endpoint.

The checkpoint advances only after a successful batch. Invalid local lines are
skipped so one corrupt record cannot stop later trading telemetry.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import DEFAULT_SPOOL_PATH

DEFAULT_ENDPOINT = "http://127.0.0.1:14318/v1/logs"
DEFAULT_CHECKPOINT = DEFAULT_SPOOL_PATH.with_suffix(".checkpoint.json")
MAX_LINE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Checkpoint:
    inode: int
    offset: int


def load_checkpoint(path: Path) -> Checkpoint | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(inode=int(value["inode"]), offset=int(value["offset"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"inode": checkpoint.inode, "offset": checkpoint.offset}),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_batch(
    spool_path: Path,
    checkpoint: Checkpoint | None,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], Checkpoint | None]:
    try:
        stat = spool_path.stat()
    except FileNotFoundError:
        return [], checkpoint

    offset = checkpoint.offset if checkpoint and checkpoint.inode == stat.st_ino else 0
    if offset > stat.st_size:
        offset = 0

    events: list[dict[str, Any]] = []
    final_offset = offset
    with spool_path.open("rb") as stream:
        stream.seek(offset)
        while len(events) < limit:
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            final_offset = stream.tell()
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                continue
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                events.append(value)

    return events, Checkpoint(inode=stat.st_ino, offset=final_offset)


def _otel_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": "" if value is None else str(value)}


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _otel_value(value)}
        for key, value in values.items()
        if value is not None
    ]


def build_otlp_payload(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = []
    first: dict[str, Any] | None = None
    for event in events:
        if first is None:
            first = event
        payload_attributes = event.get("attributes") or {}
        records.append(
            {
                "timeUnixNano": str(event.get("time_unix_nano") or "0"),
                "observedTimeUnixNano": str(time.time_ns()),
                "severityText": str(event.get("severity") or "INFO"),
                "body": {
                    "stringValue": json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                },
                "attributes": _attributes(
                    {
                        "event.name": event.get("event_type"),
                        "event.id": event.get("event_id"),
                        "prism.market": event.get("market"),
                        "prism.ticker": event.get("ticker"),
                        "prism.git_sha": event.get("git_sha"),
                        "prism.policy_version": event.get("policy_version"),
                        "prism.config_hash": event.get("config_hash"),
                        "prism.decision_id": event.get("decision_id"),
                        "prism.position_id": event.get("position_id"),
                        "prism.trigger_type": payload_attributes.get("trigger_type"),
                        "prism.feedback_mode": payload_attributes.get("mode"),
                        "prism.applied_adjust": payload_attributes.get("applied_adjust"),
                    }
                ),
                "traceId": event.get("trace_id"),
                "spanId": event.get("span_id"),
            }
        )

    resource = first or {}
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": _attributes(
                        {
                            "service.name": resource.get("service", "prism-trading"),
                            "service.version": resource.get("git_sha"),
                            "deployment.environment": resource.get("environment"),
                            "host.name": resource.get("host"),
                        }
                    )
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "prism.observability", "version": "1"},
                        "logRecords": records,
                    }
                ],
            }
        ]
    }


def send_batch(
    events: list[dict[str, Any]],
    *,
    endpoint: str,
    timeout: float,
    auth_token: str | None = None,
) -> None:
    encoded = json.dumps(build_otlp_payload(events), ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(endpoint, data=encoded, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"OTLP endpoint returned HTTP {response.status}")


def run_once(
    *,
    spool_path: Path,
    checkpoint_path: Path,
    endpoint: str,
    batch_size: int,
    timeout: float,
    auth_token: str | None = None,
) -> int:
    checkpoint = load_checkpoint(checkpoint_path)
    events, next_checkpoint = read_batch(spool_path, checkpoint, limit=batch_size)
    if next_checkpoint is None:
        return 0
    if events:
        send_batch(events, endpoint=endpoint, timeout=timeout, auth_token=auth_token)
    save_checkpoint(checkpoint_path, next_checkpoint)
    return len(events)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spool",
        type=Path,
        default=Path(os.getenv("PRISM_OBSERVABILITY_SPOOL", str(DEFAULT_SPOOL_PATH))),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path(
            os.getenv("PRISM_OBSERVABILITY_CHECKPOINT", str(DEFAULT_CHECKPOINT))
        ),
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("PRISM_OBSERVABILITY_OTLP_ENDPOINT", DEFAULT_ENDPOINT),
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    auth_token = os.getenv("PRISM_OBSERVABILITY_OTLP_TOKEN")
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        try:
            shipped = run_once(
                spool_path=args.spool,
                checkpoint_path=args.checkpoint,
                endpoint=args.endpoint,
                batch_size=max(1, args.batch_size),
                timeout=max(0.1, args.timeout),
                auth_token=auth_token,
            )
            if shipped:
                print(f"shipped={shipped}", flush=True)
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            print(f"ship_failed={type(error).__name__}: {error}", file=sys.stderr, flush=True)
            if args.once:
                return 1

        if args.once or stopping:
            return 0
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
