from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from observability.events import build_event
from tools.backfill_observability import (
    emit_backfill,
    iter_actual_events,
    iter_candidate_events,
    iter_regime_events,
    load_state,
    save_state,
)


def _database():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE trading_history (
          id INTEGER, ticker TEXT, company_name TEXT, buy_price REAL,
          buy_date TEXT, sell_price REAL, sell_date TEXT, profit_rate REAL,
          holding_days INTEGER, scenario TEXT, trigger_type TEXT,
          trigger_mode TEXT, sector TEXT, exit_kind TEXT
        );
        CREATE TABLE us_trading_history AS SELECT * FROM trading_history WHERE 0;
        CREATE TABLE analysis_performance_tracker (
          id INTEGER, ticker TEXT, company_name TEXT, trigger_type TEXT,
          trigger_mode TEXT, analyzed_date TEXT, decision TEXT,
          was_traded INTEGER, skip_reason TEXT, buy_score REAL,
          min_score REAL, target_price REAL, stop_loss REAL,
          risk_reward_ratio REAL, tracked_7d_return REAL,
          tracked_14d_return REAL, tracked_30d_return REAL,
          tracked_30d_date TEXT
        );
        CREATE TABLE us_analysis_performance_tracker (
          id INTEGER, ticker TEXT, company_name TEXT, trigger_type TEXT,
          trigger_mode TEXT, analysis_date TEXT, decision TEXT,
          was_traded INTEGER, skip_reason TEXT, buy_score REAL,
          target_price REAL, stop_loss REAL, risk_reward_ratio REAL,
          return_7d REAL, return_14d REAL, return_30d REAL,
          last_updated TEXT, hit_target INTEGER, hit_stop_loss INTEGER,
          sector TEXT
        );
        """
    )
    connection.execute(
        """
        INSERT INTO trading_history VALUES (
          1, '005930', 'Samsung', 70000, '2026-08-01 09:00:00',
          68000, '2026-08-10 09:00:00', -2.857, 9, '{"score": 7}',
          'Gap', 'topdown', 'Semiconductor', 'hard_stop'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO us_trading_history VALUES (
          2, 'AAPL', 'Apple', 200, '2026-08-02 09:00:00',
          210, '2026-08-11 09:00:00', 5.0, 9, '{"score": 8}',
          'Closing', 'bottomup', 'Technology', 'target'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_performance_tracker VALUES (
          10, '000660', 'SK Hynix', 'Gap', 'topdown',
          '2026-08-01 10:00:00', 'watch', 0, 'score',
          6, 7, 150000, 130000, 2.0, 0.01, 0.02, -0.03,
          '2026-08-31 10:00:00'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO us_analysis_performance_tracker VALUES (
          20, 'MSFT', 'Microsoft', 'Closing', 'bottomup',
          '2026-08-02 10:00:00', 'watch', 0, 'score',
          7, 500, 440, 2.5, 0.02, 0.03, 0.04,
          '2026-09-01 10:00:00', 0, 0, 'Technology'
        )
        """
    )
    connection.commit()
    return connection


def test_explicit_event_id_is_deterministic():
    first = build_event("test", service="test", event_id="source:1")
    second = build_event("test", service="test", event_id="source:1")
    assert first["event_id"] == second["event_id"]
    assert len(first["event_id"]) == 32


def test_actual_and_candidate_backfill_keep_units_and_provenance():
    connection = _database()
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    try:
        actual = list(iter_actual_events(connection, "KR", since=since))
        candidate = list(iter_candidate_events(connection, "US", since=since))
    finally:
        connection.close()

    assert actual[0]["event_type"] == "trade.outcome"
    assert actual[0]["attributes"]["profit_rate_pct"] == -2.857
    assert actual[0]["attributes"]["scenario_hash"] != '{"score": 7}'
    assert actual[0]["attributes"]["ingestion_mode"] == "backfill"
    assert candidate[0]["event_type"] == "candidate.outcome"
    assert candidate[0]["attributes"]["return_30d_pct"] == 4.0
    assert candidate[0]["attributes"]["was_traded"] == 0


def test_regime_backfill_skips_invalid_lines(tmp_path):
    path = tmp_path / "regime.jsonl"
    path.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps(
                    {
                        "ts": "2026-08-01 09:00:00",
                        "market": "KR",
                        "regime": "sideways",
                        "confidence": 0.55,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    events = list(
        iter_regime_events(
            path,
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    )
    assert len(events) == 1
    assert events[0]["attributes"]["regime"] == "sideways"
    assert events[0]["attributes"]["source"] == "regime_history_jsonl"


def test_state_and_emit_backfill_are_idempotent(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    event = {
        "event_type": "trade.outcome",
        "event_id": "a" * 32,
        "service": "test",
        "attributes": {},
    }
    emitted_ids: set[str] = set()
    monkeypatch.setattr(
        "tools.backfill_observability.emit_event",
        lambda **kwargs: kwargs,
    )

    first = emit_backfill([event], emitted_ids=emitted_ids, dry_run=False)
    second = emit_backfill([event], emitted_ids=emitted_ids, dry_run=False)
    save_state(state_path, emitted_ids)

    assert first["emitted"] == 1
    assert second["skipped"] == 1
    assert load_state(state_path) == {"a" * 32}
