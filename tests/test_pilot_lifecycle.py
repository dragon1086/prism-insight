from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import copy
import sqlite3

import pytest

from prism_core.pilot_lifecycle import create_pilot, evaluate_pilot
from prism_core.strategy_ledger import LedgerError, StrategyLedger


def calendar():
    # Friday entry; Monday US holiday is deliberately absent from the fixture.
    dates = ["2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10"]
    return {"market": "US", "timezone": "America/New_York", "source_kind": "test_fixture",
            "source_ref": "holiday-fixture-v1", "verification_status": "VERIFIED",
            "sessions": [{"date": d, "open_at": d + "T09:30:00-04:00", "close_at": d + "T16:00:00-04:00"} for d in dates]}


ENTRY = "2026-09-04T10:00:00-04:00"
NOW = "2026-09-08T10:05:00-04:00"


def signal_bar():
    return {"open_at": "2026-09-04T09:30:00-04:00", "close_at": ENTRY,
            "close": 100, "high": 102, "completed": True, "observed_at": ENTRY}


def pilot():
    return create_pilot(market="US", mode="SHADOW", owner="split-pilot-v1", entry_at=ENTRY,
                        entry_price=100, signal_bar=signal_bar(), calendar=calendar(), entry_eligible=True)


def evidence():
    return {"observed_at": NOW, "thesis_valid": True, "market_regime": "sideways", "pulse": "UPTREND",
            "bar": {"open_at": "2026-09-08T09:30:00-04:00", "close_at": "2026-09-08T10:00:00-04:00",
                    "close": 103, "high": 104, "completed": True, "observed_at": NOW},
            "quote": {"price": 104, "observed_at": NOW},
            "ai": {"decision": "Enter", "score": 7, "ordinary_min_score": 7, "observed_at": NOW},
            "gates": {"risk": True, "regime": True, "pulse": True, "risk_reward": True, "sector": True, "slot": True}}


def evaluate(state=None, facts=None, now=NOW, **kw):
    return evaluate_pilot(state or pilot(), facts if facts is not None else evidence(), now=now,
                          cumulative_allocation=kw.get("cumulative_allocation", ".5"),
                          remaining_allocation=kw.get("remaining_allocation", ".5"),
                          normalized_units=kw.get("normalized_units", ".005"))


def test_half_add_only_after_later_regular_confirmation():
    result = evaluate()
    assert result["state"] == "FULL_100"
    assert Decimal(result["delta_allocation"]) == Decimal(".5")
    assert pilot()["expires_at"] == "2026-09-10T20:00:00+00:00"


def test_missing_evidence_waits_and_expiry_does_not_exit_holding():
    assert evaluate(facts={})["state"] == "WAIT"
    result = evaluate(facts={}, now="2026-09-10T16:00:00-04:00")
    assert result["state"] == "ADD_EXPIRED"
    assert result["delta_allocation"] == "0"


def test_reduction_cancels_instead_of_refilling():
    result = evaluate(remaining_allocation=".25", normalized_units=".0025")
    assert result["state"] == "ADD_CANCELLED"
    assert result["delta_allocation"] == "0"


def test_ledger_open_pilot_is_atomic_and_shadow_only(tmp_path):
    ledger = StrategyLedger(tmp_path / "pilot.sqlite")
    ledger.create_book("shadow", "US", cohort="split-pilot-v1", mode="SHADOW")
    state = ledger.open_pilot("open", "shadow", "c", "TEST", 100, ENTRY,
                              owner="split-pilot-v1", signal_bar=signal_bar(), calendar=calendar(), entry_eligible=True)
    assert state["campaigns"][0]["pilot"]["state"] == "PILOT_50"
    state = ledger.advance_pilot("advance", "c", expected_revision=0, evidence=evidence(), occurred_at=NOW)
    assert state["campaigns"][0]["pilot"]["state"] == "FULL_100"
    assert len(state["campaigns"][0]["legs"]) == 2


@pytest.mark.parametrize("change", [
    ("quote", "price", 100), ("bar", "close", 102), ("ai", "score", 6),
    ("ai", "decision", "Hold"), ("ai", "ordinary_min_score", 8),
    ("bar", "completed", False), ("quote", "observed_at", "2026-09-08T10:02:59-04:00"),
    ("ai", "observed_at", "2026-09-08T10:05:01-04:00"),
    ("gates", "sector", False), ("gates", "slot", None),
    ("gates", "risk_reward", False), ("gates", "pulse", False),
])
def test_confirmation_is_strict_and_missing_or_temporary_failure_waits(change):
    facts = evidence()
    container, key, value = change
    facts[container][key] = value
    assert evaluate(facts=facts)["state"] == "WAIT"


