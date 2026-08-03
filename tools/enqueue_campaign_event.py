"""Place one campaign event on this host's queue. Reads the event on stdin.

The remote half of `forward_campaign_events.py`. It lives in the repo as a
file rather than being sent inline as `python -c "<script>"`, because ssh joins
its trailing arguments into a single string and hands that to the *remote
shell* — a multi-line script arrives split into separate shell commands
("import: command not found"). Quoting could paper over it; shipping the code
with the repo removes the class of bug entirely and makes the remote half
readable and testable like anything else.

The event arrives on stdin so it never appears in argv, where `ps` would show
it to every user on the box.

Prints NEW when the queue accepted the event and DUPLICATE when it already had
it. Both mean the caller is done: `campaign_id` is UNIQUE and the insert is
INSERT OR IGNORE, so re-sending is a no-op by design.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

DEFAULT_QUEUE = "/var/lib/prism-kakao/prism_campaign_queue.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue-path",
        default=os.getenv("PRISM_CAMPAIGN_QUEUE_PATH", DEFAULT_QUEUE),
    )
    args = parser.parse_args(argv)

    payload = json.load(sys.stdin)
    with SQLiteBatchCampaignQueue(Path(args.queue_path).expanduser()) as queue:
        accepted = queue.enqueue(payload)

    print("NEW" if accepted else "DUPLICATE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
