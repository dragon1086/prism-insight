from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from live import shared_entry_coordinator as coordinator, shared_entry_policy as policy, tracking
from live.entry_reservations import EntryReservationStore
from core.portfolio_risk import ProposedEntry
from live.demo import DemoAdapter
from live.swing import ExchangeBackend
from live.entry_reservations import LockBusy
from .test_shared_entry_coordinator import Session


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for key in tuple(__import__("os").environ):
        if key.startswith(coordinator.PREFIX):
            monkeypatch.delenv(key)
    conn = tracking.get_connection(tmp_path / "policy.sqlite")
    tracking.ensure_schema(conn)
    sessions = {"main": Session("101"), "swing": Session("202")}
    for session in sessions.values():
        session.endpoint = "https://api-demo.bybit.com"
        for method in ("get_api_key_information", "get_wallet_balance", "get_positions", "get_open_orders", "get_tickers"):
            original = getattr(session, method)
            def checked(*args, _original=original, **kwargs):
                assert not conn.in_transaction
                return _original(*args, **kwargs)
            monkeypatch.setattr(session, method, checked)
    yield conn, sessions
    conn.close()


def enable(setup, **kwargs):
    conn, sessions = setup
    return policy.manage(conn, "enable", heat=.065, slippage=.001, demo=True, sessions=sessions, **kwargs)


def test_enable_idempotent_private_and_pause(setup):
    conn, _ = setup
    assert coordinator.configuration(conn) is None
    first = enable(setup)
    assert first["state"] == "active"
    assert "uid" not in json.dumps(first) and "101" not in json.dumps(first)
    assert enable(setup) == first
    assert coordinator.configuration(conn) == coordinator.Config(.065, .001, "101", "202")
    paused = policy.manage(conn, "pause")
    assert paused["state"] == "paused"
    assert paused["policy_id"] == first["policy_id"]
    with pytest.raises(ValueError, match="policy_paused"):
        coordinator.configuration(conn)
    assert policy.manage(conn, "probe", heat=.065, slippage=.001, demo=True, sessions=setup[1])["state"] == "ready"
    assert policy.policy_status(conn)["state"] == "paused"
    assert enable(setup)["state"] == "active"


def test_control_audit_is_private_and_same_enable_is_not_duplicated(setup):
    conn, _ = setup
    first = enable(setup)
    enable(setup)
    rows = conn.execute("SELECT message FROM btc_events WHERE kind='shared_policy_control'").fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0][0])
    assert data == {'action': 'enable', 'state': 'active', 'policy_id': first['policy_id'],
                    'heat': .065, 'slippage': .001}


def test_control_audit_failure_rolls_back_policy(setup, monkeypatch):
    conn, _ = setup
    def fail(*args, **kwargs):
        raise RuntimeError('audit_storage_failure')
    monkeypatch.setattr(tracking, 'log_event', fail)
    with pytest.raises(RuntimeError, match='audit_storage_failure'):
        enable(setup)
    assert policy.policy_status(conn)['state'] == 'unconfigured'


def test_explicit_policy_limit_update_rechecks_flat_and_keeps_binding(setup):
    conn, sessions = setup
    old = enable(setup)
    updated = policy.manage(conn, "enable", heat=.05, slippage=.002, demo=True, sessions=sessions)
    assert updated["heat"] == .05 and updated["slippage"] == .002
    assert updated["policy_id"] != old["policy_id"]
    assert coordinator.configuration(conn).main_uid == "101"


