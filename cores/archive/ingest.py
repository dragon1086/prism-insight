"""
ingest.py — PRISM report archive ingest pipeline.

Entry points:
  ingest_report(file_path, market)        — single report (async)
  ingest_directory(dir_path, market, ...) — batch ingest (async)

Called fire-and-forget from orchestrators after generate_reports() completes.
"""

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from .archive_db import init_db, insert_report, upsert_enrichment, upsert_market_timeline  # type: ignore[import]
from .data_enricher import get_enricher  # type: ignore[import]

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Season 2 start — only ingest reports on or after this date
SEASON2_START = "2025-09-29"

# Module-level sentinel: avoid calling init_db() on every report in a batch
_db_initialized: bool = False


# ---------------------------------------------------------------------------
# Filename parser
# ---------------------------------------------------------------------------

def parse_report_filename(file_path: str) -> Optional[dict]:
    """
    Parse PRISM report filename into metadata.

    Supported formats:
      KR: {6-digit-ticker}_{company}_{YYYYMMDD}_{mode}_{model}.md
      US: {TICKER}_{company}_{YYYYMMDD}_{mode?}_{model}.md

    Algorithm:
      1. Split stem on '_'
      2. First segment = ticker
      3. Scan for date segment matching ^20[0-9]{6}$
      4. Between ticker and date = company name (rejoin with '_')
      5. After date = mode (morning/afternoon) and model (gpt/claude/o[0-9])
    """
    stem = Path(file_path).stem
    parts = stem.split("_")

    if len(parts) < 3:
        return None

    ticker = parts[0]

    if re.match(r"^\d{6}$", ticker):
        market = "kr"
    elif re.match(r"^[A-Z]{1,6}(\^)?$", ticker):
        market = "us"
    else:
        logger.debug(f"Cannot determine market from ticker '{ticker}' in {file_path}")
        return None

    date_idx = None
    for i, p in enumerate(parts):
        if re.match(r"^20\d{6}$", p):
            date_idx = i
            break

    if date_idx is None or date_idx < 2:
        return None

    company_name = "_".join(parts[1:date_idx])
    date_raw = parts[date_idx]
    report_date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"

    after_date = parts[date_idx + 1:]
    mode = "morning"
    model = "unknown"
    for p in after_date:
        p_lower = p.lower()
        if p_lower in ("morning", "afternoon", "evening", "analysis"):
            mode = p_lower
        elif p_lower.startswith("gpt") or p_lower.startswith("claude") or re.match(r"^o\d", p_lower):
            model = p

    return {
        "ticker": ticker,
        "company_name": company_name,
        "report_date": report_date,
        "mode": mode,
        "model": model,
        "market": market,
    }


# ---------------------------------------------------------------------------
# Performance tracker lookup (KR + US)
# ---------------------------------------------------------------------------

# Whitelist: maps market -> (db_path, table_name, date_column)
_TRACKER_CONFIG = {
    "kr": (
        PROJECT_ROOT / "stock_trading.db",
        "analysis_performance_tracker",
        "analyzed_date",
    ),
    "us": (
        PROJECT_ROOT / "prism-us" / "trading" / "us_stock_trading.db",
        "us_analysis_performance_tracker",
        "analysis_date",
    ),
}


def _get_tracker_data(ticker: str, report_date: str, market: str) -> dict:
    """
    Look up stop_loss and target_price from performance_tracker.
    Returns empty dict if not found or DB unavailable.
    """
    cfg = _TRACKER_CONFIG.get(market)
    if not cfg:
        return {}
    db_path, table, date_col = cfg
    try:
        if not db_path.exists():
            return {}
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            # table and date_col are from _TRACKER_CONFIG whitelist
            cur = conn.execute(
                f"SELECT stop_loss, target_price FROM {table} WHERE ticker=? AND {date_col} LIKE ? LIMIT 1",
                (ticker, f"{report_date}%"),
            )
            row = cur.fetchone()
            if row:
                return {"stop_loss": row["stop_loss"], "target_price": row["target_price"]}
    except Exception as e:
        logger.debug(f"Tracker lookup failed for {ticker} {report_date}: {e}")
    return {}


def _update_report_path(ticker: str, report_date: str, market: str, file_path: str) -> None:
    """Write report_path back into performance_tracker row."""
    cfg = _TRACKER_CONFIG.get(market)
    if not cfg:
        return
    db_path, table, date_col = cfg
    try:
        if not db_path.exists():
            return
        with sqlite3.connect(str(db_path)) as conn:
            # table and date_col are from _TRACKER_CONFIG whitelist
            conn.execute(
                f"UPDATE {table} SET report_path=? WHERE ticker=? AND {date_col} LIKE ?",
                (file_path, ticker, f"{report_date}%"),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"report_path update failed for {ticker} {report_date}: {e}")


# ---------------------------------------------------------------------------
# Core ingest logic
# ---------------------------------------------------------------------------

