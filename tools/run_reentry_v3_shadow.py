"""Re-entry v3 runner (campaign re-entry). One BUY recheck per live trigger when
REENTRY_V3_LLM_RECHECK=true (.env or environment; default off), never with --no-llm or a dry run.
Real buys only when REENTRY_V3_LIVE_ENABLED=true (default off) for a market in
REENTRY_V3_LIVE_MARKETS, at most 2 per market and session (prism_core/reentry_v3_live.py).

    python tools/run_reentry_v3_shadow.py --market KR --phase intraday   # 14:00 KST decision
    python tools/run_reentry_v3_shadow.py --market KR --phase close      # 16:40 KST after the close
    python tools/run_reentry_v3_shadow.py --market US --phase intraday   # 13:50 New York decision
    python tools/run_reentry_v3_shadow.py --market US --phase close      # 17:20 New York
    python tools/run_reentry_v3_shadow.py --market KR --phase intraday --dry-run   # no LLM, no order, no write

The decision run sits before the afternoon batches (KR 14:46, US 14:30 ET) and before the KR
closing auction (15:20), where KIS current-price and order behaviour differ.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from observability import reentry_v3_shadow as V3  # noqa: E402
from tools import run_reentry_shadow as collectors  # noqa: E402

HISTORY_DAYS = 480          # 52-week high for the BUY 2a target + 70-day enrolment + 60-session watch
KR_CACHE_DIR = ROOT / "runtime/reentry_v3_kr_daily_cache"
QUOTE_MAX_AGE_SECONDS = 120     # same freshness bound as the B3 quote input
CALENDARS = {"KR": "XKRX", "US": "NYSE"}
log = logging.getLogger("reentry_v3_shadow")


def _kospi_rows(completed):
    """KOSPI daily rows for the deterministic KR regime, kept in the same per-session cache."""
    import pandas as pd

    from cores.market_data.kis_source import KisSource
    from observability.reentry_shadow import _atomic
    cache = KR_CACHE_DIR / (completed + ".json")
    try:
        data = json.loads(cache.read_text()) if cache.exists() else {}
    except ValueError:
        data = {}
    if "1001" not in data:
        start = (pd.Timestamp(completed) - pd.Timedelta(days=HISTORY_DAYS)).strftime("%Y%m%d")
        try:
            time.sleep(collectors.KIS_SPACING_SECONDS)
            rows = collectors._frame_rows(KisSource().index_history("1001", start, completed.replace("-", "")),
                                          completed)
        except Exception as error:  # noqa: BLE001 - the regime stays explicitly missing
            log.info("KOSPI history unavailable for the v3 regime (%s)", type(error).__name__)
            return []
        if rows and rows[-1]["date"] == completed:
            data["1001"] = rows
            _atomic(cache, data)
    return data.get("1001") or []


def collect(market, tickers, completed):
    collectors.HISTORY_DAYS = HISTORY_DAYS
    if market == "KR":
        out = collectors.collect_kr(tickers, completed, cache_dir=KR_CACHE_DIR)
        out["__regime_rows"] = _kospi_rows(completed)
        return out
    out = collectors.collect_us(tickers, completed)
    out["__regime_rows"] = next(iter((out.get("__benchmark_rows") or {}).values()), [])   # SPY (S&P 500 proxy)
    return out


def decision_day(market, now=None):
    """Today's session date while its regular session is open, else None (holiday / outside hours)."""
    import pandas as pd
    import pandas_market_calendars as mcal

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(V3.TZ[market])).date()
    schedule = mcal.get_calendar(CALENDARS[market]).schedule(start_date=pd.Timestamp(local),
                                                             end_date=pd.Timestamp(local))
    if schedule.empty:
        return None
    opened, closed = schedule.iloc[0]["market_open"].to_pydatetime(), schedule.iloc[0]["market_close"].to_pydatetime()
    return local.isoformat() if opened <= now < closed else None


