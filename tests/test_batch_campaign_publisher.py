"""Focused contract tests for channel-neutral batch campaign publication."""

from __future__ import annotations

import ast
import subprocess
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from messaging.batch_campaign_publisher import (
    BatchCampaignPublisher,
    COLLECTING,
    COMPLETED,
    SKIPPED,
    build_batch_campaign_event,
    campaign_id_for,
    publish_batch_campaign_best_effort,
    select_reported_candidates,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

PROJECT_ROOT = Path(__file__).parent.parent
ORCHESTRATORS = (
    PROJECT_ROOT / "stock_analysis_orchestrator.py",
    PROJECT_ROOT / "prism-us" / "us_stock_analysis_orchestrator.py",
)


def test_campaign_id_is_deterministic_and_normalizes_trade_date():
    assert campaign_id_for("kr", "afternoon", "20260723") == ("kr-afternoon-2026-07-23")
    assert campaign_id_for("KR", "AFTERNOON", "2026-07-23") == (
        "kr-afternoon-2026-07-23"
    )


def test_contract_accepts_string_enums_and_rejects_unknown_dimensions():
    class TestRegime(str, Enum):
        CORRECTION = "CORRECTION"

    event = build_batch_campaign_event(
        market="KR",
        session="MORNING",
        trade_date="20260723",
        regime=TestRegime.CORRECTION,
        status=SKIPPED,
        skip_reason="policy rest",
    )
    assert event["regime"] == "CORRECTION"

    common = {
        "trade_date": "20260723",
        "regime": "UPTREND",
        "status": COMPLETED,
        "candidates": [{"ticker": "005930"}],
    }
    with pytest.raises(ValueError, match="unsupported market"):
        build_batch_campaign_event(market="JP", session="MORNING", **common)
    with pytest.raises(ValueError, match="unsupported session"):
        build_batch_campaign_event(market="KR", session="both", **common)
    unknown_regime_event = build_batch_campaign_event(
        market="KR",
        session="MORNING",
        **{**common, "regime": None},
    )
    assert unknown_regime_event["regime"] == "UNKNOWN"


def test_completed_payload_is_canonical_and_limited_to_five_candidates():
    occurred_at = datetime(2026, 7, 23, 5, 30, tzinfo=timezone.utc)
    candidates = [
        {
            "code": f"00000{index}",
            "name": f"회사 {index}",
            "risk_reward_ratio": index + 0.5,
            "trigger_type": "Closing Strength Top",
        }
        for index in range(6)
    ]

    event = build_batch_campaign_event(
        market="kr",
        session="afternoon",
        trade_date="20260723",
        regime="correction",
        status=COMPLETED,
        candidates=candidates,
        display_message="🔔 텔레그램과 공유하는 프리즘 시그널",
        occurred_at=occurred_at,
    )

    assert event == {
        "schema_version": 1,
        "event_type": "BATCH_CAMPAIGN_COMPLETED",
        "campaign_id": "kr-afternoon-2026-07-23",
        "market": "KR",
        "session": "AFTERNOON",
        "trade_date": "2026-07-23",
        "regime": "CORRECTION",
        "status": "COMPLETED",
        "occurred_at": "2026-07-23T05:30:00Z",
        "candidates": [
            {
                "ticker": f"00000{index}",
                "company_name": f"회사 {index}",
                "score": index + 0.5,
                "rationale": "Closing Strength Top",
            }
            for index in range(5)
        ],
        "display_message": "🔔 텔레그램과 공유하는 프리즘 시그널",
    }


def test_skipped_payload_has_reason_and_no_candidates():
    event = build_batch_campaign_event(
        market="US",
        session="MORNING",
        trade_date="2026-07-23",
        regime="CORRECTION",
        status=SKIPPED,
        skip_reason="CORRECTION morning batch rests",
        occurred_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )

    assert event["event_type"] == "BATCH_CAMPAIGN_SKIPPED"
    assert event["skip_reason"] == "CORRECTION morning batch rests"
    assert "candidates" not in event


def test_collecting_payload_carries_the_immediate_screening_briefing():
    event = build_batch_campaign_event(
        market="KR",
        session="AFTERNOON",
        trade_date="20260723",
        regime="UPTREND",
        status=COLLECTING,
        candidates=[{"code": "005930", "name": "삼성전자"}],
        display_message="🔔 선정 직후 프리즘 시그널",
    )

    assert event["event_type"] == "BATCH_CAMPAIGN_COLLECTING"
    assert event["display_message"] == "🔔 선정 직후 프리즘 시그널"


def test_report_filter_excludes_candidates_whose_generation_failed():
    candidates = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
    ]

    selected = select_reported_candidates(
        candidates,
        ["reports/000660_SK하이닉스_20260723_afternoon_gpt.md"],
    )

    assert selected == [candidates[1]]


