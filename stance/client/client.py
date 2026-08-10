"""Stance 프로토콜 — 레퍼런스 클라이언트.

참여자가 계산할 값은 목표비중 하나뿐이다. 나눗셈 한 번이면 나온다.

    target_weight = (이 종목의 평가액 + 이번에 더 넣을 금액) / 총자산

`target_weight` 는 원가가 아니라 **평가액 기준**이다.
주가가 오르면 아무것도 안 해도 비중이 올라간다는 점에 주의한다.
아래 to_target_weight() 헬퍼가 그 변환을 대신해 준다.
"""

from __future__ import annotations

from decimal import Decimal

try:
    import requests
except ImportError:  # 선언 전송 없이 헬퍼만 쓸 때는 requests 가 없어도 된다
    requests = None  # type: ignore

PROTOCOL = "stance/1"


def to_target_weight(
    position_value: Decimal | float,
    total_assets: Decimal | float,
    add_amount: Decimal | float = 0,
) -> Decimal:
    """자기 시스템의 매매 의도를 목표비중으로 바꾼다.

    position_value  지금 이 종목의 평가액 (없으면 0)
    total_assets    총자산 = 현금 + 보유 종목 평가액 전부
    add_amount      이번에 더 넣을 금액. 줄이려면 음수.

    예) 정액 매수 시스템 — 총자산 5,000만원에서 100만원어치 신규 매수
        to_target_weight(0, 50_000_000, 1_000_000)  ->  0.02

    예) N등분 슬랏 — 이미 평가액 700만원, 총자산 5,000만원에서 한 칸(500만원) 추가
        to_target_weight(7_000_000, 50_000_000, 5_000_000)  ->  0.24

    예) 절반 익절 — 평가액 700만원의 절반을 정리
        to_target_weight(7_000_000, 50_000_000, -3_500_000)  ->  0.07
    """
    total = Decimal(str(total_assets))
    if total <= 0:
        raise ValueError("총자산은 0보다 커야 한다")
    target = (Decimal(str(position_value)) + Decimal(str(add_amount))) / total
    return max(Decimal(0), min(Decimal(1), target))


class StanceClient:
    """선언을 보낸다. 이게 전부다."""

    def __init__(self, endpoint: str, strategy_id: str, token: str, market: str = "KRX",
                 timeout: float = 10.0):
        """timeout 은 매매 경로에 물릴 때 특히 중요하다.

        서버가 죽어 있으면 그 시간만큼 주문이 지연된다. 3초 안팎을 권한다.
        """
        self.endpoint = endpoint.rstrip("/")
        self.strategy_id = strategy_id
        self.token = token
        self.market = market
        self.timeout = timeout
        self.seq = self._recover_seq()

    # ── 선언 ──────────────────────────────────────────────────────────────

    def set(self, symbol: str, target_weight: Decimal | float, reason: str | None = None) -> dict:
        """이 종목을 총자산의 target_weight 비율로 만든다. 0 이면 전량 청산."""
        return self._send({"kind": "set", "symbol": symbol,
                           "target_weight": str(Decimal(str(target_weight))),
                           "reason": reason})

    def exit(self, symbol: str, reason: str | None = None) -> dict:
        return self.set(symbol, 0, reason)

    def hold(self, reason: str | None = None) -> dict:
        """매매하지 않기로 판단한 날에도 보낸다. 제출률에 반영된다."""
        return self._send({"kind": "hold", "reason": reason})

    def pause(self, reason: str | None = None) -> dict:
        """점검·휴가 등으로 당분간 판단하지 않음을 밝힌다.

        제출률 집계에서만 빠진다. **자산 추이는 그대로 계산된다** —
        수익까지 면제하면 하락장에 중단을 걸어 손실을 피하는 공짜 보험이 된다.
        """
        return self._send({"kind": "pause", "reason": reason})

    def resume(self, reason: str | None = None) -> dict:
        """다시 판단을 시작한다. 선언을 보내면 자동으로 풀리므로 생략해도 된다."""
        return self._send({"kind": "resume", "reason": reason})

    # ── 조회 ──────────────────────────────────────────────────────────────

    def portfolio(self) -> dict:
        """검산용. 선언을 보내기 전에 호출할 필요는 없다."""
        r = requests.get(f"{self.endpoint}/portfolio", headers=self._headers(),
                         params={"strategy_id": self.strategy_id}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ── 내부 ──────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _recover_seq(self) -> int:
        """프로세스를 재시작해도 일련번호가 1 로 돌아가지 않게 서버에서 복구한다."""
        try:
            return int(self.portfolio().get("last_seq", 0))
        except Exception:
            return 0

    def _send(self, body: dict) -> dict:
        next_seq = self.seq + 1          # 전송에 성공했을 때만 증가시킨다
        payload = {"protocol": PROTOCOL, "strategy": self.strategy_id,
                   "seq": next_seq, "market": self.market,
                   **{k: v for k, v in body.items() if v is not None}}

        r = requests.post(f"{self.endpoint}/stances", headers=self._headers(),
                          json=payload, timeout=self.timeout)
        r.raise_for_status()
        self.seq = next_seq

        result = r.json()
        admit = result.get("admit")
        if admit == "clamped":
            print(f"[stance] 축소 반영: 요청 {payload.get('target_weight')} → "
                  f"{result.get('effective_weight')}. 총자산 계산 기준을 확인하세요.")
        elif admit == "rejected":
            print(f"[stance] 거부: {result.get('reason')}")
        return result
