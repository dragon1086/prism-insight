from __future__ import annotations

import json

import pandas as pd
import pytest

from observability.third_slot_shadow import (
    HORIZONS,
    emit_evaluation,
    shadow_enabled,
    track_matured_outcomes,
)
from tools.build_third_slot_evidence_packet import (
    build_third_slot_evidence_packet,
)


def _candidates() -> list[dict]:
    return [
        {
            "rank": 1,
            "role": "LIVE_SELECTED",
            "ticker": "AAA",
            "company_name": "Alpha",
            "trigger_type": "Trigger A",
            "screening_price": 100.0,
            "score": 3.0,
        },
        {
            "rank": 2,
            "role": "LIVE_SELECTED",
            "ticker": "BBB",
            "company_name": "Beta",
            "trigger_type": "Trigger B",
            "screening_price": 200.0,
            "score": 2.0,
        },
        {
            "rank": 3,
            "role": "SHADOW_THIRD",
            "ticker": "CCC",
            "company_name": "Gamma",
            "trigger_type": "Trigger C",
            "screening_price": 300.0,
            "score": 1.0,
        },
    ]


def test_shadow_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED", raising=False)
    assert shadow_enabled() is False
    assert shadow_enabled("true") is True


def test_evaluation_event_has_three_roles_and_no_trading_effect(tmp_path):
    spool = tmp_path / "events.jsonl"
    event = emit_evaluation(
        trade_date="20260904",
        trigger_mode="morning",
        regime="moderate_bear",
        candidates=_candidates(),
        spool_path=spool,
        enabled=True,
    )

    assert event is not None
    assert event["event_type"] == "screening.third_slot_shadow_evaluated"
    attributes = event["attributes"]
    assert attributes["mode"] == "SHADOW"
    assert attributes["trading_impact"] == "none"
    assert [row["role"] for row in attributes["candidates"]] == [
        "LIVE_SELECTED",
        "LIVE_SELECTED",
        "SHADOW_THIRD",
    ]
    assert len(spool.read_text().splitlines()) == 1

    repeated = emit_evaluation(
        trade_date="20260904",
        trigger_mode="morning",
        regime="moderate_bear",
        candidates=_candidates(),
        spool_path=spool,
        enabled=True,
    )
    assert repeated["event_id"] == event["event_id"]
    assert len(spool.read_text().splitlines()) == 1


def test_outcome_tracker_uses_exact_trading_horizons_and_is_idempotent(tmp_path):
    spool = tmp_path / "events.jsonl"
    emit_evaluation(
        trade_date="20260904",
        trigger_mode="afternoon",
        regime="sideways",
        candidates=_candidates(),
        spool_path=spool,
        enabled=True,
    )
    bases = {"AAA": 100.0, "BBB": 200.0, "CCC": 300.0}
    multipliers = {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0}

    def price_loader(ticker, _start, _end):
        base = bases[ticker]
        scale = multipliers[ticker]
        index = pd.bdate_range("2026-09-07", periods=10)
        closes = [base * (1 + scale * day / 100) for day in range(1, 11)]
        return pd.DataFrame(
            {
                "High": [price * 1.005 for price in closes],
                "Low": [base * 0.995 for _ in closes],
                "Close": closes,
            },
            index=index,
        )

    first = track_matured_outcomes(
        spool_path=spool,
        as_of="20260930",
        price_loader=price_loader,
    )
    second = track_matured_outcomes(
        spool_path=spool,
        as_of="20260930",
        price_loader=price_loader,
    )

    assert first == {"evaluations": 1, "emitted": 12, "pending": 0, "errors": 0}
    assert second == {"evaluations": 1, "emitted": 0, "pending": 0, "errors": 0}
    events = [json.loads(line) for line in spool.read_text().splitlines()]
    outcomes = [
        event
        for event in events
        if event["event_type"] == "screening.third_slot_shadow_outcome"
    ]
    assert len(outcomes) == 3 * len(HORIZONS)
    target = next(
        event
        for event in outcomes
        if event["ticker"] == "CCC"
        and event["attributes"]["horizon_trading_days"] == 5
    )
    assert target["attributes"]["return_pct"] == pytest.approx(15.0)
    assert target["attributes"]["outcome_date"] == "20260911"
    assert target["attributes"]["mfe_pct"] > 15.0
    assert target["attributes"]["mae_pct"] == pytest.approx(-0.5)

    packet = build_third_slot_evidence_packet(events)
    reversed_packet = build_third_slot_evidence_packet(list(reversed(events)))
    metrics = packet["horizon_metrics"]["5"]
    assert reversed_packet["packet_id"] == packet["packet_id"]
    assert metrics["matured_experiment_count"] == 1
    assert metrics["median_third_return_pct"] == pytest.approx(15.0)
    assert metrics["median_pair_delta_pct_points"] == pytest.approx(7.5)
    assert packet["readiness"]["verdict"] == "CONTINUE_CAPTURE"


def test_outcome_tracker_keeps_unmatured_horizons_pending(tmp_path):
    spool = tmp_path / "events.jsonl"
    emit_evaluation(
        trade_date="20260904",
        trigger_mode="morning",
        regime="moderate_bear",
        candidates=_candidates(),
        spool_path=spool,
        enabled=True,
    )

    def price_loader(_ticker, _start, _end):
        return pd.DataFrame(
            {"High": [101.0], "Low": [99.0], "Close": [100.5]},
            index=[pd.Timestamp("2026-09-07")],
        )

    result = track_matured_outcomes(
        spool_path=spool,
        as_of="20260907",
        price_loader=price_loader,
    )

    assert result == {"evaluations": 1, "emitted": 3, "pending": 9, "errors": 0}
