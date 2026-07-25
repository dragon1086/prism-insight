# analysis/round8b_collect_basket.py — 라운드8b: 알트 바스켓 현물 1d 수집
#
# 설계: tasks/btc_round8b_leadership_robustness.md §E-A.
# ETH 외 대조군(동일가중 알트 바스켓)을 위해 주요 알트 현물 일봉을 추가 수집한다.
# 저장은 기존 state/btc_spot.db 의 spot_klines 테이블 (스키마 동일, symbol 만 추가).
# 미상장 구간은 자연히 데이터가 없어 바스켓에서 제외됨 (survivorship 최소화).
#
# 실행: ../.venv-bt/bin/python -m analysis.round8b_collect_basket
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import requests

BASE = "https://api.binance.com/api/v3/klines"
INTERVAL = "1d"
START_MS = 1502928000000  # 2017-08-17
DB = Path(__file__).resolve().parents[1] / "state" / "btc_spot.db"

# ETH 는 이미 round8_spot_collector 가 수집 — 여기선 바스켓 추가분만.
BASKET = ("BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT", "DOGEUSDT", "LTCUSDT")

DDL = """
CREATE TABLE IF NOT EXISTS spot_klines (
  symbol TEXT NOT NULL, timeframe TEXT NOT NULL, open_time INTEGER NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL, quote_volume REAL,
  confirmed INTEGER NOT NULL DEFAULT 0, fetched_at INTEGER NOT NULL,
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
    for sym in BASKET:
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
                ot, o, h, l, c, v = int(k[0]), k[1], k[2], k[3], k[4], k[5]
                close_time, qv = int(k[6]), k[7]
                confirmed = 1 if close_time < now_ms else 0
                conn.execute(
                    "INSERT INTO spot_klines VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(symbol,timeframe,open_time) DO UPDATE SET "
                    "close=excluded.close, volume=excluded.volume, "
                    "confirmed=excluded.confirmed, fetched_at=excluded.fetched_at",
                    (sym, INTERVAL, ot, float(o), float(h), float(l), float(c),
                     float(v), float(qv), confirmed, now_ms))
            total += len(batch)
            cursor = int(batch[-1][6]) + 1
            conn.commit()
            if len(batch) < 1000:
                break
            time.sleep(0.3)
        n, lo, hi = conn.execute(
            "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM spot_klines "
            "WHERE symbol=? AND confirmed=1", (sym,)).fetchone()
        print(f"{sym}: +{total} rows, confirmed {n}, "
              f"{time.strftime('%Y-%m-%d', time.gmtime(lo/1000))} ~ "
              f"{time.strftime('%Y-%m-%d', time.gmtime(hi/1000))}")
    conn.close()
    print(f"saved → {DB}")


if __name__ == "__main__":
    main()
