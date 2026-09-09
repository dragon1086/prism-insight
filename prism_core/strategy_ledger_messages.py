"""Readable notification-only views. No broker, transport, or trade-signal imports."""
import re
from decimal import Decimal


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except Exception:  # noqa: BLE001 - malformed display data must stay unknown
        return None


def _label(value):
    return re.sub(r"[^A-Za-z0-9가-힣. -]", "", str(value))[:48] or "미확인"


def format_campaign(snapshot, campaign_id, *, unresolved_execution_overlays=()):
    """Slot-weight accounting and broker evidence remain visibly separate."""
    campaign = next(c for c in snapshot["campaigns"] if c["campaign_id"] == campaign_id)

    def percent(value, *, signed=False):
        number = _number(value)
        if number is None:
            return "미확인"
        return f"{number * 100:+.2f}%" if signed else f"{(number * 100).normalize():f}%"

    def price(value):
        number = _number(value)
        if number is None:
            return "미확인"
        return f"{number:,.2f}" + ("원" if snapshot["market"] == "KR" else " USD")

    units = _number(campaign["normalized_units"])
    status = "보유" if units else "청산 완료"
    invested_return = _number(campaign.get("invested_price_return_pct"))
    lines = [
        "📒 슬롯·비중 전략 원장 · 검증용",
        f"**{_label(campaign['symbol'])} · {status}**",
        "",
        f"점유 슬롯: {1 if units else 0}개",
        f"잔여 배분: {percent(campaign['remaining_allocation'])}",
        f"누적 투입 배분: {percent(campaign['cumulative_deployed_allocation'])}",
        f"잔여 가중평단: {price(campaign.get('average_cost')) if units else '청산 완료'}",
        f"마지막 원장 가격: {price(campaign['mark_price'])}",
        f"추가 가능 잔여 배분: {percent(campaign['conditional_remaining_allocation'])} · 조건 충족 시에만 가능",
        "※ 잔여 배분은 추가 매수 일정이나 계좌 현금 확보를 뜻하지 않습니다.",
        "",
        f"투입분 가격수익률: {invested_return:+.2f}%" if invested_return is not None else "투입분 가격수익률: 잔여 보유 없음",
        f"실현 1슬롯 기여: {percent(campaign['realized_contribution'], signed=True)}",
        f"미실현 1슬롯 기여: {percent(campaign['unrealized_contribution'], signed=True)}",
        f"합계 1슬롯 기여: {percent(campaign['one_slot_contribution'], signed=True)}",
    ]
    if snapshot.get("validation_only"):
        lines.append("개별 과거 포지션 검증 장부 · 전체 포트폴리오 합산 금지")
    else:
        lines.append(f"고정 {snapshot['contribution_denominator_slots']}슬롯 기준 기여: "
                     f"{percent(snapshot['capacity_normalized_contribution'], signed=True)}")
    if campaign["add_permission"] == "CANCELLED_BY_REDUCTION":
        lines.append("전략 비중 축소로 추가 배분 권한이 취소되었습니다.")
    lines.extend([
        "기여도는 비용을 반영하며 가격수익률과 다릅니다.",
        "평가는 마지막 원장 가격 기준이며 최신 시세를 보장하지 않습니다.",
        "", "🏦 실계좌 집행 증거",
    ])
    status_labels = {
        "UNKNOWN": "결과 미확인", "SUBMITTED": "접수 · 체결 미확인",
        "ACCEPTED": "접수 · 체결 미확인", "REJECTED": "주문 거절",
        "CANCELLED": "취소", "PARTIAL": "부분 체결 확인", "FILLED": "체결 확인",
    }
    relevant = [e for e in snapshot.get("executions", []) if e.get("campaign_id") == campaign_id]
    latest = {}
    for record in relevant:
        key = (record.get("execution_profile_ref"), record.get("intent_ref"))
        if key not in latest or record.get("observed_at", "") > latest[key].get("observed_at", ""):
            latest[key] = record
    for index, (_, record) in enumerate(sorted(latest.items(), key=lambda item: str(item[0]))[:3], 1):
        line = f"집행 기록 {index}: {status_labels.get(record.get('status'), '상태 미확인')}"
        if record.get("confirmed_quantity") is not None:
            quantity = _number(record["confirmed_quantity"])
            line += f" · 확인 수량 {quantity:g}주" if quantity is not None else " · 확인 수량 미확인"
        lines.append(line)
    if len(latest) > 3:
        lines.append(f"외 집행 기록 {len(latest) - 3}건 생략")
    unresolved = [r for r in unresolved_execution_overlays if r.get("campaign_id") == campaign_id]
    if unresolved:
        record = max(unresolved, key=lambda r: str(r.get("observed_at", "")))
        label = "프로필 미확인" if record.get("profile_status", "UNKNOWN") == "UNKNOWN" else "체결 근거 미완료"
        lines.append(f"{label}: {status_labels.get(record.get('status'), '상태 미확인')}")
    if not latest and not unresolved:
        lines.append("연결된 주문·체결 증거 없음 (0주로 단정하지 않음)")
    lines.extend(["실계좌 주문 거절·미체결에도 전략 원장은 유지됩니다.",
                  "전략 손익과 실제 계좌 손익은 별도입니다. 실제 주문 신호가 아닙니다."])
    return "\n".join(lines)
