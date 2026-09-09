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
    """Keep strategy accounting, cash, and broker evidence visibly separate."""
    campaign = next(c for c in snapshot["campaigns"] if c["campaign_id"] == campaign_id)
    currency = snapshot["currency"]

    def money(value):
        number = _number(value)
        if number is None:
            return "미확인"
        return f"{number:,.0f}원" if currency == "KRW" else f"${number:,.2f}"

    quantity = _number(campaign["quantity"])
    target = _number(campaign["target_pct"])
    unit = _number(snapshot["unit_budget"])
    invested = _number(campaign["invested_budget"])
    unused_base = max(Decimal(0), unit - invested)
    status = "보유" if quantity else "청산 완료"
    lines = [
        "📒 분할진입 전략 원장 · 검증용",
        f"**{_label(campaign['symbol'])} · {status}**",
        "",
        f"누적 투입 목표: 기준 1단위의 {target:g}%",
        f"누적 투입원금: {money(invested)}",
        f"가상 수량: {quantity:,.6f}주",
        f"잔여 가중평단: {money(campaign.get('average_cost')) if quantity else '청산 완료'}",
        f"기본 100%까지 미투입 예산: {money(unused_base)}",
        "※ 미투입 예산은 확보된 계좌 현금이 아닙니다.",
        "",
        f"원장 실현손익: {money(campaign['realized_pnl'])}",
        f"원장 평가손익: {money(campaign['unrealized_pnl'])}",
        f"장부 가상 현금: {money(snapshot['free_cash'])}",
        f"장부 평가자산: {money(snapshot['equity'])}",
        f"장부 수익률: {_number(snapshot['portfolio_return_pct']):+.2f}%",
        "평가는 마지막 원장 가격 기준이며 최신 시세를 보장하지 않습니다.",
        "",
        "🏦 실계좌 집행 증거",
    ]
    if str(snapshot.get("book_id", "")).startswith("isolated:"):
        lines.insert(2, "개별 과거 포지션 검증 장부 · 전체 포트폴리오 합산 금지")
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
            line += f" · 확인 수량 {_number(record['confirmed_quantity']):g}주"
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
