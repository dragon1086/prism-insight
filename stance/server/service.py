"""Stance 프로토콜 — 서비스 계층.

HTTP 를 모른다. 프레임워크도 모른다. 그래서 프레임워크 없이 테스트할 수 있다.
api.py 는 이 위에 얹힌 얇은 HTTP 껍데기일 뿐이다.

── 접수 순서가 중요하다 ────────────────────────────────────────────────

    ① 선언을 원장에 먼저 넣는다 → 접수시각이 그 순간 박힌다
    ② 그 다음에 시세를 찍는다
    ③ 장부에 반영하고 판정을 돌려준다

①이 ②보다 먼저여야 한다. 접수시각이 권위 시각이므로,
그보다 앞선 가격은 원리적으로 인정될 수 없어야 하기 때문이다.

── 판정은 동기로 돌려준다 ──────────────────────────────────────────────

축소·거부를 몇 초 뒤에 알려주면 참여자는 이미 실계좌 주문을 낸 뒤다.
그래서 시세 조회까지 마치고 최종 결과를 그 자리에서 반환한다.
시세를 못 구하면 거부가 아니라 보류(PENDING)다 — 서버 사정으로 참여자를 벌하지 않는다.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Protocol

from .engine import Engine
from .ledger import Ledger
from .markets import MarketProfile, profile_for
from .models import Admit, Cadence, Fill, Kind, Quote, normalize_symbol
from .scoring import Metrics, score


class QuoteProvider(Protocol):
    """시세를 찍는 주체. 서버가 주입한다.

    구현체는 접수 직후의 **현재가**를 돌려줘야 한다.
    구할 수 없으면 None 을 반환한다 (예외를 던져도 보류로 처리된다).
    """

    def __call__(self, market: str, symbol: str) -> Quote | None: ...


class StanceError(Exception):
    """참여자에게 그대로 보여줄 수 있는 오류."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── 요청 한도 ─────────────────────────────────────────────────────────────

