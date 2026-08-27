from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from performance_tracker_batch import PerformanceTrackerBatch
from tracking.db_schema import TABLE_ANALYSIS_PERFORMANCE_TRACKER


def test_completed_candidate_tracking_emits_linked_live_outcome(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "tracking.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(TABLE_ANALYSIS_PERFORMANCE_TRACKER)
    analyzed_at = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d 09:30:00")
    connection.execute(
        """
        INSERT INTO analysis_performance_tracker
        (decision_id, ticker, company_name, trigger_type, trigger_mode,
         analyzed_date, analyzed_price, decision, was_traded, skip_reason,
         buy_score, min_score, target_price, stop_loss, risk_reward_ratio,
         tracking_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            "report:KR_005930_test.pdf",
            "005930",
            "Samsung Electronics",
            "GapAndHold",
            "morning",
            analyzed_at,
            100.0,
            "Watch",
            0,
            "score floor",
            7,
            8,
            120.0,
            90.0,
            2.0,
            analyzed_at,
            analyzed_at,
        ),
    )
    connection.commit()
    connection.close()

    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    batch = PerformanceTrackerBatch(str(database))
    batch.get_current_price = lambda _ticker: 110.0

    stats = batch.run()

    assert stats["completed"] == 1
    events = [json.loads(line) for line in spool.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["event_type"] == "candidate.outcome"
    assert events[0]["decision_id"] == "report:KR_005930_test.pdf"
    assert events[0]["attributes"]["return_30d_pct"] == 10.0
