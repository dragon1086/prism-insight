"""Persistent MAIN-demo strategy handoff. No order or scheduler side effects.

Absent policy preserves legacy operation. Transition/active/paused prohibit
legacy NEW entries; active/paused also prevent legacy code managing new positions.
Only verified flat handoff may activate. Pausing never authorizes old entries.
"""
from __future__ import annotations

import json
import math
import time

from live.shared_entry_coordinator import mutation_lock


def read_control(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_scenario_control'").fetchone():
        return None
    row=conn.execute("SELECT body FROM llm_scenario_control WHERE id=1").fetchone()
    if not row:
        raise ValueError("scenario_control_missing")
    value=json.loads(row[0])
    if (not isinstance(value,dict) or value.get("state") not in {"transition","active","paused"}
            or value.get("version") != 1 or not isinstance(value.get("main_uid"),str)
            or not value["main_uid"].isascii() or not value["main_uid"].isdigit()):
        raise ValueError("scenario_control_invalid")
    return value


def legacy_entries_allowed(conn):
    try:
        return read_control(conn) is None
    except Exception:
        return False


def legacy_management_allowed(conn):
    # Malformed policy fences all writes: never let old code touch a new position.
    try:
        control=read_control(conn)
        return control is None or control["state"] == "transition"
    except Exception:
        return False


def _write(conn,value):
    conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_control (id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL)")
    conn.execute("INSERT INTO llm_scenario_control VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                 (json.dumps(value,allow_nan=False,sort_keys=True),))
    conn.commit()


def begin_transition(conn,main_uid,*,clock=time.time):
    if not isinstance(main_uid,str) or not main_uid.isascii() or not main_uid.isdigit() or int(main_uid)<=0:
        raise ValueError("verified_main_uid_required")
    with mutation_lock(conn):
        previous=read_control(conn)
        if previous is not None:
            if previous["main_uid"]!=main_uid:
                raise ValueError("handoff_account_changed")
            return previous
        value={"version":1,"state":"transition","main_uid":main_uid,"started_at":clock()}
        _write(conn,value)
        return value


def existing_account_bindings(conn):
    """Reuse the operator's existing verified two-account binding, never key text."""
    row=conn.execute("SELECT value FROM btc_meta WHERE mode='demo' AND key='shared_entry_policy_v1'").fetchone()
    if not row:
        raise ValueError("existing_account_bindings_required")
    value=json.loads(row[0])
    main,swing=value.get("main_uid"),value.get("swing_uid")
    if (not all(isinstance(v,str) and v.isascii() and v.isdigit() and int(v)>0 for v in (main,swing))
            or main==swing):
        raise ValueError("distinct_verified_account_bindings_required")
    return main,swing


def flat_handoff_probe(conn,broker,swing_session,expected_swing_uid,*,clock=time.time):
    """No orders. Caller holds mutation_lock through probe and policy write."""
    from live.exchange_snapshot import read_complete
    from live.scenario_runtime import REQUIRED_CAPABILITIES
    if getattr(swing_session,"endpoint",None)!="https://api-demo.bybit.com":
        raise ValueError("swing_demo_endpoint_required")
    started=clock()
    main=broker.capture_account()
    identity=swing_session.get_api_key_information()
    if identity.get("retCode")!=0 or str(identity.get("result",{}).get("userID"))!=expected_swing_uid:
        raise ValueError("swing_identity_not_confirmed")
    call=lambda method,**params:getattr(swing_session,method)(**params)
    positions=read_complete(call,"get_positions",category="linear",symbol="BTCUSDT")
    orders=read_complete(call,"get_open_orders",category="linear",symbol="BTCUSDT")
    if positions is None or orders is None:
        raise ValueError("swing_snapshot_unavailable")
    rows=positions["result"]["list"]
    swing_flat=bool(rows) and all(r.get("symbol")=="BTCUSDT" and r.get("positionIdx")==0 and float(r["size"])==0 for r in rows)
    local_clear=not conn.execute("SELECT 1 FROM btc_positions WHERE mode IN ('demo','swing') AND qty>0 LIMIT 1").fetchone()
    for (raw,) in conn.execute("SELECT value FROM btc_meta WHERE mode IN ('demo','swing') AND key LIKE '%pending%'"):
        if json.loads(raw):
            local_clear=False
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='entry_reservations'").fetchone():
        local_clear=local_clear and not conn.execute("SELECT 1 FROM entry_reservations WHERE state NOT IN ('FILLED','CANCELLED_CONFIRMED') LIMIT 1").fetchone()
    if not 0<=clock()-started<=10:
        raise ValueError("handoff_probe_stale")
    return dict(captured_at=started,main_uid=broker.expected_main_uid,
                main_flat=main.get("exchange_flat") is True,swing_flat=swing_flat,
                all_orders_terminal=not main.get("open_orders") and not orders["result"]["list"],
                legacy_clear=bool(local_clear),broker_ready=REQUIRED_CAPABILITIES <= set(broker.capabilities))


def activate(conn,*,proof,clock=time.time):
    """proof is collected by a trusted host-side read-only probe under this lock."""
    with mutation_lock(conn):
        value=read_control(conn)
        if value is None or value["state"]!="transition":
            raise ValueError("transition_required")
        evidence=proof()  # Trusted callback, never LLM-supplied JSON.
        now=clock()
        captured=evidence.get("captured_at")
        if (type(captured) not in (int,float) or not math.isfinite(captured)
                or not 0<=now-captured<=10 or evidence.get("main_uid")!=value["main_uid"]
                or any(evidence.get(flag) is not True for flag in (
                    "main_flat","swing_flat","all_orders_terminal","legacy_clear","broker_ready"))):
            raise ValueError("verified_flat_handoff_required")
        value.update(state="active",activated_at=now)
        _write(conn,value)
        return value
