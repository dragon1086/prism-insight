"""Render transport-neutral campaign deliveries as Kakao SkillResponse."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kakao_bot.adapters.kakao.skill_response import (
    list_card_output,
    simple_text,
    skill_response,
)
from kakao_bot.domain.models import ClaimedOutboundDelivery

_MARKET_LABELS = {
    "KR": ("🇰🇷", "한국"),
    "US": ("🇺🇸", "미국"),
}
_SESSION_LABELS = {
    "MORNING": "오전",
    "AFTERNOON": "오후",
}


def render_campaign_delivery(
    delivery: ClaimedOutboundDelivery,
) -> dict[str, object]:
    if delivery.message_type == "signal_campaign":
        return _signal_campaign(delivery.payload)
    if delivery.message_type == "campaign_rest_notice":
        return _rest_notice(delivery.payload)
    raise ValueError(f"unsupported Kakao delivery type: {delivery.message_type}")


def _signal_campaign(payload: Mapping[str, object]) -> dict[str, object]:
    candidates = payload.get("candidates")
    if (
        not isinstance(candidates, Sequence)
        or isinstance(candidates, (str, bytes))
        or not candidates
    ):
        raise ValueError("signal campaign requires candidates")

    items: list[dict[str, object]] = []
    for raw_candidate in candidates[:5]:
        if not isinstance(raw_candidate, Mapping):
            raise ValueError("campaign candidates must be objects")
        ticker = _required_text(raw_candidate, "ticker")
        company_name = _required_text(raw_candidate, "company_name")
        descriptions: list[str] = []
        score = raw_candidate.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            descriptions.append(f"점수 {score:g}")
        rationale = raw_candidate.get("rationale")
        if isinstance(rationale, str) and rationale.strip():
            descriptions.append(rationale.strip())
        items.append(
            {
                "title": f"{company_name} ({ticker})",
                "description": " · ".join(descriptions) or "분석 리포트 생성 완료",
            }
        )

    market, session = _slot_labels(payload)
    _required_text(payload, "trade_date")
    _required_text(payload, "regime")
    header = f"{market[0]} {market[1]} {session} 시그널"
    return skill_response(
        [
            list_card_output(
                header_title=header,
                items=items,
            )
        ]
    )


def _rest_notice(payload: Mapping[str, object]) -> dict[str, object]:
    market, session = _slot_labels(payload)
    reason = payload.get("reason")
    reason_text = (
        reason.strip()[:500]
        if isinstance(reason, str) and reason.strip()
        else "시장 국면 정책에 따른 배치 휴식"
    )
    return simple_text(
        (
            f"{market[0]} {market[1]} {session} 분석은 쉬어갑니다.\n"
            "오후 종가 확인 후 선별 결과를 알려드리겠습니다.\n\n"
            f"사유: {reason_text}"
        )
    )


def _slot_labels(
    payload: Mapping[str, object],
) -> tuple[tuple[str, str], str]:
    market_code = _required_text(payload, "market").upper()
    session_code = _required_text(payload, "session").upper()
    try:
        market = _MARKET_LABELS[market_code]
        session = _SESSION_LABELS[session_code]
    except KeyError as exc:
        raise ValueError(
            f"unsupported campaign slot: {market_code}/{session_code}"
        ) from exc
    return market, session


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"campaign payload requires {key}")
    return value.strip()