async def ingest_report(
    file_path: str,
    market: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[int]:
    """
    Ingest a single report file into the archive.

    Steps:
      1. Parse filename -> metadata
      2. Read content
      3. Insert into report_archive (FTS5)
      4. Update report_path in performance_tracker
      5. Fetch KIS/yfinance enrichment data
      6. Save to report_enrichment + market_timeline

    Args:
        file_path: Absolute path to .md report file
        market: 'kr' or 'us' (auto-detected from filename if None)
        dry_run: If True, log what would be done but skip DB writes

    Returns:
        report_id if ingested, None if skipped
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"Report file not found: {file_path}")
        return None

    meta = parse_report_filename(str(path))
    if not meta:
        logger.warning(f"Cannot parse filename: {path.name}")
        return None

    if market:
        meta["market"] = market

    if meta["report_date"] < SEASON2_START:
        logger.debug(f"Skipping pre-Season2 report: {path.name}")
        return None

    if dry_run:
        logger.info(f"[DRY-RUN] Would ingest: {path.name} ({meta['market'].upper()} {meta['ticker']} {meta['report_date']})")
        return None

    # Init DB once per process
    global _db_initialized
    if not _db_initialized:
        await init_db()
        _db_initialized = True

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return None

    # Store path relative to PROJECT_ROOT for portability
    try:
        rel_path = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        rel_path = str(path)

    report_id = await insert_report(
        ticker=meta["ticker"],
        company_name=meta["company_name"],
        report_date=meta["report_date"],
        mode=meta["mode"],
        model=meta["model"],
        market=meta["market"],
        file_path=rel_path,
        content=content,
    )

    if report_id is None:
        return None

    _update_report_path(meta["ticker"], meta["report_date"], meta["market"], rel_path)

    tracker = _get_tracker_data(meta["ticker"], meta["report_date"], meta["market"])

    try:
        enricher = get_enricher(meta["market"])
        result = await enricher.enrich(
            ticker=meta["ticker"],
            analysis_date=meta["report_date"],
            stop_loss=tracker.get("stop_loss"),
            target_1=tracker.get("target_price"),
        )
        enrich_data = result.to_dict()
        enrich_data["analysis_date"] = meta["report_date"]
        await upsert_enrichment(report_id, enrich_data)

        await upsert_market_timeline(
            date=meta["report_date"],
            market=meta["market"],
            index_close=enrich_data.get("index_at_analysis"),
            index_change=enrich_data.get("index_change_20d"),
            market_phase=enrich_data.get("market_phase"),
            increment_report_count=True,
        )
    except Exception as e:
        logger.warning(f"Enrichment failed for {meta['ticker']} {meta['report_date']}: {e}")

    return report_id


async def ingest_reports_async(
    report_paths: list,
    market: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """
    Fire-and-forget batch ingest called from orchestrators.
    Processes up to 5 reports concurrently.
    """
    if not report_paths:
        return
    semaphore = asyncio.Semaphore(5)

    async def _bounded(fp: str):
        async with semaphore:
            try:
                await ingest_report(fp, market=market, dry_run=dry_run)
            except Exception as e:
                logger.warning(f"Ingest failed for {fp}: {e}")

    await asyncio.gather(*[_bounded(fp) for fp in report_paths])
    logger.info(f"Archive ingest complete: {len(report_paths)} reports processed")


async def ingest_directory(
    dir_path: str,
    market: Optional[str] = None,
    pattern: str = "*.md",
    dry_run: bool = False,
    backfill: bool = False,
) -> dict:
    """
    Batch ingest all reports in a directory.

    Returns:
        Summary dict: {total, ingested, skipped, errors}
    """
    dir_ = Path(dir_path)
    if not dir_.exists():
        logger.error(f"Directory not found: {dir_path}")
        return {"total": 0, "ingested": 0, "skipped": 0, "errors": 0}

    files = sorted(dir_.glob(pattern))
    if backfill:
        logger.info(f"Backfill mode: report_path will be updated for all matched tracker rows")
    logger.info(f"Found {len(files)} files in {dir_path}")

    semaphore = asyncio.Semaphore(5)

    async def _process(fp: Path) -> str:
        """Returns 'ingested', 'skipped', or 'error'."""
        async with semaphore:
            try:
                rid = await ingest_report(str(fp), market=market, dry_run=dry_run)
                return "ingested" if rid is not None else "skipped"
            except Exception as e:
                logger.error(f"Ingest error for {fp.name}: {e}")
                return "error"

    results = await asyncio.gather(*[_process(f) for f in files])
    counts = {
        "total": len(files),
        "ingested": results.count("ingested"),
        "skipped": results.count("skipped"),
        "errors": results.count("error"),
    }
    logger.info(
        f"Ingest complete: {counts['ingested']} ingested, "
        f"{counts['skipped']} skipped, {counts['errors']} errors"
    )
    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="PRISM Archive Ingest")
    parser.add_argument("--dir", help="Directory to ingest (e.g. reports/ or prism-us/reports/)")
    parser.add_argument("--file", help="Single report file to ingest")
    parser.add_argument("--market", choices=["kr", "us"], help="Market (auto-detected if omitted)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be ingested without writing")
    parser.add_argument("--backfill", action="store_true", help="Also fill report_path in performance_tracker")
    args = parser.parse_args()

    async def _main():
        if args.file:
            rid = await ingest_report(args.file, market=args.market, dry_run=args.dry_run)
            print(f"Ingested: report_id={rid}")
        elif args.dir:
            summary = await ingest_directory(
                args.dir, market=args.market, dry_run=args.dry_run, backfill=args.backfill
            )
            print(f"Summary: {summary}")
        else:
            parser.print_help()

    asyncio.run(_main())
