"""Compact Korean scenario notices from verified events, no network or DB."""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TITLES = {
    "PLAN": "📝 BTC 데모 매매 계획 · 주문 전",
    "SUBMITTED": "📨 BTC 데모 주문 접수 · 체결 미확정",
    "FILLED": "📌 BTC 데모 분할 진입 체결",
    "PROTECTION": "🛡 BTC 데모 보호주문 변경 확인",
    "PARTIAL": "✂️ BTC 데모 부분 청산 체결",
    "CLOSED": "🏁 BTC 데모 시나리오 종료 · 정산 확인",
    "PENDING": "⚠️ BTC 데모 상태 미확정 · 신규 주문 보류",
    "HALTED": "⛔ BTC 데모 신규 진입 중단 · 운영자 검토 필요",
    "RESOLVED": "✅ BTC 데모 미확정 상태 해소",
    "MODEL_ERROR": "⚠️ BTC 데모 LLM 판단 오류 · 이번 판단 미반영",
    "MODEL_RECOVERED": "✅ BTC 데모 LLM 응답 검증 정상 확인",
}
REASONS = {
    "BREAKOUT":"수렴 뒤 돌파", "RETEST":"돌파 구간 재확인", "TREND_CONTINUATION":"단기 추세 지속",
    "TREND_INVALIDATED":"단기 추세 무효화", "HARD_STOP":"최종 손절", "PARTIAL_STOP":"부분 손절",
    "TAKE_PROFIT":"부분 익절", "TRAILING_STOP":"추적 손절", "TIME_EXPIRED":"시나리오 만료",
    "THREE_LOSSES":"3개 시나리오 연속 순손실", "DAILY_LOSS":"하루 손실 한도 도달",
}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value


def _money(value):
    value = _number(value)
    return f"{value:,.2f} USDT" if value is not None else "미확인"


