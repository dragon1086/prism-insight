"""
price_tracker.py — Long-term price history tracker for PRISM archived tickers.

Fetches daily closes from report_date to today, stores in ticker_price_history,
and computes performance aggregates in report_enrichment:
  - return_180d, return_365d, return_current
  - max_return_since / max_return_date  (best return ever achieved since report)
  - max_drawdown / max_drawdown_date    (worst drawdown from entry price)
  - drawdown_from_peak                 (current price vs all-time high since report)

Usage:
    python -m cores.archive.price_tracker
    python -m cores.archive.price_tracker --ticker 005930 --market kr
    python -m cores.archive.price_tracker --dry-run
    python -m cores.archive.price_tracker --concurrency 2
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Aggregate computation
# ---------------------------------------------------------------------------

def _compute_aggregates(
    closes: Dict[str, float],
    base_date: str,
    base_price: float,
) -> Dict[str, Any]:
    """
    Given daily closes (date→price) strictly AFTER base_date and the entry price,
    compute long-term performance metrics.

    Returns dict with keys:
        return_current, price_current,
        return_180d, return_365d,
        max_return_since, max_return_date,
        max_drawdown, max_drawdown_date,
        drawdown_from_peak
    All return values are percentages (× 100), rounded to 4 decimal places.
    None when insufficient data.
    """
    agg: Dict[str, Any] = {
        "return_current": None,
        "price_current": None,
        "return_180d": None,
        "return_365d": None,
        "max_return_since": None,
        "max_return_date": None,
        "max_drawdown": None,
        "max_drawdown_date": None,
        "drawdown_from_peak": None,
    }

    post = {d: p for d, p in closes.items() if d > base_date and p > 0}
    if not post or base_price <= 0:
        return agg

    sorted_dates = sorted(post.keys())
    base_dt = datetime.strptime(base_date, "%Y-%m-%d")

    # Current (latest available)
    latest_date = sorted_dates[-1]
    latest_close = post[latest_date]
    agg["price_current"] = round(latest_close, 4)
    agg["return_current"] = round((latest_close - base_price) / base_price * 100, 4)

    # Fixed-window returns (search ±5 trading days)
    for days, key in [(180, "return_180d"), (365, "return_365d")]:
        target_dt = base_dt + timedelta(days=days)
        price = None
        for offset in range(6):
            for sign in (-1, 1):
                candidate = (target_dt + timedelta(days=offset * sign)).strftime("%Y-%m-%d")
                if candidate in post and candidate > base_date:
                    price = post[candidate]
                    break
            if price is not None:
                break
        if price is not None:
            agg[key] = round((price - base_price) / base_price * 100, 4)

    # Max return since entry
    max_ret = None
    max_ret_date = None
    for d in sorted_dates:
        ret = (post[d] - base_price) / base_price * 100
        if max_ret is None or ret > max_ret:
            max_ret = ret
            max_ret_date = d
    if max_ret is not None:
        agg["max_return_since"] = round(max_ret, 4)
        agg["max_return_date"] = max_ret_date

    # Max drawdown from entry (worst dip below entry price)
    min_ret = None
    min_ret_date = None
    for d in sorted_dates:
        ret = (post[d] - base_price) / base_price * 100
        if min_ret is None or ret < min_ret:
            min_ret = ret
            min_ret_date = d
    if min_ret is not None:
        agg["max_drawdown"] = round(min_ret, 4)
        agg["max_drawdown_date"] = min_ret_date

    # Drawdown from peak (current vs all-time high since report)
    max_close = max(post[d] for d in sorted_dates)
    if max_close > 0:
        agg["drawdown_from_peak"] = round((latest_close - max_close) / max_close * 100, 4)

    return agg


# ---------------------------------------------------------------------------
# Price fetcher helpers (reuse enricher sync methods)
# ---------------------------------------------------------------------------

def _fetch_kr_daily(ticker: str, start_date: str, end_date: str) -> Dict[str, float]:
    """Fetch KR daily close prices via KIS API. Returns {date: close}."""
    try:
        from cores.archive.data_enricher import KRDataEnricher  # type: ignore[import]
        enricher = KRDataEnricher()
        raw = enricher._sync_fetch_daily(ticker, start_date, end_date)
        return {d: v["close"] for d, v in raw.items() if v.get("close", 0) > 0}
    except Exception as e:
        logger.warning(f"[KR] Price fetch failed for {ticker}: {e}")
        return {}


def _fetch_us_daily(ticker: str, start_date: str, end_date: str) -> Dict[str, float]:
    """Fetch US daily close prices via yfinance. Returns {date: close}."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(start=start_date, end=end_date, auto_adjust=True)
        if hist.empty:
            return {}
        return {str(idx)[:10]: float(row["Close"]) for idx, row in hist.iterrows()
                if float(row["Close"]) > 0}
    except Exception as e:
        logger.warning(f"[US] Price fetch failed for {ticker}: {e}")
        return {}


# ---------------------------------------------------------------------------
# PriceTracker
# ---------------------------------------------------------------------------

