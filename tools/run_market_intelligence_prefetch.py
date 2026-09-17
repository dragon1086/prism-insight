"""Isolated, bounded daily ETF prefetch. No LLM, broker, or authentication writes."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core.market_intelligence import ETF_LABELS, ROOT, build_etf_packet


def collect(reference_date):
    # Separate per-analysis-date cache: never relabel old observations as fresh.
    day = datetime.strptime(reference_date, "%Y%m%d")
    if day.date() > datetime.now(ZoneInfo("America/New_York")).date():
        raise ValueError("future_US_analysis_date_could_include_unfinished_session")
    cache = ROOT / "runtime/market_intelligence" / f"us_etf_{reference_date}.json"
    try:
        packet = json.loads(cache.read_text())
        if (packet.get("reference_date") == reference_date
                and packet.get("contract") == "market_etf_rotation_v1"
                and packet.get("calendar_verified") is True and packet.get("input_sha256")):
            return packet
    except (OSError, ValueError):
        pass
    import yfinance as yf
    import pandas_market_calendars as mcal
    market_days = [day.date().isoformat() for day in mcal.get_calendar("NYSE").valid_days(
        start_date=(day - timedelta(days=150)).strftime("%Y-%m-%d"),
        end_date=(day - timedelta(days=1)).strftime("%Y-%m-%d"))]
    frame = yf.download(list(ETF_LABELS), start=(day - timedelta(days=150)).strftime("%Y-%m-%d"),
                        end=day.strftime("%Y-%m-%d"), auto_adjust=True, progress=False, threads=4, timeout=5)
    series = {}
    for symbol in ETF_LABELS:
        try:
            series[symbol] = [(str(date.date()), float(value)) for date, value in frame["Close"][symbol].items()]
        except (KeyError, TypeError, ValueError):
            series[symbol] = []
    packet = build_etf_packet(series, reference_date, market_days)
    if packet["status"] == "AVAILABLE":
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(packet))
        os.replace(temporary, cache)
    return packet


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.date)))
