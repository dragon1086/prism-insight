from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKER_DOCS = ("README_DOCKER.md", "README_DOCKER_ko.md")
ALL_DOCS = (*DOCKER_DOCS, "docs/SETUP_ko.md")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mcp_versions_and_commands_match_runtime():
    for path in ALL_DOCS:
        text = _read(path)
        assert "firecrawl-mcp@3.23.6" in text, path
        assert "@perplexity-ai/mcp-server@1.2.0" in text, path
        assert "perplexity-ask/dist/index.js" not in text, path


def test_docker_docs_match_kst_cron_schedule():
    for path in DOCKER_DOCS:
        text = _read(path)
        assert "Asia/Seoul" in text, path
        assert "10:05" in text, path
        assert "04:00" not in text, path
        assert "3 times daily" not in text, path
        assert "하루 3회" not in text, path
        assert "perplexity-ask/" not in text, path


def test_korean_setup_documents_verified_openai_pair():
    text = _read("docs/SETUP_ko.md")
    assert "openai==2.43.0" in text
    assert "openai-agents==0.7.0" in text
