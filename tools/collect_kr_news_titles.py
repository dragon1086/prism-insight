"""Collect the KIS whole-market headline feed into runtime/kr_news_titles.sqlite
(`--market us`: the KIS overseas feed into runtime/us_news_titles.sqlite, same schema).

Read-only KIS calls; no orders, no channel sends.

  live                 newest pages until they overlap what is stored (cron, every few minutes)
  backfill --until D   walk back from the oldest stored headline to date D (YYYYMMDD)
  prune --keep-days N  drop headlines older than N days
"""
import argparse
import fcntl
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prism_core import kr_news_store as store  # noqa: E402

logger = logging.getLogger("collect_kr_news_titles")
PAUSE = 0.35
MAX_EMPTY_STEPS = 5


def _lock(name, market="KR"):
    path = store.db_path(market).with_name(f".{market.lower()}_news_{name}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _step_back(day, clock):
    """One second before the cursor, for pages that held nothing new."""
    moment = datetime.strptime(day + clock, "%Y%m%d%H%M%S") - timedelta(seconds=1)
    return moment.strftime("%Y%m%d"), moment.strftime("%H%M%S")


def _cursor(day, clock):
    """KIS reads HOUR '000000' as 'no hour' and answers with the day's newest page,
    so a cursor at midnight moves to the previous day's last second instead."""
    return _step_back(day, clock) if clock == "000000" else (day, clock)


def walk(source, conn, *, start_day, start_clock, stop, max_calls, pause=PAUSE, fetch_page=None):
    """Page backwards from the cursor until `stop(page, new)` or the call budget ends."""
    fetch_page = fetch_page or source.news_title_page
    day, clock = start_day, start_clock
    calls = new_total = empty = 0
    while calls < max_calls:
        page = fetch_page("", day, clock)
        calls += 1
        new = store.upsert(conn, page)
        new_total += new
        if not page:
            break
        oldest = min(page, key=lambda r: (r["day"], r["time"]))
        if stop(page, new, oldest):
            break
        if new == 0:
            empty += 1
            if empty >= MAX_EMPTY_STEPS:
                break
            next_cursor = _step_back(oldest["day"], oldest["time"])
        else:
            empty = 0
            next_cursor = (oldest["day"], oldest["time"])
        if next_cursor == (day, clock):
            next_cursor = _step_back(day, clock)
        day, clock = _cursor(*next_cursor)
        time.sleep(pause)
    return calls, new_total, (day, clock)


def live(source, conn, max_calls, fetch_page=None):
    newest = store.bounds(conn)[1]

    def stop(page, new, oldest):
        # Stop once the page reaches what earlier runs stored. (Not `new < len(page)`:
        # the cursor row repeats at the top of every next page.)
        return newest is not None and store.stamp(oldest) <= newest

    return walk(source, conn, start_day=datetime.now().strftime("%Y%m%d"), start_clock="",
                stop=stop, max_calls=max_calls, fetch_page=fetch_page)


def backfill(source, conn, until, max_calls, fetch_page=None):
    oldest = store.bounds(conn)[0]
    if oldest:
        # Resume one second before the oldest stored headline.
        day, clock = _cursor(*_step_back(oldest[:10].replace("-", ""), oldest[11:].replace(":", "")))
    else:
        day, clock = datetime.now().strftime("%Y%m%d"), ""

    def stop(page, new, row):
        return row["day"] < until

    return walk(source, conn, start_day=day, start_clock=clock, stop=stop, max_calls=max_calls,
                fetch_page=fetch_page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["kr", "us"], default="kr")
    sub = parser.add_subparsers(dest="mode", required=True)
    p_live = sub.add_parser("live")
    p_live.add_argument("--max-calls", type=int, default=60)
    p_back = sub.add_parser("backfill")
    p_back.add_argument("--until", required=True)
    p_back.add_argument("--max-calls", type=int, default=3000)
    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--keep-days", type=int, default=400)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    market = args.market.upper()
    handle = _lock(args.mode, market)
    if handle is None:
        logger.info("%s %s already running; skipped", market, args.mode)
        return
    with handle, store.connect(store.db_path(market)) as conn:
        started = time.monotonic()
        if args.mode == "prune":
            removed = store.prune(conn, args.keep_days)
            logger.info("prune removed=%d bounds=%s", removed, store.bounds(conn))
            return
        from cores.market_data.kis_source import KisSource

        source = KisSource()
        fetch_page = source.us_news_title_page if market == "US" else None
        if args.mode == "live":
            calls, new, cursor = live(source, conn, args.max_calls, fetch_page)
        else:
            calls, new, cursor = backfill(source, conn, args.until, args.max_calls, fetch_page)
        logger.info("%s %s calls=%d new=%d cursor=%s bounds=%s %.0fs", market, args.mode, calls, new, cursor,
                    store.bounds(conn), time.monotonic() - started)


if __name__ == "__main__":
    main()
