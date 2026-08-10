"""Stance 프로토콜 — 시장 프로파일.

★ 이 파일은 코어가 아니다. 채점 프로파일과 같은 위치의 교체 가능한 레이어다. ★

코어(메시지 2개 + 규칙 5개)는 자산군을 모른다. 그러나 규칙을 실제로 적용하려면
시장마다 다른 사실들이 필요하다 — 언제가 거래일인지, 어느 시세가 권위인지,
법정 세금이 얼마인지, 하루를 언제 마감하는지.

그것들을 코어에 넣으면 자산군이 늘 때마다 프로토콜이 흔들린다.
그래서 프로파일로 뺀다. 새 시장을 붙이는 것은 이 파일에 항목 하나를 더하는 일이 된다.

── v1 지원 범위 ────────────────────────────────────────────────────────

    STABLE        KRX · NASDAQ · NYSE          — 현물 주식. 정식 지원
    EXPERIMENTAL  CRYPTO                        — 미해결 항목 있음 (아래 참조)

크립토를 정식 지원으로 올리려면 먼저 풀어야 할 것이 있다.
**코어 규칙 ①("가격은 서버가 정한다")이 성립하지 않는다.**
거래소마다 가격이 다르고 김치프리미엄은 수 %에 달한다.
주식은 KRX 하나뿐이라 권위가 자명했지만 크립토는 그렇지 않다.

따라서 크립토 보드를 운영하려면 **어느 거래소를 권위로 삼는지 공개하고 고정**해야 하며,
그 선택 자체가 결과를 바꾼다. 실운영 경험 없이 지금 정하는 것은 이르다고 판단했다.

선물·마진·무기한 계약은 어느 시장이든 **v1 범위 밖**이다.
규칙 ③(비중 합 1.0 이하)이 롱온리 현물을 전제하기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .models import Costs, normalize_symbol


class Support(str, Enum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class MarketProfile:
    """한 시장을 채점 가능하게 만드는 데 필요한 사실들."""

    code: str
    currency: str
    support: Support
    costs: Costs

    periods_per_year: int
    """1년에 자산을 몇 번 마킹하는가. 주식 252 거래일, 크립토 365 일.

    참가 요건을 '60거래일' 같은 절대 숫자로 두면 시장마다 기간이 달라진다.
    주식 60거래일은 3개월이지만 크립토 60일은 2개월이다. 그래서 비율로 환산한다.
    """

    downside_floor_daily: float
    """채점에서 0 으로 나누는 것을 막는 하한.

    변동성 스케일이 시장마다 다르므로 하나의 값을 쓸 수 없다.
    크립토 일간 변동성은 주식의 3~5배라 주식용 하한은 사실상 무력하다.
    """

    price_authority: str
    """어느 시세가 권위인가. **참여자에게 공개해야 하는 값이다.**

    주식은 거래소가 하나라 자명하지만 크립토는 선택이며, 그 선택이 결과를 바꾼다.
    """

    mark_at: str
    """하루를 언제 마감하는가. 자산 추이의 시간축이 된다."""

    has_price_limits: bool
    """상한가·하한가 제도가 있는가. 없으면 체결 불가 판정이 거래정지만 본다."""

    notes: tuple[str, ...] = field(default_factory=tuple)
    """이 시장에서 아직 해결되지 않은 것들. 숨기지 않고 적는다."""

    # ── 파생 ──────────────────────────────────────────────────────────

    @property
    def min_track_periods(self) -> int:
        """참가 요건의 최소 운영 기간 = 3개월. 시장 단위로 환산한다."""
        return round(self.periods_per_year * 0.25)

    @property
    def is_experimental(self) -> bool:
        return self.support is Support.EXPERIMENTAL

    def normalize(self, symbol: str) -> str:
        return normalize_symbol(symbol, self.code)


KRX = MarketProfile(
    code="KRX",
    currency="KRW",
    support=Support.STABLE,
    # 2026-01-01 시행: 코스피 거래세 0.05% + 농특세 0.15%, 코스닥 0.20%
    costs=Costs(tax=Decimal("0.0020"), dividend_tax=Decimal("0.154")),
    periods_per_year=252,
    downside_floor_daily=0.0005,
    price_authority="한국거래소 정규장 체결가",
    mark_at="정규장 종가 (15:30 KST)",
    has_price_limits=True,
    notes=(
        "동시호가 구간(08:30–09:00, 15:20–15:30)은 확정 체결가를 기다려야 한다 — 미구현",
        "장 마감 후 접수분은 다음 정규장 시가로 확정해야 한다 — 미구현",
        "결제 주기 T+2 를 반영하지 않는다. 매도 대금을 즉시 현금으로 본다 (§비고)",
    ),
)

_US_COSTS = Costs(tax=Decimal("0.0001"), dividend_tax=Decimal("0.15"))

NASDAQ = MarketProfile(
    code="NASDAQ", currency="USD", support=Support.STABLE, costs=_US_COSTS,
    periods_per_year=252, downside_floor_daily=0.0005,
    price_authority="해당 거래소 정규장 체결가",
    mark_at="정규장 종가 (16:00 ET, 서머타임 반영)",
    has_price_limits=False,
    notes=("서머타임에 따라 마감 시각이 바뀐다 — 캘린더 미구현",),
)

NYSE = MarketProfile(
    code="NYSE", currency="USD", support=Support.STABLE, costs=_US_COSTS,
    periods_per_year=252, downside_floor_daily=0.0005,
    price_authority="해당 거래소 정규장 체결가",
    mark_at="정규장 종가 (16:00 ET, 서머타임 반영)",
    has_price_limits=False,
    notes=("서머타임에 따라 마감 시각이 바뀐다 — 캘린더 미구현",),
)

CRYPTO = MarketProfile(
    code="CRYPTO",
    currency="KRW",
    support=Support.EXPERIMENTAL,
    costs=Costs(tax=Decimal("0"), dividend_tax=Decimal("0")),
    periods_per_year=365,          # 휴장일이 없다
    downside_floor_daily=0.002,    # 주식의 4배. 변동성 스케일이 다르다
    price_authority="미확정 — 보드 운영자가 거래소를 지정하고 공개해야 한다",
    mark_at="UTC 00:00 (잠정)",
    has_price_limits=False,
    notes=(
        "★코어 규칙 ①이 성립하지 않는다 — 거래소마다 가격이 다르고 김치프리미엄이 존재한다",
        "거래소 수수료는 거래소·등급마다 달라 법정 비용으로 볼 수 없어 0 으로 둔다",
        "스테이블코인 보유를 현금으로 볼지 포지션으로 볼지 미정 — 투자비중 계산이 갈린다",
        "선물·마진·무기한 계약은 v1 범위 밖이다 (규칙 ③이 롱온리 현물을 전제한다)",
        "24시간 시장이라 '장 마감 후' 개념이 없다. 마킹 기준 시각이 임의적이다",
    ),
)

PROFILES: dict[str, MarketProfile] = {
    p.code: p for p in (KRX, NASDAQ, NYSE, CRYPTO)
}
_ALIASES = {"US": NASDAQ, "KOSPI": KRX, "KOSDAQ": KRX,
            "UPBIT": CRYPTO, "BINANCE": CRYPTO, "BTC": CRYPTO}


def profile_for(market: str) -> MarketProfile:
    key = (market or "").upper()
    if key in PROFILES:
        return PROFILES[key]
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(
        f"알 수 없는 시장: {market!r}. "
        f"지원 시장: {', '.join(sorted(PROFILES))}"
    )


def stable_markets() -> list[str]:
    return sorted(c for c, p in PROFILES.items() if p.support is Support.STABLE)


def describe(profile: MarketProfile) -> list[str]:
    """참여자에게 보여줄 시장 설명. 미해결 항목을 숨기지 않는다."""
    lines = [
        f"시장            {profile.code} ({profile.currency})",
        f"지원 수준       {profile.support.value}"
        + ("  ⚠️ 실험적 — 아래 미해결 항목을 확인할 것" if profile.is_experimental else ""),
        f"시세 권위       {profile.price_authority}",
        f"일별 마감       {profile.mark_at}",
        f"법정 거래세     {profile.costs.tax:.4%} (매도 시)",
        f"최소 운영 기간  {profile.min_track_periods}일 (3개월 환산)",
    ]
    if profile.notes:
        lines.append("미해결")
        lines.extend(f"  · {n}" for n in profile.notes)
    return lines