@pytest.mark.asyncio
async def test_local_publish_is_durable_idempotent_and_fail_open(tmp_path):
    event = build_batch_campaign_event(
        market="KR",
        session="AFTERNOON",
        trade_date="20260723",
        regime="UPTREND",
        status=COMPLETED,
        candidates=[{"code": "005930", "name": "삼성전자"}],
    )
    database_path = tmp_path / "campaigns.sqlite"
    publisher = BatchCampaignPublisher(database_path=database_path)
    await publisher.connect()

    assert await publisher.publish(event) == "kr-afternoon-2026-07-23"
    assert await publisher.publish(event) is None
    await publisher.disconnect()

    with SQLiteBatchCampaignQueue(database_path) as queue:
        [entry] = queue.list_entries()
    assert entry["campaign_id"] == "kr-afternoon-2026-07-23"
    assert entry["status"] == "PENDING"
    assert entry["attempt_count"] == 0

    failing_queue = MagicMock()
    failing_queue.enqueue.side_effect = RuntimeError("disk unavailable")
    publisher = BatchCampaignPublisher(queue=failing_queue)
    await publisher.connect()
    assert await publisher.publish(event) is None


@pytest.mark.asyncio
async def test_local_publisher_missing_parent_is_fail_open(tmp_path):
    publisher = BatchCampaignPublisher(
        database_path=tmp_path / "missing" / "campaigns.sqlite"
    )

    await publisher.connect()

    assert publisher._queue is None


def test_local_publisher_reads_dedicated_environment_configuration(
    monkeypatch,
):
    monkeypatch.setenv(
        "PRISM_CAMPAIGN_QUEUE_PATH",
        "state/campaigns.sqlite",
    )

    publisher = BatchCampaignPublisher()

    assert publisher.database_path == Path("state/campaigns.sqlite")


@pytest.mark.asyncio
async def test_best_effort_helper_swallows_invalid_event():
    assert (
        await publish_batch_campaign_best_effort(
            market="KR",
            session="MORNING",
            trade_date="20260723",
            regime="CORRECTION",
            status=SKIPPED,
        )
        is None
    )


def test_campaign_publisher_has_no_network_transport_dependency():
    source = (PROJECT_ROOT / "messaging" / "batch_campaign_publisher.py").read_text(
        encoding="utf-8"
    )

    assert "upstash_redis" not in source
    assert "UPSTASH_REDIS" not in source
    assert "prism:batch-campaigns" not in source
    assert "google.cloud" not in source
    assert "pubsub" not in source.lower()


def test_campaign_import_does_not_load_legacy_redis_publisher():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import messaging.batch_campaign_publisher; "
                "assert 'messaging.redis_signal_publisher' not in sys.modules; "
                "assert 'google.cloud.pubsub_v1' not in sys.modules"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _import_targets(tree: ast.AST) -> list[str]:
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
    return targets


@pytest.mark.parametrize("orchestrator_path", ORCHESTRATORS)
def test_orchestrators_depend_on_messaging_not_kakao(orchestrator_path):
    tree = ast.parse(orchestrator_path.read_text(encoding="utf-8"))
    imports = _import_targets(tree)

    assert "messaging.batch_campaign_publisher" in imports
    assert not any(
        target == "kakao_bot" or target.startswith("kakao_bot.") for target in imports
    )


@pytest.mark.parametrize("orchestrator_path", ORCHESTRATORS)
def test_orchestrator_publishes_screening_before_report_generation(orchestrator_path):
    tree = ast.parse(orchestrator_path.read_text(encoding="utf-8"))

    run_pipeline = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_full_pipeline"
    )
    collecting_calls = [
        node
        for node in ast.walk(run_pipeline)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "publish_batch_campaign_best_effort"
        and any(
            keyword.arg == "status"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "COLLECTING"
            for keyword in node.keywords
        )
    ]
    report_calls = [
        node
        for node in ast.walk(run_pipeline)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "generate_reports"
    ]
    assert len(collecting_calls) == 1
    assert len(report_calls) == 1
    keyword_names = {keyword.arg for keyword in collecting_calls[0].keywords}
    assert "display_message" in keyword_names
    assert collecting_calls[0].lineno < report_calls[0].lineno
    completed_calls = [
        node
        for node in ast.walk(run_pipeline)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "publish_batch_campaign_best_effort"
        and any(
            keyword.arg == "status"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "COMPLETED"
            for keyword in node.keywords
        )
    ]
    assert completed_calls == []

    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    live_branch = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "_mp_mode"
        and any(
            isinstance(comparator, ast.Constant) and comparator.value == "live"
            for comparator in node.test.comparators
        )
    )
    assert isinstance(live_branch.body[-1], ast.Return)
    skipped_statement = live_branch.body[-2]
    skipped_calls = [
        node
        for node in ast.walk(skipped_statement)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "publish_batch_campaign_best_effort"
        and any(
            keyword.arg == "status"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "SKIPPED"
            for keyword in node.keywords
        )
    ]
    assert len(skipped_calls) == 1
