#!/usr/bin/env python3
"""Daily extreme-fear alert for the user (maintenance chat). Information only: no orders, no trading input.

All three at the US close: QQQ RSI(14, Wilder) <= 30, CNN Fear & Greed <= 10, VIX close >= 30.
The rule comes from a SOXL/KORU post; QQQ is the RSI series that reproduces its signal dates (2026-11-25 check).
Cron: 45 16 * * 1-5 America/New_York. Missing or stale data raises, and tools/ops_watchdog.py reports it.
"""
import argparse
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
RSI_MAX, FEAR_GREED_MAX, VIX_MIN = 30, 10, 30
CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
# CNN answers 418 to non-browser clients.
CNN_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36",
               "Referer": "https://edition.cnn.com/"}


def wilder_rsi(closes, n=14):
    diff = closes.diff().dropna()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    loss = (-diff).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    return float(100 * gain / (gain + loss))


def checks(rsi, fear_greed, vix):
    if not all(math.isfinite(v) for v in (rsi, fear_greed, vix)):
        raise ValueError(f"missing input: rsi={rsi} fear_greed={fear_greed} vix={vix}")
    return rsi <= RSI_MAX, fear_greed <= FEAR_GREED_MAX, vix >= VIX_MIN


def message(day, rsi, fear_greed, vix):
    return (f"[극단 공포 조건 충족] {day} 미국 종가 기준\n"
            f"- 나스닥100(QQQ) RSI {rsi:.1f} (30 이하)\n"
            f"- 공포탐욕지수 {fear_greed:.1f} (10 이하)\n"
            f"- VIX {vix:.1f} (30 이상)\n"
            "과거 같은 신호 뒤에도 SOXL이 추가로 최대 69%(2020-02) 더 빠진 적이 있습니다. 정보 알림이며 주문은 없습니다.")


def closes(ticker):
    from prism_core.us_market_data import history
    return history(ticker, period="2y", auto_adjust=False)["Close"].dropna()


def qqq_closes(qqq, ndx, day):
    """Settled QQQ closes plus today's, carried by the Nasdaq-100 index return.

    KIS's daily bar for a stock keeps moving with after-hours trades (17:10 ET records, 2026-11-25 check); an index does not.
    """
    import pandas as pd
    settled = qqq[qqq.index.date < day]
    if len(ndx) < 2 or ndx.index[-1].date() != day or settled.empty or settled.index[-1].date() != ndx.index[-2].date():
        raise ValueError(f"QQQ/^NDX closes do not line up for {day}")
    today = settled.iloc[-1] * ndx.iloc[-1] / ndx.iloc[-2]
    return pd.concat([settled, pd.Series([today], index=[ndx.index[-1]])])


def fetch(day):
    import httpx
    response = httpx.get(CNN_URL, headers=CNN_HEADERS, timeout=20)
    response.raise_for_status()
    fg = response.json()["fear_and_greed"]
    if datetime.fromisoformat(fg["timestamp"]).astimezone(ET).date() != day:
        raise ValueError(f"fear & greed is stale: {fg['timestamp']}")
    vix = closes("^VIX")
    if vix.empty or vix.index[-1].date() != day:
        raise ValueError(f"no VIX close for {day}")
    return wilder_rsi(qqq_closes(closes("QQQ"), closes("^NDX"), day)), float(fg["score"]), float(vix.iloc[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from prism_core.add_plan import is_session
    from prism_core.ops_alert import send_ops_alert_sync

    day = datetime.now(ET).date()
    if not is_session("US", day):
        print(f"{day} not a US session")
        return
    for attempt in range(3):
        try:
            rsi, fear_greed, vix = fetch(day)
            break
        except Exception:  # provider lag right after the close; the last failure is raised
            if attempt == 2 or args.dry_run:
                raise
            time.sleep(180)
    met = all(checks(rsi, fear_greed, vix))
    text = message(day, rsi, fear_greed, vix)
    if args.dry_run:
        print(text)
    elif met and not send_ops_alert_sync(text):
        raise RuntimeError("extreme-fear alert was not delivered")
    print(f"{datetime.now(ZoneInfo('Asia/Seoul')):%Y-%m-%d %H:%M:%S} Extreme-fear US summary: met={met} "
          f"qqq_rsi={rsi:.1f} fear_greed={fear_greed:.1f} vix={vix:.1f}")


if __name__ == "__main__":
    main()
