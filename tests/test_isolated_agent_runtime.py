import json
import os
from pathlib import Path
import sqlite3

import pytest
from mcp_agent.config import Settings

from prism_core.isolated_agent_runtime import (
    ROOT_MARKER, prepare_isolated_runtime, validated_mcp_settings, virtual_account_label,
    configured_mcp_app, attach_isolated_llm,
)


def settings():
    return {"mcp": {"servers": {}},
            "openai": {"base_url": "http://127.0.0.1:9999/v1", "api_key": "explicit-test-placeholder"}}


def private_root(tmp_path, market="KR"):
    root = tmp_path / "isolated"
    root.mkdir(mode=0o700)
    marker = root / ROOT_MARKER
    marker.write_text(json.dumps({"schema_version": 1, "purpose": "PRISM_AGENT_SHADOW",
                                 "market": market, "runtime_id": "baseline-test"}))
    marker.chmod(0o600)
    return root


def account(market="kr"):
    return {"name": "SHADOW baseline", "account_key": "virtual:baseline",
            "market": market, "virtual": True}


def prepare(root, accounts=None, **kwargs):
    values = {"db_path": str(root / "state.sqlite"), "virtual_accounts": [account()] if accounts is None else accounts,
              "isolated_db_root": str(root), "mcp_settings_factory": settings, "market": "KR"}
    values.update(kwargs)
    return prepare_isolated_runtime(**values)


def test_default_requires_no_paths_or_settings():
    assert prepare_isolated_runtime("legacy.sqlite", None, None, None, "KR") is None


@pytest.mark.parametrize("accounts_on,root_on,factory_on", [
    (False, False, True), (False, True, False), (False, True, True),
    (True, False, False), (True, False, True), (True, True, False),
])
def test_incomplete_opt_in_never_enables_production_execution(tmp_path, accounts_on, root_on, factory_on):
    root = private_root(tmp_path)
    with pytest.raises(ValueError):
        prepare_isolated_runtime(str(root / "state.sqlite"), [account()] if accounts_on else None,
                                 str(root) if root_on else None, settings if factory_on else None, "KR")
    assert not (root / "state.sqlite").exists()


def test_private_marked_database_initialization_restart_and_copy(tmp_path):
    root = private_root(tmp_path)
    records = [account()]
    runtime = prepare(root, records)
    records[0]["name"] = "CHANGED"
    assert runtime.accounts()[0]["name"] == "SHADOW baseline"
    one = runtime.accounts()
    one[0]["account_key"] = "vps:real:01"
    assert runtime.accounts()[0]["account_key"] == "virtual:baseline"
    conn = runtime.connect()
    conn.execute("CREATE TABLE local_state (value TEXT)")
    conn.execute("INSERT INTO local_state VALUES ('retained')")
    conn.commit()
    conn.close()
    resumed = prepare(root).connect()
    assert resumed.execute("SELECT value FROM local_state").fetchone()[0] == "retained"
    resumed.close()
    assert os.stat(root / "state.sqlite").st_mode & 0o077 == 0


@pytest.mark.parametrize("change", [{"account_key": "vps:123:01"}, {"name": "primary"},
                                   {"virtual": False}, {"market": "us"},
                                   {"buy_amount_krw": 100000}, {"app_key": "SECRET"},
                                   {"account": "12345678"}])
def test_virtual_records_reject_broker_cash_or_identity_fields(tmp_path, change):
    root = private_root(tmp_path)
    with pytest.raises(ValueError):
        prepare(root, [{**account(), **change}])
    assert not (root / "state.sqlite").exists()


def test_root_must_be_explicit_private_marked_and_matching_market(tmp_path):
    root = tmp_path / "unmarked"
    root.mkdir()
    with pytest.raises(ValueError):
        prepare(root)
    root = private_root(tmp_path, "US")
    with pytest.raises(ValueError):
        prepare(root)
    root.chmod(0o755)
    with pytest.raises(ValueError):
        prepare(root)


def test_path_escape_symlink_and_unmarked_existing_db_rejected(tmp_path):
    root = private_root(tmp_path)
    with pytest.raises(ValueError):
        prepare(root, db_path=str(tmp_path / "outside.sqlite"))
    outside = tmp_path / "outside.sqlite"
    sqlite3.connect(outside).close()
    (root / "state.sqlite").symlink_to(outside)
    with pytest.raises(ValueError):
        prepare(root)
    (root / "state.sqlite").unlink()
    sqlite3.connect(root / "state.sqlite").close()
    with pytest.raises(ValueError):
        prepare(root).connect()
    assert outside.stat().st_size == 0


def test_hard_link_and_foreign_runtime_marker_rejected(tmp_path):
    root = private_root(tmp_path)
    runtime = prepare(root)
    runtime.connect().close()
    os.link(root / "state.sqlite", root / "alias.sqlite")
    with pytest.raises(ValueError):
        prepare(root).connect()
    (root / "alias.sqlite").unlink()
    changed = [{**account(), "account_key": "virtual:other"}]
    with pytest.raises(ValueError):
        prepare(root, changed).connect()


def test_virtual_account_log_is_explicit_and_never_contains_broker_key():
    assert virtual_account_label(account()) == "SHADOW baseline (가상 계정)"
    with pytest.raises(ValueError):
        virtual_account_label({**account(), "account_key": "vps:12345678:01"})


@pytest.mark.parametrize("factory", [None, "settings.yaml", lambda: None, lambda: {},
                                     lambda: "mcp_agent.config.yaml",
                                     lambda: Settings(_env_file=None)])
