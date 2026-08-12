"""PRISM ↔ Stance 프로토콜 어댑터.

★ 이 파일만 양쪽을 안다. ★

`stance/` 패키지는 PRISM 을 전혀 모르며, PRISM 코드를 import 하지 않는다.
그 규칙을 지키는 한 `git subtree split` 으로 커밋 이력을 보존한 채
`stance/` 를 통째로 별도 저장소로 뽑아낼 수 있다.

── PRISM 의 포지션 방식 ──────────────────────────────────────────────────

에이전트 프롬프트(cores/agents/trading_agents.py)에는
"1슬롯 = 포트폴리오의 10%" 라고 적혀 있지만, 실제 집행은 그렇지 않다.
`trading/config/kis_devlp.yaml` 의 `default_unit_amount` 가 말해주듯
**한 슬롯은 고정 금액**(예: 100만원)이며 비중이 아니다.

따라서 실제 비중은 계좌 크기에 따라 달라진다.
총자산 1,000만원이면 슬롯 하나가 10% 지만, 2,000만원으로 불어나면 5% 다.
즉 계좌가 커질수록 PRISM 은 자동으로 보수적이 된다.

Stance 는 이것을 있는 그대로 기록한다. 왜곡이 아니라 사실이다.

── 변환 ─────────────────────────────────────────────────────────────────

PRISM 은 분할매매를 하지 않는다(올인/올아웃). 그래서 변환이 두 줄로 끝난다.

    매수 → target_weight = (기존 평가액 + 슬롯금액) / 총자산
    매도 → target_weight = 0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountSnapshot:
    """선언 시점의 계좌 상태. 브로커 조회 결과를 담는 단순 구조체."""

    cash: Decimal                      # 예수금
    holdings_value: Decimal            # 보유 종목 평가액 합계
    position_values: dict[str, Decimal]  # 종목별 평가액

    @property
    def total_assets(self) -> Decimal:
        """운용자산 = 예수금 + 보유 평가액.

        예수금만 쓰면 안 된다. 이것이 가장 흔한 실수다.
        """
        return self.cash + self.holdings_value

    def value_of(self, symbol: str) -> Decimal:
        return self.position_values.get(symbol, Decimal(0))


def buy_target_weight(
    snapshot: AccountSnapshot, symbol: str, unit_amount: Decimal | float
) -> Decimal:
    """PRISM 의 '한 슬롯 매수' 를 목표비중으로 바꾼다."""
    total = snapshot.total_assets
    if total <= 0:
        raise ValueError("총자산이 0 이하다. 계좌 조회 결과를 확인하라")
    target = (snapshot.value_of(symbol) + Decimal(str(unit_amount))) / total
    return max(Decimal(0), min(Decimal(1), target))


def sell_target_weight(
    snapshot: AccountSnapshot, symbol: str,
    sold_qty: Decimal | float | None = None,
    held_qty: Decimal | float | None = None,
) -> Decimal:
    """매도 후 남을 목표비중.

    PRISM 은 올인/올아웃이라 대개 0 이지만, 수량을 주면 부분매도도 계산한다.
    """
    if sold_qty is None or held_qty is None:
        return Decimal(0)

    sold, held = Decimal(str(sold_qty)), Decimal(str(held_qty))
    if held <= 0 or sold >= held:
        return Decimal(0)

    total = snapshot.total_assets
    if total <= 0:
        return Decimal(0)
    remaining = snapshot.value_of(symbol) * (held - sold) / held
    return max(Decimal(0), min(Decimal(1), remaining / total))


def snapshot_from_trader(trader) -> AccountSnapshot:
    """브로커 클라이언트에서 계좌 스냅샷을 만든다.

    `get_portfolio()` 와 `get_account_summary()` 를 쓴다.
    실패하면 예외가 올라가며, 호출자가 선언을 건너뛴다 — 매매를 막지는 않는다.
    """
    positions: dict[str, Decimal] = {}
    for row in trader.get_portfolio() or []:
        code = str(row.get("stock_code") or "").strip()
        if code:
            positions[code] = Decimal(str(row.get("eval_amount") or 0))

    summary = trader.get_account_summary() or {}
    cash = Decimal(str(summary.get("deposit") or 0))
    return AccountSnapshot(
        cash=cash,
        holdings_value=sum(positions.values(), Decimal(0)),
        position_values=positions,
    )


class StanceReporter:
    """PRISM 의 매매 판단을 Stance 로 흘려보낸다.

    전송 실패가 PRISM 의 매매를 막아서는 안 된다.
    이 클래스의 모든 예외는 삼켜지고 로그로만 남는다.
    """

    def __init__(self, client=None, unit_amount: Decimal | float = 0, enabled: bool = True):
        self.client = client
        self.unit_amount = Decimal(str(unit_amount))
        self.enabled = enabled and client is not None

    def report_buy(self, snapshot: AccountSnapshot, symbol: str, reason: str | None = None):
        if not self.enabled:
            return None
        try:
            w = buy_target_weight(snapshot, symbol, self.unit_amount)
            return self.client.set(symbol, w, reason=reason)
        except Exception:
            logger.exception("[stance] 매수 선언 전송 실패 (%s) — 매매는 계속 진행한다", symbol)
            return None

    def report_sell(self, symbol: str, reason: str | None = None,
                    snapshot: AccountSnapshot | None = None,
                    sold_qty=None, held_qty=None):
        if not self.enabled:
            return None
        try:
            target = (
                sell_target_weight(snapshot, symbol, sold_qty, held_qty)
                if snapshot is not None else Decimal(0)
            )
            return self.client.set(symbol, target, reason=reason)
        except Exception:
            logger.exception("[stance] 매도 선언 전송 실패 (%s) — 매매는 계속 진행한다", symbol)
            return None

    @classmethod
    def from_env(cls) -> "StanceReporter":
        """환경변수가 없으면 **꺼진 상태**로 만들어진다.

        서버가 뜨기 전에 켜면 주문마다 죽은 엔드포인트로 HTTP 를 때린다.
        그래서 기본이 꺼짐이고, 명시적으로 설정해야만 켜진다.

            STANCE_ENDPOINT   http://127.0.0.1:8800
            STANCE_API_KEY    stk_...
            STANCE_UNIT_AMOUNT  슬롯 금액 (기본 kis_devlp.yaml 의 default_unit_amount)
        """
        import os

        endpoint = os.getenv("STANCE_ENDPOINT")
        api_key = os.getenv("STANCE_API_KEY")
        if not (endpoint and api_key):
            return cls(client=None)

        try:
            from stance.client import StanceClient

            client = StanceClient(
                endpoint, api_key, timeout=float(os.getenv("STANCE_TIMEOUT", "3")),
            )
        except Exception:
            logger.exception("[stance] 클라이언트를 만들지 못했습니다 — 선언을 보내지 않는다")
            return cls(client=None)

        return cls(client=client, unit_amount=os.getenv("STANCE_UNIT_AMOUNT") or 0)

    def report_hold(self, reason: str | None = None):
        """매매하지 않은 날에도 보낸다. 제출률에 반영된다."""
        if not self.enabled:
            return None
        try:
            return self.client.hold(reason=reason)
        except Exception:
            logger.exception("[stance] hold 선언 전송 실패 — 매매는 계속 진행한다")
            return None


def snapshot_from_kis(balance: dict) -> AccountSnapshot:
    """KIS 잔고조회 응답을 AccountSnapshot 으로 바꾼다.

    실제 응답 스키마는 계좌 유형과 API 버전에 따라 다르므로,
    연동 시 이 함수의 키 매핑을 반드시 확인할 것. (미검증 — HANDOFF.md 참조)
    """
    positions: dict[str, Decimal] = {}
    for row in balance.get("output1", []) or []:
        code = (row.get("pdno") or "").strip()
        value = row.get("evlu_amt") or 0
        if code:
            positions[code] = Decimal(str(value))

    summary = (balance.get("output2") or [{}])[0]
    cash = Decimal(str(summary.get("dnca_tot_amt") or 0))
    holdings = sum(positions.values(), Decimal(0))
    return AccountSnapshot(cash=cash, holdings_value=holdings, position_values=positions)
