"""Stance 프로토콜 — HTTP 껍데기.

로직은 전부 service.py 에 있다. 이 파일은 HTTP 를 서비스 호출로 옮기는 일만 한다.
그래서 다른 프레임워크로 갈아타도 service.py 는 그대로다.

실행
    pip install fastapi uvicorn
    uvicorn stance.server.api:app --host 127.0.0.1 --port 8800

엔드포인트
    POST /strategies        전략 등록 → 인증키 발급 (전략당 1회)
    POST /stances           선언 접수  ← 참여자가 쓰는 유일한 쓰기 엔드포인트
    GET  /portfolio         검산용 보유·자산 스냅샷
    GET  /leaderboard       리더보드 (원장을 재생해 만든 계산 결과)
    GET  /markets           지원 시장과 각 보드의 규칙
    GET  /health
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .leaderboard import build as build_leaderboard
from .leaderboard import preparing
from .ledger import Ledger
from .markets import PROFILES, describe
from .models import PROTOCOL_VERSION
from .scoring import PROFILE_VERSION
from .service import StanceError, StanceService

app = FastAPI(
    title="Stance",
    description="매매 판단 공개 프로토콜 — 실적을 신고받지 말고, 판단을 미리 선언받아라.",
    version=PROTOCOL_VERSION,
)

_service: StanceService | None = None


def get_service() -> StanceService:
    """운영에서는 lifespan 에서 원장 경로와 시세 제공자를 주입한다."""
    global _service
    if _service is None:
        _service = StanceService(ledger=Ledger(os.getenv("STANCE_DB", ":memory:")))
    return _service


def set_service(service: StanceService) -> None:
    global _service
    _service = service


@app.exception_handler(StanceError)
async def _stance_error(_request, exc: StanceError):
    return JSONResponse(status_code=exc.status, content={"error": exc.message})


def _bearer(authorization: str | None) -> str | None:
    """Authorization: Bearer <key>.

    Supabase 의 apikey 헤더(프로젝트 공용 키 자리)에 전략 키를 넣으면 안 된다.
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def current_strategy(
    authorization: str | None = Header(default=None),
    service: StanceService = Depends(get_service),
) -> str:
    return service.authenticate(_bearer(authorization))


# ── 스키마 ────────────────────────────────────────────────────────────────

class RegisterIn(BaseModel):
    strategy: str = Field(description="전략 ID. 등록 후 바꿀 수 없다")
    display_name: str
    handle: str = Field(description="계정 핸들. 한 계정의 전 전략이 함께 노출된다")
    market: str = "KRX"
    cadence: str = Field(default="daily", description="daily | weekly | monthly | event")


class StanceIn(BaseModel):
    """참여자가 보내는 유일한 메시지.

    가격·수량·금액·시각을 담지 않는다. 그것들은 서버가 정한다.
    """

    protocol: str = PROTOCOL_VERSION
    strategy: str | None = None          # 인증키로 결정되므로 참고용
    seq: int
    kind: str = "set"                    # set | hold | pause | resume
    symbol: str | None = None
    target_weight: float | str | None = Field(default=None, description="0 이상 1 이하")
    reason: str | None = Field(default=None, max_length=500)


# ── 엔드포인트 ────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "protocol": PROTOCOL_VERSION, "score_profile": PROFILE_VERSION}


@app.get("/markets")
def markets() -> dict[str, Any]:
    """지원 시장과 각 보드의 규칙. 미해결 항목을 숨기지 않는다."""
    return {
        "protocol": PROTOCOL_VERSION,
        "markets": {
            code: {
                "code": p.code,
                "currency": p.currency,
                "support": p.support.value,
                "price_authority": p.price_authority,
                "mark_at": p.mark_at,
                "min_track_periods": p.min_track_periods,
                "notes": list(p.notes),
                "description": describe(p),
            }
            for code, p in PROFILES.items()
        },
    }


@app.post("/strategies", status_code=201)
def register(body: RegisterIn, service: StanceService = Depends(get_service)) -> dict[str, Any]:
    """전략을 등록하고 인증키를 발급한다.

    **인증키는 이 응답에서만 평문으로 보인다.** 서버는 해시만 저장한다.
    전략은 삭제할 수 없다 — 여러 개 등록해 놓고 잘된 것만 홍보하는 것을 막기 위해서다.
    """
    reg = service.register(body.strategy, body.display_name, body.handle,
                           market=body.market, cadence=body.cadence)
    return {
        "strategy": reg.strategy_id,
        "market": reg.market,
        "cadence": reg.cadence,
        "api_key": reg.api_key,
        "notice": "이 키는 다시 볼 수 없습니다. 안전한 곳에 보관하세요.",
    }


@app.post("/stances")
def submit(
    body: StanceIn,
    strategy_id: str = Depends(current_strategy),
    service: StanceService = Depends(get_service),
) -> dict[str, Any]:
    """선언을 접수한다.

    판정(accepted / clamped / rejected / pending)을 **그 자리에서** 돌려준다.
    몇 초 뒤에 알려주면 참여자는 이미 실계좌 주문을 낸 뒤이기 때문이다.
    """
    if body.protocol != PROTOCOL_VERSION:
        raise HTTPException(400, f"지원하지 않는 프로토콜 버전: {body.protocol}")
    if body.strategy and body.strategy != strategy_id:
        raise HTTPException(403, "인증키가 가리키는 전략과 다릅니다")

    return service.submit(
        strategy_id, body.seq, kind=body.kind, symbol=body.symbol,
        target_weight=body.target_weight, reason=body.reason,
    )


@app.get("/portfolio")
def portfolio(
    strategy_id: str = Depends(current_strategy),
    service: StanceService = Depends(get_service),
) -> dict[str, Any]:
    """검산용 스냅샷.

    선언을 보내기 전에 호출할 필요는 없다 — 목표 비중은 자기 시스템이 아는 값으로 계산된다.
    `last_seq` 는 프로세스 재시작 시 일련번호 복구에 쓴다.
    """
    return service.portfolio(strategy_id)


@app.get("/leaderboard")
def leaderboard(
    market: str | None = Query(default=None),
    service: StanceService = Depends(get_service),
) -> dict[str, Any]:
    entries = service.strategies()
    if market:
        entries = [e for e in entries if e[3].upper() == market.upper()]
    if not entries:
        return preparing([market.upper()] if market else ["KRX"])
    return build_leaderboard(service.ledger, entries)
