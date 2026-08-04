#!/usr/bin/env python3
"""When does the KRX OPEN API publish a session's data?

The answer decides how the screening batch can use this source, and it cannot be
reasoned out — the service documents no publication schedule. So this probe runs
at fixed points through the trading day and records what is actually there.

What the answer changes:

- If session T-1 is available before 09:30, the morning batch can take
  ``prev_snapshot`` and ``cap_df`` from the OPEN API and drop that part of the
  KRX scraping path.
- If session T appears shortly after the 15:30 close, the afternoon numbers that
  go into published reports can come from the sanctioned source the same day.
- If publication is late or irregular, the batch has to keep a second source and
  the migration is partial rather than complete.

One observation on 2026-08-04 23:44 KST is the starting point: session 2026-08-03
was present, session 2026-08-04 was not. So the lag is at least overnight, and
the open question is where between those two points it lands.

Writes one JSON line per run to ``logs/krx_openapi_availability.jsonl`` so runs
accumulate into a timeline rather than overwriting each other.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKETS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
DEFAULT_LOG = Path("logs/krx_openapi_availability.jsonl")


def _auth_key() -> str:
    key = os.getenv("KRX_OPENAPI_AUTH_KEY", "")
    if not key:
        # Only load dotenv when the variable is absent, so a deliberately
        # exported key in the environment always wins.
        try:
            from dotenv import load_dotenv

            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
            key = os.getenv("KRX_OPENAPI_AUTH_KEY", "")
        except ImportError:
            pass
    if not key:
        sys.exit("KRX_OPENAPI_AUTH_KEY is not set")
    return key


def probe_date(date: str, key: str, timeout: float = 20.0) -> dict:
    """Row counts per market for one session date."""
    result: dict[str, object] = {"date": date}
    total = 0
    for market, path in MARKETS.items():
        try:
            response = requests.get(
                f"{BASE_URL}/{path}",
                params={"basDd": date},
                headers={"AUTH_KEY": key},
                timeout=timeout,
            )
            if response.status_code != 200:
                result[market] = f"HTTP {response.status_code}"
                continue
            rows = response.json().get("OutBlock_1", [])
            result[market] = len(rows)
            total += len(rows)
        except Exception as exc:  # noqa: BLE001 - a probe records failures, it does not raise
            result[market] = f"{type(exc).__name__}: {exc}"
    result["total"] = total
    return result


def recent_dates(now: datetime, back: int) -> list[str]:
    return [(now - timedelta(days=n)).strftime("%Y%m%d") for n in range(back + 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", nargs="*", help="YYYYMMDD; default: today and the 3 days before")
    parser.add_argument("--back", type=int, default=3)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--label", default="", help="tag for this probe point, e.g. 'pre-open'")
    args = parser.parse_args()

    key = _auth_key()
    now = datetime.now(KST)
    dates = args.dates or recent_dates(now, args.back)

    record = {
        "probed_at": now.isoformat(timespec="seconds"),
        "label": args.label,
        "weekday": now.strftime("%a"),
        "results": [probe_date(d, key) for d in dates],
    }

    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    stamp = now.strftime("%m-%d %H:%M")
    parts = []
    for item in record["results"]:
        total = item["total"]
        parts.append(f"{item['date']}={total if total else '없음'}")
    print(f"[{stamp} KST{' ' + args.label if args.label else ''}] " + "  ".join(parts))

    # Say plainly whether the previous session is usable right now, because that
    # is the single fact the morning batch design depends on.
    today = now.strftime("%Y%m%d")
    for item in record["results"]:
        if item["date"] != today and item["total"]:
            print(f"  → 가장 최근 조회 가능 세션: {item['date']} ({item['total']}건)")
            break
    else:
        print("  → 조회 가능한 최근 세션 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
