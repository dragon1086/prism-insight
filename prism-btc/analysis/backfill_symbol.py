# analysis/backfill_symbol.py — 임의 심볼 백필 (멀티에셋, 별도 DB)
# usage: python analysis/backfill_symbol.py ETHUSDT|SOLUSDT|...
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collector.bybit_public as bp

symbol = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
bp.BYBIT_SYMBOL = symbol  # 모듈 바인딩 오버라이드 (BTC DB 불변)

from collector.backfill import backfill_all

suffix = symbol.replace("USDT", "").lower()
DB = str(Path(__file__).resolve().parents[1] / "state" / f"market_{suffix}.db")

if __name__ == "__main__":
    results = backfill_all(db_path=DB)
    print(f"\n=== {symbol} Backfill Summary ===")
    for tf, count in results.items():
        print(f"  {tf:>4s}: {count:>8,} rows")
