from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.build_micro_split_evidence_packet import (
    build_micro_split_evidence_packet,
    load_replay_profiles,
)

ROOT = Path(__file__).resolve().parents[1]


def _event(
    event_id: str,
    event_type: str,
    timestamp: str,
    *,
    ticker: str,
    decision_id: str,
    attributes: dict,
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "market": "US",
        "ticker": ticker,
        "decision_id": decision_id,
        "attributes": attributes,
    }


def test_packet_is_deterministic_secret_minimized_and_separates_replay() -> None:
    micro_v1 = _event(
        "micro-1",
        "micro_split.shadow_evaluated",
        "2026-09-01T00:00:00Z",
        ticker="SECRET1",
        decision_id="raw-decision-1",
        attributes={
            "shadow_schema_version": 1,
            "mode": "SHADOW",
            "policy_version": "micro-split-v1-draft",
            "execution_profile_ref": "a" * 16,
            "projection_status": "PROJECTED",
            "target_pct": 10,
            "projected_whole_share_quantity": 0,
        },
    )
    micro_v2 = _event(
        "micro-2",
        "micro_split.shadow_evaluated",
        "2026-09-03T00:00:00Z",
        ticker="SECRET2",
        decision_id="raw-decision-2",
        attributes={
            "shadow_schema_version": 2,
            "mode": "SHADOW",
            "policy_version": "micro-split-v1-draft",
            "execution_profile_ref": "b" * 16,
            "unit_amount_snapshot_ref": "c" * 16,
            "projection_status": "PROJECTED",
            "target_pct": 10,
            "projected_whole_share_quantity": 0,
            "base_stage_projection_quantities": {
                "10": 0,
                "30": 1,
                "60": 2,
                "100": 4,
            },
            "first_executable_target_pct": 30,
            "internal_target_independent_of_execution": True,
        },
    )
    candidate = _event(
        "candidate-1",
        "candidate.evaluated",
        "2026-09-02T00:00:00Z",
        ticker="SECRET3",
        decision_id="raw-decision-3",
        attributes={
            "decision_context": {
                "price": 250,
                "is_add": False,
                "selected_for_entry": False,
            }
        },
    )
    profiles = [{"profile_ref": "profile-safe", "unit_amount": 1000.0}]
    raw = [micro_v2, candidate, micro_v1, dict(micro_v1)]

    first = build_micro_split_evidence_packet(raw, replay_profiles=profiles)
    second = build_micro_split_evidence_packet(
        list(reversed(raw)), replay_profiles=profiles
    )

    assert first["packet_id"] == second["packet_id"]
    assert first["observed_shadow"]["raw_event_count"] == 3
    assert first["observed_shadow"]["distinct_event_count"] == 2
    assert first["observed_shadow"]["duplicate_event_id_count"] == 1
    assert first["observed_shadow"]["schema_v2_coverage_rate"] == 0.5
    assert first["candidate_replay"]["candidate_count"] == 1
    assert first["candidate_replay"]["projected_row_count"] == 1
    replay = first["candidate_replay"]["rows"][0]
    assert replay["base_stage_projection_quantities"] == {
        "10": 0,
        "30": 1,
        "60": 2,
        "100": 4,
    }
    assert replay["first_executable_target_pct"] == 30
    encoded = json.dumps(first, ensure_ascii=False)
    for secret in (
        "SECRET1",
        "SECRET2",
        "SECRET3",
        "raw-decision",
        "unit_amount",
    ):
        assert secret not in encoded


def test_replay_profile_loader_reads_only_usd_amounts(tmp_path) -> None:
    config = tmp_path / "kis_devlp.yaml"
    config.write_text(
        """
default_unit_amount_usd: 1000
my_app: very-secret-app-key
my_sec: very-secret-app-secret
accounts:
  - name: first-secret-account
    account: 12345678
    buy_amount_usd: 750
  - name: second-secret-account
    account: 87654321
    market: us
""".strip(),
        encoding="utf-8",
    )

    profiles = load_replay_profiles(config)

    assert len(profiles) == 2
    assert sorted(profile["unit_amount"] for profile in profiles) == [750.0, 1000.0]
    encoded = json.dumps(profiles)
    for secret in (
        "very-secret",
        "first-secret-account",
        "second-secret-account",
        "12345678",
        "87654321",
    ):
        assert secret not in encoded


def test_packet_reports_hold_until_v2_coverage_and_sample_thresholds() -> None:
    packet = build_micro_split_evidence_packet([], replay_profiles=[])

    assert packet["readiness"]["data_sufficient"] is False
    assert packet["readiness"]["verdict"] == "CONTINUE_CAPTURE"
    assert {item["code"] for item in packet["readiness"]["reasons"]} >= {
        "OBSERVED_DECISIONS_LT_30",
        "OBSERVED_DATES_LT_20",
        "SCHEMA_V2_COVERAGE_LT_100_PCT",
        "CANDIDATE_REPLAY_UNAVAILABLE",
    }


def test_candidate_duplicate_is_reported_separately_from_micro_duplicates() -> None:
    candidate = _event(
        "candidate-duplicate",
        "candidate.evaluated",
        "2026-09-02T00:00:00Z",
        ticker="SAFE",
        decision_id="decision-safe",
        attributes={"decision_context": {"price": 100, "is_add": False}},
    )

    packet = build_micro_split_evidence_packet(
        [candidate, dict(candidate)],
        replay_profiles=[{"profile_ref": "profile-safe", "unit_amount": 1000}],
    )

    assert packet["observed_shadow"]["duplicate_event_id_count"] == 0
    assert packet["candidate_replay"]["duplicate_event_id_count"] == 1
    assert "DUPLICATE_EVENT_IDS_PRESENT" not in {
        reason["code"] for reason in packet["readiness"]["reasons"]
    }


def test_cli_runs_directly_without_repo_root_on_python_path(tmp_path) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text("", encoding="utf-8")
    config = tmp_path / "kis_devlp.yaml"
    config.write_text("default_unit_amount_usd: 1000\n", encoding="utf-8")
    output = tmp_path / "packet.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "build_micro_split_evidence_packet.py"),
            "--input",
            str(source),
            "--replay-config",
            str(config),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["packet_id"]
