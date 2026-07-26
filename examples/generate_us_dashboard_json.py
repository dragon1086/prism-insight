#!/usr/bin/env python3
"""US compatibility CLI for the unified safe Phase 1 dashboard export.

The exported envelope already contains both KR and US research sections.  This legacy
entrypoint remains only as a filename-compatible wrapper and has no account/provider
capability.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from prism_app.dashboard_export import dashboard_export_main


DEFAULT_OUTPUT = Path(__file__).parent / "dashboard" / "public" / "us_dashboard_data.json"


def main() -> int:
    return dashboard_export_main(default_output=DEFAULT_OUTPUT)


if __name__ == "__main__":
    raise SystemExit(main())
