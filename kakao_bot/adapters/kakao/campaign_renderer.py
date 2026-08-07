"""Render transport-neutral campaign deliveries as Kakao SkillResponse."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kakao_bot.adapters.kakao.skill_response import (
    MAX_SIMPLE_TEXT_LENGTH,
    list_card_output,
    simple_text,
    simple_text_output,
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
    if delivery.message_type == "campaign_report":
        return _report(delivery.payload)
    if delivery.message_type == "campaign_decision":
        return _story_text(delivery.payload, "🧭 프리즘봇 가상운용 판단")
    if delivery.message_type == "campaign_portfolio":
        return _story_text(delivery.payload, "📊 배치 종료 후 가격 기준 포트폴리오")
    raise ValueError(f"unsupported Kakao delivery type: {delivery.message_type}")


def _signal_campaign(payload: Mapping[str, object]) -> dict[str, object]:
    candidates = payload.get("candidates")
    if (
        not isinstance(candidates, Sequence)
        or isinstance(candidates, (str, bytes))
        or not candidates
    ):
        raise ValueError("signal campaign requires candidates")

    fallback_lines: list[str] = []
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
        detail = " · ".join(descriptions)
        fallback_lines.append(
            f"· {company_name} ({ticker})" + (f"\n  {detail}" if detail else "")
        )

    market, session = _slot_labels(payload)
    trade_date = _required_text(payload, "trade_date")
    _required_text(payload, "regime")
    display_message = payload.get("display_message")
    if isinstance(display_message, str) and display_message.strip():
        body = display_message.strip().replace("*", "")
    else:
        body = (
            f"🔔 {market[0]} {market[1]} {session} 프리즘 시그널\n"
            f"📅 {trade_date} 포착된 관심종목\n\n" + "\n\n".join(fallback_lines)
        )

    disclosure = (
        "🤖 프리즘봇의 가상 포트폴리오 운용 과정을 투명하게 공유합니다. "
        "실제 매수 권유가 아닙니다."
    )
    if "가상 포트폴리오" not in body:
        body = f"{disclosure}\n\n{body}"
    return simple_text(body[:MAX_SIMPLE_TEXT_LENGTH])


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


def _report(payload: Mapping[str, object]) -> dict[str, object]:
    ticker = _required_text(payload, "ticker")
    company_name = _required_text(payload, "company_name")
    message = _required_text(payload, "message")
    text = f"📄 {company_name} ({ticker}) 리포트\n\n{message}"
    outputs: list[dict[str, object]] = [
        simple_text_output(text[:MAX_SIMPLE_TEXT_LENGTH])
    ]
    pdf_url = payload.get("pdf_url")
    if isinstance(pdf_url, str) and pdf_url.strip():
        outputs.append(
            list_card_output(
                header_title="전체 리포트",
                items=[
                    {
                        "title": f"{company_name} PDF 준비 완료",
                        "description": "아래 버튼으로 만료 전 원문 리포트를 열어보세요",
                    }
                ],
                buttons=[
                    {
                        "action": "webLink",
                        "label": "📄 전체 리포트",
                        "webLinkUrl": pdf_url.strip(),
                    }
                ],
            )
        )
    return skill_response(outputs)


def _story_text(payload: Mapping[str, object], heading: str) -> dict[str, object]:
    message = _required_text(payload, "message")
    # The source messages still say "실시간" for historical Telegram reasons.
    # Kakao receives a post-batch snapshot, not a streaming quote surface.
    if heading.startswith("📊"):
        message = message.replace(
            "실시간 포트폴리오", "배치 종료 후 가격 기준 포트폴리오"
        )
    body = f"{heading}\n\n{message}"
    disclosure = "🤖 AI 모의운용 과정 공개이며 실제 매수·매도 권유가 아닙니다."
    if "실제 매수" not in body and "실제 매매" not in body:
        body = f"{body}\n\n{disclosure}"
    chunks = []
    remaining = body
    while remaining and len(chunks) < 3:
        chunk = remaining[:MAX_SIMPLE_TEXT_LENGTH]
        if len(remaining) > MAX_SIMPLE_TEXT_LENGTH and "\n" in chunk:
            split_at = chunk.rfind("\n")
            if split_at >= MAX_SIMPLE_TEXT_LENGTH // 2:
                chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk) :].lstrip("\n")
    if remaining:
        chunks[-1] = chunks[-1][:-1].rstrip() + "…"
    return skill_response([simple_text_output(chunk) for chunk in chunks])


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
