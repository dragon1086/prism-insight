import sqlite3
from types import SimpleNamespace

import pytest
from live.scenario_control import (begin_transition, activate, legacy_entries_allowed,
                                    legacy_management_allowed, read_control)
from live import tracking


def test_no_policy_preserves_legacy_and_transition_blocks_new(tmp_path):
    c=sqlite3.connect(tmp_path/'root.db')
    assert legacy_entries_allowed(c) and legacy_management_allowed(c)
    begin_transition(c,'123',clock=lambda:1000)
    assert not legacy_entries_allowed(c) and legacy_management_allowed(c)
    c.close()


def test_active_requires_fresh_flat_proof_and_disables_legacy_management(tmp_path):
    c=sqlite3.connect(tmp_path/'root.db')
    begin_transition(c,'123',clock=lambda:1000)
    proof=dict(captured_at=1000,main_uid='123',main_flat=True,swing_flat=True,
               all_orders_terminal=True,legacy_clear=True,broker_ready=True)
    with pytest.raises(ValueError):
        activate(c,proof=lambda:{**proof,'main_flat':False},clock=lambda:1001)
    with pytest.raises(ValueError):
        activate(c,proof=lambda:proof,clock=lambda:1011)
    activate(c,proof=lambda:proof,clock=lambda:1001)
    assert read_control(c)['state']=='active'
    assert not legacy_management_allowed(c) and not legacy_entries_allowed(c)
    c.close()


def test_corrupt_policy_fences_both_paths(tmp_path):
    c=sqlite3.connect(tmp_path/'root.db')
    begin_transition(c,'123')
    c.execute("UPDATE llm_scenario_control SET body='bad'");c.commit()
    assert not legacy_entries_allowed(c) and not legacy_management_allowed(c)
    c.close()


def test_both_legacy_submit_boundaries_block_before_broker_io(tmp_path):
    from live.demo import DemoAdapter
    from live.swing import ExchangeBackend
    c=tracking.get_connection(tmp_path/'root.db');tracking.ensure_schema(c)
    begin_transition(c,'123')
    demo=object.__new__(DemoAdapter);demo.conn=c
    assert demo._place_limit_postonly('long',1,100,stop_price=90) is None
    swing=object.__new__(ExchangeBackend);swing.conn=c
    assert swing.open('long',1,90,100) is None
    c.close()
