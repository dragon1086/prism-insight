"""No models, servers, production databases or credentials are loaded."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import probe_codex_trading_runtime as probe
from tools.codex_probe_sandbox import DISABLED_FEATURES


def make_case(market="KR", action="SELL"):
    schema = {"type": "object"}
    return {"market": market, "action": action,
            "system_prompt": "PRIVATE_SYSTEM_ACCOUNT_CANARY",
            "user_prompt": "PRIVATE_USER_ACCOUNT_CANARY",
            "system_sha256": probe.digest("PRIVATE_SYSTEM_ACCOUNT_CANARY"),
            "user_sha256": probe.digest("PRIVATE_USER_ACCOUNT_CANARY"),
            "schema": schema, "schema_sha256": probe.schema_digest(schema),
            "deviations": ["missing_ancillary_load"]}


@pytest.fixture
def setup_files(tmp_path):
    # Resolve macOS /var alias, which is correctly rejected for runtime inputs.
    root = tmp_path.resolve()
    home = root / "home"
    home.mkdir()
    (home / "auth.json").write_text("DUMMY_AUTH")
    (home / "config.toml").write_text('cli_auth_credentials_store="file"\nweb_search="disabled"\n[features]\nfast_mode=true\ncode_mode_host=true\n' +
                                      "\n".join(name + "=false" for name in sorted(DISABLED_FEATURES)))
    database = root / "isolated.sqlite"
    database.write_bytes(b"FIXTURE_NOT_OPENED")
    for market, profile in (("KR", "kr_trading"), ("US", "us_trading")):
        names = ["time", "sqlite", "perplexity", "kospi_kosdaq" if market == "KR" else "yahoo_finance"]
        lines = []
        for name in names:
            lines.extend([f"[mcp_servers.{name}]", 'command="/nonexistent-fixture-command"',
                          "enabled_tools=" + json.dumps(sorted(probe.READ_TOOLS[name])),
                          'default_tools_approval_mode="approve"', "required=true"])
            if name == "sqlite":
                lines.append("args=" + json.dumps(["--db-path", str(database)]))
            if name in {"perplexity", "kospi_kosdaq", "yahoo_finance"}:
                lines[-4] = "command=" + json.dumps(str(root / "runtime/bin/python3.11"))
                lines.append("args=" + json.dumps([str(root / "mcp/read_bridge_client.py"), "--client", f"/mcp-{name}.sock"]))
                lines.append("env={PYTHONHOME=" + json.dumps(str(root / "runtime")) + ",LD_LIBRARY_PATH=" + json.dumps(str(root / "runtime/lib")) + "}")
        (home / (profile + ".config.toml")).write_text("\n".join(lines))
    fixture = root / "fixture.json"
    fixture.write_text(json.dumps({"version": 1, "cases": [make_case()]}))
    return root, home, fixture


def test_validation_no_execution_or_raw_data(setup_files, monkeypatch, capsys):
    root, home, fixture = setup_files
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("no process allowed"))
    args = ["--run-root", str(root), "--codex-home", str(home), "--fixture", str(fixture)]
    assert probe.main(args) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["status"] == "validated_only"
    assert manifest["execution_enabled"] is False
    assert "CANARY" not in json.dumps(manifest)
    assert probe.main(args + ["--execute"]) == 2
    assert json.loads(capsys.readouterr().out)["category"] == "isolated_launcher_required"


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(account_id="CANARY"),
    lambda c: c.update(market="CANARY"),
    lambda c: c.update(action="ORDER"),
    lambda c: c.update(system_prompt="changed"),
    lambda c: c.update(schema_sha256="bad"),
    lambda c: c.update(deviations=["CANARY"]),
    lambda c: c.update(deviations="missing_rank"),
])
def test_reject_fixture_metadata(setup_files, mutation):
    root, _, fixture = setup_files
    case = make_case()
    mutation(case)
    fixture.write_text(json.dumps({"version": 1, "cases": [case]}))
    with pytest.raises(probe.ProbeRejected) as error:
        probe.load_fixture(fixture, root)
    assert "CANARY" not in str(error.value)


def test_reject_symlink_and_hardlink_configuration(setup_files):
    import os
    root, home, _ = setup_files
    config = home / "config.toml"
    real = root / "production.toml"
    config.rename(real)
    config.symlink_to(real)
    with pytest.raises(probe.ProbeRejected, match="unsafe_path"):
        probe.validate_home(home, root, {"KR"})
    config.unlink()
    os.link(real, config)
    with pytest.raises(probe.ProbeRejected, match="unsafe_file"):
        probe.validate_home(home, root, {"KR"})


def test_auth_symlink_allowed_but_never_read(setup_files):
    root, home, _ = setup_files
    real = root / "dedicated-auth.json"
    (home / "auth.json").rename(real)
    (home / "auth.json").symlink_to(real)
    probe.validate_home(home, root, {"KR", "US"})


@pytest.mark.parametrize("replacement", [
    ('"read_query"', '"write_query"'),
    ('"--db-path"', '"--wrong-path"'),
    ('default_tools_approval_mode="approve"', 'default_tools_approval_mode="auto"'),
    ('required=true', 'required=false'),
])
def test_reject_unsafe_profile(setup_files, replacement):
    root, home, _ = setup_files
    config = home / "kr_trading.config.toml"
    config.write_text(config.read_text().replace(*replacement))
    with pytest.raises(probe.ProbeRejected):
        probe.validate_home(home, root, {"KR"})


def test_reject_database_outside_root(setup_files):
    root, home, _ = setup_files
    config = home / "kr_trading.config.toml"
    config.write_text(config.read_text().replace(str(root / "isolated.sqlite"), "/production/account.sqlite"))
    with pytest.raises(probe.ProbeRejected, match="unsafe_path"):
        probe.validate_home(home, root, {"KR"})


def test_reject_shell_enabled(setup_files):
    root, home, _ = setup_files
    config = home / "config.toml"
    config.write_text(config.read_text().replace("shell_tool=false", "shell_tool=true"))
    with pytest.raises(probe.ProbeRejected, match="unsafe_features"):
        probe.validate_home(home, root, {"KR"})


def good_result(text='{"private": "RAW_RESPONSE_CANARY"}'):
    call = SimpleNamespace(server="time", tool="get_current_time", status="completed", error=None)
    return SimpleNamespace(text=text, mcp_calls=[call], latency_s=0.1)


def test_sell_sequential_then_bounded_buy_no_fallback():
    cases = [make_case("KR", "BUY"), make_case("US", "SELL"), make_case("KR", "SELL")]
    cases += [make_case("US", "BUY") for _ in range(5)]
    async def run():
        active = 0
        peak = 0
        order = []
        async def invoke(case):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            order.append(case["action"])
            if case["action"] == "SELL":
                assert active == 1
            await asyncio.sleep(0.001)
            active -= 1
            return good_result()
        records = await probe._schedule_diagnostic(cases, invoke=invoke, concurrency=2)
        assert order[:2] == ["SELL", "SELL"]
        assert peak == 2
        assert len(records) == len(cases)
        assert all(r["category"] == "ok" for r in records)
        assert all(r["schema_validated"] is False for r in records)
        assert "CANARY" not in json.dumps(records)
    asyncio.run(run())


@pytest.mark.parametrize("kind,expected", [
    ("exception", "backend_error"), ("array", "invalid_json_object"),
    ("malformed", "invalid_json_object"), ("no_mcp", "missing_or_unsafe_mcp"),
    ("unsafe_mcp", "missing_or_unsafe_mcp"), ("failed_mcp", "missing_or_unsafe_mcp"),
])
def test_failure_categories_are_safe(kind, expected):
    async def invoke(_):
        if kind == "exception":
            raise RuntimeError("ACCOUNT_TOKEN_CANARY")
        result = good_result()
        if kind == "array":
            result.text = "[]"
        if kind == "malformed":
            result.text = "RAW_CANARY"
        if kind == "no_mcp":
            result.mcp_calls = []
        if kind == "unsafe_mcp":
            result.mcp_calls[0].tool = "submit_order_CANARY"
        if kind == "failed_mcp":
            result.mcp_calls[0].error = "SECRET_CANARY"
        return result
    records = asyncio.run(probe._schedule_diagnostic([make_case()], invoke=invoke))
    assert records[0]["category"] == expected
    assert "CANARY" not in json.dumps(records)


def test_no_production_import_or_environment_mutation():
    source = Path(probe.__file__).read_text()
    assert "stock_tracking_enhanced_agent" not in source
    assert "app.run(" not in source
    assert "os.environ[" not in source  # Reads only through .get; no mutation.
    assert "sqlite3.connect" not in source
    assert "send_message(" not in source


def test_execute_uses_validated_adapter_and_reports_safe_outcome(setup_files, monkeypatch, capsys):
    root, home, fixture = setup_files
    fixture.write_text(json.dumps({"version": 1, "cases": [make_case("US")]}))
    async def invoke(case):
        result = good_result()
        result.mcp_calls = [SimpleNamespace(server=server, tool=tool, status="completed", error=None) for server, tool in (
            ("time", "get_current_time"), ("sqlite", "read_query"), ("perplexity", "perplexity_ask"), ("yahoo_finance", "get_stock_info"))]
        return result
    monkeypatch.setattr(probe, "_execution_adapter", lambda args, root: invoke)
    assert probe.main(["--fixture", str(fixture), "--run-root", str(root),
                       "--codex-home", str(home), "--execute"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["execution_enabled"] is True
    assert report["parity"] == "incomplete"
    assert report["outcomes"][0]["category"] == "ok"
    assert report["outcomes"][0]["successful_mcp_calls_by_server"] == {"time": 1, "sqlite": 1, "perplexity": 1, "yahoo_finance": 1}
    assert "CANARY" not in json.dumps(report)


def test_kr_real_read_execution_is_pending_not_silently_launched(setup_files, monkeypatch, capsys):
    root, home, fixture = setup_files
    async def forbidden(case):
        pytest.fail("KR must not launch before auth boundary review")
    monkeypatch.setattr(probe, "_execution_adapter", lambda args, root: forbidden)
    assert probe.main(["--fixture", str(fixture), "--run-root", str(root), "--codex-home", str(home), "--execute"]) == 2
    assert json.loads(capsys.readouterr().out)["category"] == "kr_auth_boundary_pending"


def test_partial_real_read_coverage_is_not_reported_as_success(setup_files, monkeypatch, capsys):
    root, home, fixture = setup_files
    fixture.write_text(json.dumps({"version": 1, "cases": [make_case("US")]}))
    async def time_only(case):
        return good_result('{"missing":["PRIVATE_CANARY"]}')
    monkeypatch.setattr(probe, "_execution_adapter", lambda args, root: time_only)
    assert probe.main(["--fixture", str(fixture), "--run-root", str(root), "--codex-home", str(home), "--execute"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["outcomes"][0]["category"] == "incomplete_read_tools"
    assert report["outcomes"][0]["missing_read_servers"] == ["perplexity", "sqlite", "yahoo_finance"]
    assert report["outcomes"][0]["model_declared_missing_count"] == 1
    assert "CANARY" not in json.dumps(report)


def test_per_server_counts_are_safe_transport_counts_not_data_accuracy():
    async def invoke(case):
        result = good_result()
        result.mcp_calls = [SimpleNamespace(server=server, tool=tool, status="completed", error=error,
                                            arguments={"token": "PRIVATE_CANARY"}) for server, tool, error in (
            ("time", "get_current_time", None), ("time", "get_current_time", None),
            ("yahoo_finance", "get_stock_info", None), ("perplexity", "perplexity_ask", "PRIVATE_CANARY"))]
        return result
    record, = asyncio.run(probe._schedule_diagnostic([make_case("US")], invoke=invoke))
    assert record["successful_mcp_calls_by_server"] == {"time": 2, "yahoo_finance": 1}
    assert record["failed_mcp_calls"] == 1
    assert record["mcp_count_basis"] == "transport_completed_status_only_not_financial_data_accuracy"
    assert "CANARY" not in json.dumps(record)


def test_kr_public_opt_in_preserves_no_vendor_parity_label(setup_files, monkeypatch, capsys):
    root, home, fixture = setup_files
    async def public(case):
        result = good_result()
        result.mcp_calls = [SimpleNamespace(server=server, tool=tool, status="completed", error=None) for server, tool in (
            ("time", "get_current_time"), ("sqlite", "read_query"), ("perplexity", "perplexity_ask"), ("kospi_kosdaq", "get_stock_ohlcv"))]
        return result
    public.kr_public_diagnostic = True
    monkeypatch.setattr(probe, "_execution_adapter", lambda args, root: public)
    assert probe.main(["--fixture", str(fixture), "--run-root", str(root), "--codex-home", str(home), "--execute"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["kr_profile"] == "KR_PUBLIC_DIAGNOSTIC"
    assert report["kr_production_vendor_parity"] is False
    assert "market_data_source_order_deviation_no_kis" in report["cases"][0]["deviations"]


def test_actual_adapter_pins_wrapper_root_and_binary(setup_files, monkeypatch):
    from tools import codex_probe_sandbox as sandbox
    root, home, _ = setup_files
    monkeypatch.setattr(probe.sys, "platform", "linux")
    args = SimpleNamespace(sandbox_wrapper=root / "wrapper", codex_home=home)
    monkeypatch.setenv("PRISM_PROBE_ROOT", "/wrong")
    with pytest.raises(probe.ProbeRejected, match="sandbox_root_mismatch"):
        probe._execution_adapter(args, root)
    monkeypatch.setenv("PRISM_PROBE_ROOT", str(root))
    monkeypatch.setenv("PRISM_PROBE_CODEX_BINARY", "/wrong")
    with pytest.raises(probe.ProbeRejected, match="sandbox_binary_mismatch"):
        probe._execution_adapter(args, root)
    monkeypatch.setenv("PRISM_PROBE_CODEX_BINARY", sandbox.TRUSTED_CODEX_BINARY)
    python = root / "python3.11"
    python.write_text("trusted interpreter fixture")
    python.chmod(0o700)
    (root / "python3").symlink_to(python)
    monkeypatch.setattr(sandbox, "TRUSTED_HOST_PYTHON", str(python))
    monkeypatch.setenv("PATH", str(root))
    # The fake host interpreter alias is outside the stage under real deployment.
    monkeypatch.setattr(sandbox, "validate_stage", lambda root: root)
    (root / ".isolated-codex-probe").write_text("fixture")
    args.sandbox_wrapper.write_text("UNTRUSTED")
    args.sandbox_wrapper.chmod(0o700)
    with pytest.raises(probe.ProbeRejected, match="untrusted_wrapper"):
        probe._execution_adapter(args, root)


def test_smoke_profile_is_explicit_and_never_parity(setup_files, capsys):
    root, home, fixture = setup_files
    profile = home / "kr_trading.config.toml"
    text = profile.read_text()
    sections = text.split("[mcp_servers.")
    profile.write_text("".join("[mcp_servers." + s for s in sections[1:] if s.startswith(("time]", "sqlite]"))))
    with pytest.raises(probe.ProbeRejected, match="unsafe_servers"):
        probe.validate_home(home, root, {"KR"})
    probe.validate_home(home, root, {"KR"}, smoke=True)
    assert probe.main(["--fixture", str(fixture), "--run-root", str(root),
                       "--codex-home", str(home), "--smoke"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "synthetic_smoke"
    assert result["parity"] == "not_parity"


def test_account_backed_capabilities_must_be_explicitly_disabled(setup_files):
    root, home, _ = setup_files
    config = home / "config.toml"
    config.write_text(config.read_text().replace("\napps=false\n", "\n"))
    with pytest.raises(probe.ProbeRejected, match="capability_disable_missing"):
        probe.validate_home(home, root, {"KR"})


def test_backend_stage_logging_is_scoped_and_allowlisted(capsys):
    import logging
    logger = logging.getLogger("cores.llm.codex_oauth_fast_backend")
    old_handlers, old_level, old_propagate = logger.handlers, logger.level, logger.propagate
    root_level = logging.getLogger().level
    try:
        probe._configure_backend_logging()
        logger.info("ACCOUNT_SECRET_CANARY")
        logger.info("[CODEX_FAST] category=first_event model=gpt-6-astra effort=medium profile=kr_trading "
                    "timeout_s=90 elapsed_s=0.5 rc=None request_id=%s last_stage=first_event "
                    "mcp_started=0 mcp_completed=0 mcp_errors=0 mcp_pending=0 server=none tool=none tool_s=-1 "
                    "stdin_total_bytes=200000 stdin_sent_bytes=200000 stdin_closed=1",
                    "a" * 32)
        output = capsys.readouterr()
        assert "first_event" in output.err
        assert "CANARY" not in output.err
        assert output.out == ""
        assert logging.getLogger().level == root_level
    finally:
        logger.handlers, logger.level, logger.propagate = old_handlers, old_level, old_propagate


@pytest.mark.parametrize("category", ["agent_message", "unsupported_platform", "incomplete_stdin", "timeout", "success"])
def test_new_backend_stdin_telemetry_is_preserved(category, capsys):
    import logging
    logger = logging.getLogger("cores.llm.codex_oauth_fast_backend")
    old = logger.handlers, logger.level, logger.propagate
    try:
        probe._configure_backend_logging()
        message = (f"[CODEX_FAST] category={category} model=gpt-6-astra effort=high profile=us_trading "
                   "timeout_s=240 elapsed_s=0.5 rc=None request_id=" + "b" * 32 + " last_stage=agent_message "
                   "mcp_started=0 mcp_completed=0 mcp_errors=0 mcp_pending=0 server=none tool=none tool_s=-1 "
                   "stdin_total_bytes=240001 stdin_sent_bytes=8192 stdin_closed=1")
        logger.info(message)
        captured = capsys.readouterr()
        assert captured.err.strip() == message
        assert captured.out == ""
    finally:
        logger.handlers, logger.level, logger.propagate = old


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace("stdin_total_bytes=200000", "stdin_total_bytes=PRIVATE_CANARY"),
    lambda s: s.replace("stdin_sent_bytes=8192", "stdin_sent_bytes=-1"),
    lambda s: s.replace("stdin_sent_bytes=8192", "stdin_sent_bytes=200001"),
    lambda s: s.replace("stdin_sent_bytes=8192", "stdin_sent_bytes=nan"),
    lambda s: s.replace("stdin_closed=0", "stdin_closed=2"),
    lambda s: s.replace("stdin_closed=0", "stdin_closed=PRIVATE_CANARY"),
    lambda s: s + " prompt=PRIVATE_CANARY",
    lambda s: s.replace("category=timeout", "category=PRIVATE_CANARY category=timeout"),
    lambda s: s.replace("stdin_total_bytes=200000", "stdin_total_bytes=PRIVATE_CANARY stdin_total_bytes=200000"),
])
def test_stdin_log_fields_reject_tainted_or_invalid_records(mutation, capsys):
    import logging
    logger = logging.getLogger("cores.llm.codex_oauth_fast_backend")
    old = logger.handlers, logger.level, logger.propagate
    try:
        probe._configure_backend_logging()
        message = ("[CODEX_FAST] category=timeout model=gpt-6-astra effort=high profile=us_trading "
                   "timeout_s=240 elapsed_s=0.5 rc=None request_id=" + "b" * 32 + " last_stage=start "
                   "mcp_started=0 mcp_completed=0 mcp_errors=0 mcp_pending=0 server=none tool=none tool_s=-1 "
                   "stdin_total_bytes=200000 stdin_sent_bytes=8192 stdin_closed=0")
        logger.info(mutation(message))
        assert capsys.readouterr().err == ""
    finally:
        logger.handlers, logger.level, logger.propagate = old


def test_safe_logger_accepts_actual_fixed_backend_large_stdin_records(monkeypatch, capsys):
    import logging
    import sys
    from cores.llm import codex_oauth_fast_backend as backend

    logger = logging.getLogger(backend.__name__)
    old = logger.handlers, logger.level, logger.propagate
    script = ("import json,sys,time;time.sleep(.3);sys.stdin.buffer.read();"
              "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'{}'}}));"
              "print(json.dumps({'type':'turn.completed'}))")
    monkeypatch.setattr(backend, "_resolve_codex_executable", lambda _: sys.executable)
    monkeypatch.setattr(backend, "_command", lambda *args: [sys.executable, "-u", "-c", script])
    try:
        probe._configure_backend_logging()
        assert backend.generate_codex_fast(system_prompt="PRIVATE_CANARY", user_prompt="x" * 200000, timeout=1.5).text == "{}"
        captured = capsys.readouterr()
        assert "category=agent_message " in captured.err
        assert "category=model_final " in captured.err
        assert "category=success " in captured.err
        assert "stdin_closed=1" in captured.err
        assert "PRIVATE_CANARY" not in captured.err and captured.out == ""
    finally:
        logger.handlers, logger.level, logger.propagate = old


def test_prepare_exact_three_large_synthetic_cases_without_raw_output(setup_files, capsys):
    from tools import prepare_codex_large_stdin_fixture as prepare
    root, _, _ = setup_files
    (root / ".isolated-codex-probe").write_text("diagnostic-only-v1")
    (root / "smoke-fixture.json").write_text(json.dumps({"version": 1, "cases": [make_case("KR"), make_case("US")]}))
    assert prepare.main(["--run-root", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["user_bytes"] == [200000] * 3
    assert report["case_count"] == 3 and report["production_parity"] is False
    assert "CANARY" not in json.dumps(report)
    cases = probe.load_fixture(root / "large-stdin-3buy-fixture.json", root)
    assert [case["action"] for case in cases] == ["BUY"] * 3
    assert [case["market"] for case in cases] == ["US", "KR", "US"]
    assert len({case["user_sha256"] for case in cases}) == 3
    assert all("synthetic_report" in case["deviations"] for case in cases)
    before = (root / "large-stdin-3buy-fixture.json").read_bytes()
    assert prepare.main(["--run-root", str(root)]) == 2
    assert (root / "large-stdin-3buy-fixture.json").read_bytes() == before


@pytest.mark.parametrize("size", [199999, 1048577, -1, True])
def test_large_fixture_budget_is_bounded(setup_files, size):
    from tools.prepare_codex_large_stdin_fixture import prepare
    with pytest.raises(probe.ProbeRejected, match="padding_budget"):
        prepare(setup_files[0], size)
