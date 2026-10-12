"""Maintenance-only summaries of this invocation; never infer a trade fill."""
from __future__ import annotations

import json
import math
import re
import time

from live.shared_entry_coordinator import mutation_lock


def _reason(value, *, wait=False):
    if not isinstance(value, str):
        return "판단 근거 요약을 확인하지 못했습니다."
    # Untrusted model prose is not an error log or a transport payload.
    if re.search(r"https?://|(?:^|\s)[/~][A-Za-z]|\\|[{}<>]|@|(?:token|secret|password|api.?key|input_id|scenario_id|traceback)", value, re.I):
        return "판단 근거 요약을 확인하지 못했습니다."
    value = re.sub(r"\s+", " ", value).strip()
    if re.search(r"[A-Za-z0-9_-]{24,}|\d{8,}", value):
        return "판단 근거 요약을 확인하지 못했습니다."
    sentences = re.split(r"(?<=[.!?])\s+", value)
    causal = [sentence for sentence in sentences if re.search(r"불리|부족|보류|때문|다만|저항|미충족|미확인|위험|반하|어려", sentence)]
    if wait and causal:
        value = " ".join(causal[:2])
    for label, replacement in (("15m", "15분"), ("30m", "30분"), ("1h", "1시간"),
                               ("4h", "4시간"), ("12h", "12시간"), ("1d", "일봉"),
                               ("MA10", "10선"), ("MA35", "35선")):
        value = re.sub(r"\b" + label + r"\b", replacement, value)
    return (value[:137] + "…" if len(value) > 140 else value) or "판단 근거 요약을 확인하지 못했습니다."


def render_decision(event):
    from live.scenario_notice import _time
    return (f"🧭 BTC 데모 · 5분 판단\n🕐 {_time(event['timestamp'])}\n\n"
            f"{event['summary']}\n💬 {_reason(event['reason_text'], wait=event.get('wait') is True)}\n"
            f"🔎 {event['next_check']}")


def build_decision(conn, result, *, slot, captured=None, now=None):
    now = time.time() if now is None else now
    if result.get("status") in {"execution_disabled", "lock_busy", "duplicate_slot", "protection_checked"}:
        return None
    if type(slot) is not int or not slot * 300 <= now < (slot + 1) * 300:
        return None
    status = result.get("status")
    summary = "⏸ 판단 미반영"
    reason = {"fenced": "기존 주문 또는 보호 상태 확인이 필요해 보류했습니다.",
              "stale_proposal": "판단 중 계좌나 입력 상태가 바뀌어 적용하지 않았습니다.",
              "intent_pending": "이전 주문의 처리 결과를 확인 중입니다. 체결 완료를 뜻하지 않습니다."}.get(
                  status, "이번 판단을 완료하지 못했습니다. 기존 보호 상태는 별도 확인 대상입니다.")
    if result.get("reason") == "new_risk_halted":
        reason = "위험 관리 제한으로 신규 진입을 보류했습니다."
    next_check = "다음 5분 주기에 다시 평가합니다."
    wait = False
    if captured and status in {"wait", "intent_pending"}:
        row = conn.execute("SELECT context,proposal,outcome FROM llm_scenario_decisions WHERE slot=?", (slot,)).fetchone()
        if row and all(row):
            ctx, proposal, outcome = map(json.loads, row)
            matched = (ctx.get("input_id") == captured.get("input_id")
                       and ctx.get("now") == captured.get("now")
                       and int(ctx["now"] // 300) == slot and outcome == result)
            action = proposal.get("action")
            accepted = (status == "wait" and action == "WAIT") or (
                status == "intent_pending" and result.get("reason") in {"awaiting_exact_evidence", "submission_unknown"})
            if matched and accepted and action in {"WAIT", "OPEN", "ADJUST", "EXIT"}:
                if action == "WAIT" and status == "wait":
                    summary = "⏸ 기존 계획 유지" if ctx.get("scenario_id") else "⏸ 신규 진입 관망"
                else:
                    summary = {"OPEN": "📨 진입 판단", "ADJUST": "🛠 계획 변경 판단",
                               "EXIT": "📤 청산 판단", "WAIT": "🛠 주문 취소 판단"}[action] + " · 체결 미확정"
                reason = proposal.get("rationale")
                wait = action == "WAIT" and status == "wait"
                # Only conditions actually registered by the review subsystem
                # are eligible; a raw review proposal is not a registered rule.
                receipt = result.get("review_update_receipt") or {}
                from live.scenario_review_memory import _receipt_valid
                conditions = receipt["conditions"] if _receipt_valid(receipt) else []
                checks = []
                for condition in conditions[:3]:
                    if not isinstance(condition, dict) or condition.get("status") not in {"ADDED", "ALREADY_RETAINED"}:
                        continue
                    price = condition.get("price")
                    if (condition.get("source") == "MARK_PRICE" and condition.get("operator") in {"ge", "le"}
                            and type(price) in {int, float} and math.isfinite(price) and 0 < price < 1e9):
                        checks.append(f"{price:,.2f} {'이상' if condition['operator'] == 'ge' else '이하'}")
                if checks:
                    next_check = "재검토 조건: 표시가격 " + " / ".join(checks) + " (진입 약속 아님)"
    return dict(kind="DECISION", timestamp=now, summary=summary, reason_text=reason,
                next_check=next_check, wait=wait, expires_at=(slot + 1) * 300)


def enqueue_decision(conn, result, *, slot, captured=None, clock=time.time):
    """Optional evidence delivery cannot change or interrupt trading results."""
    from live.scenario_outbox import enqueue
    try:
        event = build_decision(conn, result, slot=slot, captured=captured, now=clock())
        if event is None:
            return
        with mutation_lock(conn):
            # Multiple runners within a slot must not replace the first report.
            identity = f"decision:{slot}"
            conn.execute("SAVEPOINT decision_notice")
            try:
                from live.scenario_outbox import _schema
                _schema(conn)
                if not conn.execute("SELECT 1 FROM llm_scenario_outbox WHERE event_id=?", (identity,)).fetchone():
                    enqueue(conn, identity, event)
                conn.execute("RELEASE decision_notice")
                conn.commit()
            except Exception:
                conn.execute("ROLLBACK TO decision_notice")
                conn.execute("RELEASE decision_notice")
    except Exception:
        return None  # Diagnostic-only path; preserve the original trading outcome.
