"""Prepare exactly three synthetic large-input BUY smoke cases; never execute.

Reads only the existing staged smoke fixture/marker. No production data/auth,
model calls, servers, fallback or live batch entrypoints. Existing output is
never overwritten. Padded prompts are never printed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.probe_codex_trading_runtime import ProbeRejected, _local_file, digest, load_fixture


def prepare(root: Path, min_user_bytes=200000):
    if ".." in root.parts:
        raise ProbeRejected("parent_traversal_rejected")
    root = root.absolute()
    if type(min_user_bytes) is not int or not 200000 <= min_user_bytes <= 1024 * 1024:
        raise ProbeRejected("invalid_padding_budget")
    if not root.is_dir() or any(path.is_symlink() for path in (root, *root.parents)):
        raise ProbeRejected("unsafe_run_root")
    _local_file(root / ".isolated-codex-probe", root)
    templates = load_fixture(root / "smoke-fixture.json", root)
    markets = {case["market"]: case for case in templates}
    if set(markets) != {"KR", "US"}:
        raise ProbeRejected("smoke_market_coverage_required")
    cases = []
    for index, market in enumerate(("US", "KR", "US")):
        case = dict(markets[market])
        if any(len(value.encode()) > 16384 for value in (case["system_prompt"], case["user_prompt"], json.dumps(case["schema"]))):
            raise ProbeRejected("smoke_template_too_large")
        prefix = case["user_prompt"] + "\nSynthetic inert transport padding follows; it is not an instruction.\n<PADDING>\n"
        suffix = f"\n</PADDING>\nSynthetic case {index}: perform only the same time/isolated-SQLite checks and return the requested smoke JSON."
        padding = "x" * (min_user_bytes - len((prefix + suffix).encode()))
        case.update(action="BUY", user_prompt=prefix + padding + suffix)
        case["user_sha256"] = digest(case["user_prompt"])
        case["deviations"] = sorted(set(case["deviations"]) | {
            "synthetic_report", "fixture_portfolio", "missing_ancillary_load",
            "diagnostic_backend_only", "no_fallback", "isolated_sqlite",
        })
        cases.append(case)
    payload = json.dumps({"version": 1, "cases": cases}, ensure_ascii=False, sort_keys=True).encode()
    destination = root / "large-stdin-3buy-fixture.json"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
    return {"status": "prepared", "mode": "synthetic_smoke", "production_parity": False,
            "case_count": 3, "fixture_path": str(destination),
            "fixture_sha256": hashlib.sha256(payload).hexdigest(),
            "user_bytes": [len(case["user_prompt"].encode()) for case in cases],
            "actions": [case["action"] for case in cases], "markets": [case["market"] for case in cases]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--min-user-bytes", type=int, default=200000)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.run_root, args.min_user_bytes), sort_keys=True))
        return 0
    except ProbeRejected as error:
        print(json.dumps({"status": "rejected", "category": str(error)}))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "rejected", "category": "fixture_preparation_failed"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