def test_probe_readonly_no_schema_or_record_write(setup):
    conn, sessions = setup
    before = list(conn.iterdump())
    ro = sqlite3.connect(coordinator.database_path(conn).as_uri() + "?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    try:
        assert policy.manage(ro, "probe", heat=.065, slippage=.001, demo=True, sessions=sessions)["state"] == "ready"
    finally:
        ro.close()
    assert before == list(conn.iterdump())


@pytest.mark.parametrize("value", [True, False, None, "0.1", float("nan"), float("inf"), 0, 1, -.1])
def test_invalid_fraction_blocks_before_broker(setup, value):
    conn, _ = setup
    with pytest.raises(ValueError, match="invalid_policy_fraction"):
        policy.manage(conn, "enable", heat=value, slippage=.001, demo=True, sessions={})
    assert policy.policy_status(conn)["state"] == "unconfigured"


@pytest.mark.parametrize("endpoint", [None, "https://api.bybit.com", "https://api-testnet.bybit.com", "https://api-demo.bybit.com.evil"])
def test_both_endpoints_checked_before_any_get(setup, monkeypatch, endpoint):
    conn, sessions = setup
    sessions["swing"].endpoint = endpoint
    monkeypatch.setattr(sessions["main"], "get_api_key_information", lambda: pytest.fail("GET before endpoint check"))
    with pytest.raises(ValueError, match="demo_endpoint"):
        enable(setup)
    assert policy.policy_status(conn)["state"] == "unconfigured"


@pytest.mark.parametrize("uid", ["101", "0", None, True, "nan"])
def test_unknown_or_shared_identity_cannot_enable(setup, uid):
    conn, sessions = setup
    sessions["swing"].uid = uid
    with pytest.raises(ValueError):
        enable(setup)
    assert policy.policy_status(conn)["state"] == "unconfigured"


@pytest.mark.parametrize("kind", ["pending", "native", "stop", "reservation", "order", "position", "equity"])
def test_nonflat_or_unknown_preflight_no_write(setup, kind):
    conn, sessions = setup
    if kind in ("pending", "native", "stop"):
        key = {"pending": "pending_order", "native": "native_entry_intent", "stop": "stop_submission_intent"}[kind]
        tracking.set_meta(conn, key, {"status": "SUBMITTED_UNKNOWN"}, "demo")
    elif kind == "reservation":
        EntryReservationStore(coordinator.database_path(conn)).reserve(intent_id="intent", order_link_id="link",
            proposed=ProposedEntry("main", "long", 1., 100., 95.), eligibility=lambda _: True)
    elif kind == "order":
        sessions["swing"].orders = [{"reduceOnly": True}]
    elif kind == "position":
        sessions["main"].positions = [{"symbol": "BTCUSDT", "positionIdx": 0, "size": "1", "side": "Buy", "avgPrice": "100"}]
    else:
        sessions["main"].get_wallet_balance = lambda **_: {"retCode": 0, "result": {"list": [{"totalEquity": "nan"}]}}
    before = list(conn.iterdump())
    with pytest.raises((ValueError, KeyError)):
        enable(setup)
    assert before == list(conn.iterdump())


def test_env_conflict_and_offline_pause(setup, monkeypatch):
    conn, _ = setup
    enable(setup)
    monkeypatch.setenv(coordinator.PREFIX + "ENABLED", "false")
    with pytest.raises(ValueError, match="source_conflict"):
        coordinator.configuration(conn)
    assert policy.policy_status(conn)["reason"] == "shared_entry_policy_source_conflict"
    with pytest.raises(ValueError, match="source_conflict"):
        enable(setup)
    policy.manage(conn, "pause")
    monkeypatch.delenv(coordinator.PREFIX + "ENABLED")
    assert policy.policy_status(conn)["state"] == "paused"


@pytest.mark.parametrize("raw", ["null", "{}", "[]", "bad-json", '{"version": 2}'])
def test_corrupt_policy_fails_closed_and_can_pause(setup, raw):
    conn, _ = setup
    conn.execute("INSERT INTO btc_meta(mode,key,value) VALUES('demo',?,?)", (policy.KEY, raw))
    conn.commit()
    with pytest.raises(ValueError, match="malformed"):
        coordinator.configuration(conn)
    assert policy.policy_status(conn)["state"] == "error"
    assert policy.manage(conn, "pause")["state"] == "paused"


def test_transaction_and_explicit_demo_gates(setup):
    conn, sessions = setup
    with pytest.raises(ValueError, match="explicit_demo"):
        policy.manage(conn, "enable", heat=.065, slippage=.001, sessions=sessions)
    conn.execute("BEGIN")
    try:
        with pytest.raises(ValueError, match="transaction_active"):
            enable(setup)
    finally:
        conn.rollback()


def test_cli_status_and_pause_do_not_make_clients(setup, monkeypatch, capsys):
    conn, _ = setup
    monkeypatch.setattr(policy, "_make_sessions", lambda: pytest.fail("offline operation made clients"))
    path = str(coordinator.database_path(conn))
    assert policy.main(["status", "--root-db", path]) == 0
    assert policy.main(["pause", "--root-db", path]) == 0
    assert "uid" not in capsys.readouterr().out


@pytest.mark.parametrize("lane", ["main", "swing"])
@pytest.mark.parametrize("state", ["active", "paused", "malformed", "conflict"])
def test_runtime_policy_controls_both_actual_entry_boundaries(setup, monkeypatch, lane, state):
    conn, sessions = setup
    enable(setup)
    monkeypatch.setattr(coordinator, "_sessions", lambda *_: sessions)
    monkeypatch.setattr("core.leadership.leadership_multipliers", lambda: (1., 1., "fixture"))
    monkeypatch.setattr("live.demo.time.sleep", lambda _: None)
    if state == "paused":
        policy.manage(conn, "pause")
    elif state == "malformed":
        conn.execute("UPDATE btc_meta SET value='null' WHERE key=?", (policy.KEY,))
        conn.commit()
    elif state == "conflict":
        monkeypatch.setenv(coordinator.PREFIX + "ENABLED", "false")
    if lane == "main":
        adapter = DemoAdapter.__new__(DemoAdapter)
        adapter.conn, adapter.sess, adapter.mode = conn, sessions[lane], "demo"
        adapter._last_execution_capture = {}
        adapter._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload={
            "bar_idx": 1, "tranche_index": 0, "side": "long", "sizing_qty": 1.,
            "limit_price": 100., "sizing_sl_price": 95.})
    else:
        adapter = ExchangeBackend(conn, sessions[lane])
        adapter.entry_context = {"decision_bar": "bar-1", "leverage": 1., "logical_capital": 10000., "entry_bar_idx": 1}
        adapter.open("long", 1., 95., 100.)
    assert len(sessions[lane].submissions) == (1 if state == "active" else 0)


