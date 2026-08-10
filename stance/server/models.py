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
    SPLIT = "split"        # 액면분할·병합·무상증자 (비율로 표현)
    DIVIDEND = "dividend"  # 현금배당
    DELIST = "delist"      # 상장폐지
    HALT = "halt"          # 거래정지
    RESUME = "resume"      # 거래재개
    RENAME = "rename"      # 종목코드 변경 — 놔두면 포지션이 끊긴다
    MERGE = "merge"        # 합병·주식교환 — A 가 비율에 따라 B 로 전환된다


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
    """서버가 그 순간 직접 찍은 시세. 참여자는 가격을 보낼 수 없다.

    체결 가능성은 **방향마다 다르다.**

        거래정지   양쪽 다 불가
        상한가     매수 불가 (살 물량이 없다). 매도는 오히려 유리하게 된다
        하한가     매도 불가 (받아줄 사람이 없다). 매수는 가능하다

    하나의 boolean 으로 뭉치면 상한가에서 매도까지 막게 되는데, 그건 틀렸다.
    """

    symbol: str
    price: Decimal
    tradable: bool = True            # 거래정지 등 양방향 불가
    at_upper_limit: bool = False     # 상한가 도달
    at_lower_limit: bool = False     # 하한가 도달
    observed_at: datetime | None = None
    source: str = "primary"

    @property
    def can_buy(self) -> bool:
        return self.tradable and not self.at_upper_limit

    @property
    def can_sell(self) -> bool:
        return self.tradable and not self.at_lower_limit


@dataclass(frozen=True)
class MarketEvent:
    """서버가 발행하는 시장 이벤트.

    과거를 소급해 고치지 않는다. 발생한 시점에 장부에 반영될 뿐이다.
    수정주가 방식을 쓰지 않는 이유가 이것이다 — 소급 수정은 원장 불변성과 충돌한다.
    """

    event_type: EventType
    symbol: str
    at: datetime
    ratio: Decimal | None = None            # split/merge: 1주가 몇 주가 되는가
    per_share: Decimal | None = None        # dividend: 주당 배당금
    final_price: Decimal | None = None      # delist: 정리매매 최종가 (없으면 0)
    to_symbol: str | None = None            # rename/merge: 어느 종목이 되는가


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
              반영하지 않으면 연 100회전 전략에게 20%p 를 공짜로 주는 셈이 된다.

    빼놓는다 — 증권사 수수료(회사마다 다르다), 호가 스프레드와 시장충격
              (주문 크기·유동성·집행 실력에 따라 달라진다).
              상수로 근사하면 새로운 왜곡이 생기므로 아예 넣지 않는다.

    빠진 비용은 고회전 전략에 유리하게 작용하므로,
    회전율을 전략 카드에 항상 함께 노출해 해석 가능하게 만든다.

    ── KRX 세율 (2026-01-01 시행) ─────────────────────────────────────

        KOSPI    증권거래세 0.05% + 농어촌특별세 0.15%  = 0.20%
        KOSDAQ   증권거래세 0.20% (농특세 없음)          = 0.20%
        KONEX    0.10%  ← 구분하지 않는다. KRX 를 0.20% 로 본다

    둘 다 0.20% 라 상수 하나로 충분하다. **전액 법정 부담**이므로
    "모두에게 똑같이 적용되는 것만 반영한다" 는 기준에 정확히 들어맞는다.

    ⚠️ 세율은 바뀐다. 2026년에도 인상되었다(코스피 0% → 0.05%).
       바꿀 때는 채점 프로파일 버전을 함께 올려 과거 채점과 구분해야 한다.
    """

    tax: Decimal = Decimal("0.0020")            # 법정 거래세 + 농특세 (매도 시)
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
    m = market.upper()

    if m in ("KRX", "KOSPI", "KOSDAQ"):
        s = s.split(".")[0]
        if len(s) == 7 and s[0] == "A" and s[1:].isdigit():
            s = s[1:]
        if s.isdigit():
            s = s.zfill(6)
        return s

    if m in ("CRYPTO", "UPBIT", "BINANCE"):
        # BTC/KRW · BTC-KRW · KRW-BTC · BTCKRW · XBT 는 전부 BTC 다.
        # 보유하는 것은 기초자산이며, 상대통화는 전략 단위로 이미 고정되어 있다.
        #
        # 까다로운 점: BTC 와 ETH 는 기초자산이면서 동시에 상대통화로도 쓰인다
        # (ETH/BTC). 그래서 법정통화 계열과 코인 계열을 나눠서 판정한다.
        for sep in ("/", "-", "_"):
            if sep in s:
                a, b = s.split(sep, 1)
                if b in _QUOTE_ALL and a not in _QUOTE_FIAT:
                    s = a          # BTC/KRW · BTC_USDT · ETH/BTC
                elif a in _QUOTE_ALL:
                    s = b          # KRW-BTC (업비트식 상대통화 선행)
                else:
                    s = a
                break
        else:
            for q in _QUOTE_SUFFIX:   # 긴 것부터 — USDT 가 USD 보다 먼저
                if len(s) > len(q) and s.endswith(q):
                    s = s[: -len(q)]
                    break
        return "BTC" if s == "XBT" else s

    return s.split(".")[0]


_QUOTE_FIAT = ("KRW", "USD", "USDT", "USDC", "JPY", "EUR")
_QUOTE_ALL = _QUOTE_FIAT + ("BTC", "ETH")
_QUOTE_SUFFIX = tuple(sorted(_QUOTE_ALL, key=len, reverse=True))
