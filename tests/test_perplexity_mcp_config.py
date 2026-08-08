from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_perplexity_uses_versioned_official_mcp_package():
    config = yaml.safe_load(
        (ROOT / "cores" / "llm" / "mcp_servers.yaml").read_text(encoding="utf-8")
    )

    perplexity = config["servers"]["perplexity"]
    assert perplexity["command"] == "npx"
    assert perplexity["args"] == ["-y", "@perplexity-ai/mcp-server@1.2.0"]
    assert perplexity["env"] == {"PERPLEXITY_API_KEY": "${PERPLEXITY_API_KEY}"}