@dataclass
class RateLimit:
    """지울 수 없는 테이블에 무제한 쓰기를 열어두면 안 된다.

    분 단위는 넉넉하게 두되(분 단위로 도는 시스템을 막지 않기 위해)
    일 총량으로 실질적인 상한을 건다.
    """

    per_minute: int = 120
    per_day: int = 5000
    _hits: dict[str, deque] = field(default_factory=dict)

    def check(self, key: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > 86400:
            q.popleft()
        if len(q) >= self.per_day:
            raise StanceError("일일 요청 한도를 초과했습니다", status=429)
        recent = sum(1 for t in q if now - t <= 60)
        if recent >= self.per_minute:
            raise StanceError("분당 요청 한도를 초과했습니다", status=429)
        q.append(now)


# ── 서비스 ────────────────────────────────────────────────────────────────

@dataclass
class Registration:
    strategy_id: str
    api_key: str          # 이 응답에서만 평문으로 보인다. 서버는 해시만 저장한다
    market: str
    cadence: str


class StanceService:
    def __init__(
        self,
        ledger: Ledger | None = None,
        quote_provider: QuoteProvider | None = None,
        rate_limit: RateLimit | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.ledger = ledger or Ledger()
        self.quote_provider = quote_provider
        self.rate_limit = rate_limit or RateLimit()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._engines: dict[str, Engine] = {}
        self._profiles: dict[str, MarketProfile] = {}

    # ── 등록 ──────────────────────────────────────────────────────────

    def register(
        self, strategy_id: str, display_name: str, handle: str,
        market: str = "KRX", cadence: str = "daily",
    ) -> Registration:
        profile = profile_for(market)          # 알 수 없는 시장이면 여기서 걸린다
        try:
            cad = Cadence(cadence)
        except ValueError:
            raise StanceError(f"알 수 없는 판단 주기: {cadence}")

        if self._exists(strategy_id):
            raise StanceError("이미 등록된 전략 ID 입니다", status=409)

        api_key = "stk_" + secrets.token_urlsafe(32)
        self.ledger.register(
            strategy_id, display_name, handle,
            market=profile.code, currency=profile.currency,
            api_key_hash=_hash(api_key), cadence=cad,
        )
        return Registration(strategy_id, api_key, profile.code, cad.value)

    def _exists(self, strategy_id: str) -> bool:
        row = self.ledger.conn.execute(
            "SELECT 1 FROM strategies WHERE strategy_id=?", (strategy_id,)
        ).fetchone()
        return row is not None

    def authenticate(self, api_key: str | None) -> str:
        """인증키로 전략을 찾는다. 참여자는 자기 전략에만 쓸 수 있다."""
        if not api_key:
            raise StanceError("인증키가 없습니다", status=401)
        row = self.ledger.conn.execute(
            "SELECT strategy_id FROM strategies WHERE api_key_hash=?", (_hash(api_key),)
        ).fetchone()
        if row is None:
            raise StanceError("인증키가 올바르지 않습니다", status=401)
        return row["strategy_id"]

    # ── 선언 접수 ─────────────────────────────────────────────────────

    def submit(
        self, strategy_id: str, seq: int, kind: str = "set",
        symbol: str | None = None, target_weight: str | float | None = None,
        reason: str | None = None,
    ) -> dict:
        self.rate_limit.check(strategy_id)
        profile = self._profile(strategy_id)

        try:
            k = Kind(kind)
        except ValueError:
            raise StanceError(f"알 수 없는 선언 종류: {kind}")

        expected = self.ledger.next_seq(strategy_id)
        if seq < expected:
            # 재전송·역순. 원장이 append-only 이므로 조용히 덮어쓸 수 없다.
            raise StanceError(
                f"일련번호가 이미 지난 값입니다 (기대 {expected}, 받은 {seq})", status=409
            )

        weight: Decimal | None = None
        if k is Kind.SET:
            if not symbol:
                raise StanceError("set 선언에는 symbol 이 필요합니다")
            if target_weight is None:
                raise StanceError("set 선언에는 target_weight 가 필요합니다")
            weight = _to_weight(target_weight)
            symbol = normalize_symbol(symbol, profile.code)
        else:
            symbol, weight = None, None

        if reason and len(reason) > 500:
            raise StanceError("reason 은 500자를 넘을 수 없습니다")

        # ① 원장에 먼저 — 접수시각이 여기서 박힌다
        received_at = self.clock().isoformat()
        stance_id = self.ledger.append_stance(
            strategy_id, seq, k, symbol, weight, reason=reason, received_at=received_at
        )

        # ② 그 다음에 시세
        quote = self._observe(profile, symbol) if k is Kind.SET else None
        if quote is not None:
            self.ledger.append_quote(stance_id, quote)

        # ③ 장부에 반영
        engine = self._engine(strategy_id)
        from .models import Stance as StanceModel

        fill = engine.apply_stance(
            StanceModel(seq=seq, received_at=datetime.fromisoformat(received_at),
                        kind=k, symbol=symbol, target_weight=weight, reason=reason),
            quote,
        )
        return _fill_to_dict(fill, received_at, seq)

    def _observe(self, profile: MarketProfile, symbol: str | None) -> Quote | None:
        if not symbol or self.quote_provider is None:
            return None
        try:
            return self.quote_provider(profile.code, symbol)
        except Exception:
            # 시세 소스 장애는 서버 책임이다. 선언은 보류되고 참여자는 벌받지 않는다.
            return None

    # ── 조회 ──────────────────────────────────────────────────────────

    def portfolio(self, strategy_id: str) -> dict:
        engine = self._engine(strategy_id)
        book = engine.book
        total = book.assets()
        return {
            "strategy": strategy_id,
            "market": self._profile(strategy_id).code,
            "as_of": self.clock().isoformat(),
            "last_seq": self.ledger.next_seq(strategy_id) - 1,
            "total_assets": _num(total),
            "cash": _num(book.cash),
            "invested_ratio": _num(book.exposure()),
            "positions": [
                {
                    "symbol": s,
                    "avg_cost": _num(p.avg_cost),
                    "market_value": _num(p.qty * book.last_price.get(s, Decimal(0))),
                    "weight": _num(book.weight_of(s)),
                }
                for s, p in sorted(book.positions.items())
            ],
        }

    def metrics(self, strategy_id: str) -> Metrics:
        engine = self._engine(strategy_id)
        return score(engine.result,
                     cadence=self.ledger.cadence_of(strategy_id),
                     profile=self._profile(strategy_id))

    def strategies(self) -> list[tuple[str, str, str, str]]:
        rows = self.ledger.conn.execute(
            "SELECT strategy_id, display_name, handle, market FROM strategies ORDER BY created_at"
        ).fetchall()
        return [(r["strategy_id"], r["display_name"], r["handle"], r["market"]) for r in rows]

    # ── 내부 ──────────────────────────────────────────────────────────

    def _profile(self, strategy_id: str) -> MarketProfile:
        if strategy_id not in self._profiles:
            row = self.ledger.conn.execute(
                "SELECT market FROM strategies WHERE strategy_id=?", (strategy_id,)
            ).fetchone()
            if row is None:
                raise StanceError("등록되지 않은 전략입니다", status=404)
            self._profiles[strategy_id] = profile_for(row["market"])
        return self._profiles[strategy_id]

    def _engine(self, strategy_id: str) -> Engine:
        """장부는 원장을 재생해 만든다. 프로세스가 죽어도 원장만 있으면 복원된다."""
        if strategy_id not in self._engines:
            profile = self._profile(strategy_id)
            engine = Engine(profile=profile)
            for stance, quote in self.ledger.timeline(strategy_id):
                engine.apply_stance(stance, quote)
            self._engines[strategy_id] = engine
        return self._engines[strategy_id]


# ── 헬퍼 ──────────────────────────────────────────────────────────────────

def _hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _to_weight(value: str | float) -> Decimal:
    try:
        w = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise StanceError(f"target_weight 를 숫자로 읽을 수 없습니다: {value!r}")
    if not (Decimal(0) <= w <= Decimal(1)):
        raise StanceError(f"target_weight 는 0 이상 1 이하여야 합니다: {w}")
    return w


def _num(v: Decimal | None) -> float | None:
    return None if v is None else float(round(v, 10))


def _fill_to_dict(fill: Fill, received_at: str, seq: int) -> dict:
    return {
        "seq": seq,
        "received_at": received_at,
        "admit": fill.admit.value,
        "reason": fill.reason,
        "symbol": fill.symbol,
        "fill_price": _num(fill.fill_price),
        "requested_weight": _num(fill.requested_weight),
        "effective_weight": _num(fill.effective_weight),
        "realized_pnl": _num(fill.realized_pnl),
        "total_assets_after": _num(fill.assets_after),
        "pending": fill.admit is Admit.PENDING,
    }
