"""Ship queued campaign events to the host that actually runs the Kakao bot.

The orchestrator and the Kakao bot no longer live together. The orchestrator
runs on db-server (1 CPU, 1.7 GB) because that is where the analysis and the
master DB are; the Kakao runtimes run on prism-backend because db-server has no
room for them. The campaign queue, though, is a SQLite file — it cannot span
hosts.

So this moves the *event* to the bot rather than the *rooms* to the
orchestrator. db-server keeps no Kakao state at all: no room list, no outbox,
no bot token. It publishes an event and forwards it; everything Kakao-shaped
stays on one host, which is what the runbook requires and what keeps room
approvals from going stale in two places.

This is a queue consumer in the same shape as `LocalBatchCampaignConsumer` —
claim, do the work, acknowledge — except the work is a remote enqueue instead
of planning deliveries. Nothing here imports `kakao_bot`; db-server should not
need the Kakao stack installed to run it.
"""

from __future__ import annotations

import json
import logging

# ssh is invoked with a fixed argv list and shell=False; the only caller-supplied
# value is the payload, and that goes in on stdin.
import subprocess  # nosec B404
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

logger = logging.getLogger(__name__)

DEFAULT_SSH_TIMEOUT = 60


@dataclass(frozen=True)
class CampaignForwardRunResult:
    claimed: int
    forwarded: int
    duplicate: int
    retry_scheduled: int
    dead: int
    stale_lease: int


class CampaignShipper(Protocol):
    def ship(self, payload: Mapping[str, object]) -> bool:
        """Place one event on the remote queue.

        Returns True when the remote accepted it as new, False when the remote
        already had it. Both are success — the remote queue's ``campaign_id`` is
        UNIQUE and its insert is ``INSERT OR IGNORE``, so re-sending is a no-op
        and this forwarder never has to track what it already sent. Raise to
        signal a real failure worth retrying.
        """


# The remote half ships with the repo. Sending it inline as `python -c` does
# not survive the hop: ssh joins its trailing arguments into one string for the
# *remote shell*, which then splits a multi-line script into separate commands
# and reports "import: command not found". A file has no quoting surface.
_REMOTE_ENTRYPOINT = "tools/enqueue_campaign_event.py"


class SshCampaignShipper:
    """Enqueue on the remote host by calling its own queue code over SSH.

    Deliberately not `sqlite3 <<SQL` — going through `enqueue()` keeps payload
    validation, JSON normalisation, and the INSERT OR IGNORE semantics in one
    place instead of forking a second, silently diverging implementation.
    """

    def __init__(
        self,
        *,
        host: str,
        repo_path: str,
        queue_path: str,
        python_path: str,
        ssh_binary: str = "ssh",
        identity_file: str | None = None,
        timeout: int = DEFAULT_SSH_TIMEOUT,
    ) -> None:
        self._host = host
        self._repo_path = repo_path
        self._queue_path = queue_path
        self._python_path = python_path
        self._ssh_binary = ssh_binary
        self._identity_file = identity_file
        self._timeout = timeout

    def _command(self) -> list[str]:
        command = [self._ssh_binary, "-o", "BatchMode=yes"]
        if self._identity_file:
            command += ["-i", self._identity_file]
        entrypoint = f"{self._repo_path.rstrip('/')}/{_REMOTE_ENTRYPOINT}"
        command += [
            self._host,
            self._python_path,
            entrypoint,
            "--queue-path",
            self._queue_path,
        ]
        return command

    def ship(self, payload: Mapping[str, object]) -> bool:
        completed = subprocess.run(  # nosec B603
            self._command(),
            input=json.dumps(dict(payload), ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=self._timeout,
            # Handled below: a non-zero return has to become a retryable
            # RuntimeError, not a CalledProcessError that skips the message.
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"remote enqueue failed (rc={completed.returncode}): "
                f"{completed.stderr.strip()[:300]}"
            )
        return completed.stdout.strip().endswith("NEW")


class CampaignForwarder:
    """Claim local queue events and hand them to the remote bot host."""

    def __init__(
        self,
        queue: SQLiteBatchCampaignQueue,
        shipper: CampaignShipper,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
        batch_size: int = 20,
        retry_seconds: int = 60,
        max_attempts: int = 5,
    ) -> None:
        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if min(lease_seconds, batch_size, retry_seconds, max_attempts) <= 0:
            raise ValueError("forwarder limits must be positive")
        self._queue = queue
        self._shipper = shipper
        self._lease_owner = lease_owner
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size
        self._retry_seconds = retry_seconds
        self._max_attempts = max_attempts

    def run_once(
        self,
        *,
        now: datetime | None = None,
    ) -> CampaignForwardRunResult:
        run_at = _as_utc(now or datetime.now(timezone.utc))
        entries = self._queue.claim(
            lease_owner=self._lease_owner,
            now=run_at,
            lease_seconds=self._lease_seconds,
            limit=self._batch_size,
        )

        forwarded = 0
        duplicate = 0
        retry_scheduled = 0
        dead = 0
        stale_lease = 0

        for entry in entries:
            if entry.attempt_count > self._max_attempts:
                marked = self._queue.mark_dead(
                    entry.queue_id,
                    lease_owner=self._lease_owner,
                    error="maximum campaign forward attempts exceeded",
                )
                dead += int(marked)
                stale_lease += int(not marked)
                logger.error(
                    "Giving up on campaign %s after %d attempts",
                    entry.campaign_id,
                    entry.attempt_count,
                )
                continue

            try:
                accepted = self._shipper.ship(entry.payload)
            except Exception as exc:  # noqa: BLE001 - retried on the next run
                logger.warning(
                    "Forwarding campaign %s failed: %s", entry.campaign_id, exc
                )
                marked = self._queue.release(
                    entry.queue_id,
                    lease_owner=self._lease_owner,
                    next_attempt_at=run_at + timedelta(seconds=self._retry_seconds),
                    error=str(exc) or type(exc).__name__,
                )
                retry_scheduled += int(marked)
                stale_lease += int(not marked)
                continue

            marked = self._queue.acknowledge(
                entry.queue_id,
                lease_owner=self._lease_owner,
                consumed_at=run_at,
            )
            stale_lease += int(not marked)
            if marked:
                forwarded += int(accepted)
                duplicate += int(not accepted)
            logger.info(
                "Forwarded campaign %s (%s)",
                entry.campaign_id,
                "new" if accepted else "already present",
            )

        return CampaignForwardRunResult(
            claimed=len(entries),
            forwarded=forwarded,
            duplicate=duplicate,
            retry_scheduled=retry_scheduled,
            dead=dead,
            stale_lease=stale_lease,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
