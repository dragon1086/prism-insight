#!/usr/bin/env python3
"""Run the B3 all-entries SHADOW worker for one market (KR or US). No orders.

OFF unless B3_AE_SHADOW_ENABLED=true (.env). Writes runtime/b3-ae-health-<market>.json
after every tick. KR and US run as separate processes (cores package isolation).
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_health(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".b3-ae-health-", dir=path.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, default=str)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["kr", "us"], required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from observability import b3_ae_capture
    from prism_core.b3_ae_shadow import B3AeShadowStore
    from prism_core.b3_ae_worker import B3AeWorker, kr_providers, us_providers, utc_now

    market = args.market.upper()
    health = ROOT / f"runtime/b3-ae-health-{args.market}.json"
    worker = None
    while True:
        try:
            if not b3_ae_capture.enabled():
                result = dict(contract="b3-ae-worker-v1", market=market, at=utc_now(), status="OFF", rows=[])
            else:
                if worker is None:
                    path = b3_ae_capture.store_path()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    worker = B3AeWorker(market, store=B3AeShadowStore(path),
                                        holdings_db=os.getenv("STOCK_TRACKING_DB") or ROOT / "stock_tracking_db.sqlite",
                                        providers=kr_providers() if market == "KR" else us_providers())
                result = worker.once()
        except Exception as error:  # noqa: BLE001 - keep the SHADOW loop alive, record the failure
            result = dict(contract="b3-ae-worker-v1", market=market, at=utc_now(), status="ERROR",
                          error_type=type(error).__name__, detail=str(error)[:200], rows=[])
        write_health(health, result)
        print(json.dumps(result, sort_keys=True, default=str), flush=True)
        if args.once:
            return 2 if result["status"] == "ERROR" else 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
