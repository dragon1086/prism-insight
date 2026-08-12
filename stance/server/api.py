"""Stance 프로토콜 — HTTP 껍데기.

로직은 전부 service.py 에 있다. 이 파일은 HTTP 를 서비스 호출로 옮기는 일만 한다.
그래서 다른 프레임워크로 갈아타도 service.py 는 그대로다.

실행
    pip install fastapi uvicorn
    uvicorn stance.server.api:app --host 127.0.0.1 --port 8800

엔드포인트
    POST /strategies        전략 등록 → 인증키 발급 (전략당 1회)
    PATCH /profile          선택 공개 프로필 수정 (점수와 무관)
    POST /stances           선언 접수  ← 참여자가 쓰는 유일한 쓰기 엔드포인트
    POST /keys/rotate       인증키 교체
    GET  /portfolio         검산용 보유·자산 스냅샷
    GET  /leaderboard       리더보드 (원장을 재생해 만든 계산 결과)
    GET  /markets           지원 시장과 각 보드의 규칙
    GET  /health
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .leaderboard import build as build_leaderboard
from .leaderboard import preparing
from .ledger import Ledger
from .markets import PROFILES, describe
from .models import PROTOCOL_VERSION
from .scoring import PROFILE_VERSION
from .service import StanceError, StanceService

logger = logging.getLogger(__name__)

DEFAULT_DB = "stance_ledger.db"

app = FastAPI(
    title="Stance",
    description="매매 판단 공개 프로토콜 — 실적을 신고받지 말고, 판단을 미리 선언받아라.",
    version=PROTOCOL_VERSION,
)

_service: StanceService | None = None


def _db_path() -> str:
    """원장은 반드시 디스크에 남아야 한다.

    인메모리로 띄우면 프로세스가 죽는 순간 원장이 통째로 사라진다.
    "기록은 고치거나 지울 수 없다" 는 규칙 ④ 와 정면으로 충돌하므로
    기본값을 파일로 두고, 굳이 인메모리를 쓸 때만 크게 경고한다.
    """
    path = os.getenv("STANCE_DB")
    if not path:
        logger.warning(
            "STANCE_DB 가 지정되지 않아 %s 를 사용합니다. "
            "운영에서는 영속 경로를 명시하십시오.", DEFAULT_DB
        )
        return DEFAULT_DB
    if path == ":memory:":
        logger.error(
            "STANCE_DB=:memory: — 프로세스가 죽으면 원장이 사라집니다. "
            "테스트 용도로만 사용하십시오."
        )
    return path


def get_service() -> StanceService:
    """운영에서는 기동 스크립트가 시세 제공자를 주입한다 (set_service).

    ⚠️ **단일 워커로 띄워야 한다.** 장부(계산장부)를 프로세스 메모리에 캐시하므로
       워커가 여럿이면 한쪽에서 접수한 선언이 다른 쪽 장부에 반영되지 않아
       현금 판정(축소 수락)이 어긋난다. SQLite 다중 프로세스 쓰기 경합도 생긴다.
       `uvicorn ... --workers 1` (기본값) 을 유지할 것.
    """
    global _service
    if _service is None:
        _service = StanceService(ledger=Ledger(_db_path()))
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

class PublicProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_name: str | None = Field(default=None, max_length=100)
    tagline: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    website_url: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=500)

    @field_validator("owner_name", "tagline", "description", "website_url", "source_url", mode="before")
    @classmethod
    def empty_profile_values_are_none(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("website_url", "source_url")
    @classmethod
    def safe_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("공개 링크는 사용자 정보가 없는 HTTPS 주소여야 합니다")
        return value


class RegisterIn(PublicProfileFields):
    strategy: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="전략 ID. 등록 후 바꿀 수 없다",
    )
    display_name: str = Field(min_length=1, max_length=100)
    handle: str = Field(
        min_length=1,
        max_length=100,
        description="계정 핸들. 한 계정의 전 전략이 함께 노출된다",
    )
    market: str = Field(default="KRX", min_length=1, max_length=16)
    cadence: Literal["daily", "weekly", "monthly", "event"] = "daily"


class RegisterOut(BaseModel):
    strategy: str
    market: str
    cadence: str
    api_key: str
    notice: str


class ProfileIn(PublicProfileFields):
    pass


class ProfileOut(ProfileIn):
    strategy: str


class KeyRotationOut(BaseModel):
    strategy: str
    api_key: str
    notice: str


class StanceIn(BaseModel):
    """참여자가 보내는 유일한 메시지.

    가격·수량·금액·시각을 담지 않는다. 그것들은 서버가 정한다.
    """

    model_config = ConfigDict(extra="forbid")

    protocol: str = PROTOCOL_VERSION
    strategy: str | None = Field(
        default=None,
        deprecated=True,
        description="생략 권장. 인증키가 전략을 식별하며, 이전 클라이언트 호환용이다",
    )
    seq: int = Field(gt=0)
    kind: Literal["set", "hold", "pause", "resume"] = "set"
    symbol: str | None = None
    target_weight: float | str | None = Field(default=None, description="0 이상 1 이하")
    reason: str | None = Field(default=None, max_length=500)

class AdmissionOut(BaseModel):
    seq: int
    next_seq: int
    received_at: datetime
    admit: Literal["accepted", "clamped", "rejected", "pending"]
    reason: str | None = None
    symbol: str | None = None
    fill_price: float | None = None
    requested_weight: float | None = None
    effective_weight: float | None = None
    realized_pnl: float | None = None
    total_assets_after: float | None = None
    pending: bool
    replayed: bool


class PositionOut(BaseModel):
    symbol: str
    avg_cost: float | None = None
    market_value: float | None = None
    weight: float | None = None


class PortfolioOut(BaseModel):
    strategy: str
    market: str
    as_of: datetime
    last_seq: int
    total_assets: float | None = None
    cash: float | None = None
    invested_ratio: float | None = None
    positions: list[PositionOut]


# ── 엔드포인트 ────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, Any]:
    db = os.getenv("STANCE_DB", DEFAULT_DB)
    return {
        "status": "ok",
        "protocol": PROTOCOL_VERSION,
        "score_profile": PROFILE_VERSION,
        "ledger": db,
        # 인메모리면 재시작 시 원장이 사라진다. 운영 점검에서 바로 보이도록 노출한다.
        "durable": db != ":memory:",
        "registration": "closed" if os.getenv("STANCE_REGISTRATION_TOKEN") else "open",
    }


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


@app.post("/strategies", status_code=201, response_model=RegisterOut)
def register(
    body: RegisterIn,
    registration_token: str | None = Header(default=None, alias="X-Stance-Registration-Token"),
    service: StanceService = Depends(get_service),
) -> RegisterOut:
    """전략을 등록하고 인증키를 발급한다.

    **인증키는 이 응답에서만 평문으로 보인다.** 서버는 해시만 저장한다.
    전략은 삭제할 수 없다 — 여러 개 등록해 놓고 잘된 것만 홍보하는 것을 막기 위해서다.
    """
    required_token = os.getenv("STANCE_REGISTRATION_TOKEN")
    if required_token and not secrets.compare_digest(registration_token or "", required_token):
        raise HTTPException(401, "등록 토큰이 올바르지 않습니다")

    reg = service.register(body.strategy, body.display_name, body.handle,
                           market=body.market, cadence=body.cadence,
                           owner_name=body.owner_name, tagline=body.tagline,
                           description=body.description, website_url=body.website_url,
                           source_url=body.source_url)
    return RegisterOut(
        strategy=reg.strategy_id,
        market=reg.market,
        cadence=reg.cadence,
        api_key=reg.api_key,
        notice="이 키는 다시 볼 수 없습니다. 안전한 곳에 보관하세요.",
    )


@app.patch("/profile", response_model=ProfileOut)
def update_profile(
    body: ProfileIn,
    strategy_id: str = Depends(current_strategy),
    service: StanceService = Depends(get_service),
) -> ProfileOut:
    """Update optional public presentation metadata; scoring inputs remain unchanged."""
    return ProfileOut(**service.update_profile(
        strategy_id, **body.model_dump(exclude_unset=True)
    ))


@app.post("/keys/rotate", response_model=KeyRotationOut)
def rotate_key(
    strategy_id: str = Depends(current_strategy),
    service: StanceService = Depends(get_service),
) -> KeyRotationOut:
    """새 키를 한 번만 보여주고 기존 키를 즉시 폐기한다."""
    return KeyRotationOut(
        strategy=strategy_id,
        api_key=service.rotate_key(strategy_id),
        notice="이 키는 다시 볼 수 없습니다. 기존 키는 폐기되었습니다.",
    )


@app.post("/stances", response_model=AdmissionOut)
def submit(
    body: StanceIn,
    strategy_id: str = Depends(current_strategy),
    service: StanceService = Depends(get_service),
) -> AdmissionOut:
    """선언을 접수한다.

    판정(accepted / clamped / rejected / pending)을 **그 자리에서** 돌려준다.
    몇 초 뒤에 알려주면 참여자는 이미 실계좌 주문을 낸 뒤이기 때문이다.
    """
    if body.protocol != PROTOCOL_VERSION:
        raise HTTPException(400, f"지원하지 않는 프로토콜 버전: {body.protocol}")
    claimed_strategy = body.model_dump().get("strategy")
    if claimed_strategy and claimed_strategy != strategy_id:
        raise HTTPException(403, "인증키가 가리키는 전략과 다릅니다")

    return AdmissionOut(**service.submit(
        strategy_id, body.seq, kind=body.kind, symbol=body.symbol,
        target_weight=body.target_weight, reason=body.reason,
    ))


@app.get("/portfolio", response_model=PortfolioOut)
def portfolio(
    strategy_id: str = Depends(current_strategy),
    service: StanceService = Depends(get_service),
) -> PortfolioOut:
    """검산용 스냅샷.

    선언을 보내기 전에 호출할 필요는 없다 — 목표 비중은 자기 시스템이 아는 값으로 계산된다.
    `last_seq` 는 프로세스 재시작 시 일련번호 복구에 쓴다.
    """
    return PortfolioOut(**service.portfolio(strategy_id))


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
