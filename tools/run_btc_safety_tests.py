"""Development-only BTC tests; forbid Internet access in parent/spawned Python.

Run from a clean checkout without production .env or runtime databases:
    python tools/run_btc_safety_tests.py --report /tmp/btc-test-result.json
Optional positional arguments select pytest files, otherwise all BTC tests run.
Unix sockets remain available for multiprocessing IPC. No broker orders or
production runner are invoked. The audit log records no network addresses.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time


def _deny_network(event, args):
    denied = event == "socket.getaddrinfo" or (
        event in {"socket.connect", "socket.sendto"}
        and getattr(args[0], "family", None) in {socket.AF_INET, socket.AF_INET6})
    if not denied:
        return
    path = os.environ.get("BTC_TEST_NETWORK_AUDIT_FILE")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()} {event}\n")
    raise RuntimeError("Internet access forbidden in isolated BTC tests")


# Spawn imports this module as __mp_main__; keep the guard outside main().
sys.addaudithook(_deny_network)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root / "prism-btc"))
    os.environ["PYTHONPATH"] = str(root / "prism-btc")
    import pytest

    class Results:
        def __init__(self):
            self.counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}

        def pytest_runtest_logreport(self, report):
            if report.when == "call" or report.outcome == "skipped":
                self.counts[report.outcome] += 1
            elif report.outcome == "failed":
                self.counts["errors"] += 1

        def pytest_collectreport(self, report):
            if report.failed:
                self.counts["errors"] += 1

    results = Results()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="btc-network-audit-") as directory:
        audit = Path(directory) / "attempts.log"
        os.environ["BTC_TEST_NETWORK_AUDIT_FILE"] = str(audit)
        exit_code = int(pytest.main(["--noconftest", "-q", "--tb=short",
                                    *(args.tests or ["prism-btc/tests"])], plugins=[results]))
        attempts = len(audit.read_text().splitlines()) if audit.exists() else 0
    summary = {**results.counts, "pytest_exit_code": exit_code,
               "network_attempts": attempts, "elapsed_seconds": round(time.monotonic() - started, 3),
               "python": sys.version.split()[0]}
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return exit_code or int(attempts > 0)


if __name__ == "__main__":
    raise SystemExit(main())
