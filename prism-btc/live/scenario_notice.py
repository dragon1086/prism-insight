"""Compact Korean scenario notices from verified events, no network or DB."""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TITLES = {
    "PLAN": "📝 BTC 데모 매매 계획 · 주문 전",
    "SUBMITTED": "📨 BTC 데모 주문 접수 · 체결 미확정",
    "FILLED": "📌 BTC 데모 진입 체결",
    "PROTECTION": "🛡 BTC 데모 보호 변경 확인",
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


def closed_account_snapshot(snapshot, closed_at):
    """Only a verified post-exit flat observation; never initial equity + PnL.

    Zero equity is valid for display, not a percentage denominator. Both the
    position and account must be observed after the last exit, within 120s.
    """
    position = _position(snapshot, closed_at)
    if position is None or position['quantity'] != 0 or position['timestamp'] < closed_at:
        return None
    account = position.get('account_snapshot')
    if not isinstance(account, dict) or account.get('same_event') is not True or account.get('same_account') is not True:
        return None
    equity, timestamp = (_number(account.get(k)) for k in ('equity', 'timestamp'))
    if (equity is None or equity < 0 or timestamp is None or timestamp != position['timestamp']
            or not 0 <= timestamp-closed_at <= 120 or _time(timestamp) == '미확인'):
        return None
    return account


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
    result = f"{_decimal(pnl, True)} USDT"
    if equity is not None and math.isfinite(pnl/equity*100):
        result += f" · 계좌 {_percent(pnl/equity*100, True)}"
    result += f" (1배 {_percent(change, True)}"
    if lev is not None and lev > 0 and math.isfinite(change * lev):
        result += f" / {lev:g}배 {_percent(change*lev, True)}"
    else:
        result += " · 레버리지 환산 미확인"
    return result + ")"


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


def _partial_result_lines(event, after):
    lines = []
    gross = _number(event.get("fill_gross_pnl"))
    if event.get("fill_gross_pnl_confirmed") is True and gross is not None:
        text = f"• 이번 청산 가격손익: {_decimal(gross, True)} USDT"
        account = _account(after.get("account_snapshot"), after["timestamp"]) if after else None
        if account and math.isfinite(gross/account["equity"]*100):
            text += f" · 계좌 {_percent(gross/account['equity']*100, True)}"
        lines.extend([text, "  수수료·펀딩 전 · 확정 순손익 아님"])
    return lines


def _position_lines(event, after):
    """Compact public view; the complete verified snapshot stays in the event."""
    lines = []
    before = _position(event.get("position_before"))
    if before is not None and before["timestamp"] > after["timestamp"]:
        before = None
    qty = after["quantity"]
    changed = event["kind"] == "PROTECTION" and before is not None
    if event["kind"] == "PROTECTION" and before is None:
        lines.append("기존 보유 상태 안내 · 신규 진입 아님 · 이전 상태 미확인")
    if event["kind"] in {"FILLED", "PARTIAL"}:
        fill_qty = _number(event.get("quantity"))
        lines.append("")
        lines.append(f"🧾 이번 체결: {fill_qty:g} BTC · {_money(event.get('price'))}" if fill_qty is not None and fill_qty > 0 else "🧾 이번 체결 수량·가격 미확인")
        fill_price = _number(event.get("price"))
        if fill_qty is not None and fill_qty > 0 and fill_price is not None and fill_price > 0 and math.isfinite(fill_qty * fill_price):
            lines.append(f"• 체결금액(명목): {_money(fill_qty * fill_price)} · 증거금 아님")
        if event["kind"] == "PARTIAL":
            lines.extend(_partial_result_lines(event, after))
            if event.get("settlement_confirmed") is not True:
                lines.append("손익·비용 정산 미확정: 확정 수익으로 집계하지 않음")
    if not changed:
        lines.extend(["", "📦 전체 포지션"])
    if changed:
        holdings = []
        for field, label, fmt in (("quantity", "보유량", lambda v: f"{v:g} BTC"),
                                 ("average_entry_price", "평단", _money)):
            old, new = before.get(field), after.get(field)
            value = fmt(new) if new is not None else '미확인'
            if old != new and old is not None:
                value = fmt(old) + " → " + value
            holdings.append(f"{label} {value}")
        lines.extend(["", "📦 " + " · ".join(holdings)])
        if before.get("side") != after.get("side"):
            names = {"LONG":"롱", "SHORT":"숏"}
            lines.append(f"방향: {names.get(before.get('side'), '보유 없음')} → {names.get(after.get('side'), '보유 없음')}")
    else:
        old_qty = before['quantity'] if before is not None else None
        amount = f"{old_qty:g} BTC → {qty:g} BTC" if old_qty is not None and old_qty != qty else f"{qty:g} BTC"
        average = _money(after.get('average_entry_price'))
        if before is not None and before['quantity'] > 0 and before.get('average_entry_price') != after.get('average_entry_price'):
            average = _money(before.get('average_entry_price')) + " → " + average
        lines.append(f"• 누적 보유 {amount} · 관측 기준")
        if before is None and (event["kind"] == "PARTIAL" or event.get("entry_stage") == "additional"):
            lines.append("• 이전 보유량 미확인 · 이번 체결만으로 역산하지 않음")
        lines.append(f"• 평단 {average}")
    if qty <= 0:
        lines.append("현재 보유 포지션 없음 · 손익 확정은 별도")
        return lines
    account = _account(after.get("account_snapshot"), after["timestamp"])
    equity = account["equity"] if account else None
    lev = _number(after.get("exchange_leverage"))
    mode = {"ISOLATED_MARGIN":"격리", "REGULAR_MARGIN":"교차", "PORTFOLIO_MARGIN":"포트폴리오"}.get(account.get("margin_mode"), "방식 미확인") if account else "방식 미확인"
    margin = _number(account.get("position_margin")) if account else None
    capital = f"증거금 {_money(margin)}(계좌 {_percent(margin/equity*100)})" if margin is not None and margin >= 0 and math.isfinite(margin/equity*100) else "증거금·비중 미확인"
    leverage_text = f"{lev:g}배" if lev is not None and lev > 0 else "배율 미확인"
    lines.extend(["", f"💰 계좌 순자산: {_money(equity)}", f"• 총 {capital} · {leverage_text} · {mode}"])
    # The short timestamp preserves the independent observation time without
    # repeating an entire date on every account field.
    stamps = []
    if after["timestamp"] != event["timestamp"]:
        stamps.append("포지션 " + _time(after["timestamp"]))
    if account and account["timestamp"] != after["timestamp"]:
        stamps.append("계좌 " + _time(account["timestamp"]))
    if stamps:
        lines.append("• 조회 " + " / ".join(stamps))
    stop = _number(after.get("hard_stop"))
    missing = []
    exposure_changed = changed and any(before.get(k) != after.get(k) for k in ("quantity", "average_entry_price", "side"))
    if stop is not None and stop > 0:
        protected_gain = (stop-after["average_entry_price"]) * (1 if after["side"] == "LONG" else -1) >= 0
        label = "수익 보호 SL" if protected_gain else "손절 SL"
        prior = _number(before.get("hard_stop")) if changed and before.get("hard_stop") != stop else None
        price = f"{_decimal(prior)} → {_decimal(stop)} USDT" if prior is not None else _money(stop)
        lines.extend(["", f"🛡 {label} {price} · 남은 전량",
                      "• 예상 손익: " + _target_text(after, stop, qty, equity)])
        realized = _number(after.get("scenario_realized_net_pnl"))
        if after.get("scenario_accounting_confirmed") is True and realized is not None:
            remaining_pnl, _ = _stop_impact(after)
            combined = realized + remaining_pnl if remaining_pnl is not None else None
            lines.append(f"• 누적 실현손익(기록된 비용 포함): {_decimal(realized, True)} USDT")
            if combined is not None and math.isfinite(combined):
                impact = f" · 계좌 {_percent(combined/equity*100, True)}" if equity is not None and math.isfinite(combined/equity*100) else ""
                lines.append(f"• SL 시 매매 전체 예상: {_decimal(combined, True)} USDT{impact}")
    else:
        missing.append("손절 SL")
    for field, label in (("take_profits", "익절 TP"), ("partial_stops", "부분 손절")):
        targets = _targets(after, field)
        old = _targets(before, field) if changed else None
        if targets is None:
            missing.append(label)
            continue
        if field != "take_profits" and changed and targets == old and not exposure_changed:
            continue
        if changed and targets != old:
            previous = " / ".join(f"{_decimal(t['price'])}({t['quantity']:g} BTC)" for t in old[:2]) if old else ("없음" if old == [] else "미확인")
            lines.append(f"{label} 이전 {previous} → 아래 설정" if targets else f"{label} 이전 {previous} → 없음")
        for target in targets[:2]:
            icon = "🎯" if field == "take_profits" else "🛡"
            lines.extend(["", f"{icon} {label} {_money(target['price'])} · {target['quantity']/qty*100:g}%({target['quantity']:g} BTC)",
                          "• 해당 물량 예상 손익: " + _target_text(after, target["price"], target["quantity"], equity)])
        if len(targets) > 2:
            lines.append(f"{label} 총 {len(targets)}개 · 나머지 {len(targets)-2}개 상세 생략")
        if field == "take_profits":
            if not targets:
                lines.extend(["", "🎯 고정 TP 없음"])
            total = sum(t["quantity"] for t in targets)
            remaining = 0 if math.isclose(qty, total, rel_tol=1e-12, abs_tol=0) else max(0, qty-total)
            if remaining > 1e-12:
                protection = "SL 보호" if event.get("protection_confirmed") is True and stop is not None and stop > 0 else "SL 보호 미확인"
                lines.append((f"익절 후 {remaining/qty*100:g}% 추세 추종" if targets else "남은 전량 추세 추종") + " · " + protection)
    if missing:
        lines.append("⚠ 미확인: " + "·".join(missing))
    budget, initial = (_number(after.get(k)) for k in ("scenario_budget", "scenario_initial_equity"))
    risk = _number(after.get("scenario_risk"))
    if budget is not None and budget >= 0:
        basis = _percent(budget/initial*100) if initial is not None and initial > 0 and math.isfinite(budget/initial*100) else "비율 미확인"
        text = f"전체 매매 손실 목표 {basis}({_money(budget)}, 시작 계좌 기준)"
        lines.extend(["", "📌 " + text])
        if risk is not None and risk >= 0:
            risk_pct = _percent(risk/initial*100) if initial is not None and initial > 0 and math.isfinite(risk/initial*100) else "비율 미확인"
            text = f"• 누적손실+잔여위험 {risk_pct}({_money(risk)})"
            text += " · 비용·미체결 포함" if after.get("scenario_risk_includes_pending") is True else " · 미체결 포함 미확인"
            lines.append(text)
    else:
        lines.append("매매 전체 손실 목표 미확인")
    lines.extend(["※ TP/SL은 비용 전, 전체 예상은 향후 비용 전(수수료·슬리피지·펀딩). 배율 단순환산.",
                  "증거금≠손실한도, 실제 손실은 목표 초과 가능."])
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
    after = _position(event.get("position_after"), at)
    title = TITLES[kind]
    if kind == "CLOSED" and event.get("recovery_confirmation") is True:
        title = "🏁 BTC 데모 이전 매매 정산 확인 · 지연 안내"
    if kind == "FILLED":
        title = {"initial":"📌 BTC 데모 첫 진입 체결", "additional":"➕ BTC 데모 추가 체결 · 기존 매매"}.get(event.get("entry_stage"), "📌 BTC 데모 진입 체결 확인")
    if kind == "PARTIAL" and after is None:
        title = "✂️ BTC 데모 청산 체결 · 잔량 확인 중"
    if kind == "PARTIAL" and after is not None and after["quantity"] == 0:
        title = "🏁 BTC 데모 전량 청산 체결 · 정산 대기"
    if kind == "PROTECTION" and event.get("change_type") == "initial_protection":
        title = "🛡 BTC 데모 기존 포지션 확인"
    lines = [title, "🕐 " + _time(at)]
    if kind == "CLOSED" and event.get("recovery_confirmation") is True:
        lines.append("과거에 종료된 매매의 누락된 정산 안내입니다. 새 진입·추가 주문이 아닙니다.")
    if event.get("reason_code") in REASONS:
        lines.append("사유: " + REASONS[event["reason_code"]])
    if kind == "PROTECTION" and _position(event.get("position_before")) is None and event.get("change_type") != "initial_protection":
        lines[0] = "🛡 BTC 데모 보호 설정 확인 · 이전 상태 미확인"
    if kind in {"FILLED", "PROTECTION", "PARTIAL"} and after is not None:
        lines[0] += " · " + {"LONG":"롱", "SHORT":"숏"}.get(after.get("side"), "보유 없음")
        lines.extend(_position_lines(event, after))
        if after["quantity"] > 0 and event.get("protection_confirmed") is not True:
            lines.append("⚠ 보호주문 적용 미확인: 신규 위험 확대 보류")
    elif kind in {"PLAN", "SUBMITTED", "FILLED", "PROTECTION"}:
        if event.get("side") not in {"LONG", "SHORT"}:
            raise ValueError("side_required")
        lines.extend(["", "📝 주문·체결 상태", "방향: " + ("롱" if event["side"] == "LONG" else "숏")])
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
        lines.extend(["", f"🛡 최종 SL: {_money(event.get('hard_stop'))}"])
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
                lines.append("🎯 분할 TP 계획: " + " / ".join(targets[:2]))
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
            lines.extend(["", f"📌 매매 전체 손실 목표 한도: {_money(budget)}{basis}",
                          "비용 포함 계획 한도이며 초과 손실 가능"])
        if risk is not None and risk >= 0:
            label = "비용·미체결까지 반영한 계획 위험" if event.get("scenario_risk_includes_pending") is True else "계획 위험(미체결 예약 포함 여부 미확인)"
            basis = f"시나리오 시작 순자산의 {_percent(risk/initial*100)}" if initial is not None and initial > 0 and math.isfinite(risk/initial*100) else "시작 순자산 대비 비율 미확인"
            lines.extend([f"{label}: {_money(risk)} ({basis})", "이미 소진한 손실＋남은 위험"])
        # Snapshot must be captured for this event and same account; renderer
        # never fetches another position or invents margin from notional/10.
        snap = event.get("account_snapshot")
        if _account(snap, at) is not None:
            im, equity = _number(snap.get("position_margin")), _number(snap.get("equity"))
            lines.extend(["", f"💰 계좌 순자산: {_money(equity)}"])
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
    if kind == "CLOSED" or (kind == "PARTIAL" and (after is None or event.get("settlement_confirmed") is True)):
        lines.extend(["", "💵 청산 결과"])
        if kind == "PARTIAL" and after is None:
            lines.extend(_partial_result_lines(event, after))
        qty = _number(event.get("quantity"))
        if qty is not None and (kind == "CLOSED" or after is None):
            lines.append(f"정리 수량: {qty:g} BTC")
        if kind == "CLOSED" or after is None:
            lines.append(f"진입 → 청산: {_money(event.get('entry_price'))} → {_money(event.get('price'))}")
        entry_at = _number(event.get("entry_timestamp"))
        if entry_at is not None and _time(entry_at) != "미확인" and entry_at <= at:
            lines.append(f"보유 시간: {(at-entry_at)/60:.1f}분")
        net, fees, funding = (_number(event.get(k)) for k in ("net_pnl", "fees", "funding"))
        if event.get("settlement_confirmed") is True and all(v is not None for v in (net, fees, funding)):
            lines.append(f"확정 순손익: {_money(net)}")
            initial = _number(event.get("scenario_initial_equity"))
            if kind == "CLOSED" and initial is not None and initial > 0 and math.isfinite(net / initial * 100):
                lines.append(f"• 시작 계좌 {_money(initial)} 대비 {_percent(net / initial * 100, True)}")
            lines.append(f"• 수수료 {_money(fees)} · 펀딩 {_money(funding)} 반영")
        else:
            if kind == "CLOSED":
                raise ValueError("complete_cost_evidence_required")
            lines.append("손익·비용 정산 미확정: 확정 수익으로 집계하지 않음")
        if kind == "CLOSED":
            closed_account = closed_account_snapshot(event.get('position_after'), at)
            lines.append("💰 종료 후 순자산: " + (
                f"{_money(closed_account['equity'])} · 조회 {_time(closed_account['timestamp'])}"
                if closed_account else "미확인"))
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
