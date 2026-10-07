"""Managed normalization permissions remain bounded at the exchange boundary."""
import json

import pytest

from live.scenario_broker import BrokerNotReady
from tests.test_scenario_execution import live as execution_fixture, persist, probe_permit


@pytest.fixture
def live(tmp_path):
    return execution_fixture.__wrapped__(tmp_path)


def managed(broker, payload, stage):
    state = probe_permit(broker, payload)
    fraction = (.005, .01, .02)[stage]
    state["recovery"].update(policy_version=2, stage=stage,
                             risk_fraction=fraction, budget=10000 * fraction)
    save(broker, state)
    return state


def save(broker, state):
    broker.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (json.dumps(state),))
    broker.conn.commit()


@pytest.mark.parametrize("stage,allowed", [(0, False), (1, True), (2, True)])
def test_managed_fresh_and_pending_budget_uses_actual_stage(live, stage, allowed):
    broker, exchange = live
    payload = persist(broker, entries=[dict(id="e1", price=100, quantity=25)],
                      chase=dict(max_bps=0, max_reprices=0))
    managed(broker, payload, stage)
    if not allowed:
        with pytest.raises(BrokerNotReady, match="fresh_risk_budget_exceeded"):
            broker.execute(payload, "a1")
        assert not exchange.writes
        return
    broker.execute(payload, "a1")
    broker.reconcile()
    assert len([w for w in exchange.writes if w[0] == "place"]) == 1
    assert not any(w[0] == "cancel" for w in exchange.writes)
    assert broker.context()["scenario_risk_fraction"] == (.005, .01, .02)[stage]


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_managed_adjust_permission_requires_normal_stage(live, stage):
    broker, exchange = live
    payload = persist(broker, chase=dict(max_bps=0, max_reprices=0))
    managed(broker, payload, stage)
    broker.execute(payload, "a1")
    adjust = persist(broker, action="ADJUST", action_id="a2",
                     entries=[dict(id="extra", price=99, quantity=.1)],
                     chase=dict(max_bps=0, max_reprices=0))
    if stage < 2:
        with pytest.raises(BrokerNotReady, match="new_risk_halted"):
            broker.execute(adjust, "a2")
    else:
        broker.execute(adjust, "a2")
        broker.reconcile()
        assert len([w for w in exchange.writes if w[0] == "place"]) == 2
        assert not any(w[0] == "cancel" for w in exchange.writes)


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_managed_chase_only_normal_permission(live, stage):
    broker, exchange = live
    payload = persist(broker, chase=dict(max_bps=20 if stage == 2 else 0,
                                        max_reprices=1 if stage == 2 else 0))
    managed(broker, payload, stage)
    broker.execute(payload, "a1")
    if stage < 2:
        with pytest.raises(BrokerNotReady, match="recovery_probe_chase_forbidden"):
            broker.chase("a1", "e1", 100.1)
    else:
        broker.chase("a1", "e1", 100.1)
        assert len([w for w in exchange.writes if w[0] == "place"]) == 2


@pytest.mark.parametrize("damage", ["foreign", "side", "equity", "budget", "daily", "raw_fraction"])
def test_normal_fraction_does_not_replace_permission_identity(live, damage):
    broker, exchange = live
    payload = persist(broker)
    state = managed(broker, payload, 2)
    if damage == "foreign":
        state["recovery"]["scenario_id"] = "foreign"
    elif damage == "side":
        state["recovery"]["side"] = "SHORT"
    elif damage == "equity":
        state["recovery"]["initial_equity"] = 20000
    elif damage == "budget":
        state["recovery"]["budget"] = 201
    elif damage == "daily":
        state["breaker"]["reasons"].append("daily_loss")
    else:
        state["recovery"]["policy_version"] = 1
    save(broker, state)
    with pytest.raises(BrokerNotReady, match="new_risk_halted"):
        broker.execute(payload, "a1")
    assert not exchange.writes


@pytest.mark.parametrize("stage", [1, 2])
def test_managed_cost_overrun_cancels_pending_but_keeps_sl(live, stage):
    broker, exchange = live
    payload = persist(broker, chase=dict(max_bps=0, max_reprices=0))
    managed(broker, payload, stage)
    broker.execute(payload, "a1")
    exchange.fill(next(iter(exchange.orders)), .05)
    exchange.fills[-1]["execFee"] = "210"
    result = broker.reconcile()
    assert any(w[0] == "cancel" for w in exchange.writes)
    assert result["protection_confirmed"] is True
    assert exchange.orders["native"]["orderStatus"] == "Untriggered"


@pytest.mark.parametrize("stage", [1, 2])
def test_managed_lost_ack_is_journaled_without_duplicate_post(live, stage):
    broker, exchange = live
    payload = persist(broker, chase=dict(max_bps=0, max_reprices=0))
    managed(broker, payload, stage)
    exchange.lose_ack = True
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, "a1")
    broker.execute(payload, "a1")
    assert len([w for w in exchange.writes if w[0] == "place"]) == 1
    kinds = {r[0] for r in broker.conn.execute("SELECT kind FROM llm_scenario_recovery_events")}
    assert {"EXCHANGE_REQUEST", "EXCHANGE_ERROR"} <= kinds


def test_normal_conditional_original_deadline_survives_active_extension(live):
    broker, exchange = live
    payload = persist(broker, entries=[dict(id="e1", price=101, trigger_price=100.5, quantity=.1)],
                      chase=dict(max_bps=0, max_reprices=0))
    state = managed(broker, payload, 2)
    broker.execute(payload, "a1")
    state["active"]["expires_at"] = 1800000900
    save(broker, state)
    broker.clock = lambda: 1800000301
    broker.reconcile()
    assert any(w[0] == "cancel" for w in exchange.writes)


def test_normal_chase_stale_revision_is_rejected(live):
    broker, exchange = live
    payload = persist(broker)
    managed(broker, payload, 2)
    broker.execute(payload, "a1")
    persist(broker, action="WAIT", action_id="a2", entries=[])
    with pytest.raises(BrokerNotReady, match="chase_stale_revision"):
        broker.chase("a1", "e1", 100.1)
    assert not any(w[0] == "cancel" for w in exchange.writes)


@pytest.mark.parametrize("stage", [1, 2])
def test_managed_required_request_journal_failure_blocks_post(live, monkeypatch, stage):
    from live import scenario_recovery
    broker, exchange = live
    payload = persist(broker, chase=dict(max_bps=0, max_reprices=0))
    managed(broker, payload, stage)
    def unavailable(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(scenario_recovery, "journal", unavailable)
    with pytest.raises(BrokerNotReady, match="recovery_entry_audit_unavailable"):
        broker.execute(payload, "a1")
    assert not exchange.writes
