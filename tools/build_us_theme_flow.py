"""Compute today's US theme flow before a US batch (cron, a few minutes ahead) -> runtime/us_theme_flow.json.

    python tools/build_us_theme_flow.py --mode morning|afternoon

Read-only: yfinance quotes for the theme map members and the local US headline store. No model call, no orders,
no channel sends. The US alert renders the file when it is fresh (prism_core/us_theme_flow.py).
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prism_core import us_theme_flow as flow  # noqa: E402

logger = logging.getLogger("build_us_theme_flow")
NY = ZoneInfo("America/New_York")


def quote_changes(tickers, trade_date):
    """{ticker: % change of the latest bar vs the previous close} for bars dated `trade_date` (New York)."""
    import yfinance as yf
    day = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    symbols = {t: t.replace(".", "-") for t in tickers}
    data = yf.download(list(symbols.values()), period="5d", interval="1d", auto_adjust=False, actions=False,
                       progress=False, threads=True, timeout=20, group_by="ticker")
    out = {}
    for ticker, symbol in symbols.items():
        try:
            closes = data[symbol]["Close"].dropna()
        except (KeyError, TypeError):
            continue
        if len(closes) >= 2 and str(closes.index[-1])[:10] == day and float(closes.iloc[-2]) > 0:
            out[ticker] = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["morning", "afternoon"], required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = time.monotonic()
    path = flow.latest_map_path()
    if path is None:
        logger.warning("[US_THEME_FLOW] no theme map; skipped")
        return
    theme_map = json.loads(path.read_text())
    tickers = sorted({m["code"] for t in theme_map["themes"] for m in t["members"]})
    trade_date = datetime.now(NY).strftime("%Y%m%d")
    changes = quote_changes(tickers, trade_date)
    conn = None
    try:
        from prism_core import kr_news_store as store
        conn = store.connect(store.db_path("US"), readonly=True)
    except Exception as error:  # noqa: BLE001 - headlines are optional
        logger.warning("[US_THEME_FLOW] headline store unavailable (%s)", type(error).__name__)
    result = flow.build(theme_map, changes, trade_date=trade_date, mode=args.mode, conn=conn)
    tmp = flow.FLOW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    tmp.replace(flow.FLOW_PATH)
    logger.info("[US_THEME_FLOW] mode=%s date=%s map=%s quoted=%d/%d scored=%d picked=%s %.0fs", args.mode,
                trade_date, path.name, len(changes), len(tickers), result["themes_scored"],
                ",".join(f"{t['name']}{t['median']:+.1f}" for t in result["themes"]), time.monotonic() - started)


if __name__ == "__main__":
    main()
