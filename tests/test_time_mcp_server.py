from __future__ import annotations

from datetime import datetime

import pytest

from cores.llm.config_loader import load_mcp_registry
from cores.llm.time_mcp_server import convert_time, get_current_time


def test_get_current_time_uses_requested_timezone():
    result = get_current_time("Asia/Seoul")

    parsed = datetime.fromisoformat(result["datetime"])
    assert result["timezone"] == "Asia/Seoul"
    assert parsed.utcoffset().total_seconds() == 9 * 60 * 60
    assert result["is_dst"] is False


def test_convert_time_preserves_instant():
    result = convert_time(
        "Asia/Seoul",
        "2026-08-06T01:00:00",
        "UTC",
    )

    source = datetime.fromisoformat(result["source_datetime"])
    target = datetime.fromisoformat(result["target_datetime"])
    assert source.timestamp() == target.timestamp()
    assert target.isoformat() == "2026-08-05T16:00:00+00:00"


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValueError, match="unknown timezone"):
        get_current_time("Mars/Olympus")


def test_registry_uses_configured_python_for_time_server(monkeypatch):
    monkeypatch.setenv("PRISM_MCP_PYTHON", "/opt/prism/venv/bin/python")
    monkeypatch.setenv("PRISM_REPO_ROOT", "/opt/prism/repo")

    spec = load_mcp_registry().get("time")

    assert spec.command == "/opt/prism/venv/bin/python"
    assert spec.args == ("-m", "cores.llm.time_mcp_server")
    assert spec.env == {"PYTHONPATH": "/opt/prism/repo"}
