import json
import sqlite3

import pytest

from live.scenario_decision_notice import build_decision, enqueue_decision, render_decision
from live.scenario_outbox import flush, _destination


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "notice.db")
    connection.execute("CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY, context TEXT, proposal TEXT, outcome TEXT)")
    yield connection
    connection.close()


def record(conn, *, slot=10, outcome=None, active=False, rationale="1시간 저항이 가까워 보상이 불리합니다.", action="WAIT"):
    context = dict(input_id=f"input-{slot}", now=slot * 300 + 5, scenario_id="active" if active else None)
    proposal = dict(action=action, side=None, rationale=rationale,
                    review={"conditions": [{"source": "MARK_PRICE", "operator": "ge", "price": 85000}]})
    outcome = outcome or {"status": "wait"}
    conn.execute("INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)", (slot, json.dumps(context), json.dumps(proposal), json.dumps(outcome)))
    conn.commit()
    return context, outcome


def test_valid_flat_wait_keeps_why_without_direction_or_unregistered_conditions(conn):
    ctx, result = record(conn)
    body = render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))
    assert "신규 진입 관망" in body and "저항이 가까워" in body
    assert "롱" not in body and "숏" not in body and "85,000" not in body
    assert "다음 5분" in body and len(body) < 350


def test_active_wait_is_plan_maintenance(conn):
    ctx, result = record(conn, active=True)
    assert "기존 계획 유지" in render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))


@pytest.mark.parametrize("outcome", [{"status": "blocked", "reason": "llm_output_contract_failed"}, {"status": "fenced"}, {"status": "stale_proposal"}, {"status": "intent_pending"}])
def test_failed_or_early_result_never_reuses_saved_rationale(conn, outcome):
    ctx, _ = record(conn, rationale="과거 근거 누출")
    body = render_decision(build_decision(conn, outcome, slot=10, captured=ctx, now=3020))
    assert "과거 근거" not in body


@pytest.mark.parametrize("change", [dict(input_id="another"), dict(now=2705)])
def test_exact_input_correlation_required(conn, change):
    ctx, result = record(conn, rationale="노출금지")
    ctx.update(change)
    assert "노출금지" not in render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))


@pytest.mark.parametrize("status", ["duplicate_slot", "lock_busy", "execution_disabled", "protection_checked"])
def test_nondecision_status_skipped(conn, status):
    assert build_decision(conn, {"status": status}, slot=10, now=3020) is None


@pytest.mark.parametrize("action", ["OPEN", "ADJUST", "EXIT"])
def test_accepted_action_never_says_filled(conn, action):
    result = dict(status="intent_pending", reason="awaiting_exact_evidence")
    ctx, result = record(conn, action=action, outcome=result)
    body = render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))
    assert "체결 미확정" in body and "체결 완료" not in body


def test_registered_review_receipt_only(conn):
    from live.scenario_review_memory import apply_review
    active = {"scenario_id": "test"}
    review = {"conditions": [dict(source="MARK_PRICE", operator="ge", price=85000)], "acknowledgements": []}
    receipt = apply_review(active, review, "action-test", now=3005)
    receipt["conditions"].append(dict(source="MARK_PRICE", operator="le", price=81000, status="NOT_ADDED_CAPACITY"))
    result = dict(status="wait", review_update_receipt=receipt)
    ctx, result = record(conn, outcome=result)
    body = render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))
    assert "85,000.00 이상" in body and "81,000" not in body and "진입 약속 아님" in body


@pytest.mark.parametrize("unsafe", ["https://example.com/token", "/root/private/key", "api_key=secret", '{"raw":"payload"}', "input_id=abcdefgh", "a" * 40])
def test_unsafe_rationale_is_not_published(conn, unsafe):
    ctx, result = record(conn, rationale=unsafe)
    body = render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))
    assert unsafe not in body and "요약을 확인하지" in body


def test_causal_sentence_not_generic_opening(conn):
    ctx, result = record(conn, rationale="회복 단계0은 관찰 유지. 상승 구조입니다. 다만 1h MA35 저항이 가까워 비용 반영 보상이 불리합니다. 재평가합니다.")
    body = render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))
    assert "비용 반영 보상이 불리" in body and "1시간 35선" in body


def test_harmless_timeframe_slash_preserves_rationale(conn):
    ctx, result = record(conn, rationale="30분/1시간 지지가 부족합니다.")
    assert "30분/1시간 지지가 부족" in render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))


def test_open_keeps_entry_reason_not_just_caution(conn):
    ctx, result = record(conn, action="OPEN", outcome={"status": "intent_pending", "reason": "awaiting_exact_evidence"},
                         rationale="추세 돌파로 롱 진입을 검토합니다. 다만 추격 위험은 제한합니다.")
    body = render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))
    assert "추세 돌파로 롱 진입을 검토" in body


