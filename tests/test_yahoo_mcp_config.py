from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ARGS = [
    "tool", "run", "--from", "yahoo-finance-mcp==0.1.2",
    "--with", "mcp==1.26.0", "yahoo-finance-mcp",
]


def test_native_yahoo_uses_uv_tool_run_with_compatible_mcp():
    config = yaml.safe_load(
        (ROOT / "cores" / "llm" / "mcp_servers.yaml").read_text(encoding="utf-8")
    )
    yahoo = config["servers"]["yahoo_finance"]

    assert yahoo["command"] == "uv"
    assert yahoo["args"] == EXPECTED_ARGS


def test_legacy_example_matches_native_yahoo_command():
    config = yaml.safe_load(
        (ROOT / "mcp_agent.config.yaml.example").read_text(encoding="utf-8")
    )
    yahoo = config["mcp"]["servers"]["yahoo_finance"]

    assert yahoo["command"] == "uv"
    assert yahoo["args"] == EXPECTED_ARGS