def test_management_shares_execution_lock_with_other_thread(setup):
    conn, _ = setup
    path = coordinator.database_path(conn)
    def attempt():
        with sqlite3.connect(path) as other:
            with pytest.raises(LockBusy):
                policy.manage(other, "pause")
    with coordinator.mutation_lock(conn):
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(attempt).result(timeout=5)
    assert policy.policy_status(conn)["state"] == "unconfigured"


def test_cli_masks_raw_sdk_exception(setup, monkeypatch, capsys):
    conn, _ = setup
    def failure():
        raise ValueError("privateUID123456")
    monkeypatch.setattr(policy, "_make_sessions", failure)
    assert policy.main(["enable", "--root-db", str(coordinator.database_path(conn)), "--demo",
                        "--heat", ".065", "--slippage", ".001"]) == 1
    assert json.loads(capsys.readouterr().out) == {"state": "error", "reason": "policy_operation_failed"}


def test_reenable_cannot_silently_change_account_binding(setup):
    conn, sessions = setup
    first = enable(setup)
    sessions["swing"].uid = "303"
    with pytest.raises(ValueError, match="account_uid_mismatch|account_binding_changed"):
        enable(setup)
    assert policy.policy_status(conn) == first


def test_lazy_environment_conflict_checked_before_snapshot_get(setup, monkeypatch):
    conn, sessions = setup
    enable(setup)
    config = coordinator.configuration(conn)
    def lazy_sessions(*_):
        monkeypatch.setenv(coordinator.PREFIX + "ENABLED", "false")
        return sessions
    monkeypatch.setattr(coordinator, "_sessions", lazy_sessions)
    monkeypatch.setattr(sessions["main"], "get_api_key_information", lambda: pytest.fail("GET under mixed source"))
    from types import SimpleNamespace
    with pytest.raises(ValueError, match="source_conflict"):
        coordinator.snapshot(SimpleNamespace(conn=conn), "main", config, policy._EmptyReservations())