def test_slot_dedup_next_slot_delivery_and_unknown_not_retried(conn):
    ctx, result = record(conn)
    enqueue_decision(conn, result, slot=10, captured=ctx, clock=lambda: 3020)
    enqueue_decision(conn, result, slot=10, captured=ctx, clock=lambda: 3021)
    assert flush(conn, sender=lambda *_: None, clock=lambda: 3022) == dict(sent=0, unknown=1)
    assert flush(conn, sender=lambda *_: pytest.fail("retry"), clock=lambda: 3023) == dict(sent=0, unknown=0)
    ctx2, result2 = record(conn, slot=11)
    enqueue_decision(conn, result2, slot=11, captured=ctx2, clock=lambda: 3320)
    assert flush(conn, sender=lambda *_: 123, clock=lambda: 3321) == dict(sent=1, unknown=0)
    assert conn.execute("SELECT count(*) FROM llm_scenario_outbox").fetchone()[0] == 2


def test_expired_queue_never_floods_after_config_recovery(conn):
    ctx, result = record(conn)
    enqueue_decision(conn, result, slot=10, captured=ctx, clock=lambda: 3020)
    assert flush(conn, sender=lambda *_: pytest.fail("stale"), clock=lambda: 3300) == dict(sent=0, unknown=0)
    assert conn.execute("SELECT status FROM llm_scenario_outbox").fetchone()[0] == "EXPIRED"


def test_missing_private_destination_never_public_fallback(conn, monkeypatch):
    for key in ("OAUTH_ALERT_BOT_TOKEN", "OAUTH_ALERT_CHAT_ID", "BTC_OPS_CHANNEL_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "public-token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "public-room")
    assert _destination("DECISION") == (None, None)
    monkeypatch.setenv("OAUTH_ALERT_BOT_TOKEN", "ops-token")
    monkeypatch.setenv("OAUTH_ALERT_CHAT_ID", "private-room")
    assert _destination("DECISION") == ("ops-token", "private-room")
    monkeypatch.setenv("OAUTH_ALERT_CHAT_ID", "public-room")
    assert _destination("DECISION") == (None, None)


def test_notice_db_failure_does_not_mutate_result(conn):
    result = {"status": "wait"}
    conn.close()
    enqueue_decision(conn, result, slot=10, captured={"input_id": "x"}, clock=lambda: 3020)
    assert result == {"status": "wait"}


def test_stale_completed_run_not_published(conn):
    assert build_decision(conn, {"status": "wait"}, slot=10, now=3301) is None


@pytest.mark.parametrize("protect,hook_failure", [(False, False), (True, False), (False, True)])
def test_main_only_decision_loop_captures_this_invocation(tmp_path, monkeypatch, capsys, protect, hook_failure):
    import sys
    from live import scenario_runner, scenario_control, scenario_broker, scenario_decision_notice
    path = tmp_path / "runner.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr(sys, "argv", ["scenario_runner", "--protect-only" if protect else "--execute", "--root-db", str(path)])
    monkeypatch.setattr(scenario_control, "read_control", lambda conn: dict(main_uid="main", state="active"))
    monkeypatch.setattr(scenario_control, "existing_account_bindings", lambda conn: ("main", "swing"))
    monkeypatch.setattr(scenario_broker, "ScenarioDemoBroker", lambda *a, **k: object())
    calls = []
    monkeypatch.setattr(scenario_runner, "propose", lambda *a: calls.append("model") or {"action": "WAIT"})
    def run(conn, broker, **kwargs):
        if protect:
            return {"status": "protection_checked"}
        kwargs["proposal"]({}, {"input_id": "current", "now": 3005}, {})
        return {"status": "wait"}
    monkeypatch.setattr(scenario_runner, "run_once", run)
    monkeypatch.setattr(scenario_runner, "_record_health_best_effort", lambda *a, **k: None)
    monkeypatch.setattr(scenario_runner, "forward_run", lambda *a, **k: None)
    monkeypatch.setattr(scenario_runner, "deliver_notices", lambda *a: calls.append("delivery"))
    def hook(conn, result, **kwargs):
        calls.append(kwargs["captured"])
        if hook_failure:
            raise RuntimeError("optional helper failure")
    monkeypatch.setattr(scenario_decision_notice, "enqueue_decision", hook)
    assert scenario_runner.main() == 0
    capsys.readouterr()
    assert calls == (["delivery"] if protect else ["model", {"input_id": "current", "now": 3005}, "delivery"])


def test_mismatched_wait_action_never_announces_entry(conn):
    ctx, result = record(conn, action="OPEN", rationale="노출금지")
    body = render_decision(build_decision(conn, result, slot=10, captured=ctx, now=3020))
    assert "진입 판단" not in body and "노출금지" not in body


def test_expired_decisions_do_not_starve_trade_queue(conn):
    from live.scenario_outbox import enqueue
    for slot in range(1, 15):
        enqueue_decision(conn, {"status": "fenced"}, slot=slot, clock=lambda slot=slot: slot * 300 + 5)
    enqueue(conn, "trade", {"kind": "PENDING", "timestamp": 5000})
    conn.commit()
    seen = []
    assert flush(conn, sender=lambda body, kind: seen.append(kind) or 123, limit=1, clock=lambda: 5000) == dict(sent=1, unknown=0)
    assert seen == ["PENDING"]
