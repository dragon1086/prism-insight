"""Explicit read-only DART diagnostic. No trading, auth or automatic execution."""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path


def _receipt(value):
    """Keep provenance/metrics, not source text or full documents in artifacts."""
    if isinstance(value, dict):
        return {key: _receipt(item) for key, item in value.items()
                if "preview" not in key.lower() and "html" not in key.lower()
                and key.lower() not in {"body", "raw_content", "raw", "text"}}
    if isinstance(value, list):
        return [_receipt(item) for item in value]
    return value


def main(argv=None, *, collector=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly permit public DART HTTP reads")
    parser.add_argument("--corp-code", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--scope", choices=("consolidated", "standalone"), required=True)
    parser.add_argument("--out", type=Path, help="Create a new JSON receipt; existing files are never replaced")
    args = parser.parse_args(argv)
    try:
        cutoff = datetime.fromisoformat(args.decision_at.replace("Z", "+00:00"))
        start = date.fromisoformat(args.start_date)
        if cutoff.utcoffset() is None or not re.fullmatch(r"[0-9]{8}", args.corp_code):
            raise ValueError
    except ValueError:
        parser.error("Use an 8-digit DART corporation code, aware ISO cutoff and ISO start date")
    if not args.live:
        print(json.dumps({"status": "FAILED", "reason": "LIVE_ACK_REQUIRED"}))
        return 2
    if args.out is not None and (args.out.exists() or args.out.is_symlink()):
        print(json.dumps({"status": "FAILED", "reason": "OUTPUT_EXISTS"}))
        return 2
    try:
        if collector is None:
            from prism_core.dart_public_filings import collect_dart_periodic_filings
            collector = collect_dart_periodic_filings
        started = time.monotonic()
        result = asyncio.run(collector(corp_code=args.corp_code, decision_at=cutoff,
                                       start_date=start, scope=args.scope))
        if not isinstance(result, dict):
            raise TypeError
        result = {**result, "probe_elapsed_seconds": round(time.monotonic() - started, 6)}
        encoded = json.dumps(_receipt(result), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        if args.out is None:
            print(encoded, end="")
        else:
            with args.out.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        return 0 if result.get("status") in {"COMPLETE_WITHIN_QUERY", "EMPTY"} else 2
    except Exception:  # noqa: BLE001 - no provider text, exception paths or secrets in diagnostic errors
        print(json.dumps({"status": "FAILED", "reason": "PROBE_FAILED"}))
        return 2


if __name__ == "__main__":
    # Support the repository's normal `python tools/...py` diagnostic invocation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
