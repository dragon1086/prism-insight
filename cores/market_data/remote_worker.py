"""One deadline-isolated, read-only KIS request; stdout is bounded JSON only."""

import contextlib
import json
import sys


def main():
    payload = {"error": "unavailable"}
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from fastapi import HTTPException

            from cores.market_data.remote_api import (
                MarketDataRequest,
                execute_market_data,
            )
            raw = sys.stdin.read(4097)
            if len(raw.encode()) > 4096:
                raise ValueError("Request too large")
            try:
                payload = execute_market_data(MarketDataRequest.model_validate_json(raw))
            except HTTPException as exc:
                if exc.status_code == 422:
                    payload = {"error": "unsupported"}
    except Exception:  # noqa: BLE001 - fixed response on every worker failure
        payload = {"error": "unavailable"}
    sys.stdout.write(json.dumps(payload, allow_nan=False))


if __name__ == "__main__":
    main()
