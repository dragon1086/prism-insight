"""KR/US inverse signal recorder (observe only, no orders). Run after each market's close.

    python tools/run_inverse_signals.py --market kr --dry-run           # compute and print, write nothing
    INVERSE_SIGNALS_MODE=observe python tools/run_inverse_signals.py --market kr   # lane DB + events
    INVERSE_SIGNALS_MODE=observe python tools/run_inverse_signals.py --market us   # 28 sector ETFs, synthetic -2x

``INVERSE_SIGNALS_MODE`` defaults to ``off``. Nothing schedules this; turning observe on is a
separate, user-approved server step.

Live lane (S5): ``INVERSE_LANE_LIVE_MARKETS=KR,US`` (default empty) runs the live cycle after the
recorder in observe mode only: next-open reserved orders on every configured real account, one
account slot (KR one position at a time, US three sectors). ``--dry-run`` never orders.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PRISM_DISABLE_SIGNAL_PUBLISH", "1")

KIS_MIN_INTERVAL = 0.4  # seconds between KIS calls


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=("kr", "us"), required=True)
    ap.add_argument("--date", type=date.fromisoformat, default=date.today())
    ap.add_argument("--dry-run", action="store_true", help="compute and print; write nothing")
    args = ap.parse_args(argv)

    mode = "dry-run" if args.dry_run else os.environ.get("INVERSE_SIGNALS_MODE", "off").strip().lower()
    if mode == "off":
        print("INVERSE_SIGNALS_MODE=off: nothing to do")
        return 0
    if mode not in ("dry-run", "observe"):
        print(f"unsupported mode {mode!r}: the signal recorder has no order path", file=sys.stderr)
        return 2
    from prism_inverse.adapters.fakes import FakeEvents, MemoryStore
    from prism_inverse.adapters.kis_data import KisKrData, KisUsData
    from prism_inverse.adapters.prism_events import PrismEvents
    from prism_inverse.adapters.registry import load_kr_sectors, load_us_sectors
    from prism_inverse.adapters.sqlite_store import SqliteStore
    from prism_inverse.app.signals_cycle import run_signals, run_us_signals

    observe = mode == "observe"
    store, events = SqliteStore() if observe else MemoryStore(), PrismEvents() if observe else FakeEvents()
    if args.market == "kr":
        sectors, bench = load_kr_sectors()
        result = run_signals(args.date, sectors, bench, KisKrData(min_interval=KIS_MIN_INTERVAL), store, events, mode=mode)
    else:
        result = run_us_signals(args.date, load_us_sectors(), KisUsData(), store, events, mode=mode)
    for fam, (status, inputs, error) in result.days.items():
        extra = {k: inputs[k] for k in ("driver", "corr", "close", "ma50", "atr", "switch", "virtual") if inputs.get(k) is not None}
        print(f"{fam:12} {status:9} {extra}" + (f" error={error}" if error else ""))
    for t in result.trades:
        print(f"VIRTUAL {t.trade_id} {t.status} entry={t.entry_date} {t.entry_open} exit={t.exit_date} "
              f"{t.exit_open} {t.exit_reason or ''} ret={t.ret}" + (f" {t.error}" if t.error else ""))
    for code, err in sorted(result.data_errors.items()):
        print(f"DATA  {code}: {err}")
    if args.market.upper() in live_markets():
        if not observe:
            print("LIVE  skipped (dry-run never orders)")
        else:
            for line in run_live_lane(args.market.upper(), args.date, result.trades, store, events).actions:
                print(f"LIVE  {line}")
    return 0


def live_markets() -> set[str]:
    return {m.strip().upper() for m in os.environ.get("INVERSE_LANE_LIVE_MARKETS", "").split(",") if m.strip()}


def run_live_lane(market: str, day: date, trades, store, events):
    from datetime import timedelta

    from prism_inverse.adapters.kis_broker import brokers_for
    from prism_inverse.adapters.kis_data import KisUsData
    from prism_inverse.adapters.registry import load_us_inverse
    from prism_inverse.app.live_cycle import kr_product_map, run_live, us_product_map
    from prism_inverse.domain.lane import KR_LANE, US_LANE
    from prism_inverse.domain.models import Instrument
    from prism_inverse.ports import Unavailable, Unsupported

    if market == "KR":
        return run_live("KR", day, trades, kr_product_map(), KR_LANE, brokers_for("KR"), store, events)

    def last_close(product):
        try:
            bars = KisUsData().daily_bars(Instrument("US", product.code, "etf", product.exchange), day - timedelta(days=14), day)
        except (Unavailable, Unsupported):
            return None
        return bars[-1].close if bars else None

    return run_live("US", day, trades, us_product_map(load_us_inverse()), US_LANE, brokers_for("US"), store, events, last_close)


if __name__ == "__main__":
    raise SystemExit(main())