def render_notice(event: dict) -> str:
    """No raw identifiers/payloads. Acknowledgement never implies a fill or PnL."""
    kind = event.get("kind")
    if kind not in TITLES:
        raise ValueError("unknown_notice_kind")
    if kind in {"FILLED", "PARTIAL"} and event.get("fill_confirmed") is not True:
        raise ValueError("fill_evidence_required")
    if kind == "CLOSED" and not (event.get("settlement_confirmed") is True
            and event.get("flat_confirmed") is True
            and event.get("orders_terminal") is True):
        raise ValueError("closure_evidence_required")
    if kind == "PROTECTION" and event.get("protection_confirmed") is not True:
        raise ValueError("protection_evidence_required")
    if kind == "RESOLVED" and event.get("resolution_confirmed") is not True:
        raise ValueError("resolution_evidence_required")
    if kind == "MODEL_RECOVERED" and event.get("model_validation_confirmed") is not True:
        raise ValueError("model_validation_evidence_required")
    at = _number(event.get("timestamp"))
    if at is None:
        raise ValueError("event_timestamp_required")
    lines = [TITLES[kind], datetime.fromtimestamp(at, KST).strftime("%m/%d %H:%M:%S KST")]
    if event.get("reason_code") in REASONS:
        lines.append("사유: " + REASONS[event["reason_code"]])
    if kind in {"PLAN", "SUBMITTED", "FILLED", "PROTECTION"}:
        if event.get("side") not in {"LONG", "SHORT"}:
            raise ValueError("side_required")
        lines.append("방향: " + ("롱" if event["side"] == "LONG" else "숏"))
        lev = _number(event.get("exchange_leverage"))
        lines.append(f"레버리지: {lev:g}배 확인" if lev else "계획 레버리지: 10배 · 거래소 적용 미확인")
        label = "체결가" if kind == "FILLED" else "계획 진입가"
        if kind != "PROTECTION":
            lines.append(f"{label}: {_money(event.get('price'))}")
        if kind == "FILLED":
            qty = _number(event.get("quantity"))
            lines.append(f"이번 체결: {qty:g} BTC" if qty is not None else "체결 수량: 미확인")
        lines.append(f"최종 SL: {_money(event.get('hard_stop'))}")
        tps = event.get("take_profits")
        if isinstance(tps, list) and len(tps) <= 8:
            targets = []
            total = 0.0
            for tp in tps:
                if not isinstance(tp, dict):
                    raise ValueError("invalid_tp_notice")
                price, fraction = _number(tp.get("price")), _number(tp.get("fraction"))
                if price is None or price <= 0 or fraction is None or not 0 < fraction <= 1:
                    raise ValueError("invalid_tp_notice")
                total += fraction
                targets.append(f"{_money(price)}({fraction*100:g}%)")
            if total > 1.0:
                raise ValueError("invalid_tp_notice")
            if targets:
                lines.append("분할 TP 계획: " + " / ".join(targets))
            if total < 1:
                lines.append(f"추세 추종 잔여 계획: {(1-total)*100:g}% · SL 보호 유지")
        if kind == "PLAN":
            lines.append("계획이며 주문·체결을 뜻하지 않습니다.")
        elif event.get("protection_confirmed") is not True:
            lines.append("⚠ 보호주문 적용 미확인: 신규 위험 확대 보류")
        budget, risk = _number(event.get("scenario_budget")), _number(event.get("scenario_risk"))
        if budget is not None:
            lines.append(f"시나리오 손실 예산: {_money(budget)} · 비용 포함 계획 한도")
        if risk is not None:
            lines.append(f"소진 손실＋잔여 손절위험: {_money(risk)}")
        # Snapshot must be captured for this event and same account; renderer
        # never fetches another position or invents margin from notional/10.
        snap = event.get("account_snapshot")
        if isinstance(snap, dict) and snap.get("same_event") is True and snap.get("same_account") is True:
            im, equity = _number(snap.get("position_margin")), _number(snap.get("equity"))
            captured = _number(snap.get("timestamp"))
            mode = {"ISOLATED_MARGIN":"격리", "REGULAR_MARGIN":"교차", "PORTFOLIO_MARGIN":"포트폴리오"}.get(snap.get("margin_mode"))
            if im is not None and equity is not None and equity > 0 and captured is not None and abs(at-captured) <= 120:
                label="체결 후 관측 증거금" if captured>at else "증거금"
                lines.append(f"{label}: {_money(im)} · 같은 계좌 순자산 대비 {im/equity*100:.2f}%")
                if mode:
                    lines.append(f"마진 방식: {mode}")
                lines.append("계좌 자료 기준: " + datetime.fromtimestamp(captured,KST).strftime("%H:%M:%S KST"))
            else:
                lines.append("계좌 증거금·비중 자료 미확인")
        elif kind == "FILLED":
            lines.append("계좌 증거금·비중 자료 미확인")
    if kind in {"PARTIAL", "CLOSED"}:
        qty = _number(event.get("quantity"))
        if qty is not None:
            lines.append(f"정리 수량: {qty:g} BTC")
        lines.append(f"진입 → 청산: {_money(event.get('entry_price'))} → {_money(event.get('price'))}")
        entry_at = _number(event.get("entry_timestamp"))
        if entry_at is not None and entry_at <= at:
            lines.append(f"보유 시간: {(at-entry_at)/60:.1f}분")
        net, fees, funding = (_number(event.get(k)) for k in ("net_pnl", "fees", "funding"))
        if event.get("settlement_confirmed") is True and all(v is not None for v in (net, fees, funding)):
            lines.append(f"확정 순손익: {_money(net)} · 수수료 {_money(fees)} · 펀딩 {_money(funding)}")
        else:
            if kind == "CLOSED":
                raise ValueError("complete_cost_evidence_required")
            lines.append("손익·비용 정산 미확정: 확정 수익으로 집계하지 않음")
    if kind in {"FILLED", "PARTIAL", "PROTECTION"}:
        remaining = _number(event.get("remaining_quantity"))
        if remaining is not None and remaining >= 0:
            lines.append(f"확인된 총잔량: {remaining:g} BTC")
    if kind == "HALTED":
        if event.get("reason_code") not in {"THREE_LOSSES", "DAILY_LOSS"}:
            lines.append("3개 시나리오 연속 순손실 또는 하루 손실 한도 도달")
        lines.append("기존 포지션 보호는 유지합니다. 검토·명시적 승인 전 자동 재개하지 않습니다.")
    if kind == "PENDING":
        lines.append("체결·취소·보호·정산의 불명확 상태를 확인 중입니다. 재주문하지 않습니다.")
    if kind == "MODEL_ERROR":
        reason = {"llm_output_contract_failed":"LLM 응답이 출력 규격 검증을 통과하지 못했습니다.",
                  "llm_call_failed":"LLM 호출에 실패했습니다."}.get(event.get("reason_code"))
        if reason is None:
            raise ValueError("model_error_reason_required")
        lines.extend([reason, "이번 응답에 따른 주문 변경은 적용하지 않습니다.",
            "거래소 주문·체결·보호 상태는 별도 확인 대상입니다."])
    if kind == "MODEL_RECOVERED":
        lines.extend(["후속 LLM 응답이 검증을 통과했습니다.",
            "주문 체결·보호·정산의 확인이나 매매 재개를 뜻하지 않습니다."])
    if kind == "RESOLVED":
        resolution = {"CANCELLED_UNFILLED":"미체결 취소 확인", "FILLED_PROTECTED":"체결·보호 확인", "FLAT_SETTLED":"잔량 0·정산 확인", "PROTECTION_QUERY_RECOVERED":"거래소 조회·보호 상태 확인 · 손익 정산 상태는 별도입니다."}.get(event.get("resolution"))
        if resolution is None:
            raise ValueError("resolution_type_required")
        lines.append(resolution)
    return "\n".join(lines)
