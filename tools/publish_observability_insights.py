"""Generate the curated observability snapshot and publish it atomically."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

if __package__:
    from .export_observability_insights import (
        build_snapshot,
        load_clickhouse_events,
        write_snapshot,
    )
else:
    from export_observability_insights import (
        build_snapshot,
        load_clickhouse_events,
        write_snapshot,
    )

_SAFE_REMOTE_PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SAFE_USER = re.compile(r"^[A-Za-z0-9_-]+$")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def publish(
    *,
    container: str,
    local_output: Path,
    days: int,
    host: str,
    port: int,
    user: str,
    destination: str,
    identity_file: str | None = None,
) -> dict[str, object]:
    if not _SAFE_HOST.fullmatch(host):
        raise ValueError("invalid dashboard host")
    if not _SAFE_USER.fullmatch(user):
        raise ValueError("invalid dashboard user")
    if not _SAFE_REMOTE_PATH.fullmatch(destination):
        raise ValueError("invalid dashboard destination")
    if not 1 <= port <= 65535:
        raise ValueError("invalid dashboard SSH port")
    if identity_file and not Path(identity_file).is_absolute():
        raise ValueError("dashboard identity file must be absolute")

    events = load_clickhouse_events(container, days=days)
    snapshot = build_snapshot(events, retention_days=days)
    write_snapshot(local_output, snapshot)

    remote = f"{user}@{host}"
    remote_temporary = destination + ".tmp"
    common_options = [
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    if identity_file:
        common_options.extend(["-i", identity_file])
    scp_options = [*common_options, "-P", str(port)]
    ssh_options = [*common_options, "-p", str(port)]
    subprocess.run(
        [
            "scp",
            *scp_options,
            str(local_output),
            f"{remote}:{remote_temporary}",
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            "ssh",
            *ssh_options,
            remote,
            "/usr/bin/install",
            "-m",
            "0644",
            "--",
            remote_temporary,
            destination,
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["ssh", *ssh_options, remote, "/usr/bin/rm", "-f", "--", remote_temporary],
        check=True,
        timeout=30,
    )
    return {
        "generated_at": snapshot["generated_at"],
        "events": snapshot["data_quality"]["total_events"],
        "destination": destination,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="prism-clickstack")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/var/lib/prism-observability/exports/observability_insights.json"
        ),
    )
    parser.add_argument("--days", type=int, default=180)
    args = parser.parse_args(argv)

    result = publish(
        container=args.container,
        local_output=args.output,
        days=max(1, args.days),
        host=_required_env("PRISM_DASHBOARD_HOST"),
        port=int(os.getenv("PRISM_DASHBOARD_PORT", "22")),
        user=os.getenv("PRISM_DASHBOARD_USER", "root"),
        destination=_required_env("PRISM_DASHBOARD_DESTINATION"),
        identity_file=os.getenv("PRISM_DASHBOARD_IDENTITY") or None,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
