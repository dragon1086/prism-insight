# analysis/round8_spot_collector.py — Phase B B-0: Binance 현물 1d 수집기
#
# 설계: tasks/btc_phase_b_design.md §6. 오닐 M(distribution/FTD) 번역에는
# 파생이 아닌 '현물 거래량'이 필요하다 — Binance spot 이 소스.
# 저장: prism-btc/state/btc_spot.db (신규 — 기존 btc_market.db 무접촉).
# fetched_at 기록으로 point-in-time 감사 가능. 증분 갱신 지원 (idempotent upsert).
#
# 실행: ../.venv-bt/bin/python -m analysis.round8_spot_collector
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import requests

BASE = "https://api.binance.com/api/v3/klines"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INTERVAL = "1d"
START_MS = 1502928000000  # 2017-08-17 (Binance 상장 초기)
DB = Path(__file__).resolve().parents[1] / "state" / "btc_spot.db"

DDL = """
CREATE TABLE IF NOT EXISTS spot_klines (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  open_time INTEGER NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, quote_volume REAL,
  confirmed INTEGER NOT NULL DEFAULT 0,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (symbol, timeframe, open_time)
)
"""


def fetch(symbol: str, start_ms: int) -> list:
    params = {"symbol": symbol, "interval": INTERVAL,
              "startTime": start_ms, "limit": 1000}
    resp = requests.get(BASE, params=params,
                        headers={"User-Agent": "prism-btc-research"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute(DDL)
    now_ms = int(time.time() * 1000)

    for sym in SYMBOLS:
        row = conn.execute(
            "SELECT MAX(open_time) FROM spot_klines WHERE symbol=? AND timeframe=?",
            (sym, INTERVAL)).fetchone()
        cursor = (row[0] + 1) if row and row[0] else START_MS
        total = 0
        while True:
            batch = fetch(sym, cursor)
            if not batch:
                break
            for k in batch:
                open_time, o, h, l, c, v = int(k[0]), k[1], k[2], k[3], k[4], k[5]
                close_time, qv = int(k[6]), k[7]
                confirmed = 1 if close_time < now_ms else 0
                conn.execute(
                    "INSERT INTO spot_klines VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(symbol,timeframe,open_time) DO UPDATE SET "
                    "open=excluded.open, high=excluded.high, low=excluded.low, "
                    "close=excluded.close, volume=excluded.volume, "
                    "quote_volume=excluded.quote_volume, confirmed=excluded.confirmed, "
                    "fetched_at=excluded.fetched_at",
                    (sym, INTERVAL, open_time, float(o), float(h), float(l),
                     float(c), float(v), float(qv), confirmed, now_ms))
            total += len(batch)
            cursor = int(batch[-1][6]) + 1  # last close_time + 1ms
            conn.commit()
            if len(batch) < 1000:
                break
            time.sleep(0.3)  # rate-limit 예의
        n, lo, hi = conn.execute(
            "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM spot_klines "
            "WHERE symbol=? AND confirmed=1", (sym,)).fetchone()
        print(f"{sym}: fetched {total}, confirmed rows {n}, "
              f"range {time.strftime('%Y-%m-%d', time.gmtime(lo/1000))} ~ "
              f"{time.strftime('%Y-%m-%d', time.gmtime(hi/1000))}")
    conn.close()
    print(f"saved → {DB}")


if __name__ == "__main__":
    main()
