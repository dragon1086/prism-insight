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
    """스탠스의 종류."""

    SET = "set"      # 이 종목을 자산의 X% 로 만든다
    HOLD = "hold"    # 오늘은 판단상 아무것도 바꾸지 않는다
    PAUSE = "pause"  # 점검·휴가 등으로 당분간 판단하지 않는다
    RESUME = "resume"  # 다시 판단을 시작한다


class Admit(str, Enum):
    """접수 판정."""

    ACCEPTED = "accepted"  # 요청대로 반영
    CLAMPED = "clamped"    # 현금이 모자라 가능한 만큼만 반영
    REJECTED = "rejected"  # 참여자 사유로 반영 불가
    PENDING = "pending"    # 서버가 시세를 구하지 못함 — 참여자 잘못이 아니다


class Cadence(str, Enum):
    """이 전략이 얼마나 자주 판단하는가. 등록 시 스스로 밝힌다.

    제출률을 '거래일마다 보냈는가' 로 재면 주간·월간·이벤트 기반 시스템이
    구조적으로 전멸한다. 자기 주기를 밝히게 하고 그 대비로 해석한다.
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    EVENT = "event"    # 신호가 나올 때만. 기대 주기가 없다


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
                raise ValueError(f"{self.kind.value} 스탠스에는 symbol/target_weight 를 넣지 않는다")


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
    """거래비용.

    ── 무엇을 반영하고 무엇을 빼는가 ───────────────────────────────────

    기준은 하나다. **모두에게 똑같이 적용되는 것만 반영하고,
    사람마다 다른 것은 제외한다.**

    반영한다 — 법정 거래세. 누가 어느 증권사에서 팔든 세율은 같다.
              그리고 "얼마나 자주 거래할 것인가" 는 집행이 아니라 판단이다.
              반영하지 않으면 연 100회전 전략에게 18%p 를 공짜로 주는 셈이 된다.

    빼놓는다 — 증권사 수수료(회사마다 다르다), 호가 스프레드와 시장충격
              (주문 크기·유동성·집행 실력에 따라 달라진다).
              상수로 근사하면 새로운 왜곡이 생기므로 아예 넣지 않는다.

    빠진 비용은 고회전 전략에 유리하게 작용하므로,
    회전율을 전략 카드에 항상 함께 노출해 해석 가능하게 만든다.

    ⚠️ 세율은 시기와 시장에 따라 바뀐다. 아래 값은 초안이며
       실제 운영 전에 반드시 확인하고, 채점 프로파일 버전에 함께 기록해야 한다.
    """

    tax: Decimal = Decimal("0.0018")            # 법정 거래세 (매도 시)
    commission: Decimal = Decimal("0")          # 증권사 수수료 — 의도적으로 0
    dividend_tax: Decimal = Decimal("0.154")    # 배당소득세

    @property
    def sell_fee(self) -> Decimal:
        return self.tax + self.commission

    @staticmethod
    def for_market(market: str) -> "Costs":
        m = market.upper()
        if m in ("NASDAQ", "NYSE", "US"):
            # 미국은 매도 시 SEC fee 등 소액. 거래세 성격의 법정 비용만 반영한다.
            return Costs(tax=Decimal("0.0001"), dividend_tax=Decimal("0.15"))
        if m in ("CRYPTO", "UPBIT", "BINANCE"):
            # 거래소 수수료는 거래소·등급마다 달라 법정 비용이라 볼 수 없다.
            return Costs(tax=Decimal("0"), dividend_tax=Decimal("0"))
        return Costs()


def normalize_symbol(symbol: str, market: str = "KRX") -> str:
    """같은 종목이 다른 표기로 들어와 두 개의 포지션이 되는 것을 막는다.

    005930 · A005930 · 005930.KS 는 전부 같은 종목이다.
    """
    s = (symbol or "").strip().upper()
    if not s:
        raise ValueError("종목 코드가 비어 있다")
    if market.upper() == "KRX":
        s = s.split(".")[0]
        if len(s) == 7 and s[0] == "A" and s[1:].isdigit():
            s = s[1:]
        if s.isdigit():
            s = s.zfill(6)
    return s
