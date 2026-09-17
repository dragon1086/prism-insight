import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("validate_us_full_pipeline", Path(__file__).parents[1] / "tools/validate_us_full_pipeline.py")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_environment_requires_explicit_isolation():
    env = {"PYTHON_DOTENV_DISABLED": "1", "PRISM_CODEX_HOME": "/private/codex", "PRISM_DISABLE_SIGNAL_PUBLISH": "1"}
    harness.check_environment("http://127.0.0.1:8080/v1", env)
    for bad in ({}, {**env, "KIS_APP_KEY": "secret"}, {**env, "TELEGRAM_BOT_TOKEN": "secret"}):
        with pytest.raises(ValueError):
            harness.check_environment("http://localhost:8080/v1", bad)
    with pytest.raises(ValueError):
        harness.check_environment("https://api.openai.com/v1", env)


def test_registry_reuses_existing_strict_readonly_server(tmp_path):
    config = harness.readonly_settings(tmp_path / "test.sqlite", "http://localhost:8080/v1")
    servers = config["mcp"]["servers"]
    assert set(servers) == {"sqlite", "time"}
    assert servers["sqlite"]["args"][0].endswith("tools/isolated_sqlite_mcp.py")
    assert servers["sqlite"]["args"][1] == "--db-path"
    assert servers["time"]["args"][0].endswith("cores/llm/time_mcp_server.py")


def test_empty_or_partial_pipeline_cannot_pass(tmp_path):
    receipt = {"stages": {}, "buy_analyses": []}
    assert harness.validate_receipt(receipt)
    artifact = tmp_path / "artifact"
    artifact.write_text("real artifact fixture")
    receipt["stages"] = {stage: {"status": "ok", "result": [str(artifact)]} for stage in harness.STAGES}
    receipt["buy_analyses"] = [{"success": True, "gate": {"allowed": False, "findings": []}}]
    assert not harness.validate_receipt(receipt)
    receipt["buy_analyses"][0]["success"] = False
    assert harness.validate_receipt(receipt)
    receipt["buy_analyses"][0]["success"] = True
    artifact.unlink()
    assert harness.validate_receipt(receipt)


def test_output_root_private_and_fresh(tmp_path):
    root = harness.prepare_output(tmp_path / "new")
    assert root.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        harness.prepare_output(root)
    with pytest.raises(ValueError):
        harness.prepare_output(harness.ROOT / "invalid-test-artifacts")
