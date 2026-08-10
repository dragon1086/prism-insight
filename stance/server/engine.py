"""Stance 프로토콜 — 장부 재구성 엔진.

원장(스탠스 + 시세관측 + 시장이벤트)을 시간 순서대로 다시 읽어
보유 현황과 자산 추이를 만들어낸다.

이 엔진은 순수 함수다. 데이터베이스도 네트워크도 모른다.
같은 입력에는 언제나 같은 출력이 나오므로, 계산장부는 언제든 지우고 다시 만들 수 있다.
그것이 이 설계의 핵심이다 — 원장은 고칠 수 없고, 계산 결과는 고정되지 않는다.

모든 전략은 자산 1.0 에서 시작한다. 실제 계좌 규모는 알 필요가 없다.
따라서 보유 수량은 소수가 될 수 있다. 이것은 모형 장부이지 실제 계좌가 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import (
    Admit,
    Costs,
    DailyMark,
    EventType,
    Fill,
    Kind,
    MarketEvent,
    Position,
    Quote,
    Stance,
    normalize_symbol,
)

ZERO = Decimal(0)
ONE = Decimal(1)
EPS = Decimal("1e-12")  # 부동소수 잔여물을 0 으로 볼 기준


@dataclass
class Book:
    """어느 시점의 장부 상태."""

    cash: Decimal = ONE
    positions: dict[str, Position] = field(default_factory=dict)
    last_price: dict[str, Decimal] = field(default_factory=dict)
    halted: set[str] = field(default_factory=set)

    def invested(self) -> Decimal:
        return sum(
            (p.qty * self.last_price.get(s, ZERO) for s, p in self.positions.items()),
            ZERO,
        )

    def assets(self) -> Decimal:
        """운용자산 = 현금 + 보유 종목 평가액."""
        return self.cash + self.invested()

    def exposure(self) -> Decimal:
        """투자비중 = 주식에 들어가 있는 비율. 현금만 들고 있으면 0."""
        total = self.assets()
        return (self.invested() / total) if total > ZERO else ZERO

    def weight_of(self, symbol: str) -> Decimal:
        total = self.assets()
        if total <= ZERO:
            return ZERO
        pos = self.positions.get(symbol)
        if pos is None:
            return ZERO
        return pos.qty * self.last_price.get(symbol, ZERO) / total


@dataclass
class ReplayResult:
    book: Book
    fills: list[Fill] = field(default_factory=list)
    daily_assets: list[tuple[date, Decimal]] = field(default_factory=list)
    daily_exposure: list[tuple[date, Decimal]] = field(default_factory=list)
    declared_days: set[date] = field(default_factory=set)
    paused_days: set[date] = field(default_factory=set)
    closed_trades: int = 0
    closed_trades_material: int = 0  # 투입비중 1% 이상이었던 거래만
    turnover: Decimal = ZERO         # 누적 거래대금 (자산 대비)
    pending: list[Fill] = field(default_factory=list)  # 서버가 시세를 못 구한 건
    pause_started: date | None = None


class Engine:
    """원장을 받아 장부를 만든다."""

    def __init__(self, costs: Costs | None = None, material_weight: Decimal = Decimal("0.01"),
                 market: str = "KRX"):
        self.costs = costs or Costs()
        self.material_weight = material_weight
        self.market = market
        self.book = Book()
        self.result = ReplayResult(book=self.book)
        self.paused = False
        self._peak_weight: dict[str, Decimal] = {}

    # ── 스탠스 ────────────────────────────────────────────────────────────

    def apply_stance(self, stance: Stance, quote: Quote | None = None) -> Fill:
        day = stance.received_at.date()
        self.result.declared_days.add(day)

        if stance.kind is Kind.PAUSE:
            # 점검·휴가를 명시적으로 밝힌다. 자산 추이는 그대로 계산되므로
            # 하락장에 pause 를 걸어 손실을 피하는 것은 불가능하다.
            self.paused = True
            self.result.pause_started = self.result.pause_started or day
            fill = Fill(seq=stance.seq, admit=Admit.ACCEPTED, reason="pause",
                        assets_after=self.book.assets())
            self.result.fills.append(fill)
            return fill

        if stance.kind is Kind.RESUME:
            self.paused = False
            fill = Fill(seq=stance.seq, admit=Admit.ACCEPTED, reason="resume",
                        assets_after=self.book.assets())
            self.result.fills.append(fill)
            return fill

        # 선언이 들어오면 중단 상태는 자동으로 풀린다
        self.paused = False

        if stance.kind is Kind.HOLD:
            fill = Fill(seq=stance.seq, admit=Admit.ACCEPTED, reason="hold",
                        assets_after=self.book.assets())
            self.result.fills.append(fill)
            return fill

        assert stance.symbol is not None and stance.target_weight is not None
        symbol = normalize_symbol(stance.symbol, self.market)
        fill = self._apply_set(stance, symbol, stance.target_weight, quote)
        self.result.fills.append(fill)
        return fill

    def _reject(self, stance: Stance, why: str) -> Fill:
        return Fill(
            seq=stance.seq,
            admit=Admit.REJECTED,
            reason=why,
            symbol=stance.symbol,
            requested_weight=stance.target_weight,
            assets_after=self.book.assets(),
        )

    def _apply_set(
        self, stance: Stance, symbol: str, target: Decimal, quote: Quote | None
    ) -> Fill:
        if quote is None or quote.price <= ZERO:
            # 시세를 못 구한 것은 서버 책임이다. 참여자를 벌하지 않는다.
            # 재시도 대상으로 남기고, 끝내 실패하면 다음 정규장 시가로 확정한다.
            fill = Fill(seq=stance.seq, admit=Admit.PENDING, symbol=symbol,
                        requested_weight=target, assets_after=self.book.assets(),
                        reason="시세 확보 대기 — 서버측 사유")
            self.result.pending.append(fill)
            return fill

        if not quote.tradable or symbol in self.book.halted:
            # 상한가 잠김·하한가 잠김·거래정지 — 현실에서 체결할 수 없다
            return self._reject(stance, "체결 불가 상태")

        price = quote.price
        self.book.last_price[symbol] = price

        pos = self.book.positions.get(symbol)
        total = self.book.assets()
        cur_value = pos.value_at(price) if pos else ZERO
        target_value = target * total
        delta = target_value - cur_value

        if pos is None and target <= ZERO:
            # 보유하지 않은 종목을 청산하겠다는 선언. 장부상으로는 무해한 no-op 이지만
            # 참여자는 보유하고 있다고 믿는 상태이므로 조용히 넘기지 않고 알려준다.
            return self._reject(stance, "보유하지 않은 종목")

        if abs(delta) <= EPS:
            return Fill(seq=stance.seq, admit=Admit.ACCEPTED, symbol=symbol,
                        fill_price=price, requested_weight=target,
                        effective_weight=self.book.weight_of(symbol),
                        assets_after=total)

        if delta > ZERO:
            return self._buy(stance, symbol, price, delta, target)
        return self._sell(stance, symbol, price, -delta, target, cur_value)

    def _buy(
        self, stance: Stance, symbol: str, price: Decimal,
        want: Decimal, target: Decimal,
    ) -> Fill:
        if self.book.cash <= EPS:
            # 현금이 아예 없을 때만 거부한다. 부족하면 줄여서 받는다.
            return self._reject(stance, "가용 현금 없음")

        spend = min(want, self.book.cash)
        admit = Admit.CLAMPED if (want - spend) > EPS else Admit.ACCEPTED

        qty_add = spend / price
        pos = self.book.positions.get(symbol)
        if pos is None:
            self.book.positions[symbol] = Position(qty=qty_add, avg_cost=price)
        else:
            new_qty = pos.qty + qty_add
            pos.avg_cost = (pos.qty * pos.avg_cost + spend) / new_qty
            pos.qty = new_qty

        self.book.cash -= spend
        self.result.turnover += spend

        eff = self.book.weight_of(symbol)
        self._peak_weight[symbol] = max(self._peak_weight.get(symbol, ZERO), eff)

        return Fill(
            seq=stance.seq, admit=admit, symbol=symbol, fill_price=price,
            requested_weight=target, effective_weight=eff,
            traded_value=spend, assets_after=self.book.assets(),
            reason=None if admit is Admit.ACCEPTED else "현금 부족으로 축소 반영",
        )

    def _sell(
        self, stance: Stance, symbol: str, price: Decimal,
        want: Decimal, target: Decimal, cur_value: Decimal,
    ) -> Fill:
        pos = self.book.positions.get(symbol)
        if pos is None:
            return self._reject(stance, "보유하지 않은 종목")

        sell_value = min(want, cur_value)
        qty_sell = sell_value / price
        fee = sell_value * self.costs.sell_fee
        proceeds = sell_value - fee
        realized = proceeds - qty_sell * pos.avg_cost

        self.book.cash += proceeds
        pos.qty -= qty_sell
        self.result.turnover += sell_value

        if pos.qty <= EPS:
            del self.book.positions[symbol]
            self._close_trade(symbol)

        return Fill(
            seq=stance.seq, admit=Admit.ACCEPTED, symbol=symbol, fill_price=price,
            requested_weight=target, effective_weight=self.book.weight_of(symbol),
            traded_value=-sell_value, fee=fee, realized_pnl=realized,
            assets_after=self.book.assets(),
        )

    def _close_trade(self, symbol: str) -> None:
        self.result.closed_trades += 1
        if self._peak_weight.pop(symbol, ZERO) >= self.material_weight:
            # 아주 작은 금액으로 거래 횟수만 채우는 것을 막기 위해 따로 센다
            self.result.closed_trades_material += 1

    # ── 시장 이벤트 ───────────────────────────────────────────────────────

    def apply_event(self, ev: MarketEvent) -> None:
        """기업행위는 과거를 고치지 않고 발생 시점에 반영한다."""
        pos = self.book.positions.get(ev.symbol)

        if ev.event_type is EventType.SPLIT:
            if ev.ratio is None or ev.ratio <= ZERO:
                return
            if pos:
                pos.qty *= ev.ratio
                pos.avg_cost /= ev.ratio
            if ev.symbol in self.book.last_price:
                self.book.last_price[ev.symbol] /= ev.ratio

        elif ev.event_type is EventType.DIVIDEND:
            if pos and ev.per_share:
                gross = pos.qty * ev.per_share
                self.book.cash += gross * (ONE - self.costs.dividend_tax)

        elif ev.event_type is EventType.HALT:
            self.book.halted.add(ev.symbol)

        elif ev.event_type is EventType.RESUME:
            self.book.halted.discard(ev.symbol)

        elif ev.event_type is EventType.DELIST:
            self.book.halted.discard(ev.symbol)
            if pos:
                price = ev.final_price if ev.final_price is not None else ZERO
                value = pos.qty * price
                fee = value * self.costs.sell_fee
                self.book.cash += value - fee
                self.book.last_price[ev.symbol] = price
                del self.book.positions[ev.symbol]
                self._close_trade(ev.symbol)

    # ── 일별 마킹 ─────────────────────────────────────────────────────────

    def apply_mark(self, mark: DailyMark) -> None:
        """하루를 마감하고 자산을 기록한다. 채점의 시간축이 된다."""
        for symbol, price in mark.prices.items():
            sym = normalize_symbol(symbol, self.market)
            if sym in self.book.halted:
                continue  # 거래정지 종목은 정지 직전 가격으로 고정한다
            self.book.last_price[sym] = price
        self.result.daily_assets.append((mark.on, self.book.assets()))
        self.result.daily_exposure.append((mark.on, self.book.exposure()))
        if self.paused:
            # 중단 기간에도 자산은 그대로 계산된다. 제출률 집계에서만 빠진다.
            self.result.paused_days.add(mark.on)


def replay(timeline: list, costs: Costs | None = None) -> ReplayResult:
    """원장 전체를 재생한다.

    timeline 은 시간 순으로 정렬된 항목들이다.
      (Stance, Quote | None) — 참여자 선언과 그때 찍은 시세
      MarketEvent            — 기업행위
      DailyMark              — 하루 마감
    """
    engine = Engine(costs=costs)
    for item in timeline:
        if isinstance(item, tuple):
            stance, quote = item
            engine.apply_stance(stance, quote)
        elif isinstance(item, Stance):
            engine.apply_stance(item, None)
        elif isinstance(item, MarketEvent):
            engine.apply_event(item)
        elif isinstance(item, DailyMark):
            engine.apply_mark(item)
        else:
            raise TypeError(f"알 수 없는 원장 항목: {type(item)!r}")
    return engine.result