def historical(setup, status="Filled", filled=1.):
    conn, sessions = setup
    native = {"link_id": "historical", "order_id": "parent", "side": "long", "qty": 1., "price": 100.,
              "status": "ACK_UNCONFIRMED", "position_ids": [99]}
    tracking.set_meta(conn, "native_entry_intent", native, "demo")
    sessions["main"].parents = [{"orderLinkId": "historical", "orderId": "parent", "symbol": "BTCUSDT",
        "positionIdx": 0, "side": "Buy", "reduceOnly": False, "orderType": "Limit", "qty": "1", "price": "100",
        "orderStatus": status, "leavesQty": "0", "cumExecQty": str(filled)}]
    sessions["main"].executions = ([{"execId": "fill", "orderId": "parent", "orderLinkId": "historical",
        "symbol": "BTCUSDT", "side": "Buy", "execType": "Trade", "execQty": str(filled), "execPrice": "100"}] if filled else [])
    return native


@pytest.mark.parametrize("status,filled", [("Filled", 1.), ("Cancelled", 0.), ("PartiallyFilledCanceled", .5), ("Rejected", 0.)])
def test_exact_historical_terminal_native_allows_resume_without_metadata_rewrite(setup, status, filled):
    conn, sessions = setup
    enable(setup)
    native = historical(setup, status, filled)
    policy.manage(conn, "pause")
    assert policy.manage(conn, "probe", heat=.065, slippage=.001, demo=True, sessions=sessions)["state"] == "ready"
    assert enable(setup)["state"] == "active"
    assert tracking.get_meta(conn, "native_entry_intent", "demo") == native


@pytest.mark.parametrize("case", ["wrong_id", "wrong_link", "wrong_symbol", "wrong_price", "active", "rejected_filled",
                                  "leaves", "missing_history", "missing_executions", "cursor", "execution_identity"])
def test_historical_native_unconfirmed_never_enables(setup, case):
    conn, sessions = setup
    historical(setup)
    parent = sessions["main"].parents[0]
    changes = {"wrong_id": ("orderId", "other"), "wrong_link": ("orderLinkId", "other"),
               "wrong_symbol": ("symbol", "ETHUSDT"), "wrong_price": ("price", "99"),
               "active": ("orderStatus", "New"), "rejected_filled": ("orderStatus", "Rejected"), "leaves": ("leavesQty", "1")}
    if case in changes:
        key, value = changes[case]
        parent[key] = value
    elif case == "missing_history":
        sessions["main"].parents = []
    elif case == "missing_executions":
        sessions["main"].executions = []
    elif case == "execution_identity":
        sessions["main"].executions[0]["orderLinkId"] = "wrong"
    else:
        sessions["main"].get_order_history = lambda **_: {"retCode": 0, "result": {"list": [parent], "nextPageCursor": "loop"}}
    before = list(conn.iterdump())
    with pytest.raises(ValueError, match="native_terminal_unconfirmed"):
        enable(setup)
    assert list(conn.iterdump()) == before


@pytest.mark.parametrize("mode,key,value", [("demo", "stop_retirements_v1", ["unknown"]),
    ("swing", "stop_retirements_v1", ["unknown"]), ("demo", "stop_retirements_v1", {}),
    ("demo", "tp_intent", {"state": "SUBMITTED"}), ("demo", "tp_intent", {"state": "RETIRED"}),
    ("demo", "tp_intent", {"state": "PARTIAL"}), ("demo", "tp_intent", {"state": "UNKNOWN"})])
def test_pending_stop_retirement_or_tp_blocks_preflight(setup, mode, key, value):
    conn, _ = setup
    tracking.set_meta(conn, key, value, mode)
    with pytest.raises(ValueError, match="unresolved_lifecycle"):
        enable(setup)


@pytest.mark.parametrize("state", ["FULFILLED", "FLAT_TERMINAL"])
def test_terminal_tp_after_remaining_position_closed_allows_resume(setup, state):
    conn, sessions = setup
    enable(setup)
    value = {"state": state, "order_id": "old", "link_id": "tp-old", "side": "long",
             "generation": 1, "qty": .5, "price": 105., "allocations": [[99, .5]], "executions": {"tp-fill": .5}}
    tracking.set_meta(conn, "tp_intent", value, "demo")
    policy.manage(conn, "pause")
    assert policy.manage(conn, "probe", heat=.065, slippage=.001, demo=True, sessions=sessions)["state"] == "ready"
    assert enable(setup)["state"] == "active"
    assert tracking.get_meta(conn, "tp_intent", "demo") == value


