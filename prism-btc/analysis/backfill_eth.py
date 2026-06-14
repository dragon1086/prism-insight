# analysis/backfill_eth.py — ETHUSDT 백필 (멀티에셋 교차검증용, 별도 DB)
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collector.bybit_public as bp

bp.BYBIT_SYMBOL = "ETHUSDT"  # 모듈 바인딩 오버라이드 (BTC DB는 건드리지 않음)

from collector.backfill import backfill_all

DB = str(Path(__file__).resolve().parents[1] / "state" / "market_eth.db")

if __name__ == "__main__":
    results = backfill_all(db_path=DB)
    print("\n=== ETH Backfill Summary ===")
    for tf, count in results.items():
        print(f"  {tf:>4s}: {count:>8,} rows")
