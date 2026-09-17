"""Write nonsecret optional research config; does not grant TradingView rights."""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core.report_research_prefetch import CONFIG_PATH, VERSION


def configure(path, enabled, timeout_seconds=60, namespace="production", market_context_enabled=None,
              validated_symbols=None):
    if not 1 <= timeout_seconds <= 60:
        raise ValueError("timeout_seconds must be 1..60")
    if not namespace or len(namespace) > 80:
        raise ValueError("namespace must be 1..80 characters")
    path = Path(path)
    existing = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(existing, dict):
        raise TypeError("existing config must be an object")
    if validated_symbols is not None and (
            not isinstance(validated_symbols, dict) or
            any(market not in {"KR", "US"} or not isinstance(symbols, list)
                or any(not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9.^-]{1,20}', symbol)
                       for symbol in symbols)
                for market, symbols in validated_symbols.items())):
        raise ValueError("validated_symbols must contain KR/US symbol lists")
    payload = {"version": VERSION, "enabled": bool(enabled),
               "market_context_enabled": (bool(existing.get("market_context_enabled", False))
                                          if market_context_enabled is None else bool(market_context_enabled)),
               "timeout_seconds": timeout_seconds, "namespace": namespace,
               "sources": ["perplexity_search", "firecrawl_scrape"],
               "tradingview_status": "RIGHTS_UNCONFIRMED"}
    if validated_symbols is not None:
        payload["validated_symbols"] = validated_symbols
    elif "validated_symbols" in existing:
        payload["validated_symbols"] = existing["validated_symbols"]
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
    market = parser.add_mutually_exclusive_group()
    market.add_argument("--enable-market-context", dest="market_context", action="store_true", default=None,
                        help="Enable market prefetch; otherwise preserve existing setting")
    market.add_argument("--disable-market-context", dest="market_context", action="store_false",
                        help="Explicitly disable market prefetch independently of research")
    parser.add_argument("--validated-symbol", action="append", metavar="MARKET:SYMBOL",
                        help="Limit enrichment to validated symbols, e.g. US:MU (repeatable)")
    args = parser.parse_args()
    scope = None
    if args.validated_symbol:
        scope = {}
        for value in args.validated_symbol:
            market, separator, symbol = value.partition(':')
            if not separator:
                parser.error('--validated-symbol requires MARKET:SYMBOL')
            scope.setdefault(market, []).append(symbol)
    result = configure(args.path, args.enable, args.timeout_seconds, args.namespace,
                       market_context_enabled=args.market_context, validated_symbols=scope)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