def test_legacy_status_validates_without_disclosing_uid(setup, monkeypatch):
    conn, _ = setup
    for key, value in {"ENABLED": "true", "COMBINED_HEAT": ".065", "SLIPPAGE": ".001", "MAIN_UID": "101", "SWING_UID": "202"}.items():
        monkeypatch.setenv(coordinator.PREFIX + key, value)
    status = policy.policy_status(conn)
    assert status["state"] == "active" and status["source"] == "legacy_env" and status["heat"] == .065
    assert "uid" not in json.dumps(status)
    monkeypatch.setenv(coordinator.PREFIX + "COMBINED_HEAT", "secret123")
    status = policy.policy_status(conn)
    assert status["state"] == "error" and status["reason"] == "legacy_policy_invalid"
    assert "secret123" not in json.dumps(status)


def test_live_authorizer_never_logs_raw_sdk_secret(setup, monkeypatch):
    conn, sessions = setup
    enable(setup)
    monkeypatch.setattr(coordinator, "_sessions", lambda *_: sessions)
    def failure():
        raise ValueError("private UID=123456 API_SECRET=never-log")
    monkeypatch.setattr(sessions["main"], "get_api_key_information", failure)
    from types import SimpleNamespace
    allowed, reservation = coordinator.authorize(SimpleNamespace(conn=conn, mode="demo"), "main", "long", 1., 100., 95., {"decision_bar": 1})
    assert not allowed and reservation is None
    dump = "\n".join(conn.iterdump())
    assert "never-log" not in dump and "123456" not in dump
    assert "snapshot_or_configuration_failed" in dump


def test_file_only_legacy_settings_effective_and_environment_unchanged(setup, monkeypatch, tmp_path):
    import os
    conn, _ = setup
    env = tmp_path / ".env"
    env.write_text("BTC_SHARED_ENTRY_ENABLED=true\nBTC_SHARED_ENTRY_COMBINED_HEAT=.065\n"
                   "BTC_SHARED_ENTRY_SLIPPAGE=.001\nBTC_SHARED_ENTRY_MAIN_UID=101\nBTC_SHARED_ENTRY_SWING_UID=202\n")
    monkeypatch.setattr(coordinator, "ENV_PATH", env)
    before = dict(os.environ)
    status = policy.policy_status(conn)
    assert status["state"] == "active" and status["source"] == "legacy_env" and status["heat"] == .065
    assert coordinator.configuration(conn).slippage == .001
    assert dict(os.environ) == before
    monkeypatch.setenv(coordinator.PREFIX + "COMBINED_HEAT", ".04")
    assert policy.policy_status(conn)["heat"] == .04


def test_file_only_runtime_conflict_status_entry_probe_pause_and_no_env_mutation(setup, monkeypatch, tmp_path):
    import os
    from types import SimpleNamespace
    conn, sessions = setup
    enable(setup)
    env = tmp_path / ".env"
    env.write_text("BTC_SHARED_ENTRY_ENABLED=false\n")
    monkeypatch.setattr(coordinator, "ENV_PATH", env)
    before = dict(os.environ)
    assert policy.policy_status(conn)["reason"] == "shared_entry_policy_source_conflict"
    with pytest.raises(ValueError, match="source_conflict"):
        coordinator.configuration(conn)
    with pytest.raises(ValueError, match="source_conflict"):
        policy.manage(conn, "probe", heat=.065, slippage=.001, demo=True, sessions=sessions)
    monkeypatch.setattr(coordinator, "_sessions", lambda *_: pytest.fail("conflict must stop before API"))
    assert coordinator.authorize(SimpleNamespace(conn=conn, mode="demo"), "main", "long", 1., 100., 95., {"decision_bar": 1}) == (False, None)
    assert policy.manage(conn, "pause")["state"] == "error"
    assert policy._read(conn)["state"] == "paused"
    assert dict(os.environ) == before