def test_invalid_factory_result_cannot_fall_back_to_global_settings(factory):
    with pytest.raises(ValueError):
        validated_mcp_settings(factory)


def test_explicit_mcp_and_openai_provider_required_and_copy_isolated():
    for factory in (
        lambda: {},
        lambda: {"mcp": {"servers": {}}},
        lambda: {"mcp": {"servers": {}}, "openai": {"api_key": "placeholder"}},
        lambda: {"mcp": {"servers": {}}, "openai": {"base_url": "http://127.0.0.1:9999"}},
    ):
        with pytest.raises(ValueError):
            validated_mcp_settings(factory)
    original = settings()
    first = validated_mcp_settings(lambda: original)
    second = validated_mcp_settings(lambda: original)
    first.openai.base_url = "http://127.0.0.1:9998"
    assert original["openai"]["base_url"] == second.openai.base_url == "http://127.0.0.1:9999/v1"


def test_all_ambient_provider_config_canaries_absent_without_config_reads(tmp_path, monkeypatch):
    import builtins
    import io
    canary = "AMBIENT_CREDENTIAL_CANARY"
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "COHERE_API_KEY",
                 "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_API_KEY", "GOOGLE_API_KEY",
                 "LM_STUDIO_API_KEY", "OAUTH__TOKEN_STORE__REDIS_URL", "ENV", "LOGGER__HTTP_ENDPOINT",
                 "OTEL__ENABLED", "USAGE_TELEMETRY__ENABLED"):
        monkeypatch.setenv(name, canary)
    monkeypatch.chdir(tmp_path)
    names = {".env", ".env.mcp-cloud", "mcp_agent.config.yaml", "mcp_agent.secrets.yaml"}
    for name in names:
        (tmp_path / name).write_text("OPENAI_API_KEY=" + canary)
    opened = []
    for owner in (builtins, io):
        original = owner.open
        def guarded(path, *args, _original=original, **kwargs):
            if isinstance(path, (str, Path)) and Path(path).name in names:
                opened.append(str(path))
                raise AssertionError("ambient file read")
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(owner, "open", guarded)
    before = dict(os.environ)
    result = validated_mcp_settings(settings)
    assert canary not in json.dumps(result.model_dump(mode="json"))
    assert opened == [] and dict(os.environ) == before
    assert all(getattr(result, field) is None for field in
               ("anthropic", "bedrock", "cohere", "azure", "google", "lm_studio", "oauth", "authorization", "agents", "temporal"))
    assert result.logger.type == "none" and not result.otel.enabled and not result.usage_telemetry.enabled
    assert result.env == []


@pytest.mark.parametrize("flag", [None, "false", 0])
def test_unknown_sdk_dotenv_bypass_contract_fails_closed(flag):
    class UnsupportedApp:
        def __init__(self, **kwargs):
            if flag is not None:
                self._dotenv_loaded = flag
    with pytest.raises(ValueError, match="compatibility"):
        configured_mcp_app(UnsupportedApp, "test", settings)


@pytest.mark.asyncio
async def test_actual_mcp_app_and_llm_use_explicit_context_without_ambient_discovery(tmp_path, monkeypatch):
    import builtins
    import io
    import socket
    import sys
    from mcp_agent.app import MCPApp
    from mcp_agent.agents.agent import Agent
    from cores.llm.openai_responses_llm import OpenAIResponsesLLM

    monkeypatch.chdir(tmp_path)
    canary = "ALL_AMBIENT_PROVIDER_CANARY"
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "COHERE_API_KEY",
                 "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_API_KEY", "GOOGLE_API_KEY",
                 "LM_STUDIO_API_KEY", "ENV"):
        monkeypatch.setenv(name, canary)
    for name in (".env", ".env.mcp-cloud", "mcp_agent.config.yaml", "mcp_agent.secrets.yaml"):
        (tmp_path / name).write_text("OPENAI_API_KEY=" + canary)
    forbidden_calls = []
    def forbid(*args, **kwargs):
        forbidden_calls.append("ambient")
        raise AssertionError("ambient settings/context/network discovery forbidden")
    # Trap all already-imported SDK aliases as well as the originating modules.
    for module in list(sys.modules.values()):
        if module is not None and getattr(module, "__name__", "").startswith("mcp_agent"):
            for name in ("get_settings", "get_current_context", "aget_current_context", "load_dotenv"):
                if hasattr(module, name):
                    monkeypatch.setattr(module, name, forbid)
    monkeypatch.setattr(socket.socket, "connect", forbid)
    for owner in (builtins, io):
        original = owner.open
        def guarded(path, *args, _original=original, **kwargs):
            if isinstance(path, (str, Path)) and Path(path).name in {
                ".env", ".env.mcp-cloud", "mcp_agent.config.yaml", "mcp_agent.secrets.yaml",
            }:
                return forbid()
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(owner, "open", guarded)
    environment = dict(os.environ)
    app = configured_mcp_app(MCPApp, "isolated-settings-test", settings)
    async with app.run():
        assert app.context is not None
        agent = Agent(name="isolated-no-tools", instruction="synthetic test", server_names=[])
        llm = await attach_isolated_llm(agent, OpenAIResponsesLLM, app.context)
        assert agent.context is app.context and llm._context is app.context
        assert canary not in json.dumps(app.context.config.model_dump(mode="json"))
    assert forbidden_calls == []
    assert dict(os.environ) == environment