@pytest.mark.parametrize("clock", ["2026-09-08T09:29:59-04:00", "2026-09-08T16:01:00-04:00", "2026-09-07T10:05:00-04:00"])
def test_pre_postmarket_and_holiday_cannot_add(clock):
    facts = evidence()
    facts["observed_at"] = clock
    assert evaluate(facts=facts, now=clock)["state"] == "WAIT"


def test_same_entry_session_and_premarket_bars_never_confirm():
    facts = evidence()
    facts["bar"] = signal_bar()
    assert evaluate(facts=facts)["state"] == "WAIT"
    facts = evidence()
    facts["bar"]["open_at"] = "2026-09-08T09:00:00-04:00"
    assert evaluate(facts=facts)["state"] == "WAIT"
    facts["bar"]["open_at"] = "2026-09-08T10:00:00-04:00"
    facts["bar"]["close_at"] = "2026-09-08T10:06:00-04:00"
    assert evaluate(facts=facts)["state"] == "WAIT"


def test_missing_signal_and_unknown_cancel_evidence_stay_wait():
    state = pilot()
    state["signal_bar_high"] = None
    assert evaluate(state=state)["reason"] == "MISSING_SIGNAL_BAR_PROVENANCE"
    facts = evidence()
    facts["thesis_valid"] = None
    assert evaluate(facts=facts)["state"] == "WAIT"
    facts["thesis_valid"] = False
    assert evaluate(facts=facts)["state"] == "ADD_CANCELLED"
    facts["observed_at"] = None
    assert evaluate(facts=facts)["state"] == "WAIT"


def test_unknown_regime_and_ordinary_regime_floor_cannot_use_pilot_six():
    facts = evidence()
    facts["market_regime"] = "unknown"
    assert evaluate(facts=facts)["state"] == "WAIT"
    facts["market_regime"] = "moderate_bear"
    assert evaluate(facts=facts)["state"] == "WAIT"
    facts["ai"]["score"] = 8
    assert evaluate(facts=facts)["state"] == "FULL_100"


@pytest.mark.parametrize("key", ["risk", "regime"])
def test_confirmed_risk_failure_cancels(key):
    facts = evidence()
    facts["gates"][key] = False
    assert evaluate(facts=facts)["state"] == "ADD_CANCELLED"


def test_expiry_uses_sessions_across_dst_and_kr_holidays():
    us = calendar()
    dates = ["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"]
    us["sessions"] = [{"date": d, "open_at": d + f"T09:30:00{offset}", "close_at": d + f"T16:00:00{offset}"}
                      for d, offset in zip(dates, ["-04:00", "-05:00", "-05:00", "-05:00"])]
    state = create_pilot(market="US", mode="SHADOW", owner="split-pilot-v1", entry_at="2026-10-30T10:00:00-04:00",
                         entry_price=100, signal_bar=None, calendar=us, entry_eligible=True)
    assert state["expires_at"] == "2026-11-04T21:00:00+00:00"
    kr = calendar()
    kr.update(market="KR", timezone="Asia/Seoul")
    dates = ["2026-09-23", "2026-09-28", "2026-09-29", "2026-09-30"]
    kr["sessions"] = [{"date": d, "open_at": d + "T09:00:00+09:00", "close_at": d + "T15:30:00+09:00"} for d in dates]
    state = create_pilot(market="KR", mode="SHADOW", owner="split-pilot-v1", entry_at="2026-09-23T10:00:00+09:00",
                         entry_price=100, signal_bar=None, calendar=kr, entry_eligible=True)
    assert state["expires_at"] == "2026-09-30T06:30:00+00:00"


@pytest.mark.parametrize("field,value", [("market", "KR"), ("timezone", "UTC"), ("verification_status", "UNKNOWN"), ("source_ref", "")])
def test_calendar_provenance_and_market_validation(field, value):
    manifest = calendar()
    manifest[field] = value
    with pytest.raises(ValueError):
        create_pilot(market="US", mode="SHADOW", owner="split-pilot-v1", entry_at=ENTRY,
                     entry_price=100, signal_bar=signal_bar(), calendar=manifest, entry_eligible=True)


def shadow_ledger(tmp_path):
    ledger = StrategyLedger(tmp_path / "pilot.sqlite")
    ledger.create_book("shadow", "US", cohort="split-pilot-v1", mode="SHADOW")
    return ledger


def open_pilot(ledger, event="open"):
    return ledger.open_pilot(event, "shadow", "c", "TEST", 100, ENTRY, owner="split-pilot-v1",
                             signal_bar=signal_bar(), calendar=calendar(), entry_eligible=True)


