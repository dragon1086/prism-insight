from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import cores.llm.codex_oauth_fast_backend as backend


@pytest.fixture
def _trusted_codex(monkeypatch) -> None:
    monkeypatch.setattr(
        backend,
        "_resolve_codex_executable",
        lambda _candidate: "/trusted/codex",
    )


def test_resolve_codex_executable_rejects_unsafe_permissions(
    monkeypatch,
    tmp_path,
) -> None:
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setattr(backend.shutil, "which", lambda _candidate: str(executable))

    assert backend._resolve_codex_executable("codex") == str(executable.resolve())

    executable.chmod(0o775)
    with pytest.raises(backend.CodexFastError, match="unsafe permissions"):
        backend._resolve_codex_executable("codex")


def test_command_rejects_unapproved_model_and_profile() -> None:
    with pytest.raises(backend.CodexFastError, match="Unsupported Codex model"):
        backend._command("/trusted/codex", "attacker-model", None)
    with pytest.raises(backend.CodexFastError, match="Unsupported Codex MCP profile"):
        backend._command("/trusted/codex", "gpt-5.6-sol", "attacker-profile")


def test_codex_fast_backend_uses_isolated_ephemeral_command(
    monkeypatch,
    _trusted_codex,
) -> None:
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(
            command=command,
            kwargs=kwargs,
            run_dir_existed=Path(kwargs["cwd"]).is_dir(),
        )
        stream = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "time",
                    "tool": "get_current_time",
                    "arguments": {"timezone": "Asia/Seoul"},
                    "status": "completed",
                },
            }),
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"decision":"미진입"}'},
            }),
            json.dumps({"type": "turn.completed", "usage": {"output_tokens": 10}}),
        ])
        return SimpleNamespace(returncode=0, stdout=stream, stderr="")

    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    result = backend.generate_codex_fast(
        system_prompt="system",
        user_prompt="user",
        codex_home="/secure/codex-home",
        mcp_profile="kr_trading",
        require_mcp_calls=True,
    )
    command = seen["command"]
    assert command[:2] == ["/trusted/codex", "exec"]
    assert "--ephemeral" in command and "read-only" in command
    assert 'service_tier="fast"' in command
    assert command[command.index("--profile") + 1] == "kr_trading"
    assert seen["run_dir_existed"] is True
    assert not Path(seen["kwargs"]["cwd"]).exists()
    assert seen["kwargs"]["env"]["CODEX_HOME"] == "/secure/codex-home"
    assert seen["kwargs"]["start_new_session"] is True
    assert "도구를 사용하지 마세요" not in seen["kwargs"]["input"]
    assert result.text == '{"decision":"미진입"}'
    assert result.usage == {"output_tokens": 10}
    assert [(call.server, call.tool) for call in result.mcp_calls] == [
        ("time", "get_current_time")
    ]


def test_codex_fast_backend_requires_mcp_call_when_requested(
    monkeypatch,
    _trusted_codex,
) -> None:
    stream = "\n".join([
        json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"decision":"미진입"}'},
        }),
        json.dumps({"type": "turn.completed", "usage": {"output_tokens": 10}}),
    ])
    monkeypatch.setattr(
        backend.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=stream, stderr=""
        ),
    )

    with pytest.raises(backend.CodexFastError, match="no MCP tool calls"):
        backend.generate_codex_fast(
            system_prompt="s",
            user_prompt="u",
            mcp_profile="us_trading",
            require_mcp_calls=True,
        )


def test_codex_fast_backend_raises_on_cli_failure(
    monkeypatch,
    _trusted_codex,
) -> None:
    monkeypatch.setattr(
        backend.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="auth failed"
        ),
    )
    with pytest.raises(backend.CodexFastError, match="auth failed"):
        backend.generate_codex_fast(system_prompt="s", user_prompt="u")


def test_us_trading_wires_codex_primary_before_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "prism-us" / "us_stock_tracking_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _extract_trading_scenario("):]
    method = method[:method.index("    async def ", 20_000)]
    assert "PRISM_US_CODEX_FAST_TRADING" in method
    assert "await asyncio.to_thread(" in method
    assert "generate_codex_fast" in method
    assert 'mcp_profile="us_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with app.run()" in method
    assert method.index("generate_codex_fast") < method.index("attach_llm(")


def test_kr_trading_wires_same_codex_primary_and_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "stock_tracking_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _extract_trading_scenario("):]
    method = method[:method.index("    def _default_scenario")]
    assert "PRISM_KR_CODEX_FAST_TRADING" in method
    assert "await asyncio.to_thread(" in method
    assert "generate_codex_fast" in method
    assert 'mcp_profile="kr_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with app.run()" in method
    assert method.index("generate_codex_fast") < method.index("attach_llm(")


def test_us_sell_wires_codex_mcp_before_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "prism-us"
        / "us_stock_tracking_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _analyze_sell_decision("):]
    method = method[:method.index("    async def ", 20_000)]
    assert "PRISM_US_CODEX_FAST_SELL" in method
    assert 'mcp_profile="us_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with app.run()" in method
    assert method.index("generate_codex_fast") < method.index("attach_llm(")


def test_kr_sell_wires_codex_mcp_before_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "stock_tracking_enhanced_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _analyze_sell_decision("):]
    method = method[:method.index("    async def ", 20_000)]
    assert "PRISM_KR_CODEX_FAST_SELL" in method
    assert 'mcp_profile="kr_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with app.run()" in method
    assert method.index("generate_codex_fast") < method.index("attach_llm(")


@pytest.mark.parametrize(
    ("profile_name", "market_server"),
    [
        ("kr_trading.config.toml", "kospi_kosdaq"),
        ("us_trading.config.toml", "yahoo_finance"),
    ],
)
def test_trading_mcp_profiles_allow_only_read_only_sqlite_tools(
    profile_name: str,
    market_server: str,
) -> None:
    import tomllib

    profile_path = Path(__file__).resolve().parents[1] / "deploy" / profile_name
    config = tomllib.loads(profile_path.read_text("utf-8"))
    servers = config["mcp_servers"]

    assert market_server in servers
    assert servers["sqlite"]["enabled_tools"] == [
        "list_tables",
        "describe_table",
        "read_query",
    ]
    assert not {
        "write_query",
        "create_table",
        "append_insight",
    }.intersection(servers["sqlite"]["enabled_tools"])
    assert servers["time"]["enabled_tools"] == ["get_current_time"]
    assert servers["perplexity"]["enabled_tools"] == ["perplexity_ask"]


@pytest.mark.parametrize(
    ("path", "runtime_helper"),
    [
        ("stock_analysis_orchestrator.py", "_kr_codex_runtime_enabled"),
        (
            "prism-us/us_stock_analysis_orchestrator.py",
            "_us_codex_runtime_enabled",
        ),
    ],
)
def test_orchestrator_avoids_nested_mcp_host_for_codex_runtime(
    path: str,
    runtime_helper: str,
) -> None:
    source = (Path(__file__).resolve().parents[1] / path).read_text("utf-8")
    assert "nullcontext" in source
    assert runtime_helper in source
    assert "tracking_app.run()" in source


@pytest.mark.parametrize(
    "path",
    ["stock_tracking_agent.py", "prism-us/us_stock_tracking_agent.py"],
)
def test_tracking_mcp_app_is_lazy_for_codex_runtime(path: str) -> None:
    source = (Path(__file__).resolve().parents[1] / path).read_text("utf-8")
    assert "class _LazyMCPApp" in source
    assert "app = _LazyMCPApp(" in source
    assert "app = MCPApp(" not in source
