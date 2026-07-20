import json
import sqlite3

import pytest

from prism_core.exit_effects import ExitEffectStore
from tools import replay_exit_effects


INTENT_ID = "intent-cli-1"


def _seed(path):
    with sqlite3.connect(path) as connection:
        store = ExitEffectStore(connection)
        store.ensure_schema()
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        store.enqueue_exit_effects(
            intent_id=INTENT_ID,
            market="KR",
            account_id="vps:kr-primary:01",
            symbol="005930",
            source="kr_batch",
            payload={
                "event_id": INTENT_ID,
                "market": "KR",
                "symbol": "005930",
                "company_name": "Samsung",
                "sell_price": 75000,
                "buy_price": 70000,
                "profit_rate": 7.1,
                "holding_days": 10,
                "sell_reason": "target",
                "message": "private sold message",
                "journal_stock_data": {"ticker": "005930"},
            },
        )
        connection.commit()


def test_replay_cli_defaults_to_read_only_audit(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "dry.sqlite"
    _seed(db_path)
    before = db_path.read_bytes()

    def unexpected_handlers(*_args, **_kwargs):
        raise AssertionError("dry-run must not build production handlers")

    monkeypatch.setattr(
        replay_exit_effects, "build_exit_effect_handlers", unexpected_handlers
    )

    result = replay_exit_effects.main(["--db-path", str(db_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["mode"] == "dry-run"
    assert payload["audit"]["unresolved_count"] == 4
    assert before == db_path.read_bytes()
    assert "private sold message" not in json.dumps(payload)


def test_execute_requires_explicit_effect_filter(tmp_path):
    db_path = tmp_path / "filter.sqlite"
    _seed(db_path)

    with pytest.raises(SystemExit) as error:
        replay_exit_effects.main(["--db-path", str(db_path), "--execute"])

    assert error.value.code == 2


def test_execute_runs_bounded_selected_handler(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "execute.sqlite"
    _seed(db_path)
    calls = []

    async def redis(payload):
        calls.append(payload["event_id"])
        return "redis-cli-message-1"

    monkeypatch.setattr(
        replay_exit_effects,
        "build_exit_effect_handlers",
        lambda *_args, **_kwargs: {"REDIS": redis},
    )

    result = replay_exit_effects.main(
        [
            "--db-path",
            str(db_path),
            "--execute",
            "--effect",
            "REDIS",
            "--limit",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    with sqlite3.connect(db_path) as connection:
        row = ExitEffectStore(connection).get_effect(f"{INTENT_ID}:redis")

    assert result == 0
    assert payload["mode"] == "execute"
    assert payload["summary"]["claimed"] == 1
    assert payload["summary"]["delivered"] == 1
    assert calls == [INTENT_ID]
    assert row["status"] == "DELIVERED"
    assert row["remote_id"] == "redis-cli-message-1"
