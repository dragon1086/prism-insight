"""Inverse ETF lane runner (S1: no orders).

    python tools/run_inverse_lane.py --market kr --dry-run       # compute and print only
    INVERSE_LANE_MODE=observe python tools/run_inverse_lane.py --market us   # lane DB + events

``INVERSE_LANE_MODE`` defaults to ``off``. ``observe`` needs the user's approval (stage S4)
and is not scheduled anywhere. There is no ``live`` mode before S5.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PRISM_DISABLE_SIGNAL_PUBLISH", "1")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=("kr", "us"), required=True)
    ap.add_argument("--date", type=date.fromisoformat, default=date.today())
    ap.add_argument("--dry-run", action="store_true", help="compute and print; write nothing")
    ap.add_argument("--equity", type=float, default=None, help="equity for sizing; omitted = no quantity")
    args = ap.parse_args(argv)

    mode = "dry-run" if args.dry_run else os.environ.get("INVERSE_LANE_MODE", "off").strip().lower()
    if mode == "off":
        print("INVERSE_LANE_MODE=off: nothing to do")
        return 0
    if mode not in ("dry-run", "observe"):
        print(f"unsupported mode {mode!r}: S1 has no order path", file=sys.stderr)
        return 2

    from prism_inverse.adapters.fakes import FakeEvents, MemoryStore
    from prism_inverse.adapters.kis_data import KisKrData, KisUsData
    from prism_inverse.adapters.long_lane_db import LongLaneDb
    from prism_inverse.adapters.prism_events import PrismEvents
    from prism_inverse.adapters.registry import load_registry
    from prism_inverse.adapters.sqlite_store import SqliteStore
    from prism_inverse.app.cycle import run_cycle
    from prism_inverse.domain.policy import InversePolicy

    market = args.market.upper()
    kis = KisKrData() if market == "KR" else KisUsData()
    observe = mode == "observe"
    result = run_cycle(
        market, args.date, load_registry(), kis, kis,
        SqliteStore() if observe else MemoryStore(), PrismEvents() if observe else FakeEvents(),
        LongLaneDb(), InversePolicy(), args.equity, mode=mode,
    )
    for d in result.decisions:
        print(f"{d.action:5} {d.lane:5} {d.etf or '-':8} {d.underlying or '-':8} {d.reason}"
              + (f" qty={d.qty} stop={d.stop_underlying:.2f}" if d.qty else ""))
    for code, err in sorted(result.data_errors.items()):
        print(f"DATA  {code}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