class PriceTracker:
    """
    Fetches daily close prices from report_date to today for each archived report
    and computes long-term performance aggregates.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        from cores.archive.archive_db import ARCHIVE_DB_PATH  # type: ignore[import]
        self.db_path = db_path or str(ARCHIVE_DB_PATH)

    async def update_report(
        self,
        report_id: int,
        ticker: str,
        market: str,
        report_date: str,
        price_at_report: float,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetch daily closes from report_date to today, compute aggregates,
        and persist to DB unless dry_run=True.

        Returns summary dict: {report_id, ticker, rows_written, aggregates}
        """
        today = datetime.today().strftime("%Y-%m-%d")

        loop = asyncio.get_running_loop()
        if market == "kr":
            closes = await loop.run_in_executor(
                None, _fetch_kr_daily, ticker, report_date, today
            )
        else:
            closes = await loop.run_in_executor(
                None, _fetch_us_daily, ticker, report_date, today
            )

        if not closes:
            logger.warning(f"[{market.upper()}] No price data for {ticker} from {report_date}")
            return {"report_id": report_id, "ticker": ticker, "rows_written": 0, "aggregates": {}}

        # Build rows for ticker_price_history
        history_rows = [
            {
                "report_id": report_id,
                "ticker": ticker,
                "market": market,
                "price_date": d,
                "close": p,
                "return_pct": round((p - price_at_report) / price_at_report * 100, 4),
            }
            for d, p in closes.items()
            if d > report_date
        ]

        aggregates = _compute_aggregates(closes, report_date, price_at_report)
        aggregates["last_price_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not dry_run:
            from cores.archive.archive_db import (  # type: ignore[import]
                bulk_upsert_price_history,
                update_enrichment_performance,
            )
            rows_written = await bulk_upsert_price_history(history_rows, self.db_path)
            await update_enrichment_performance(report_id, aggregates, self.db_path)
        else:
            rows_written = len(history_rows)
            logger.info(
                f"[DRY-RUN] {ticker} {report_date}: {rows_written} rows, "
                f"return_current={aggregates.get('return_current')}%, "
                f"max_return={aggregates.get('max_return_since')}%, "
                f"max_drawdown={aggregates.get('max_drawdown')}%"
            )

        return {
            "report_id": report_id,
            "ticker": ticker,
            "rows_written": rows_written,
            "aggregates": aggregates,
        }

    async def run(
        self,
        market: Optional[str] = None,
        ticker: Optional[str] = None,
        dry_run: bool = False,
        concurrency: int = 3,
    ) -> Dict[str, Any]:
        """
        Update price history for all eligible reports.

        Returns summary: {processed, skipped, errors, results}
        """
        from cores.archive.archive_db import (  # type: ignore[import]
            init_db,
            get_reports_for_price_update,
        )
        await init_db(self.db_path)
        reports = await get_reports_for_price_update(
            market=market, ticker=ticker, db_path=self.db_path
        )

        if not reports:
            logger.info("No reports need price update.")
            return {"processed": 0, "skipped": 0, "errors": 0, "results": []}

        logger.info(f"Updating price history for {len(reports)} reports "
                    f"(concurrency={concurrency}, dry_run={dry_run})")

        sem = asyncio.Semaphore(concurrency)
        processed = 0
        errors = 0
        results = []

        async def _update_one(r: Dict) -> None:
            nonlocal processed, errors
            async with sem:
                try:
                    result = await self.update_report(
                        report_id=r["id"],
                        ticker=r["ticker"],
                        market=r["market"],
                        report_date=r["report_date"],
                        price_at_report=r["price_at_analysis"],
                        dry_run=dry_run,
                    )
                    results.append(result)
                    processed += 1
                    logger.info(
                        f"[{r['market'].upper()}] {r['ticker']} {r['report_date']}: "
                        f"{result['rows_written']} rows, "
                        f"return_current={result['aggregates'].get('return_current')}%"
                    )
                except Exception as e:
                    errors += 1
                    logger.error(f"Failed to update {r['ticker']} {r['report_date']}: {e}",
                                 exc_info=True)

        await asyncio.gather(*[_update_one(r) for r in reports])

        summary = {
            "processed": processed,
            "skipped": len(reports) - processed - errors,
            "errors": errors,
            "results": results,
        }
        logger.info(f"Price update complete: {summary}")
        return summary


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

async def run_update(
    market: Optional[str] = None,
    ticker: Optional[str] = None,
    dry_run: bool = False,
    concurrency: int = 3,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Module-level convenience wrapper around PriceTracker.run()."""
    return await PriceTracker(db_path=db_path).run(
        market=market, ticker=ticker, dry_run=dry_run, concurrency=concurrency
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Update PRISM archive long-term price history")
    parser.add_argument("--ticker", default=None, help="Update only this ticker")
    parser.add_argument("--market", choices=["kr", "us"], default=None,
                        help="Update only this market")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be updated without writing to DB")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Parallel fetch limit (default 3; use 2 on 1-core server)")
    args = parser.parse_args()

    result = asyncio.run(run_update(
        market=args.market,
        ticker=args.ticker,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    ))
    print(f"\nDone: processed={result['processed']}, errors={result['errors']}")
