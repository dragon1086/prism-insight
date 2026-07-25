from __future__ import annotations

import json

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import ApprovalStatus
from kakao_bot.runtime.admin_main import main
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue


def output_json(capsys):
    return json.loads(capsys.readouterr().out)


def test_admin_cli_lists_approves_configures_and_rejects_room(
    tmp_path,
    capsys,
):
    database_path = tmp_path / "kakao.sqlite"
    with SQLiteKakaoRepository(database_path) as repository:
        repository.discover_room("room-1")

    assert main(["--database", str(database_path), "rooms"]) == 0
    assert output_json(capsys) == {
        "rooms": [
            {
                "approval_status": "PENDING",
                "room_id": "room-1",
            }
        ],
        "status": "ok",
    }

    assert (
        main(
            [
                "--database",
                str(database_path),
                "approve",
                "room-1",
            ]
        )
        == 0
    )
    assert output_json(capsys)["approval_status"] == "APPROVED"
    with SQLiteKakaoRepository(database_path) as repository:
        assert repository.get_subscription("room-1").kr_afternoon is True

    assert (
        main(
            [
                "--database",
                str(database_path),
                "subscription",
                "room-1",
                "--kr-afternoon",
                "off",
                "--us-morning",
                "on",
                "--rest-notices",
                "on",
            ]
        )
        == 0
    )
    configured = output_json(capsys)["subscription"]
    assert configured["kr_afternoon"] is False
    assert configured["us_morning"] is True
    assert configured["rest_notices"] is True

    assert (
        main(
            [
                "--database",
                str(database_path),
                "reject",
                "room-1",
            ]
        )
        == 0
    )
    assert output_json(capsys)["approval_status"] == "REJECTED"
    with SQLiteKakaoRepository(database_path) as repository:
        assert repository.get_room("room-1").approval_status is ApprovalStatus.REJECTED


def test_admin_cli_reports_local_campaign_queue_counts(tmp_path, capsys):
    queue_path = tmp_path / "campaigns.sqlite"
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        queue.enqueue({"campaign_id": "campaign-1"})

    assert main(["--queue", str(queue_path), "campaigns"]) == 0
    assert output_json(capsys) == {
        "campaigns": {
            "CONSUMED": 0,
            "DEAD": 0,
            "PENDING": 1,
            "SENDING": 0,
        },
        "status": "ok",
    }
