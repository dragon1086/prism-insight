"""KR inverse signal recorder (observe only, no orders). Run after the KR close.

    python tools/run_inverse_signals.py --market kr --dry-run           # compute and print, write nothing
    INVERSE_SIGNALS_MODE=observe python tools/run_inverse_signals.py --market kr   # lane DB + events

``INVERSE_SIGNALS_MODE`` defaults to ``off``. Nothing schedules this; turning observe on is a
separate, user-approved server step. There is no order path in any mode.
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
    if args.market != "kr":
        print("unsupported market: the inverse signal recorder is KR only (US lane not built, S6)")
        return 0

    from prism_inverse.adapters.fakes import FakeEvents, MemoryStore
    from prism_inverse.adapters.kis_data import KisKrData
    from prism_inverse.adapters.prism_events import PrismEvents
    from prism_inverse.adapters.registry import load_kr_sectors
    from prism_inverse.adapters.sqlite_store import SqliteStore
    from prism_inverse.app.signals_cycle import run_signals

    observe = mode == "observe"
    sectors, bench = load_kr_sectors()
    result = run_signals(args.date, sectors, bench, KisKrData(min_interval=KIS_MIN_INTERVAL),
                         SqliteStore() if observe else MemoryStore(), PrismEvents() if observe else FakeEvents(),
                         mode=mode)
    for fam, (status, inputs, error) in result.days.items():
        extra = {k: inputs[k] for k in ("driver", "corr", "close", "ma50", "atr", "virtual") if inputs.get(k) is not None}
        print(f"{fam:12} {status:9} {extra}" + (f" error={error}" if error else ""))
    for t in result.trades:
        print(f"VIRTUAL {t.trade_id} {t.status} entry={t.entry_date} {t.entry_open} exit={t.exit_date} "
              f"{t.exit_open} {t.exit_reason or ''} ret={t.ret}" + (f" {t.error}" if t.error else ""))
    for code, err in sorted(result.data_errors.items()):
        print(f"DATA  {code}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
