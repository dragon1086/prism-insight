"""Stance 프로토콜 — 원장에 기록되는 사실들.

이 모듈에는 계산이 없다. 오직 "무슨 일이 있었는가"만 담는다.
계산은 engine.py 가, 채점은 scoring.py 가 한다.

용어
    스탠스   참여자가 보낸 선언 한 건. "이 종목을 자산의 X%로 만들겠다"
    시세관측  서버가 스탠스를 받은 직후 직접 찍은 가격. 사후 재조회가 불가능하므로 원장이다
    시장이벤트 액면분할·배당·상장폐지 등. 참여자와 무관하게 발생하며 서버가 발행한다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

PROTOCOL_VERSION = "stance/1"


class Kind(str, Enum):
    """스탠스의 종류. 둘뿐이다."""

    SET = "set"    # 이 종목을 자산의 X% 로 만든다
    HOLD = "hold"  # 오늘은 판단상 아무것도 바꾸지 않는다


class Admit(str, Enum):
    """접수 판정. 거부하지 않고 줄여서 받아들이는 CLAMPED 가 핵심이다."""

    ACCEPTED = "accepted"  # 요청대로 반영
    CLAMPED = "clamped"    # 현금이 모자라 가능한 만큼만 반영
    REJECTED = "rejected"  # 반영 불가


class EventType(str, Enum):
    SPLIT = "split"        # 액면분할·병합
    DIVIDEND = "dividend"  # 현금배당
    DELIST = "delist"      # 상장폐지
    HALT = "halt"          # 거래정지
    RESUME = "resume"      # 거래재개


@dataclass(frozen=True)
class Stance:
    """참여자가 보낸 선언 한 건.

    가격·수량·금액·시각을 담지 않는다. 그것들은 서버가 정한다.
    """

    seq: int
    received_at: datetime
    kind: Kind = Kind.SET
    symbol: str | None = None
    target_weight: Decimal | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind is Kind.SET:
            if not self.symbol:
                raise ValueError("set 스탠스에는 symbol 이 필요하다")
            if self.target_weight is None:
                raise ValueError("set 스탠스에는 target_weight 가 필요하다")
            if not (Decimal(0) <= self.target_weight <= Decimal(1)):
                raise ValueError(f"target_weight 는 0 이상 1 이하여야 한다: {self.target_weight}")
        else:
            if self.symbol is not None or self.target_weight is not None:
                raise ValueError("hold 스탠스에는 symbol/target_weight 를 넣지 않는다")


@dataclass(frozen=True)
class Quote:
    """서버가 그 순간 직접 찍은 시세. 참여자는 가격을 보낼 수 없다."""

    symbol: str
    price: Decimal
    tradable: bool = True          # 상한가 잠김·거래정지면 False
    observed_at: datetime | None = None
    source: str = "primary"


@dataclass(frozen=True)
class MarketEvent:
    """서버가 발행하는 시장 이벤트.

    과거를 소급해 고치지 않는다. 발생한 시점에 장부에 반영될 뿐이다.
    수정주가 방식을 쓰지 않는 이유가 이것이다 — 소급 수정은 원장 불변성과 충돌한다.
    """

    event_type: EventType
    symbol: str
    at: datetime
    ratio: Decimal | None = None            # split: 1주가 몇 주가 되는가
    per_share: Decimal | None = None        # dividend: 주당 배당금
    final_price: Decimal | None = None      # delist: 정리매매 최종가 (없으면 0)


@dataclass(frozen=True)
class DailyMark:
    """하루의 종가로 자산을 찍는 시점. 채점의 시간축이 된다."""

    on: date
    prices: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class Position:
    qty: Decimal
    avg_cost: Decimal

    def value_at(self, price: Decimal) -> Decimal:
        return self.qty * price


@dataclass
class Fill:
    """스탠스 하나를 처리한 결과. 계산장부에 속하며 언제든 재생성된다."""

    seq: int
    admit: Admit
    reason: str | None = None
    symbol: str | None = None
    fill_price: Decimal | None = None
    requested_weight: Decimal | None = None
    effective_weight: Decimal | None = None
    traded_value: Decimal = Decimal(0)      # 양수면 매수, 음수면 매도
    fee: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    assets_after: Decimal = Decimal(0)


@dataclass(frozen=True)
class Costs:
    """시장별 상수 하나. 참여자가 지정할 수 없다."""

    sell_fee: Decimal = Decimal("0.0020")      # KRX 거래세 0.18% + 수수료 근사
    dividend_tax: Decimal = Decimal("0.154")   # 배당소득세

    @staticmethod
    def for_market(market: str) -> "Costs":
        m = market.upper()
        if m in ("NASDAQ", "NYSE", "US"):
            return Costs(sell_fee=Decimal("0.0001"), dividend_tax=Decimal("0.15"))
        return Costs()
