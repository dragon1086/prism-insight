"""Expired credentials fail closed once per session, without broker payloads."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from live import broker_recovery, demo, healthcheck, runner, swing, tracking
from live.broker_auth import auth_failure


@pytest.fixture
def conn():
    connection = tracking.get_connection(":memory:")
    tracking.ensure_schema(connection)
    yield connection
    connection.close()


def adapter(kind, conn, session):
    if kind == "swing":
        return swing.ExchangeBackend(conn, session)
    obj = demo.DemoAdapter.__new__(demo.DemoAdapter)
    obj.conn, obj.sess, obj.mode = conn, session, "demo"
    return obj


@pytest.mark.parametrize("kind", ["demo", "swing"])
@pytest.mark.parametrize("exception", [False, True])
def test_expired_latches_one_call_no_payload_and_fresh_instance_retries(conn, monkeypatch, kind, exception):
    calls = []
    def expired(**kwargs):
        calls.append(kwargs)
        if exception:
            error = type("InvalidRequestError", (Exception,), {
                "__module__": "pybit.exceptions", "status_code": 33004,
            })
            raise error("request api_key=SECRET_VALUE")
        return {"retCode": 33004, "retMsg": "SECRET_VALUE"}
    session = SimpleNamespace(get_account_info=expired, get_wallet_balance=expired,
                              get_positions=expired)
    monkeypatch.setattr(demo.time, "sleep", lambda *a: pytest.fail("no auth retry"))
    obj = adapter(kind, conn, session)
    for name in ("get_account_info", "get_wallet_balance", "get_positions"):
        assert obj._call(name) is None
    assert len(calls) == 1
    messages = [row[0] for row in conn.execute("SELECT message FROM btc_events")]
    assert len(messages) == 1
    assert "api_key_expired" in messages[0]
    assert "SECRET_VALUE" not in messages[0]
    assert adapter(kind, conn, session)._call("get_account_info") is None
    assert len(calls) == 2


@pytest.mark.parametrize("value", [None, {}, {"retCode": 10006}, RuntimeError("33004 expired")])
def test_no_auth_inference_from_text_or_unknown_codes(value):
    assert auth_failure(value) is None


@pytest.mark.parametrize("kind", ["demo", "swing"])
def test_unknown_failure_retains_retry(conn, monkeypatch, kind):
    calls = []
    def flaky(**kw):
        calls.append(kw)
        return {"retCode": 10006}
    monkeypatch.setattr(demo.time, "sleep", lambda *a: None)
    obj = adapter(kind, conn, SimpleNamespace(get_positions=flaky))
    assert obj._call("get_positions") is None
    assert len(calls) == 2
    assert not getattr(obj, "_auth_failure", None)


def test_real_main_sync_remains_unknown_and_runner_attempts_swing(conn, monkeypatch):
    calls = []
    def expired(**kwargs):
        calls.append("request")
        return {"retCode": 33004, "retMsg": "SECRET_VALUE"}
    obj = adapter("demo", conn, SimpleNamespace(get_account_info=expired,
                  get_wallet_balance=expired, get_positions=expired))
    monkeypatch.setattr(demo, "DemoAdapter", lambda *a, **kw: obj)
    monkeypatch.setattr(broker_recovery, "reconcile_swing", lambda *a: calls.append("swing"))
    errors = runner._broker_recovery(conn, "demo")
    assert errors == ["demo broker recovery: api_key_expired"]
    assert calls == ["request", "swing"]
    with pytest.raises(RuntimeError, match="position snapshot unavailable"):
        obj._sync_state(str(pd.Timestamp.now(tz="UTC")))
    assert "SECRET_VALUE" not in str([tuple(r) for r in conn.execute("SELECT * FROM btc_events")])


def test_swing_translates_latched_failure(conn, monkeypatch):
    backend = SimpleNamespace(name="exchange", _auth_failure="api_key_expired")
    monkeypatch.setattr(tracking, "load_open_positions", lambda *a: [object()])
    monkeypatch.setattr(broker_recovery, "_reconcile_swing_backend",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("SECRET_VALUE")))
    with pytest.raises(broker_recovery.RecoveryPending, match="^api_key_expired$"):
        broker_recovery.reconcile_swing(conn, "demo", pd.Timestamp.now(tz="UTC"), backend)


def test_health_expiry_action(conn):
    now = datetime.now(timezone.utc)
    for _ in range(6):
        tracking.log_event(conn, "broker_recovery", "demo broker recovery: api_key_expired",
                           level="error", mode="demo", ts=now.isoformat())
    issue = healthcheck._check_error_burst(conn, "demo", now)
    assert "재발급" in issue["msg"]
    assert "보호주문" in issue["msg"]


def test_actual_pybit_exception_is_classified_and_redacted(conn):
    exceptions = pytest.importorskip("pybit.exceptions")
    exc = exceptions.InvalidRequestError(request="api_key=SECRET_VALUE", message="SECRET_VALUE",
                                         status_code=33004, time="00:00:00", resp_headers={})
    def expired(**kwargs):
        raise exc
    obj = adapter("demo", conn, SimpleNamespace(get_positions=expired))
    assert obj._call("get_positions") is None
    assert obj._auth_failure == "api_key_expired"
    assert "SECRET_VALUE" not in str([tuple(r) for r in conn.execute("SELECT * FROM btc_events")])


def test_health_expiry_history_does_not_claim_current_failure_after_recovery(conn):
    now = datetime.now(timezone.utc)
    for _ in range(6):
        tracking.log_event(conn, "broker_recovery", "demo broker recovery: api_key_expired",
                           level="error", mode="demo", ts=(now-timedelta(minutes=5)).isoformat())
    tracking.log_event(conn, "heartbeat", "tick ok: protection=1x10m, strategy=0x30m",
                       mode="demo", ts=(now-timedelta(minutes=1)).isoformat())
    issue = healthcheck._check_error_burst(conn, "demo", now)
    assert "오류 이후 정규 실행 완료 확인" in issue["msg"]
    assert "복구 완료 미확인 시" in issue["msg"]
    assert "키가 만료되었습니다" not in issue["msg"]
