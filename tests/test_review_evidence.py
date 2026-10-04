"""Evidence added for the two-week review (docs/TWO_WEEK_REVIEW_ko.md): no behavior change, fail-open."""
import json
import os
import sys
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import fallbacks  # noqa: E402
from prism_core import trigger_quality as tq  # noqa: E402
from prism_core.b3_ae_worker import B3AeWorker  # noqa: E402


def _spool(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(path))
    return path


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_sell_fallback_returns_the_result_unchanged_and_flags_the_legacy_ten_pct_rule(tmp_path, monkeypatch):
    path = _spool(tmp_path, monkeypatch)
    stock = {"id": 3, "ticker": "005930", "current_price": 111.0, "buy_price": 100.0}
    result = (True, "Return over 10% achieved (current return: 11.00%)")
    assert fallbacks.note_sell_fallback("KR", stock, "parse_failed", result) is result
    assert fallbacks.note_sell_fallback("US", {"ticker": "AAPL"}, "empty_response", (False, "HOLD: trend")) == (
        False, "HOLD: trend")
    first, second = _events(path)
    assert first["event_type"] == "sell.fallback_used" and first["position_id"] == "legacy:KR:3"
    assert first["attributes"]["legacy_ten_pct_rule"] is True and first["attributes"]["trigger"] == "parse_failed"
    assert second["market"] == "US" and second["attributes"]["legacy_ten_pct_rule"] is False


def test_sell_fallback_never_raises_on_odd_inputs(tmp_path, monkeypatch):
    _spool(tmp_path, monkeypatch)
    assert fallbacks.note_sell_fallback("KR", None, "analysis_error", None) is None


def test_kis_rate_limit_event_only_for_egw00201(tmp_path, monkeypatch):
    path = _spool(tmp_path, monkeypatch)
    fallbacks.note_kis_rate_limit("https://x/uapi/domestic-stock/v1/quotations/inquire-price?a=1", 500,
                                  '{"msg_cd":"EGW00201","msg1":"초당 거래건수를 초과하였습니다."}')
    fallbacks.note_kis_rate_limit("https://x/other", 500, '{"msg_cd":"EGW00123"}')
    (event,) = _events(path)
    assert event["event_type"] == "kis.rate_limited"
    assert event["attributes"]["path"].endswith("inquire-price") and "?" not in event["attributes"]["path"]


def _frame(rows):
    return pd.DataFrame({"final_score": [s for _, s, _ in rows], "Close": [p for _, _, p in rows],
                         "stock_name": [t for t, _, _ in rows]}, index=[t for t, _, _ in rows])


def test_selection_record_replays_the_legacy_guarantee_within_the_slot_budget():
    candidates = {"weak_a": _frame([("A1", 0.9, 1000.0)]), "strong": _frame([("S1", 0.8, 50.0), ("S2", 0.7, 60.0)]),
                  "weak_b": _frame([("B1", 0.95, 7.5)])}
    weights = {"weak_a": 0.9, "strong": 1.1, "weak_b": 0.9}
    record = tq.selection_record(candidates, ["strong"], set(), {"S1", "S2"}, [("strong", "S2")], weights,
                                 "final_score", max_selections=2)
    # Legacy: weak_a then strong fill the two slots; weak_b would not have been reached.
    assert [(c["ticker"], c["reference_price"], c["weight"]) for c in record["displaced"]] == [("A1", 1000.0, 0.9)]
    assert [c["ticker"] for c in record["fill_picks"]] == ["S2"]
    assert record["excluded_triggers"] == ["weak_a", "weak_b"]
    assert tq.selection_record(candidates, list(candidates), set(), set(), [], {}, "final_score",
                               max_selections=2) is None


def test_selection_record_skips_a_legacy_pick_that_was_selected_anyway():
    candidates = {"weak": _frame([("W1", 0.9, 10.0)]), "strong": _frame([("S1", 0.8, 20.0)])}
    record = tq.selection_record(candidates, ["strong"], set(), {"W1", "S1"}, [("weak", "W1")], {"weak": 0.9},
                                 "final_score", max_selections=2)
    assert record["displaced"] == [] and record["fill_picks"][0]["trigger"] == "weak"


def test_rail_block_is_recorded_once_per_plan_session_and_scenario(monkeypatch):
    import observability.events as ev
    stored, emitted = [], []
    monkeypatch.setattr(ev, "emit_event", lambda name, **kw: emitted.append((name, kw["attributes"])))
    worker = SimpleNamespace(market="KR", _announced=set(),
                             store=SimpleNamespace(record_event=lambda *args: stored.append(args)))
    decision = {"action": "WAIT", "reason": "NO_SCENARIO_QUALIFIED", "plan_hash": "h1", "session_date": "2026-10-06",
                "reasons": {"breakout_1": "RISK_LIMIT", "pullback_1": "TOO_EARLY", "ACCELERATION": "CHASE_LIMIT"},
                "acceleration": {"active": True, "gain_pct": 9.1, "volume_pace": 1.7}}
    state = {"current_stop": 9300, "initial_stop": 9300}
    campaign = {"position_id": "legacy:KR:1"}
    for _ in range(2):
        B3AeWorker._record_rail_blocks(worker, "c1", campaign, "005930", "2026-10-06T01:00:00Z", decision, state,
                                       {"campaign_id": "c1", "slot_allocation": 0.45}, price=10950)
    assert [(a["scenario_id"], a["block"], a["rail"]) for _, a in emitted] == [
        ("ACCELERATION", "CHASE_LIMIT", "ACCELERATION"), ("breakout_1", "RISK_LIMIT", None)]
    assert all(name == "micro_split.add_blocked" and a["price"] == 10950 for name, a in emitted)
    assert [args[2] for args in stored] == ["add_plan.rail_blocked", "add_plan.rail_blocked"]
