"""KIS 시세를 Stance 서버에 물려주는 제공자.

`stance/` 는 시세를 어디서 가져오는지 모른다. 이 파일이 그 간극을 메운다.
`stance_adapter.py` 와 마찬가지로 **PRISM 과 Stance 양쪽을 아는 바깥 파일**이다.

── 왜 접수 직후에 찍어야 하는가 ─────────────────────────────────────────

Stance 의 위조 방지는 접수시각 하나에 달려 있다.
접수시각보다 앞선 가격은 인정될 수 없어야 하므로,
서버가 선언을 받은 **그 자리에서** 시세를 찍는다.

시세를 못 구하면 거부가 아니라 보류(PENDING)다.
소스 장애는 서버 책임이지 참여자 책임이 아니다.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from stance.server.models import Quote

logger = logging.getLogger(__name__)

# KIS 종목상태 코드. 58 이 거래정지다.
HALTED = {"58"}


class KisQuoteProvider:
    """KIS 현재가 조회를 Stance 의 시세 제공자 규약에 맞춘다.

    사용
        from trading.domestic_stock_trading import DomesticStockTrading
        provider = KisQuoteProvider(DomesticStockTrading(mode="real"))
        service = StanceService(ledger=..., quote_provider=provider)

    현재가 API 응답에 상한가(stck_mxpr)·하한가(stck_llam)가 함께 오므로
    **추가 호출 없이** 방향별 체결 가능성을 판정한다.

    ⚠️ 정확히는 "상한가 도달" 이지 "상한가 잠김(매도 잔량 0)" 이 아니다.
       호가 잔량은 별도 API(inquire-asking-price)에만 있다.
       상한가에 닿았으나 물량이 남아 살 수 있는 경우까지 거부하므로 보수적이다.
       그 편이 "못 사는데 샀다고 인정" 하는 것보다 낫다고 판단했다.
    """

    def __init__(self, trading_client, source: str = "kis"):
        self.client = trading_client
        self.source = source

    def __call__(self, market: str, symbol: str) -> Quote | None:
        if market != "KRX":
            logger.warning("[stance] KIS 제공자는 KRX 전용입니다: %s", market)
            return None

        try:
            data = self.client.get_current_price(symbol)
        except Exception:
            logger.exception("[stance] 시세 조회 실패 (%s) — 보류 처리된다", symbol)
            return None

        if not data:
            return None

        price = data.get("current_price") or 0
        if price <= 0:
            logger.warning("[stance] 유효하지 않은 가격 (%s): %r", symbol, price)
            return None

        status = str(data.get("iscd_stat_cls_code", "")).strip()
        current = Decimal(str(price))
        upper = _to_decimal(data.get("upper_limit"))
        lower = _to_decimal(data.get("lower_limit"))

        return Quote(
            symbol=symbol,
            price=current,
            tradable=status not in HALTED,
            at_upper_limit=upper is not None and current >= upper,
            at_lower_limit=lower is not None and current <= lower,
            source=self.source,
        )


def _to_decimal(value) -> Decimal | None:
    """KIS 는 숫자를 문자열로 준다. 비었거나 0 이면 판정에 쓰지 않는다."""
    try:
        d = Decimal(str(value).strip())
    except Exception:
        return None
    return d if d > 0 else None


class StaticQuoteProvider:
    """테스트·데모용. 고정 가격을 돌려준다."""

    def __init__(self, prices: dict[str, float]):
        self.prices = {k: Decimal(str(v)) for k, v in prices.items()}

    def __call__(self, market: str, symbol: str) -> Quote | None:
        price = self.prices.get(symbol)
        return None if price is None else Quote(symbol, price, source="static")
