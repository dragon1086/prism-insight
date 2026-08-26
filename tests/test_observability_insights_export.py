from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tools import publish_observability_insights as publisher
from tools.export_observability_insights import build_snapshot, load_clickhouse_events


def _event(
    event_id,
    event_type,
    timestamp,
    *,
    market=None,
    ticker=None,
    attributes=None,
    git_sha="abc123",
):
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "market": market,
        "ticker": ticker,
        "git_sha": git_sha,
        "attributes": attributes or {},
    }


def test_snapshot_separates_actual_candidate_and_market():
    events = [
        _event(
            "trade-kr-1",
            "trade.outcome",
            "2026-08-10T00:00:00Z",
            market="KR",
            attributes={
                "profit_rate_pct": -5.0,
                "buy_date": "2026-08-01 09:00:00",
                "trigger_type": "Gap",
                "exit_kind": "hard_stop",
                "ingestion_mode": "backfill",
            },
        ),
        _event(
            "trade-us-1",
            "trade.outcome",
            "2026-08-11T00:00:00Z",
            market="US",
            attributes={
                "profit_rate_pct": 10.0,
                "buy_date": "2026-08-02 09:00:00",
                "trigger_type": "Closing",
                "exit_kind": "target",
                "ingestion_mode": "backfill",
            },
        ),
        _event(
            "candidate-kr-1",
            "candidate.outcome",
            "2026-08-01T00:00:00Z",
            market="KR",
            attributes={
                "return_7d_pct": 1.0,
                "return_14d_pct": 2.0,
                "return_30d_pct": 3.0,
                "was_traded": 0,
                "trigger_type": "Gap",
                "ingestion_mode": "backfill",
            },
        ),
    ]
    snapshot = build_snapshot(
        events,
        now=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )

    assert snapshot["markets"]["KR"]["actual"]["count"] == 1
    assert snapshot["markets"]["KR"]["actual"]["stop_rate"] == 1.0
    assert snapshot["markets"]["US"]["actual"]["win_rate"] == 1.0
    assert snapshot["markets"]["KR"]["candidate"]["avg_30d_pct"] == 3.0
    assert snapshot["markets"]["KR"]["triggers"][0]["trigger_type"] == "Gap"
    assert snapshot["data_quality"]["backfill_events"] == 3


def test_clickhouse_exporter_rejects_non_local_endpoint():
    with pytest.raises(ValueError, match="local HTTP"):
        load_clickhouse_events(
            "https://clickhouse.example",
            user="user",
            password="secret",
            days=180,
        )


def test_snapshot_deduplicates_event_ids_and_deployments():
    deployment = _event(
        "deploy-live",
        "deployment.applied",
        "2026-08-10T00:00:00Z",
        attributes={"target": "db-server", "prs": [1]},
        git_sha="same-sha",
    )
    deployment_backfill = _event(
        "deploy-backfill",
        "deployment.applied",
        "2026-08-10T00:00:01Z",
        attributes={
            "target": "db-server",
            "git_sha": "same-sha",
            "ingestion_mode": "backfill",
        },
    )
    duplicate = dict(deployment)
    snapshot = build_snapshot(
        [deployment, duplicate, deployment_backfill],
        now=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    assert snapshot["data_quality"]["total_events"] == 2
    assert len(snapshot["deployments"]) == 1
    assert snapshot["deployments"][0]["ingestion_mode"] == "live"


def test_deployment_impact_uses_buy_date_cohorts():
    deployment = _event(
        "deploy",
        "deployment.applied",
        "2026-08-10T00:00:00Z",
        attributes={"target": "db-server"},
    )
    trades = []
    for index, buy_date in enumerate(
        ("2026-08-01 09:00:00", "2026-08-12 09:00:00"),
        1,
    ):
        trades.append(
            _event(
                f"trade-{index}",
                "trade.outcome",
                f"2026-08-{15 + index}T00:00:00Z",
                market="KR",
                attributes={
                    "profit_rate_pct": -2.0 if index == 1 else 4.0,
                    "buy_date": buy_date,
                    "trigger_type": "Gap",
                    "exit_kind": "normal",
                },
            )
        )
    snapshot = build_snapshot(
        [deployment, *trades],
        now=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    impact = snapshot["deployment_impacts"][0]
    assert impact["markets"]["KR"]["pre"]["count"] == 1
    assert impact["markets"]["KR"]["post"]["count"] == 1
    assert impact["post_window_complete"] is True


def test_publisher_validates_remote_destination_before_execution(tmp_path):
    with pytest.raises(ValueError, match="destination"):
        publisher.publish(
            endpoint="http://127.0.0.1:18123",
            local_output=tmp_path / "snapshot.json",
            days=180,
            host="app.example",
            port=22,
            user="root",
            destination="/tmp/good path.json",
            identity_file=None,
        )


def test_publisher_generates_then_installs_atomically(tmp_path, monkeypatch):
    snapshot = {
        "generated_at": "2026-08-26T00:00:00Z",
        "data_quality": {"total_events": 3},
    }
    calls = []
    monkeypatch.setenv("CLICKHOUSE_USER", "prism_otel")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "test-password")
    monkeypatch.setattr(publisher, "load_clickhouse_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publisher, "build_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(
        publisher,
        "write_snapshot",
        lambda path, value: path.write_text(str(value), encoding="utf-8"),
    )
    monkeypatch.setattr(
        publisher.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    result = publisher.publish(
        endpoint="http://127.0.0.1:18123",
        local_output=tmp_path / "snapshot.json",
        days=180,
        host="app.example",
        port=2222,
        user="root",
        destination="/srv/dashboard/observability_insights.json",
        identity_file="/etc/prism-observability/dashboard-publisher",
    )

    assert result["events"] == 3
    assert calls[0][0][0].endswith("/scp")
    assert "-P" in calls[0][0]
    assert "2222" in calls[0][0]
    assert "/etc/prism-observability/dashboard-publisher" in calls[0][0]
    assert calls[0][0][-1].endswith("observability_insights.json.tmp")
    assert calls[1][0][0].endswith("/ssh")
    assert "-p" in calls[1][0]
    assert "/usr/bin/install" in calls[1][0]
    assert calls[2][0][0].endswith("/ssh")
