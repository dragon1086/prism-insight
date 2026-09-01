from __future__ import annotations

import json

from observability.micro_split import (
    build_initial_shadow_context,
    emit_initial_shadow,
    shadow_enabled,
)


def test_shadow_flag_defaults_off_and_parses_explicit_values(monkeypatch) -> None:
    monkeypatch.delenv("MICRO_SPLIT_SHADOW_ENABLED", raising=False)
    assert shadow_enabled() is False
    assert shadow_enabled("true") is True
    assert shadow_enabled("0") is False


def test_initial_shadow_context_keeps_unit_amount_secret() -> None:
    context = build_initial_shadow_context(
        market="US",
        decision_id="decision-1",
        account_id="prod:secret-account:01",
        unit_amount=1_000_000,
        current_price=600_000,
        regime="moderate_bull",
    )

    encoded = json.dumps(context, ensure_ascii=False)
    assert context["mode"] == "SHADOW"
    assert context["policy_version"] == "micro-split-v1-draft"
    assert context["previous_target_pct"] == 0
    assert context["target_pct"] == 10
    assert context["target_slot_units"] == 0.1
    assert context["reason_code"] == "ENTRY_ELIGIBLE_SCOUT"
    assert context["projection_status"] == "PROJECTED"
    assert context["projected_whole_share_quantity"] == 0
    assert context["projected_buy_delta_quantity"] == 0
    assert len(context["execution_profile_ref"]) == 16
    assert "secret-account" not in encoded
    assert "1000000" not in encoded


def test_high_price_projection_does_not_remove_internal_target() -> None:
    context = build_initial_shadow_context(
        market="KR",
        decision_id="decision-2",
        account_id="prod:kr:01",
        unit_amount=1_000_000,
        current_price=1_500_000,
        regime="strong_bull",
    )

    assert context["target_pct"] == 10
    assert context["projected_whole_share_quantity"] == 0
    assert context["max_target_pct"] == 300


def test_missing_unit_amount_still_records_internal_shadow_target() -> None:
    context = build_initial_shadow_context(
        market="US",
        decision_id="decision-3",
        account_id="prod:us:01",
        unit_amount=None,
        current_price=100.0,
        regime="sideways",
    )

    assert context["target_pct"] == 10
    assert context["projection_status"] == "INPUT_UNAVAILABLE"
    assert context["projected_whole_share_quantity"] is None


def test_emit_shadow_is_flagged_deterministic_and_secret_minimized(
    monkeypatch, tmp_path
) -> None:
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    kwargs = {
        "market": "US",
        "ticker": "AAPL",
        "decision_id": "decision-4",
        "account_id": "prod:secret-account:01",
        "unit_amount": 1100.0,
        "current_price": 200.0,
        "regime": "moderate_bull",
    }

    first = emit_initial_shadow(**kwargs)
    second = emit_initial_shadow(**kwargs)

    assert first is not None
    assert second is not None
    assert first["event_id"] == second["event_id"]
    assert first["event_type"] == "micro_split.shadow_evaluated"
    assert first["attributes"]["target_pct"] == 10
    assert "secret-account" not in json.dumps(first)
    assert "1100" not in json.dumps(first)


def test_emit_shadow_off_has_no_file_side_effect(monkeypatch, tmp_path) -> None:
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "false")
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))

    result = emit_initial_shadow(
        market="US",
        ticker="AAPL",
        decision_id="decision-5",
        account_id="prod:us:01",
        unit_amount=1100.0,
        current_price=200.0,
        regime="moderate_bull",
    )

    assert result is None
    assert not spool.exists()
