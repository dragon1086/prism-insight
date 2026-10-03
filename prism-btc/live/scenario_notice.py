"""Compact Korean scenario notices from verified events, no network or DB."""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TITLES = {
    "PLAN": "📝 BTC 데모 매매 계획 · 주문 전",
    "SUBMITTED": "📨 BTC 데모 주문 접수 · 체결 미확정",
    "FILLED": "📌 BTC 데모 진입 체결",
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
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    return value


def _money(value):
    value = _number(value)
    return f"{_decimal(value)} USDT" if value is not None else "미확인"


def _decimal(value, signed=False):
    """Bound numeric display length even for malformed-but-finite magnitudes."""
    if abs(value) >= 1e9:
        return format(value, "+.5g" if signed else ".5g")
    return format(value, "+,.2f" if signed else ",.2f")


def _percent(value, signed=False):
    return _decimal(value, signed) + "%"


def _time(value):
    try:
        return datetime.fromtimestamp(value, KST).strftime("%m/%d %H:%M:%S KST")
    except (ValueError, OverflowError, OSError, TypeError):
        return "미확인"


def _position(value, at=None):
    """A prior baseline can be old; the new observation must match this event."""
    if not isinstance(value, dict) or value.get("verified") is not True:
        return None
    timestamp, qty = _number(value.get("timestamp")), _number(value.get("quantity"))
    avg = _number(value.get("average_entry_price"))
    if timestamp is None or _time(timestamp) == "미확인" or qty is None or qty < 0:
        return None
    if at is not None and abs(timestamp - at) > 120:
        return None
    if qty and (avg is None or avg <= 0 or value.get("side") not in {"LONG", "SHORT"}):
        return None
    return value


def _account(value, at):
    if not isinstance(value, dict) or value.get("same_event") is not True or value.get("same_account") is not True:
        return None
    equity, timestamp = (_number(value.get(k)) for k in ("equity", "timestamp"))
    if equity is None or equity <= 0 or timestamp is None or abs(timestamp-at) > 120:
        return None
    return value


def _targets(position, field):
    targets = position.get(field)
    if not isinstance(targets, list) or len(targets) > 20:
        return None
    total = 0
    for item in targets:
        if not isinstance(item, dict):
            return None
        price, qty = _number(item.get("price")), _number(item.get("quantity"))
        if price is None or price <= 0 or qty is None or qty <= 0:
            return None
        total += qty
    if not math.isfinite(total) or (total > position["quantity"] and not math.isclose(total, position["quantity"], rel_tol=1e-12, abs_tol=0)):
        return None
    return targets


def _target_text(position, price, qty, equity):
    avg = position["average_entry_price"]
    sign = 1 if position["side"] == "LONG" else -1
    change = sign * (price - avg) / avg * 100
    pnl = sign * (price - avg) * qty
    if not all(math.isfinite(v) for v in (change, pnl)):
        return "계산 자료 미확인"
    lev = _number(position.get("exchange_leverage"))
    result = f"1배 포지션 수익률 {_percent(change, True)}"
    if lev is not None and lev > 0 and math.isfinite(change * lev):
        result += f" · {lev:g}배 단순환산 {_percent(change*lev, True)}"
    else:
        result += " · 레버리지 환산 미확인"
    result += f" / 해당 수량 예상 손익 {_decimal(pnl, True)} USDT"
    if equity is not None and math.isfinite(pnl/equity*100):
        result += f" (현재 계좌 순자산 대비 {_percent(pnl/equity*100, True)})"
    return result


def _stop_impact(position):
    """Estimate this snapshot only; never borrow another observation's equity."""
    stop = _number(position.get("hard_stop"))
    if not position["quantity"] or stop is None or stop <= 0:
        return None, None
    sign = 1 if position["side"] == "LONG" else -1
    pnl = sign * (stop-position["average_entry_price"]) * position["quantity"]
    if not math.isfinite(pnl):
        return None, None
    account = _account(position.get("account_snapshot"), position["timestamp"])
    impact = pnl/account["equity"]*100 if account else None
    return pnl, impact if impact is not None and math.isfinite(impact) else None


def _position_lines(event, after):
    lines = []
    before = _position(event.get("position_before"))
    if before is not None and before["timestamp"] > after["timestamp"]:
        before = None
    qty = after["quantity"]
    if event["kind"] == "FILLED":
        fill_qty = _number(event.get("quantity"))
        lines.append(f"이번 체결: {fill_qty:g} BTC · 체결가 {_money(event.get('price'))}" if fill_qty is not None and fill_qty > 0 else "이번 체결 수량·가격 자료 미확인")
    if event["kind"] == "PROTECTION" and before is None:
        lines.append("보호 설정 최초 확인 · 이전 상태 미확인")
    if before is not None:
        lines.append(f"변경 전: {_time(before['timestamp'])} / 변경 후: {_time(after['timestamp'])}")
        if before.get("side") != after.get("side"):
            names = {"LONG":"롱", "SHORT":"숏"}
            lines.append(f"방향: {names.get(before.get('side'), '보유 없음')} → {names.get(after.get('side'), '보유 없음')}")
        fields = (("quantity", "전체 보유량", lambda v: f"{v:g} BTC"),
                  ("average_entry_price", "전체 평균 진입가", _money),
                  ("hard_stop", "최종 손절·수익보호 가격(SL)", _money))
        for key, label, fmt in fields:
            old, new = before.get(key), after.get(key)
            if old is None or new is None:
                lines.append(f"{label}: {fmt(old) if old is not None else '미확인'} → {fmt(new) if new is not None else '미확인'}")
            elif old == new:
                lines.append(f"{label}: {fmt(new)} · 변경 없음")
            else:
                lines.append(f"{label}: {fmt(old)} → {fmt(new)}")
        if before["quantity"] > 0 and qty > 0:
            old_pnl, old_impact = _stop_impact(before)
            new_pnl, new_impact = _stop_impact(after)
            old_money = _decimal(old_pnl, True) if old_pnl is not None else "미확인"
            new_money = _decimal(new_pnl, True) if new_pnl is not None else "미확인"
            old_percent = _percent(old_impact, True) if old_impact is not None else "미확인"
            new_percent = _percent(new_impact, True) if new_impact is not None else "미확인"
            lines.append(f"SL 가격 정리 가정 손익: {old_money} → {new_money} USDT")
            lines.append(f"계좌 영향(비용 전·각 조회 시점 순자산 기준): {old_percent} → {new_percent}")
        for key, label in (("take_profits", "익절 목표(TP)"), ("partial_stops", "부분 손절 목표")):
            old, new = _targets(before, key), _targets(after, key)
            def describe(items):
                if items is None:
                    return "미확인"
                result = " / ".join(f"{_money(t['price'])} · {t['quantity']:g} BTC" for t in items[:2]) or "없음"
                if len(items) > 2:
                    result += f" / 나머지 {len(items)-2}개 상세 생략"
                return result
            lines.append(f"{label}: {describe(old)} → {describe(new)}" if old != new or old is None else f"{label}: {describe(new)} · 변경 없음")
    else:
        lines.append(f"전체 보유량: {qty:g} BTC · 전체 평균 진입가: {_money(after.get('average_entry_price'))}")
        lines.append(f"포지션 자료 기준: {_time(after['timestamp'])}")
    if qty <= 0:
        lines.append("현재 보유 포지션 없음 · 손익 정산 확인과는 별개입니다.")
        return lines
    lev = _number(after.get("exchange_leverage"))
    lines.append(f"거래소 레버리지: {lev:g}배 확인" if lev is not None and lev > 0 else "거래소 레버리지 미확인")
    account = _account(after.get("account_snapshot"), after["timestamp"])
    equity = account["equity"] if account else None
    if account:
        margin = _number(account.get("position_margin"))
        if margin is not None and margin >= 0 and math.isfinite(margin/equity*100):
            lines.append(f"현재 포지션 증거금: {_money(margin)} / 계좌 순자산 {_money(equity)}의 {_percent(margin/equity*100)}")
        else:
            lines.append(f"계좌 순자산: {_money(equity)} · 포지션 증거금·비중 자료 미확인")
        mode = {"ISOLATED_MARGIN":"격리", "REGULAR_MARGIN":"교차", "PORTFOLIO_MARGIN":"포트폴리오"}.get(account.get("margin_mode"), "미확인")
        lines.append(f"마진 방식: {mode} · 증거금은 최대손실 한도가 아닙니다.")
        lines.append(f"계좌 자료 기준: {_time(account['timestamp'])}")
    else:
        lines.append("계좌 증거금·비중 자료 미확인")
    stop = _number(after.get("hard_stop"))
    if stop is not None and stop > 0:
        protected_gain = (stop-after["average_entry_price"]) * (1 if after["side"] == "LONG" else -1) >= 0
        lines.append(f"{'수익 보호' if protected_gain else '최종 손절'}(SL): {_money(stop)} · 남은 보유물량 전량")
        lines.append(_target_text(after, stop, qty, equity))
    else:
        lines.append("최종 손절(SL): 미확인")
    for field, label in (("take_profits", "익절 목표"), ("partial_stops", "부분 손절 목표")):
        targets = _targets(after, field)
        if targets is None:
            lines.append(f"{label}: 미확인")
            continue
        for index, target in enumerate(targets[:2], 1):
            lines.append(f"{label} {index}: {_money(target['price'])} · {target['quantity']:g} BTC (현재 보유물량 {target['quantity']/qty*100:g}%)")
            lines.append(_target_text(after, target["price"], target["quantity"], equity))
        if len(targets) > 2:
            total_qty = sum(t["quantity"] for t in targets)
            lines.append(f"{label}: 총 {len(targets)}개 · 합계 {total_qty:g} BTC ({total_qty/qty*100:g}%) · 나머지 {len(targets)-2}개 상세 생략")
        if field == "take_profits":
            target_qty = sum(t["quantity"] for t in targets)
            remaining = 0 if math.isclose(qty, target_qty, rel_tol=1e-12, abs_tol=0) else max(0, qty-target_qty)
            if remaining > 1e-12:
                lines.append(f"목표 익절 후 남길 물량: {remaining:g} BTC ({remaining/qty*100:g}%) · 추세 추종, SL 보호")
    budget, initial = (_number(after.get(k)) for k in ("scenario_budget", "scenario_initial_equity"))
    if budget is not None and budget >= 0:
        basis = f"시나리오 시작 순자산의 {_percent(budget/initial*100)}" if initial is not None and initial > 0 and math.isfinite(budget/initial*100) else "시작 순자산 대비 비율 미확인"
        lines.append(f"이번 매매 전체 손실 목표 한도: {_money(budget)} ({basis}) · 첫 계획부터 전량 청산까지 비용 포함")
    risk = _number(after.get("scenario_risk"))
    if risk is not None and risk >= 0:
        label = "비용·미체결까지 반영한 계획 위험" if after.get("scenario_risk_includes_pending") is True else "계획 위험(미체결 예약 포함 여부 미확인)"
        basis = f"시나리오 시작 순자산의 {_percent(risk/initial*100)}" if initial is not None and initial > 0 and math.isfinite(risk/initial*100) else "시작 순자산 대비 비율 미확인"
        lines.append(f"{label}: {_money(risk)} ({basis}) · 이미 소진한 손실＋남은 위험")
    lines.append("위 목표별 손익은 수수료·슬리피지·펀딩 전이며 합산 보장이 아닙니다. 실제 손실은 계획 한도를 넘을 수 있습니다.")
    lines.append("레버리지 수익률은 단순환산이며 거래소 증거금 수익률과 다를 수 있습니다.")
    return lines


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
    if at is None or _time(at) == "미확인":
        raise ValueError("event_timestamp_required")
    title = TITLES[kind]
    if kind == "PROTECTION" and event.get("change_type") == "initial_protection":
        title = "🛡 BTC 데모 포지션·보호 최초 확인"
    lines = [title, _time(at)]
    if event.get("reason_code") in REASONS:
        lines.append("사유: " + REASONS[event["reason_code"]])
    after = _position(event.get("position_after"), at)
    if kind == "PROTECTION" and _position(event.get("position_before")) is None and event.get("change_type") != "initial_protection":
        lines[0] = "🛡 BTC 데모 보호 설정 확인 · 이전 상태 미확인"
    if kind in {"FILLED", "PROTECTION", "PARTIAL"} and after is not None:
        lines.append("방향: " + {"LONG":"롱(가격 상승에 투자)", "SHORT":"숏(가격 하락에 투자)"}.get(after.get("side"), "보유 없음"))
        lines.extend(_position_lines(event, after))
        if event.get("protection_confirmed") is not True:
            lines.append("⚠ 보호주문 적용 미확인: 신규 위험 확대 보류")
    elif kind in {"PLAN", "SUBMITTED", "FILLED", "PROTECTION"}:
        if event.get("side") not in {"LONG", "SHORT"}:
            raise ValueError("side_required")
        lines.append("방향: " + ("롱" if event["side"] == "LONG" else "숏"))
        lev = _number(event.get("exchange_leverage"))
        lines.append(f"레버리지: {lev:g}배 확인" if lev is not None and lev > 0 else "레버리지: 거래소 적용 미확인")
        label = "체결가" if kind == "FILLED" else "계획 진입가"
        if kind != "PROTECTION":
            lines.append(f"{label}: {_money(event.get('price'))}")
        if kind == "FILLED":
            qty = _number(event.get("quantity"))
            lines.append(f"이번 체결: {qty:g} BTC" if qty is not None else "체결 수량: 미확인")
        if kind in {"FILLED", "PROTECTION"}:
            lines.append("전체 포지션 기준 수익률 미확인 · 아래 SL/TP는 전달된 계획 참고값이며 현재 전체 설정 확인과는 별개입니다.")
        lines.append(f"최종 SL: {_money(event.get('hard_stop'))}")
        tps = event.get("take_profits")
        if isinstance(tps, list) and len(tps) <= 20:
            targets = []
            total = 0.0
            for tp in tps:
                if not isinstance(tp, dict):
                    targets = None
                    break
                price, fraction = _number(tp.get("price")), _number(tp.get("fraction"))
                if price is None or price <= 0 or fraction is None or not 0 < fraction <= 1:
                    targets = None
                    break
                total += fraction
                targets.append(f"{_money(price)}(계획 물량의 {fraction*100:g}%)")
            if total > 1.0 and not math.isclose(total, 1.0, rel_tol=1e-12, abs_tol=0):
                targets = None
            if targets is None:
                lines.append("분할 TP 계획: 자료 미확인")
            elif targets:
                lines.append("분할 TP 계획: " + " / ".join(targets[:2]))
                if len(targets) > 2:
                    lines.append(f"전체 {len(targets)}개 중 나머지 {len(targets)-2}개 상세 생략")
            if targets is not None and total < 1:
                lines.append(f"추세 추종 잔여 계획: {(1-total)*100:g}% · SL 보호 유지")
        if kind == "PLAN":
            action = {"OPEN":"신규 포지션 구축 요청", "ADJUST":"기존 포지션 계획 수정 요청", "EXIT":"전량 청산 요청"}.get(event.get("plan_action"))
            if action:
                lines.append(action + " · 아직 적용 전")
            new_qty = _number(event.get("quantity"))
            before_qty = _number(event.get("before_quantity"))
            if event.get("plan_action") == "ADJUST":
                lines.append(f"현재 확인된 보유량: {before_qty:g} BTC" if before_qty is not None and before_qty >= 0 else "현재 보유량: 미확인")
                lines.append(f"손절 설정 변경 요청: {_money(event.get('before_hard_stop'))} → {_money(event.get('hard_stop'))} · 적용 미확정")
            if new_qty is not None and new_qty >= 0:
                lines.append(f"새로 요청할 진입 수량: {new_qty:g} BTC · 미체결")
            lines.append("계획이며 주문·체결을 뜻하지 않습니다.")
        elif event.get("protection_confirmed") is not True:
            lines.append("⚠ 보호주문 적용 미확인: 신규 위험 확대 보류")
        budget, risk = _number(event.get("scenario_budget")), _number(event.get("scenario_risk"))
        initial = _number(event.get("scenario_initial_equity"))
        if budget is not None and budget >= 0:
            basis = f" · 시나리오 시작 순자산의 {_percent(budget/initial*100)}" if initial is not None and initial > 0 and math.isfinite(budget/initial*100) else ""
            lines.append(f"매매 전체 손실 목표 한도: {_money(budget)}{basis} · 비용 포함 계획 한도이며 초과 손실 가능")
        if risk is not None and risk >= 0:
            label = "비용·미체결까지 반영한 계획 위험" if event.get("scenario_risk_includes_pending") is True else "계획 위험(미체결 예약 포함 여부 미확인)"
            basis = f"시나리오 시작 순자산의 {_percent(risk/initial*100)}" if initial is not None and initial > 0 and math.isfinite(risk/initial*100) else "시작 순자산 대비 비율 미확인"
            lines.append(f"{label}: {_money(risk)} ({basis}) · 이미 소진한 손실＋남은 위험")
        # Snapshot must be captured for this event and same account; renderer
        # never fetches another position or invents margin from notional/10.
        snap = event.get("account_snapshot")
        if _account(snap, at) is not None:
            im, equity = _number(snap.get("position_margin")), _number(snap.get("equity"))
            captured = _number(snap.get("timestamp"))
            mode = {"ISOLATED_MARGIN":"격리", "REGULAR_MARGIN":"교차", "PORTFOLIO_MARGIN":"포트폴리오"}.get(snap.get("margin_mode"))
            if im is not None and im >= 0 and math.isfinite(im/equity*100):
                label="체결 후 관측 증거금" if captured>at else "증거금"
                lines.append(f"{label}: {_money(im)} · 같은 계좌 순자산 대비 {_percent(im/equity*100)}")
                if mode:
                    lines.append(f"마진 방식: {mode}")
                lines.append("증거금은 최대손실 한도가 아닙니다.")
                lines.append("계좌 자료 기준: " + datetime.fromtimestamp(captured,KST).strftime("%H:%M:%S KST"))
            else:
                lines.append("계좌 증거금·비중 자료 미확인")
        elif kind == "FILLED" or isinstance(snap, dict):
            lines.append("계좌 증거금·비중 자료 미확인")
    if kind in {"PARTIAL", "CLOSED"}:
        qty = _number(event.get("quantity"))
        if qty is not None:
            lines.append(f"정리 수량: {qty:g} BTC")
        lines.append(f"진입 → 청산: {_money(event.get('entry_price'))} → {_money(event.get('price'))}")
        entry_at = _number(event.get("entry_timestamp"))
        if entry_at is not None and _time(entry_at) != "미확인" and entry_at <= at:
            lines.append(f"보유 시간: {(at-entry_at)/60:.1f}분")
        net, fees, funding = (_number(event.get(k)) for k in ("net_pnl", "fees", "funding"))
        if event.get("settlement_confirmed") is True and all(v is not None for v in (net, fees, funding)):
            lines.append(f"확정 순손익: {_money(net)} · 수수료 {_money(fees)} · 펀딩 {_money(funding)}")
        else:
            if kind == "CLOSED":
                raise ValueError("complete_cost_evidence_required")
            lines.append("손익·비용 정산 미확정: 확정 수익으로 집계하지 않음")
    if kind in {"FILLED", "PARTIAL", "PROTECTION"} and after is None:
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
