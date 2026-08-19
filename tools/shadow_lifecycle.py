#!/usr/bin/env python3
"""Materialize and report shadow-feature expiry on the operations server."""

from __future__ import annotations

import argparse
import json
import sys

from cores.shadow_lifecycle import apply_expiry, snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-expiry", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
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
