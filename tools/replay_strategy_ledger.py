"""NO ORDER: sanitized JSONL replay into a separate, incomplete strategy ledger."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observability.strategy_ledger_projection import replay_files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--capital-profile", required=True,
                        help="Explicit KR/US profiles: currency, initial_capital, unit_budget")
    parser.add_argument("--message-preview", action="store_true",
                        help="Return notification-only previews; never sends messages")
    args = parser.parse_args(argv)
    try:
        report = replay_files(args.input, args.ledger, args.capital_profile)
        if args.message_preview:
            from prism_core.strategy_ledger_messages import format_campaign
            report["message_previews"] = [
                format_campaign(snapshot, campaign["campaign_id"],
                                unresolved_execution_overlays=report["unresolved_execution_overlays"])
                for snapshot in report["snapshots"] for campaign in snapshot["campaigns"]
            ]
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
        print(json.dumps({"status": "error", "error_type": type(error).__name__, "no_order": True}))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
