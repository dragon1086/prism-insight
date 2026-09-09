"""Staging tests never access real auth, accounts, or installed MCP runtime."""
import json
import sqlite3

import pytest

from tools import prepare_codex_probe_stage as stage


def test_existing_directory_is_never_reused(tmp_path):
    with pytest.raises(ValueError, match="new_canonical"):
        stage.prepare(tmp_path, tmp_path, tmp_path / "auth")


def test_private_stage_does_not_read_trading_db(tmp_path, monkeypatch):
    source = tmp_path / "source"
    for name in ("cores/llm/time_mcp_server.py", "sqlite/src/mcp_server_sqlite/__init__.py"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# trusted fixture source\n")
    auth = tmp_path / "auth"
    auth.write_text('{"token":"PRIVATE_CANARY"}')
    monkeypatch.setattr(stage, "stage_runtime", lambda root, names: {"mcp": "test"})
    root = tmp_path / "isolated"
    result = stage.prepare(root, source, auth)
    assert result["production_database_opened"] is False
    assert result["production_parity"] is False
    assert "PRIVATE_CANARY" not in json.dumps(result)
    assert (root / "home/auth.json").stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(root / "portfolio.sqlite") as db:
        assert db.execute("SELECT mode FROM fixture_metadata").fetchone()[0] == "SYNTHETIC_SMOKE_NO_ORDERS"
    for path in root.rglob("*"):
        assert not path.is_symlink()
    assert not (root / "stock_tracking_db.sqlite").exists()


def test_regular_copy_dereferences_without_hardlink(tmp_path):
    original = tmp_path / "original"
    original.write_text("private")
    link = tmp_path / "link"
    link.symlink_to(original)
    target = tmp_path / "copy"
    stage.copy_regular(link, target)
    assert target.read_text() == "private"
    assert target.stat().st_ino != original.stat().st_ino
    assert target.stat().st_nlink == 1
    assert not target.is_symlink()
