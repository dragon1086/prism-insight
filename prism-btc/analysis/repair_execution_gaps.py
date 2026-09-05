"""Explicit offline market-data repair; never imports a trading adapter.

Fetch exact missing historical candles before any insertion. Confirmed data is
never updated. Stale unconfirmed rows inside confirmed history are refreshed
from source; incomplete/invalid responses abort the entire repair.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time

from collector.bybit_public import fetch_klines_page


def repair(conn, *, fetch=fetch_klines_page, apply=False):
    interval = 1800000
    times = [r[0] for r in conn.execute(
        "SELECT open_time FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time", ("30m",))]
    if len(times) < 2:
        raise ValueError("insufficient data")
    if any((b-a) <= 0 or (b-a) % interval for a,b in zip(times,times[1:])):
        raise ValueError("invalid candle grid")
    missing = {t for a,b in zip(times,times[1:]) for t in range(a+interval,b,interval)}
    if len(missing) > 5000:
        raise ValueError("repair exceeds bounded 5000-candle limit")
    result = {"missing_before": len(missing), "inserted": 0, "refreshed_unconfirmed": 0, "applied": apply}
    if not apply or not missing:
        return result
    remaining = set(missing)
    collected = {}
    while remaining:
        page = fetch("30m", end_ms=max(remaining))
        previous = len(remaining)
        for raw in page:
            stamp = int(raw[0])
            if stamp not in remaining:
                continue
            values = [float(v) for v in raw[1:7]]
            if len(values) != 6 or not all(math.isfinite(v) for v in values):
                raise ValueError("invalid OHLCV values")
            o,h,lo,c,volume,turnover = values
            if min(o,h,lo,c) <= 0 or min(volume,turnover) < 0 or not lo <= min(o,c) <= max(o,c) <= h:
                raise ValueError("inconsistent OHLCV")
            if stamp+interval > int(time.time()*1000):
                raise ValueError("unconfirmed candle")
            collected[stamp] = ("30m",stamp,*values,1)
            remaining.remove(stamp)
        if len(remaining) == previous:
            raise ValueError("exchange did not supply required candles; no writes performed")
    with conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO klines (timeframe,open_time,open,high,low,close,volume,turnover,confirmed) "
            "VALUES (?,?,?,?,?,?,?,?,?)", [collected[t] for t in sorted(collected)])
        result["inserted"] = conn.total_changes-before
        before = conn.total_changes
        for t, record in collected.items():
            conn.execute("UPDATE klines SET open=?,high=?,low=?,close=?,volume=?,turnover=?,confirmed=1 "
                         "WHERE timeframe=? AND open_time=? AND confirmed=0",
                         (*record[2:8],"30m",t))
        result["refreshed_unconfirmed"] = conn.total_changes-before
    result["missing_after"] = repair(conn)["missing_before"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    mode = "rw" if args.apply else "ro"
    with sqlite3.connect(f"file:{args.db}?mode={mode}", uri=True) as connection:
        print(json.dumps(repair(connection, apply=args.apply), sort_keys=True))
