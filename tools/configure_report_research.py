"""Write nonsecret optional research config; does not grant TradingView rights."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core.report_research_prefetch import CONFIG_PATH, VERSION


def configure(path, enabled, timeout_seconds=60, namespace="production", market_context_enabled=False):
    if not 1 <= timeout_seconds <= 60:
        raise ValueError("timeout_seconds must be 1..60")
    if not namespace or len(namespace) > 80:
        raise ValueError("namespace must be 1..80 characters")
    payload = {"version": VERSION, "enabled": bool(enabled),
               "market_context_enabled": bool(market_context_enabled),
               "timeout_seconds": timeout_seconds, "namespace": namespace,
               "sources": ["perplexity_search", "firecrawl_scrape"],
               "tradingview_status": "RIGHTS_UNCONFIRMED"}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    parser.add_argument("--path", type=Path, default=CONFIG_PATH)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--namespace", default="production")
    parser.add_argument("--enable-market-context", action="store_true",
                        help="Enable bounded descriptive market prefetch (default OFF)")
    args = parser.parse_args()
    result = configure(args.path, args.enable, args.timeout_seconds, args.namespace,
                       market_context_enabled=args.enable_market_context)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
