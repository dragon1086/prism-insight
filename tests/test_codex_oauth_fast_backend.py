from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import cores.llm.codex_oauth_fast_backend as backend


def fake_process(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(stdin=open(os.devnull, "wb"), returncode=returncode,
                           communicate=lambda **kwargs: (stdout, stderr))


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
    tmp_path,
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
        def communicate(**communication):
            seen["kwargs"].update(communication)
            seen["prompt"] = (tmp_path / "stdin.bin").read_bytes()
            return stream, ""
        return SimpleNamespace(stdin=(tmp_path / "stdin.bin").open("wb"), returncode=0, communicate=communicate)

    monkeypatch.setattr(backend.subprocess, "Popen", fake_run)
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
    assert seen["kwargs"]["text"] is False
    assert seen["kwargs"]["input"] is None
    assert "도구를 사용하지 마세요" not in seen["prompt"].decode("utf-8")
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
        "Popen",
        lambda *_args, **_kwargs: fake_process(
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
        "Popen",
        lambda *_args, **_kwargs: fake_process(
            returncode=1, stdout="", stderr="auth failed"
        ),
    )
    with pytest.raises(backend.CodexFastError, match="rc=1") as error:
        backend.generate_codex_fast(system_prompt="s", user_prompt="u")
    assert "auth failed" not in str(error.value)


@pytest.mark.parametrize(
    ("returncode", "has_final", "mcp_status", "category"),
    [
        (7, True, "completed", "nonzero_exit"),
        (0, False, "completed", "missing_final"),
        (0, True, "failed", "missing_successful_mcp"),
    ],
)
def test_failure_telemetry_has_safe_metadata_only(
    monkeypatch, _trusted_codex, caplog, returncode, has_final, mcp_status, category,
):
    secret = "DO_NOT_LOG_TOKEN_OR_ACCOUNT_DATA"
    events = [{
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call", "server": secret, "tool": secret,
            "arguments": {"token": secret}, "status": mcp_status,
            "error": None if mcp_status == "completed" else secret,
        },
    }]
    if has_final:
        events.append({
            "type": "item.completed", "item": {"type": "agent_message", "text": secret},
        })
    monkeypatch.setattr(
        backend.subprocess, "Popen",
        lambda *_args, **_kwargs: fake_process(
            returncode=returncode, stdout="\n".join(map(json.dumps, events)), stderr=secret,
        ),
    )
    caplog.set_level(logging.INFO, logger=backend.__name__)
    with pytest.raises(backend.CodexFastError) as error:
        backend.generate_codex_fast(
            system_prompt=secret, user_prompt=secret, codex_home=secret,
            model="gpt-6-astra", reasoning_effort="high", timeout=240,
            mcp_profile="kr_trading", require_mcp_calls=True,
        )
    messages = [record.getMessage() for record in caplog.records]
    assert "category=start" in messages[0]
    assert f"category={category}" in messages[-1]
    assert "model=gpt-6-astra effort=high profile=kr_trading timeout_s=240" in messages[-1]
    assert f"rc={returncode}" in messages[-1]
    assert "elapsed_s=" in messages[-1]
    assert secret not in caplog.text
    assert secret not in str(error.value)


@pytest.mark.parametrize("failure_stage", ["resolve", "popen"])
def test_launch_error_telemetry_does_not_log_exception(
    monkeypatch, _trusted_codex, caplog, failure_stage,
):
    secret = "PRIVATE_EXECUTABLE_OR_ENV_TOKEN"

    def fail(*args, **kwargs):
        if failure_stage == "resolve":
            raise backend.CodexFastError(secret)
        raise OSError(secret)

    if failure_stage == "resolve":
        monkeypatch.setattr(backend, "_resolve_codex_executable", fail)
    else:
        monkeypatch.setattr(backend.subprocess, "Popen", fail)
    caplog.set_level(logging.INFO, logger=backend.__name__)
    with pytest.raises(backend.CodexFastError) as error:
        backend.generate_codex_fast(system_prompt=secret, user_prompt=secret)
    assert "category=launch_error" in caplog.text
    assert secret not in caplog.text
    assert secret not in str(error.value)


def test_us_trading_wires_codex_primary_before_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "prism-us" / "us_stock_tracking_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _extract_trading_scenario("):]
    method = method[:method.index("    async def ", 20_000)]
    assert "PRISM_US_CODEX_FAST_TRADING" in method
    assert "await generate_codex_fast_async(" in method
    assert "generate_codex_fast" in method
    assert 'mcp_profile="us_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with legacy_app.run()" in method  # Per-instance override; default remains module app.
    assert method.index("generate_codex_fast") < method.index("attach_llm(")


def test_kr_trading_wires_same_codex_primary_and_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "stock_tracking_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _extract_trading_scenario("):]
    method = method[:method.index("    def _default_scenario")]
    assert "PRISM_KR_CODEX_FAST_TRADING" in method
    assert "await generate_codex_fast_async(" in method
    assert "generate_codex_fast" in method
    assert 'mcp_profile="kr_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with legacy_app.run()" in method  # Per-instance override; default remains module app.
    assert method.index("generate_codex_fast") < method.index("attach_llm(")


