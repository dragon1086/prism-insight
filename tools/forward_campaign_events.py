"""Forward queued campaign events from db-server to the Kakao bot host.

Run from db-server's crontab a few minutes after each orchestrator slot:

    40 09 * * *   KR morning   (orchestrator 09:30)
    56 14 * * *   KR afternoon (orchestrator 14:46)
    25 10 * * 1-5 US morning   (orchestrator 10:15)
    20 15 * * 1-5 US afternoon (orchestrator 15:10)

Missing a run is not a problem: unforwarded events stay PENDING and the next
run picks them up along with its own. Sending twice is not a problem either —
the remote `campaign_id` is UNIQUE and its insert is INSERT OR IGNORE.

Exit code is 0 whenever the run completed, even if individual events were
deferred, so a transient network blip does not turn into cron mail every time.
A non-zero exit means the forwarder itself could not run.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from messaging.campaign_forwarder import (
    CampaignForwarder,
    SshCampaignShipper,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

logger = logging.getLogger("forward_campaign_events")

DEFAULT_QUEUE = "/var/lib/prism-kakao/prism_campaign_queue.sqlite"
DEFAULT_REMOTE_REPO = "/home/prism/prism-insight"
DEFAULT_REMOTE_QUEUE = "/var/lib/prism-kakao/prism_campaign_queue.sqlite"
DEFAULT_REMOTE_PYTHON = "python3"
DEFAULT_REMOTE_ARTIFACT_ROOT = "/home/prism/prism-insight/pdf_reports"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue-path",
        default=os.getenv("PRISM_CAMPAIGN_QUEUE_PATH", DEFAULT_QUEUE),
        help="local queue the orchestrator publishes into",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("PRISM_CAMPAIGN_FORWARD_HOST", ""),
        help="ssh destination running the Kakao bot, e.g. root@10.0.0.2",
    )
    parser.add_argument(
        "--remote-repo",
        default=os.getenv("PRISM_CAMPAIGN_REMOTE_REPO", DEFAULT_REMOTE_REPO),
    )
    parser.add_argument(
        "--remote-queue",
        default=os.getenv("PRISM_CAMPAIGN_REMOTE_QUEUE", DEFAULT_REMOTE_QUEUE),
    )
    parser.add_argument(
        "--remote-python",
        default=os.getenv("PRISM_CAMPAIGN_REMOTE_PYTHON", DEFAULT_REMOTE_PYTHON),
    )
    parser.add_argument(
        "--remote-artifact-root",
        default=os.getenv(
            "PRISM_CAMPAIGN_REMOTE_ARTIFACT_ROOT", DEFAULT_REMOTE_ARTIFACT_ROOT
        ),
        help="remote directory served by the Kakao report-link runtime",
    )
    parser.add_argument(
        "--identity-file",
        default=os.getenv("PRISM_CAMPAIGN_FORWARD_IDENTITY") or None,
    )
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--retry-seconds", type=int, default=60)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be forwarded without touching the remote",
    )
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    queue_path = Path(args.queue_path).expanduser()
    if not queue_path.exists():
        # Nothing has ever been published here. That is a normal state on a
        # freshly provisioned host, not a failure.
        logger.info("No queue at %s; nothing to forward", queue_path)
        return 0

    if args.dry_run:
        with SQLiteBatchCampaignQueue(queue_path) as queue:
            pending = [
                entry
                for entry in queue.list_entries()
                if entry.get("status") == "PENDING"
            ]
        logger.info("Would forward %d pending event(s)", len(pending))
        for entry in pending:
            logger.info("  %s", entry.get("campaign_id"))
        return 0

    if not args.host.strip():
        logger.error(
            "No destination host. Set PRISM_CAMPAIGN_FORWARD_HOST or pass --host."
        )
        return 2

    shipper = SshCampaignShipper(
        host=args.host.strip(),
        repo_path=args.remote_repo,
        queue_path=args.remote_queue,
        python_path=args.remote_python,
        identity_file=args.identity_file,
        local_artifact_roots=(
            Path.cwd() / "pdf_reports",
            Path.cwd() / "prism-us" / "pdf_reports",
        ),
        remote_artifact_root=args.remote_artifact_root,
    )

    with SQLiteBatchCampaignQueue(queue_path) as queue:
        forwarder = CampaignForwarder(
            queue,
            shipper,
            lease_owner=f"{socket.gethostname()}:{os.getpid()}",
            lease_seconds=args.lease_seconds,
            batch_size=args.batch_size,
            retry_seconds=args.retry_seconds,
            max_attempts=args.max_attempts,
        )
        result = forwarder.run_once()

    logger.info(
        "Forward batch (claimed=%d, forwarded=%d, duplicate=%d, retry=%d, dead=%d)",
        result.claimed,
        result.forwarded,
        result.duplicate,
        result.retry_scheduled,
        result.dead,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
