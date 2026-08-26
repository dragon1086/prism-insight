"""Append one operational event to the PRISM observability spool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from observability.events import emit_event


def _value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_type")
    parser.add_argument("--service", default="prism-operations")
    parser.add_argument("--market")
    parser.add_argument("--ticker")
    parser.add_argument("--trace-id")
    parser.add_argument("--decision-id")
    parser.add_argument("--position-id")
    parser.add_argument(
        "--attribute",
        action="append",
        default=[],
        metavar="KEY=VALUE",
    )
    args = parser.parse_args()

    attributes = {}
    for item in args.attribute:
        if "=" not in item:
            parser.error(f"invalid --attribute {item!r}; expected KEY=VALUE")
        key, raw = item.split("=", 1)
        attributes[key] = _value(raw)

    event = emit_event(
        args.event_type,
        service=args.service,
        market=args.market,
        ticker=args.ticker,
        trace_id=args.trace_id,
        decision_id=args.decision_id,
        position_id=args.position_id,
        attributes=attributes,
    )
    if event is None:
        print("event append failed", file=sys.stderr)
        return 1
    print(event["event_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
