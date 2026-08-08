from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PACKAGE = "firecrawl-mcp@3.23.6"


def test_native_firecrawl_uses_verified_version():
    config = yaml.safe_load(
        (ROOT / "cores" / "llm" / "mcp_servers.yaml").read_text(encoding="utf-8")
    )

    firecrawl = config["servers"]["firecrawl"]
    assert firecrawl["command"] == "npx"
    assert firecrawl["args"] == ["-y", EXPECTED_PACKAGE]
    assert firecrawl["env"] == {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"}


def test_legacy_example_matches_native_firecrawl_version():
    config = yaml.safe_load(
        (ROOT / "mcp_agent.config.yaml.example").read_text(encoding="utf-8")
    )

    firecrawl = config["mcp"]["servers"]["firecrawl"]
    assert firecrawl["command"] == "npx"
    assert firecrawl["args"] == ["-y", EXPECTED_PACKAGE]
