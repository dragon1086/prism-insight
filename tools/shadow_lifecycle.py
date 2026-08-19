#!/usr/bin/env python3
"""Materialize and report shadow-feature expiry on the operations server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Cron invokes this file directly, so make the repository root importable
# without relying on an inherited PYTHONPATH.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cores.shadow_lifecycle import apply_expiry, set_feature_mode, snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-expiry", action="store_true")
    parser.add_argument("--set-mode", nargs=2, metavar=("FEATURE", "MODE"))
    parser.add_argument("--reason", default="manual operator review")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.set_mode:
        result = set_feature_mode(
            args.set_mode[0], args.set_mode[1], reason=args.reason
        )
    else:
        result = apply_expiry() if args.apply_expiry else snapshot()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"shadow lifecycle: today={result['today']} state={result['state_path']}")
        for name, item in result["features"].items():
            print(
                f"- {name}: mode={item['mode']} review_by={item['review_by']} "
                f"min_samples={item['min_samples']}"
            )
        if result.get("changed"):
            print("auto_expired:", ", ".join(result["changed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
