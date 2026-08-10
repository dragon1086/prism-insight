#!/usr/bin/env python3
"""Stance 하루 마감 — PRISM 의 시세와 시장 캘린더를 물려서 실행한다.

`stance/` 는 시세도 캘린더도 모른다. 이 파일이 그 둘을 주입한다.
그래서 `stance/` 는 여전히 통째로 떼어낼 수 있는 상태로 남는다.

**이것이 채점의 시간축을 만든다.** 돌지 않으면 자산 추이가 비어
운영일수·투자비중·위험지표가 전부 0 으로 남는다 — 리더보드가 죽는다.

cron (정규장 마감 후)
    40 15 * * 1-5  cd /opt/prism-insight && .venv/bin/python -m stance_mark --market KRX

환경변수
    STANCE_DB       원장 파일 경로 (서버와 같은 경로를 써야 한다)
    STANCE_QUOTES   kis (기본) | none
    STANCE_KIS_MODE real (기본) | vps
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

logger = logging.getLogger("stance_mark")


def build_fetcher(mode: str):
    """종가를 찍을 주체. 못 만들면 마감을 중단한다.

    시세 없이 마감하면 그날 가격이 하나도 없는 빈 마킹이 원장에 박히는데,
    원장은 고칠 수 없으므로 되돌릴 방법이 없다.
    """
    if mode == "none":
        logger.warning("시세 없이 마감합니다 — 직전 가격이 그대로 유지됩니다.")
        return None

    from prism_core.stance_quotes import KisQuoteProvider
    from trading.domestic_stock_trading import DomesticStockTrading

    return KisQuoteProvider(DomesticStockTrading(mode=os.getenv("STANCE_KIS_MODE", "real")))


def build_calendar(market: str):
    """시장 캘린더. 휴장일에 마감하면 그날이 거래일로 박혀 운영일수가 부풀려진다."""
    if market.upper() != "KRX":
        logger.warning("%s 용 캘린더가 없습니다 — 휴장일 필터 없이 진행합니다.", market)
        return None

    from check_market_day import is_market_day

    return is_market_day


def _write_dashboard_json(ledger, path: str) -> None:
    """리더보드 JSON 을 갱신한다. 실패해도 마감 자체는 성공으로 둔다."""
    from stance.server.leaderboard import build, preparing, write_json

    try:
        rows = ledger.conn.execute(
            "SELECT strategy_id, display_name, handle, market FROM strategies"
            " ORDER BY created_at"
        ).fetchall()
        entries = [(r["strategy_id"], r["display_name"], r["handle"], r["market"])
                   for r in rows]
        payload = build(ledger, entries) if entries else preparing(["KRX"])
        write_json(payload, path)
        logger.info("리더보드 JSON 갱신: %s (전략 %d개)", path, len(entries))
    except Exception:
        logger.exception("리더보드 JSON 갱신 실패 — 마감은 성공했다")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stance 하루 마감")
    parser.add_argument("--market", default="KRX")
    parser.add_argument("--db", default=os.getenv("STANCE_DB", "stance_ledger.db"))
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--quotes", default=os.getenv("STANCE_QUOTES", "kis"))
    parser.add_argument("--ignore-calendar", action="store_true",
                        help="휴장일 필터를 끈다. 테스트용")
    parser.add_argument("--dashboard-json",
                        default=os.getenv("STANCE_DASHBOARD_JSON"),
                        help="리더보드 JSON 을 쓸 경로. 대시보드가 이 파일을 읽는다")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.getenv("STANCE_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)-7s | %(message)s")

    from stance.server import Ledger, close_day

    try:
        fetcher = build_fetcher(args.quotes)
    except Exception:
        logger.exception("시세 제공자를 만들지 못했습니다 — 마감을 중단합니다.")
        return 1

    calendar = None if args.ignore_calendar else build_calendar(args.market)

    ledger = Ledger(args.db)
    try:
        on = date.fromisoformat(args.date) if args.date else None
        result = close_day(ledger, args.market, fetcher, on=on, is_trading_day=calendar)
        print(result)

        # 마감 직후 리더보드를 다시 만든다. 계산장부라 언제든 재생성해도 된다.
        if args.dashboard_json:
            _write_dashboard_json(ledger, args.dashboard_json)
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    sys.exit(main())
