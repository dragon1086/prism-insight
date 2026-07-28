"""Single user-facing command surface for the incremental PRISM application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from prism_app import (
    live_data_uat,
    llm_oauth_smoke,
    product_uat,
    us_product_uat,
    user_surface_uat,
)
from prism_app.shadow_report import append_shadow_section, read_persisted_shadow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m prism_app")
    parser.add_argument(
        "command",
        choices=(
            "live-data",
            "oauth-smoke",
            "shadow-readback",
            "shadow-run",
            "shadow-run-us",
            "user-surface",
        ),
        help="run a read-only Phase 1 application command",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "live-data":
        return live_data_uat.main(arguments[1:])
    if arguments and arguments[0] == "oauth-smoke":
        return llm_oauth_smoke.main(arguments[1:])
    if arguments and arguments[0] == "shadow-run":
        return product_uat.main(arguments[1:])
    if arguments and arguments[0] == "shadow-run-us":
        return us_product_uat.main(arguments[1:])
    if arguments and arguments[0] == "shadow-readback":
        return _shadow_readback(arguments[1:])
    if arguments and arguments[0] == "user-surface":
        return user_surface_uat.main(arguments[1:])
    parsed, remainder = _parser().parse_known_args(arguments)
    if parsed.command == "live-data":
        return live_data_uat.main(remainder)
    if parsed.command == "oauth-smoke":
        return llm_oauth_smoke.main(remainder)
    if parsed.command == "shadow-run":
        return product_uat.main(remainder)
    if parsed.command == "shadow-run-us":
        return us_product_uat.main(remainder)
    if parsed.command == "shadow-readback":
        return _shadow_readback(remainder)
    if parsed.command == "user-surface":
        return user_surface_uat.main(remainder)
    raise AssertionError("unreachable command")


def _shadow_readback(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Read one persisted Phase 1 SHADOW run without external calls."
    )
    parser.add_argument("--ops-db", required=True, type=Path)
    parser.add_argument("--job-key", required=True)
    parser.add_argument("--base-report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    readback = read_persisted_shadow(args.ops_db, job_key=args.job_key)
    base = (
        ""
        if args.base_report is None
        else args.base_report.read_text(encoding="utf-8")
    )
    combined = append_shadow_section(base, readback.markdown)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(combined, encoding="utf-8")
    return 0