def test_durable_wait_duplicate_cas_and_add_once(tmp_path):
    ledger = shadow_ledger(tmp_path)
    opened = open_pilot(ledger)
    assert not open_pilot(ledger)["event_applied"]
    waiting = ledger.advance_pilot("wait", "c", expected_revision=0, evidence={}, occurred_at=NOW)
    assert waiting["campaigns"][0]["pilot"]["revision"] == 1
    assert waiting["campaigns"][0]["pilot"]["state"] == "WAIT"
    ledger = StrategyLedger(ledger.path)
    assert not ledger.advance_pilot("wait", "c", expected_revision=0, evidence={}, occurred_at=NOW)["event_applied"]
    with pytest.raises(LedgerError, match="revision conflict"):
        ledger.advance_pilot("stale", "c", expected_revision=0, evidence=evidence(), occurred_at=NOW)
    final = ledger.advance_pilot("add", "c", expected_revision=1, evidence=evidence(), occurred_at=NOW)
    campaign = final["campaigns"][0]
    assert [Decimal(leg["allocation"]) for leg in campaign["legs"]] == [Decimal(".5"), Decimal(".5")]
    terminal = ledger.advance_pilot("terminal", "c", expected_revision=2, evidence=evidence(), occurred_at=NOW)
    assert terminal["campaigns"] == final["campaigns"]
    assert opened["campaigns"][0]["pilot"]["revision"] == 0
    with sqlite3.connect(ledger.path) as db:
        payload = db.execute("SELECT payload FROM events WHERE id=?", ("wait:transition",)).fetchone()[0]
        assert "MISSING_OR_INVALID_EVIDENCE" in payload and "evaluated_at" in payload


@pytest.mark.parametrize("full_exit", [False, True])
def test_reduction_persists_terminal_state_and_exit_is_independent(tmp_path, full_exit):
    ledger = shadow_ledger(tmp_path)
    open_pilot(ledger)
    reduced = ledger.sell("sell", "c", 110, NOW, quantity=None if full_exit else ".0025")
    campaign = reduced["campaigns"][0]
    assert campaign["pilot"]["state"] == ("EXITED" if full_exit else "ADD_CANCELLED")
    ledger = StrategyLedger(ledger.path)
    after = ledger.advance_pilot("attempt", "c", expected_revision=1, evidence=evidence(), occurred_at=NOW)
    assert len(after["campaigns"][0]["legs"]) == 2
    assert after["campaigns"] == reduced["campaigns"]


def test_expired_state_survives_restart_without_selling(tmp_path):
    ledger = shadow_ledger(tmp_path)
    open_pilot(ledger)
    now = "2026-09-10T16:00:00-04:00"
    state = ledger.advance_pilot("expiry", "c", expected_revision=0, evidence={}, occurred_at=now)
    campaign = state["campaigns"][0]
    assert campaign["pilot"]["state"] == "ADD_EXPIRED"
    assert campaign["status"] == "OPEN" and campaign["remaining_allocation"] == "0.5"
    ledger = StrategyLedger(ledger.path)
    assert ledger.advance_pilot("again", "c", expected_revision=1, evidence=evidence(), occurred_at=now)["campaigns"] == state["campaigns"]
    assert ledger.sell("exit", "c", 110, now)["campaigns"][0]["pilot"]["state"] == "EXITED"


@pytest.mark.parametrize("mode", ["VALIDATION", "VALIDATION_ONLY"])
def test_non_shadow_open_is_rejected(tmp_path, mode):
    ledger = StrategyLedger(tmp_path / "other.sqlite")
    ledger.create_book("shadow", "US", mode=mode)
    with pytest.raises(ValueError, match="SHADOW"):
        open_pilot(ledger)
    assert ledger.snapshot("shadow")["campaigns"] == []


def test_historical_campaign_never_adopted_and_split_rejects_legacy(tmp_path):
    ledger = shadow_ledger(tmp_path)
    ledger.apply_target("legacy", "shadow", "c", "TEST", 50, 100, ENTRY)
    with pytest.raises(LedgerError, match="cannot be adopted"):
        open_pilot(ledger)
    with pytest.raises(LedgerError, match="unowned"):
        ledger.advance_pilot("advance", "c", expected_revision=0, evidence=evidence(), occurred_at=NOW)


def test_parallel_legacy_claim_and_pilot_claim_have_one_winner(tmp_path):
    ledger = shadow_ledger(tmp_path)

    def attempt(pilot_claim):
        candidate = StrategyLedger(ledger.path)
        try:
            if pilot_claim:
                open_pilot(candidate)
            else:
                candidate.apply_target("legacy", "shadow", "c", "TEST", 100, 100, ENTRY)
            return True
        except LedgerError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [False, True])) == [False, True]
    assert len(ledger.snapshot("shadow")["campaigns"][0]["legs"]) == 1


