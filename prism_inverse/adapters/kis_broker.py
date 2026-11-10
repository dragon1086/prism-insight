"""KIS order adapters for the live lane (S5): one object per configured real account.

KR wraps ``trading.domestic_stock_trading.DomesticStockTrading`` (reserved market order for the next
open, reserved sell of the whole position). US wraps ``prism-us`` ``USStockTrading`` (reserved limit
buy at the last close + 2%, reserved MOO sell; never the local 10:05 queue). Holdings come from the
account's balance query and are raised as ``Unavailable`` when that query was not authoritative,
so a position is never marked rejected because KIS did not answer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from prism_inverse.ports import Unavailable

ROOT = Path(__file__).resolve().parents[2]
US_LIMIT_PAD = 1.02   # reserved US buys must be limit orders; pad the last close so the open fills


@dataclass(frozen=True)
class Order:
    ok: bool
    order_no: Optional[str]
    message: str


def _order(res: dict) -> Order:
    return Order(bool(res.get("success")), res.get("order_no"), str(res.get("message") or ""))


def account_names(market: str) -> list[str]:
    from trading import kis_auth as ka
    return [a["name"] for a in ka.get_configured_accounts(svr="prod", market=market.lower())]


class KrBroker:
    def __init__(self, account_name: str):
        from trading.domestic_stock_trading import DomesticStockTrading
        self.t = DomesticStockTrading(mode="real", account_name=account_name, auto_trading=True)
        self.name, self.slot_amount = self.t.account_name, float(self.t.buy_amount)

    def buy_next_open(self, product, amount, exchange, ref_price) -> Order:
        return _order(self.t.buy_reserved_order(product, buy_amount=int(amount)))

    def sell_next_open(self, product, qty, exchange) -> Order:
        return _order(self.t.sell_all_reserved_order(product, quantity=qty))

    def holding(self, product):
        ok, rows = self.t._get_portfolio_checked()   # the public get_portfolio() hides a failed query as []
        if not ok:
            raise Unavailable("KR balance query was not authoritative")
        row = next((r for r in rows if r.get("stock_code") == product), None)
        return (int(row["quantity"]), row.get("avg_price")) if row else None


class UsBroker:
    def __init__(self, account_name: str):
        for p in (str(ROOT / "prism-us" / "trading"), str(ROOT / "prism-us")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from us_stock_trading import USStockTrading
        self.t = USStockTrading(mode="real", account_name=account_name, auto_trading=True)
        self.name, self.slot_amount = self.t.account_name, float(self.t.buy_amount)

    def buy_next_open(self, product, amount, exchange, ref_price) -> Order:
        return _order(self.t.buy_reserved_order(product, round(ref_price * US_LIMIT_PAD, 2), buy_amount=amount,
                                                exchange=exchange, allow_local_queue=False))

    def sell_next_open(self, product, qty, exchange) -> Order:
        return _order(self.t.sell_reserved_order(product, use_moo=True, exchange=exchange, quantity=qty))

    def holding(self, product):
        from us_stock_trading import PortfolioQueryError
        try:
            rows = self.t.get_portfolio(strict=True)
        except PortfolioQueryError as exc:
            raise Unavailable(str(exc)) from exc
        row = next((r for r in rows if r.get("ticker") == product), None)
        return (int(row["quantity"]), row.get("avg_price")) if row else None


def brokers_for(market: str) -> list:
    cls = KrBroker if market.upper() == "KR" else UsBroker
    return [cls(name) for name in account_names(market)]