@pytest.mark.asyncio
@pytest.mark.parametrize("market,path", [
    ("KR", "stock_tracking_enhanced_agent.py"),
    ("US", "prism-us/us_stock_tracking_agent.py"),
])
@pytest.mark.parametrize("outcome", ["success", "error", "invalid_config", "invalid_json", "cancel", "disabled"])
async def test_sell_dispatch_uses_isolated_settings_and_safe_fallback(monkeypatch, caplog, market, path, outcome):
    """Execute the real dispatch statements, without importing broker/DB modules."""
    import ast
    import asyncio
    import json
    import logging
    import os
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from prism_core.codex_config import resolve_sell_codex_settings

    source = (Path(__file__).resolve().parents[1] / path).read_text("utf-8")
    method = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_analyze_sell_decision")
    body = next(n.body for n in method.body if isinstance(n, ast.Try))
    start = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "response" for t in n.targets))
    end = next(i for i in range(start, len(body)) if isinstance(body[i], ast.If) and ast.unparse(body[i].test) == "response is None")
    # Keep the original await, fallback context managers, and exception handlers.
    harness = ast.AsyncFunctionDef(name="dispatch", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=body[start:end + 1] + [ast.Return(value=ast.Name(id="response", ctx=ast.Load()))], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    monkeypatch.setenv(f"PRISM_{market}_CODEX_FAST_SELL", "0" if outcome == "disabled" else "1")
    monkeypatch.setenv("PRISM_SELL_CODEX_MODEL", "gpt-6-astra")
    monkeypatch.setenv("PRISM_SELL_CODEX_EFFORT", "high")
    monkeypatch.setenv("PRISM_SELL_CODEX_TIMEOUT", "nan" if outcome == "invalid_config" else "120")
    monkeypatch.setenv("PRISM_BUY_CODEX_TIMEOUT", "240")
    monkeypatch.setenv("PRISM_CODEX_FAST_TIMEOUT", "90")
    response = '{"decision":"hold"}'
    generate = AsyncMock(return_value=SimpleNamespace(text="invalid" if outcome == "invalid_json" else response, latency_s=1, mcp_calls=[{}]))
    if outcome in {"error", "cancel"}:
        generate.side_effect = RuntimeError("offline") if outcome == "error" else asyncio.CancelledError()
    legacy = AsyncMock(return_value=response)
    attach = AsyncMock(return_value=SimpleNamespace(generate_str=legacy))
    host_calls = []

    @asynccontextmanager
    async def host():
        host_calls.append("entered")
        yield
        host_calls.append("exited")

    def parse(text, **kwargs):
        try:
            return json.loads(text)
        except ValueError:
            return None

    namespace = dict(os=os, logger=logging.getLogger("sell_dispatch_test"),
                     self=SimpleNamespace(sell_decision_agent=SimpleNamespace(instruction="read-only test", attach_llm=attach), _get_legacy_fallback_lock=asyncio.Lock),
                     generate_codex_fast_async=generate, resolve_sell_codex_settings=resolve_sell_codex_settings,
                     parse_llm_json=parse, ticker="TEST", prompt_message="no orders",
                     OpenAIAugmentedLLM=object(), RequestParams=SimpleNamespace,
                     app=SimpleNamespace(run=host))
    namespace[f"_{market.lower()}_codex_runtime_enabled"] = lambda: True
    exec(compile(module, path, "exec"), namespace)
    with caplog.at_level("INFO"):
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await namespace["dispatch"]()
        else:
            assert await namespace["dispatch"]() == response
    if outcome not in {"invalid_config", "disabled"}:
        assert generate.await_count == 1
        assert generate.await_args.kwargs == dict(system_prompt="read-only test", user_prompt="no orders", model="gpt-6-astra", reasoning_effort="high", timeout=120, mcp_profile=f"{market.lower()}_trading", require_mcp_calls=True)
        assert "requested_model=gpt-6-astra requested_effort=high requested_tier=fast timeout_s=120" in caplog.text
    else:
        generate.assert_not_awaited()
    if outcome in {"success", "cancel"}:
        attach.assert_not_awaited()
        assert host_calls == []
    else:
        attach.assert_awaited_once()
        params = legacy.await_args.kwargs["request_params"]
        assert (params.model, params.reasoning_effort, params.maxTokens) == ("gpt-5.6-sol", "high", 30000)
        assert host_calls == ["entered", "exited"]


def test_us_sell_wires_codex_mcp_before_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "prism-us"
        / "us_stock_tracking_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _analyze_sell_decision("):]
    method = method[:method.index("    async def ", 20_000)]
    assert "PRISM_US_CODEX_FAST_SELL" in method
    assert "await generate_codex_fast_async(" in method
    assert "resolve_sell_codex_settings()" in method
    assert 'mcp_profile="us_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with legacy_app.run()" in method  # Per-instance override; default remains module app.
    assert method.index("generate_codex_fast") < method.index("attach_llm(")


def test_kr_sell_wires_codex_mcp_before_legacy_fallback() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "stock_tracking_enhanced_agent.py"
    ).read_text("utf-8")
    method = source[source.index("    async def _analyze_sell_decision("):]
    method = method[:method.index("    async def ", 20_000)]
    assert "PRISM_KR_CODEX_FAST_SELL" in method
    assert "await generate_codex_fast_async(" in method
    assert "resolve_sell_codex_settings()" in method
    assert 'mcp_profile="kr_trading"' in method
    assert "require_mcp_calls=True" in method
    assert "falling back to mcp-agent" in method
    assert "async with legacy_app.run()" in method  # Per-instance override; default remains module app.
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
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: pytest already depends on this backport.
        import tomli as tomllib

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
