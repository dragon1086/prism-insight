"""Stance 프로토콜 — 하루 마감(일별 마킹).

**채점의 시간축을 만드는 작업이다.** 이것이 돌지 않으면
자산 추이가 비어 있어 운영일수·투자비중·하락위험 지표가 전부 0 으로 남는다.
즉 리더보드가 영원히 죽어 있다.

하는 일은 셋뿐이다.

    ① 그 시장의 모든 전략이 들고 있는 종목을 모은다
    ② 종가를 찍는다
    ③ 원장에 봉인한다

종가를 원장에 넣는 이유는 재현 가능성 때문이다. 외부 시세 공급자를 다시 조회해야 한다면
"원장만 공개하면 제3자가 순위를 독립 재현한다" 는 주장이 성립하지 않는다.

실행 (정규장 마감 후 cron)
    python -m stance.server.marker --market KRX
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Callable

from .engine import Engine, replay
from .ledger import Ledger
from .markets import profile_for

logger = logging.getLogger(__name__)

PriceFetcher = Callable[[str, str], "object | None"]


def held_symbols(ledger: Ledger, market: str) -> set[str]:
    """그 시장의 전 전략이 지금 들고 있는 종목.

    선언이 없던 종목도 포함된다 — 보유는 선언과 무관하게 이어진다.
    """
    rows = ledger.conn.execute(
        "SELECT strategy_id FROM strategies WHERE market=?", (market,)
    ).fetchall()

    symbols: set[str] = set()
    for r in rows:
        result = replay(ledger.full_timeline(r["strategy_id"]),
                        costs=profile_for(market).costs)
        symbols |= set(result.book.positions)
    return symbols


def close_day(
    ledger: Ledger,
    market: str,
    fetcher: PriceFetcher | None,
    on: date | None = None,
    force: bool = False,
) -> dict:
    """하루를 마감한다.

    같은 날을 두 번 마감하지 않는다 — 원장은 고칠 수 없으므로
    잘못 마감하면 되돌릴 방법이 없다.
    """
    profile = profile_for(market)
    on = on or date.today()

    if ledger.has_mark(profile.code, on) and not force:
        logger.info("[%s] %s 는 이미 마감되었습니다.", profile.code, on)
        return {"market": profile.code, "date": on.isoformat(), "skipped": True}

    symbols = held_symbols(ledger, profile.code)
    prices: dict[str, Decimal] = {}
    missing: list[str] = []

    for symbol in sorted(symbols):
        quote = None
        if fetcher is not None:
            try:
                quote = fetcher(profile.code, symbol)
            except Exception:
                logger.exception("[%s] 종가 조회 실패", symbol)
        if quote is None or quote.price <= 0:
            missing.append(symbol)
            continue
        prices[symbol] = quote.price

    if missing:
        # 종가를 못 구한 종목은 직전 가격이 유지된다(엔진이 그렇게 동작한다).
        # 마감 자체를 건너뛰면 시간축에 구멍이 나므로 남은 것만으로 마감한다.
        logger.warning("[%s] 종가를 구하지 못한 종목 %d개: %s",
                       profile.code, len(missing), ", ".join(missing[:10]))

    ledger.append_daily_mark(profile.code, on, prices)
    logger.info("[%s] %s 마감 — 종목 %d개 (누락 %d)",
                profile.code, on, len(prices), len(missing))
    return {
        "market": profile.code,
        "date": on.isoformat(),
        "marked": len(prices),
        "missing": missing,
        "skipped": False,
    }


def main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Stance 하루 마감")
    parser.add_argument("--market", default="KRX")
    parser.add_argument("--db", default=os.getenv("STANCE_DB", "stance_ledger.db"))
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--quotes", default=os.getenv("STANCE_QUOTES", "kis"))
    args = parser.parse_args()

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-7s | %(message)s")

    fetcher = None
    if args.quotes != "none":
        try:
            from prism_core.stance_quotes import KisQuoteProvider
            from trading.domestic_stock_trading import DomesticStockTrading

            fetcher = KisQuoteProvider(DomesticStockTrading(mode="real"))
        except Exception:
            logger.exception("시세 제공자를 만들지 못했습니다 — 마감을 중단합니다.")
            return 1

    ledger = Ledger(args.db)
    try:
        on = date.fromisoformat(args.date) if args.date else None
        result = close_day(ledger, args.market, fetcher, on=on)
        print(result)
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