def _num(value):
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def kr_quote_fn():
    """KIS domestic current price (the quote the B3 KR inputs read), spaced for the shared quota."""
    from cores.market_data.kis_source import KisSource
    source = KisSource()

    def quote(ticker):
        time.sleep(collectors.KIS_SPACING_SECONDS)
        try:
            output, observed = source._current_quote(ticker)
        except Exception as error:  # noqa: BLE001 - explicit missing quote
            log.info("KIS quote unavailable for a v3 watch (%s)", type(error).__name__)
            return None
        price, low = _num(output.get("stck_prpr")), _num(output.get("stck_lwpr"))
        return {"price": price, "low": low if low and price and low <= price else None,
                "open": _num(output.get("stck_oprc")), "high": _num(output.get("stck_hgpr")),
                "volume": _num(output.get("acml_vol")), "observed_at": observed.astimezone(timezone.utc).isoformat(),
                "source": "kis-domestic-current-price"} if price else None
    return quote


_US_KEYS = ("regularMarketPrice", "regularMarketTime", "regularMarketDayLow", "regularMarketDayHigh",
            "regularMarketOpen", "regularMarketVolume", "dayLow")


def _us_quote_child(symbol, connection):
    """yfinance quote in a spawned child (pattern of prism_core.oneil_runtime_inputs.fetch_quote)."""
    import contextlib
    import io
    import tempfile
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                tempfile.TemporaryDirectory() as cache:
            import yfinance as yf
            yf.set_tz_cache_location(cache)
            info = yf.Ticker(symbol).get_info()
            result = {key: info.get(key) for key in _US_KEYS}
    except Exception:  # noqa: BLE001 - empty result is an explicit missing quote
        result = {}
    connection.send(result)
    connection.close()


def us_quote_fn(timeout=15):
    def quote(ticker):
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker):
            return None
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_us_quote_child, args=(ticker, send), daemon=True)
        process.start()
        send.close()
        try:
            info = receive.recv() if receive.poll(timeout) else {}
        except EOFError:
            info = {}
        finally:
            receive.close()
            process.join(.1)
            if process.is_alive():
                process.terminate()
                process.join(1)
        price, stamp = _num(info.get("regularMarketPrice")), info.get("regularMarketTime")
        if not price or isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            return None
        observed = datetime.fromtimestamp(stamp, timezone.utc)
        if not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= QUOTE_MAX_AGE_SECONDS:
            return None
        low = _num(info.get("regularMarketDayLow")) or _num(info.get("dayLow"))
        return {"price": price, "low": low if low and low <= price else None,
                "open": _num(info.get("regularMarketOpen")), "high": _num(info.get("regularMarketDayHigh")),
                "volume": _num(info.get("regularMarketVolume")), "observed_at": observed.isoformat(),
                "source": "yfinance-regular-market"}
    return quote


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--phase", choices=["intraday", "close"], required=True,
                        help="intraday: decision run (KR 14:00 KST, US 13:50 ET); close: after-close ledger update")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(V3.DB_PATH))
    parser.add_argument("--state-root", default=str(V3.STATE_DIR))
    parser.add_argument("--reports-root", default=str(ROOT))
    parser.add_argument("--archive-db", default=str(V3.ARCHIVE_DB))
    parser.add_argument("--no-llm", action="store_true", help="skip the LLM recheck of new triggers")
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)      # explicit environment (e.g. cron) wins
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not V3.enabled(args.market) and not args.dry_run:
        log.info("reentry v3 shadow disabled for %s", args.market)
        return 0
    completed = collectors.completed_session(args.market)
    day, quote_fn = None, None
    if args.phase == "intraday":
        day = decision_day(args.market)
        if day is None or day <= completed:
            log.info("reentry v3 intraday: no open %s session now", args.market)
            return 0
        quote_fn = kr_quote_fn() if args.market == "KR" else us_quote_fn()
    summary = V3.run(args.market, completed, collector=lambda t, c: collect(args.market, t, c), phase=args.phase,
                     decision_day=day, quote_fn=quote_fn, db_path=args.db, root=args.state_root,
                     reports_root=args.reports_root, archive_db=args.archive_db, dry_run=args.dry_run,
                     llm_recheck=False if args.no_llm else None)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