def test_concurrent_duplicate_add_writes_one_leg(tmp_path):
    ledger = shadow_ledger(tmp_path)
    open_pilot(ledger)

    def attempt(_):
        return StrategyLedger(ledger.path).advance_pilot("same-add", "c", expected_revision=0,
                                                        evidence=evidence(), occurred_at=NOW)["event_applied"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    assert len(ledger.snapshot("shadow")["campaigns"][0]["legs"]) == 2
    with pytest.raises(LedgerError, match="split-owned"):
        ledger.apply_target("legacy-add", "shadow", "c", "TEST", 100, 110, NOW)


def test_atomic_failure_between_target_and_lifecycle_rolls_back(tmp_path, monkeypatch):
    ledger = shadow_ledger(tmp_path)
    open_pilot(ledger)
    before = ledger.snapshot("shadow")
    save = ledger._save

    def fail(db, table, identifier, value):
        if table == "campaigns" and value.get("pilot", {}).get("state") == "FULL_100":
            raise RuntimeError("simulated failure")
        save(db, table, identifier, value)

    monkeypatch.setattr(ledger, "_save", fail)
    with pytest.raises(RuntimeError):
        ledger.advance_pilot("add", "c", expected_revision=0, evidence=evidence(), occurred_at=NOW)
    assert ledger.snapshot("shadow") == before
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE id IN (?,?)", ("add", "add:target")).fetchone()[0] == 0


def test_policy_does_not_mutate_inputs_or_use_marked_exposure():
    original = pilot()
    facts = evidence()
    saved = copy.deepcopy((original, facts))
    original["marked_exposure"] = "1.8"
    assert evaluate(state=original, facts=facts)["delta_allocation"] == "0.5"
    original.pop("marked_exposure")
    assert (original, facts) == saved


def test_early_close_is_expiry_and_invalid_calendar_order_fails():
    manifest = calendar()
    manifest["sessions"][-1]["close_at"] = "2026-09-10T13:00:00-04:00"
    state = create_pilot(market="US", mode="SHADOW", owner="split-pilot-v1", entry_at=ENTRY,
                         entry_price=100, signal_bar=signal_bar(), calendar=manifest, entry_eligible=True)
    assert state["expires_at"] == "2026-09-10T17:00:00+00:00"
    manifest["sessions"] = list(reversed(manifest["sessions"]))
    with pytest.raises(ValueError):
        create_pilot(market="US", mode="SHADOW", owner="split-pilot-v1", entry_at=ENTRY,
                     entry_price=100, signal_bar=signal_bar(), calendar=manifest, entry_eligible=True)


def test_transition_payload_conflict_and_wrong_owner_are_rejected(tmp_path):
    ledger = shadow_ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.open_pilot("bad", "shadow", "c", "TEST", 100, ENTRY, owner="legacy-pyramid",
                          signal_bar=signal_bar(), calendar=calendar(), entry_eligible=True)
    open_pilot(ledger)
    ledger.advance_pilot("wait", "c", expected_revision=0, evidence={}, occurred_at=NOW)
    with pytest.raises(LedgerError, match="payload conflict"):
        ledger.advance_pilot("wait", "c", expected_revision=0, evidence=evidence(), occurred_at=NOW)


@pytest.mark.parametrize("cohort", ["baseline-v1", "adx-h2-v1", "stop-5m-v1"])
def test_experimental_split_cannot_enter_attribution_arm(tmp_path, cohort):
    ledger = StrategyLedger(tmp_path / "arm.sqlite")
    ledger.create_book("shadow", "US", cohort=cohort, mode="SHADOW")
    with pytest.raises(LedgerError, match="dedicated"):
        open_pilot(ledger)
    assert ledger.snapshot("shadow")["campaigns"] == []


@pytest.mark.parametrize("field,value", [("high", 1), ("low", 105), ("open", 105), ("close", -1)])
def test_malformed_ohlc_never_confirms(field, value):
    facts = evidence()
    facts["bar"][field] = value
    assert evaluate(facts=facts)["state"] == "WAIT"
    malformed_signal = signal_bar()
    malformed_signal["high"] = 99
    state = create_pilot(market="US", mode="SHADOW", owner="split-pilot-v1", entry_at=ENTRY,
                         entry_price=100, signal_bar=malformed_signal, calendar=calendar(), entry_eligible=True)
    assert state["signal_bar_high"] is None
    assert evaluate(state=state)["state"] == "WAIT"


def test_unknown_pulse_and_out_of_range_score_do_not_confirm():
    facts = evidence()
    facts.update(market_regime="moderate_bull", pulse="nonsense")
    assert evaluate(facts=facts)["state"] == "WAIT"
    facts["pulse"] = "UPTREND"
    facts["ai"]["score"] = 11
    assert evaluate(facts=facts)["state"] == "WAIT"
