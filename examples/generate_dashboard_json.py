#!/usr/bin/env python3
"""Compatibility CLI for the safe Phase 1 dashboard export contract.

This script no longer opens the mixed legacy tracking database or imports KIS
account/trading adapters.  All three authoritative stores must be supplied explicitly.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from prism_app.dashboard_export import dashboard_export_main


DEFAULT_OUTPUT = Path(__file__).parent / "dashboard" / "public" / "dashboard_data.json"


def main() -> int:
    return dashboard_export_main(default_output=DEFAULT_OUTPUT)


if __name__ == "__main__":
    raise SystemExit(main())
