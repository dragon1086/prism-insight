# analysis/backfill_funding.py — Bybit 펀딩비 히스토리 백필 (BTCUSDT)
# 목적: 엔진의 "항상 불리한 고정 -0.01%/8h" 비관 가정을 실데이터로 교체 (모델 정밀화)
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from collector.store import get_connection
from engine.config import BYBIT_BASE_URL

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
URL = BYBIT_BASE_URL + "/v5/market/funding/history"


def main():
    conn = get_connection(None)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS funding ("
        " funding_time INTEGER PRIMARY KEY, rate REAL NOT NULL)"
    )
    end_ms = None
    total = 0
    while True:
        params = {"category": "linear", "symbol": SYMBOL, "limit": 200}
        if end_ms:
            params["endTime"] = end_ms
        r = requests.get(URL, params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()["result"]["list"]  # newest first
        if not rows:
            break
        conn.executemany(
            "INSERT OR REPLACE INTO funding VALUES (?, ?)",
            [(int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in rows],
        )
        conn.commit()
        total += len(rows)
        oldest = int(rows[-1]["fundingRateTimestamp"])
        if oldest <= 1577836800000 or len(rows) < 200:  # 2020-01-01 or 마지막 페이지
            break
        end_ms = oldest - 1
        time.sleep(0.15)
    n, lo, hi = conn.execute(
        "SELECT COUNT(*), MIN(funding_time), MAX(funding_time) FROM funding"
    ).fetchone()
    import datetime as dt
    print(f"funding rows={n} range={dt.datetime.utcfromtimestamp(lo/1000):%Y-%m-%d}"
          f"~{dt.datetime.utcfromtimestamp(hi/1000):%Y-%m-%d}")
    avg, pos = conn.execute("SELECT AVG(rate), AVG(rate>0) FROM funding").fetchone()
    print(f"avg rate={avg*100:.4f}%/8h, positive(롱이 지불) 비율={pos*100:.0f}%")
    conn.close()


if __name__ == "__main__":
    main()
