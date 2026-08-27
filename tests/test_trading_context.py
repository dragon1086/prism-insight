from __future__ import annotations

import json

from cores.regime_policy import stamp_scenario_market_regime
from observability.trading_context import (
    build_trading_context,
    emit_candidate_outcome,
    emit_trading_context,
    latest_regime_snapshot,
)


def test_regime_stamp_preserves_the_full_deterministic_market_snapshot() -> None:
    scenario = stamp_scenario_market_regime(
        {"market_condition": "bullish prose"},
        {
            "market_regime": "strong_bull",
            "primary_trend_regime": "strong_bull",
            "effective_entry_regime": "moderate_bull",
            "swing_state": "consolidation",
            "regime_confidence": 0.8,
            "index_summary": {"sp500_4w_change_pct": -0.5, "vix_current": 18.2},
        },
    )

    snapshot = scenario["_deterministic_market_context"]
    assert snapshot["market_regime"] == "strong_bull"
    assert snapshot["effective_entry_regime"] == "moderate_bull"
    assert snapshot["swing_state"] == "consolidation"
    assert snapshot["index_summary"]["vix_current"] == 18.2
    assert scenario["llm_market_condition"] == "bullish prose"


def test_latest_regime_snapshot_reads_only_the_requested_market(tmp_path) -> None:
    path = tmp_path / "regime.jsonl"
    path.write_text(
        "\n".join(
            (
                json.dumps({"ts": "1", "market": "KR", "regime": "sideways"}),
                json.dumps({"ts": "2", "market": "US", "regime": "strong_bull"}),
                json.dumps({"ts": "3", "market": "KR", "regime": "moderate_bull"}),
            )
        ),
        encoding="utf-8",
    )

    assert latest_regime_snapshot("KR", path=path)["regime"] == "moderate_bull"
    assert latest_regime_snapshot("US", path=path)["regime"] == "strong_bull"


def test_context_event_links_decision_entry_and_exit(monkeypatch, tmp_path) -> None:
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    scenario = {
        "_decision_id": "report:US_AAA_20260827.pdf",
        "_position_id": "legacy:US:7",
        "_deterministic_market_context": {
            "market_regime": "strong_bull",
            "swing_state": "consolidation",
            "regime_confidence": 0.9,
        },
        "_deterministic_trend_facts": "price above MA50; RS +12%p",
        "target_price": 120,
        "stop_loss": 90,
        "sector": "Technology",
    }

    entry = emit_trading_context(
        "entry.executed",
        market="US",
        ticker="AAA",
        decision_id=scenario["_decision_id"],
        position_id=scenario["_position_id"],
        trigger_type="GapAndHold",
        scenario=scenario,
        decision_context={"decision": "entry", "buy_score": 8},
    )
    exit_event = emit_trading_context(
        "exit.executed",
        market="US",
        ticker="AAA",
        decision_id=scenario["_decision_id"],
        position_id=scenario["_position_id"],
        trigger_type="GapAndHold",
        scenario=scenario,
        market_context={
            "market_regime": "sideways",
            "swing_state": "pullback",
            "regime_confidence": 0.6,
        },
        decision_context={"decision": "exit", "profit_rate_pct": -3.0},
    )

    assert entry is not None and exit_event is not None
    assert entry["trace_id"] == exit_event["trace_id"]
    assert entry["event_id"] != exit_event["event_id"]
    assert entry["attributes"]["regime"] == "strong_bull"
    assert entry["attributes"]["swing_state"] == "consolidation"
    assert exit_event["attributes"]["regime"] == "sideways"
    assert (
        exit_event["attributes"]["entry_market_context"]["market_regime"]
        == "strong_bull"
    )
    assert entry["attributes"]["security_context"]["trend_facts"].startswith("price")
    rows = [json.loads(line) for line in spool.read_text(encoding="utf-8").splitlines()]
    assert [row["event_type"] for row in rows] == ["entry.executed", "exit.executed"]


def test_build_context_keeps_gate_and_portfolio_facts() -> None:
    context = build_trading_context(
        market="KR",
        scenario={"market_regime": "sideways", "sector": "반도체"},
        decision_context={"gate_allowed": False, "gate_reason": "score floor"},
        portfolio_context={"slots_used": 9, "slots_max": 10},
    )

    assert context["market_context"]["market_regime"] == "sideways"
    assert context["decision_context"]["gate_allowed"] is False
    assert context["portfolio_context"] == {"slots_used": 9, "slots_max": 10}


def test_candidate_outcome_keeps_zero_returns_and_decision_link(monkeypatch, tmp_path) -> None:
    spool = tmp_path / "outcomes.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))

    event = emit_candidate_outcome(
        market="KR",
        record={
            "id": 9,
            "decision_id": "report:KR_005930_20260827.pdf",
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "analyzed_date": "2026-07-28 09:30:00",
            "analyzed_price": 100.0,
            "trigger_type": "GapAndHold",
            "was_traded": 0,
            "tracked_7d_return": 0.0,
            "tracked_14d_return": 0.01,
        },
        updates={
            "tracked_30d_return": -0.02,
            "tracking_status": "completed",
            "updated_at": "2026-08-27",
        },
    )

    assert event is not None
    assert event["decision_id"] == "report:KR_005930_20260827.pdf"
    assert event["attributes"]["return_7d_pct"] == 0.0
    assert event["attributes"]["return_14d_pct"] == 1.0
    assert event["attributes"]["return_30d_pct"] == -2.0
