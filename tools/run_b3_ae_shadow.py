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


def live_add_provider(market):
    """Each LIVE in-slot add runs in tools/run_micro_split_add.py (own market path isolation)."""
    import subprocess

    from prism_core import micro_split_live

    def live_add(campaign, decision, now):
        if not micro_split_live.live_enabled(market):
            return {"status": "LIVE_OFF"}
        payload = json.dumps({"campaign": campaign, "decision": decision, "now": now}, default=str)
        try:
            done = subprocess.run([sys.executable, str(ROOT / "tools/run_micro_split_add.py"), "--market",
                                   market.lower()], input=payload, capture_output=True, text=True, timeout=180,
                                  cwd=str(ROOT), check=False)
            lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
            return json.loads(lines[-1]) if lines else {"status": "ERROR", "error": done.stderr[-300:]}
        except Exception as error:  # noqa: BLE001 - one failed add never stops the loop
            return {"status": "ERROR", "error": f"{type(error).__name__}: {str(error)[:160]}"}
    return live_add


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
            from prism_core import micro_split_live
            if not (b3_ae_capture.enabled() or micro_split_live.live_enabled(market)):
                result = dict(contract="b3-ae-worker-v1", market=market, at=utc_now(), status="OFF", rows=[])
            else:
                if worker is None:
                    path = b3_ae_capture.store_path()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    providers = kr_providers() if market == "KR" else us_providers()
                    providers["live_add"] = live_add_provider(market)
                    worker = B3AeWorker(market, store=B3AeShadowStore(path),
                                        holdings_db=os.getenv("STOCK_TRACKING_DB") or ROOT / "stock_tracking_db.sqlite",
                                        providers=providers)
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
