"""Fixture-only full-stage integration. No providers, models or real auth."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import codex_probe_mcp_bridge as bridge
from tools import codex_probe_sandbox as sandbox
from tools import prepare_codex_full_stage as full
from tools import prepare_codex_probe_stage as smoke
from tools.probe_codex_trading_runtime import load_fixture, validate_home


def test_fifo_node_input_fails_without_writer_or_leftover_copy(tmp_path):
    fifo = tmp_path / "node-fifo"
    os.mkfifo(fifo, 0o600)
    host = tmp_path / "private-host"
    host.mkdir(mode=0o700)
    code = """
import os, sys
from tools import codex_probe_mcp_bridge as bridge
bridge.HOST_PROVIDERS['perplexity']['executable'] = sys.argv[1]
original_open, opened = os.open, []
def tracked_open(path, *args, **kwargs):
    fd = original_open(path, *args, **kwargs)
    opened.append(fd)
    return fd
os.open = tracked_open
try:
    bridge.snapshot_node_runtime(sys.argv[2])
except bridge.BridgeError:
    for fd in opened:
        try:
            os.fstat(fd)
        except OSError:
            continue
        raise SystemExit(2)
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run([sys.executable, "-c", code, str(fifo), str(host)],
                            capture_output=True, timeout=3, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0
    assert not (host / "node-runtime").exists()


def test_private_node_snapshot_is_pinned_and_never_executes_source(tmp_path, monkeypatch):
    source = tmp_path / "source-node"
    source.write_bytes(b"\x7fELF-pinned-node-fixture")
    source.chmod(0o700)
    specs = copy.deepcopy(bridge.HOST_PROVIDERS)
    specs["perplexity"]["executable"] = str(source)
    specs["perplexity"]["executable_sha256"] = bridge.file_sha256(source)
    monkeypatch.setattr(bridge, "HOST_PROVIDERS", specs)
    original_stat = Path.stat
    def foreign_source_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == source:
            fields = list(value)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return value
    monkeypatch.setattr(Path, "stat", foreign_source_stat)
    with pytest.raises(bridge.BridgeError, match="unsafe_provider_source"):
        bridge.verified_provider_pins(("perplexity",))
    host = tmp_path / "private-host"
    host.mkdir(mode=0o700)
    target = bridge.snapshot_node_runtime(host)
    assert target == host / "node-runtime"
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o700
    assert bridge.verified_node_runtime(host) == target
    source.write_bytes(b"changed-original-must-not-be-used")
    assert bridge.verified_node_runtime(host) == target
    target.write_bytes(b"\x7fELF-tampered")
    with pytest.raises(bridge.BridgeError):
        bridge.verified_node_runtime(host)


@pytest.mark.parametrize("bad", ["digest", "elf", "symlink", "writable"])
def test_node_snapshot_rejects_unsafe_read_input(tmp_path, monkeypatch, bad):
    source = tmp_path / "source-node"
    source.write_bytes(b"not-elf" if bad == "elf" else b"\x7fELF-fixture")
    source.chmod(0o722 if bad == "writable" else 0o700)
    specs = copy.deepcopy(bridge.HOST_PROVIDERS)
    specs["perplexity"]["executable_sha256"] = "0" * 64 if bad == "digest" else bridge.file_sha256(source)
    if bad == "symlink":
        link = tmp_path / "linked-node"
        link.symlink_to(source)
        source = link
    specs["perplexity"]["executable"] = str(source)
    monkeypatch.setattr(bridge, "HOST_PROVIDERS", specs)
    host = tmp_path / "private-host"
    host.mkdir(mode=0o700)
    with pytest.raises(bridge.BridgeError):
        bridge.snapshot_node_runtime(host)
    assert not (host / "node-runtime").exists()


@pytest.fixture
def prepared(tmp_path, monkeypatch, request):
    root, host = tmp_path / "new-model", tmp_path / "new-host"
    repo = tmp_path / "repo"
    for name in ("cores/llm/time_mcp_server.py", "sqlite/src/mcp_server_sqlite/__init__.py"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture only\n")
    auth = tmp_path / "auth"
    auth.write_text("PRIVATE_MODEL_AUTH_CANARY")
    monkeypatch.setattr(smoke, "stage_runtime", lambda root, names: {"mcp": "fixture"})
    monkeypatch.setattr(bridge, "verified_provider_pins", lambda **kw: copy.deepcopy(bridge.HOST_PROVIDERS))
    def fixture_runtime(host):
        path = host / "node-runtime"
        path.write_bytes(b"\x7fELF-test-runtime-never-executed")
        path.chmod(0o700)
    monkeypatch.setattr(bridge, "snapshot_node_runtime", fixture_runtime)
    result = full.prepare(root, host, repo, auth, as_of_date="2026-09-10", kr_public_diagnostic=getattr(request, "param", False))
    # The staged host module has its own fixed constants; pin harmless fixture bytes.
    host_code = host / "read_bridge_host.py"
    digest = bridge.file_sha256(host / "node-runtime")
    host_code.write_text(host_code.read_text().replace(bridge.HOST_PROVIDERS["perplexity"]["executable_sha256"], digest))
    manifest_path = host / "host-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["providers"]["perplexity"]["executable_sha256"] = digest
    manifest["bridge_sha256"] = bridge.file_sha256(host_code)
    manifest_path.write_text(json.dumps(manifest))
    return root, host, repo, auth, result


def test_full_stage_keeps_host_config_and_secrets_outside_model_root(prepared):
    root, host, _, _, result = prepared
    assert not host.is_relative_to(root)
    assert result["production_database_opened"] is False and result["production_parity"] is False
    assert "CANARY" not in json.dumps(result)
    assert (host / "host-manifest.json").stat().st_mode & 0o777 == 0o600
    assert (host / "read_bridge_host.py").stat().st_mode & 0o777 == 0o600
    client = (root / "mcp/read_bridge_client.py").read_text()
    assert "HOST_PROVIDERS" not in client and "ReadMcpBridge" not in client
    assert "/root/.npm" not in client and "/root/prism-insight" not in client
    compile(client, "read_bridge_client.py", "exec")
    validate_home(root / "home", root, {"KR", "US"})
    cases = load_fixture(root / "read-tool-fixture.json", root)
    assert len(cases) == 1 and {c["market"] for c in cases} == {"US"}
    assert result["kr_status"] == "auth_boundary_pending"
    assert all("diagnostic_backend_only" in c["deviations"] for c in cases)
    for path in (root / "home").glob("*.toml"):
        assert "PERPLEXITY_API_KEY" not in path.read_text()
        assert "/root/.cache/uv" not in path.read_text()
    module, manifest = sandbox.load_host_manifest(host / "host-manifest.json", root)
    assert manifest["providers"] == module.HOST_PROVIDERS
    assert (root / "wrapper.py").read_bytes() == Path(sandbox.__file__).read_bytes()


@pytest.mark.parametrize("tamper", ["client", "host_code", "manifest", "inside_model", "public_permissions"])
def test_host_manifest_or_bridge_tampering_fails_closed(prepared, tamper):
    root, host, _, _, _ = prepared
    path = host / "host-manifest.json"
    if tamper == "client":
        (root / "mcp/read_bridge_client.py").write_text("UNTRUSTED")
    elif tamper == "host_code":
        (host / "read_bridge_host.py").write_text("UNTRUSTED")
    elif tamper == "manifest":
        manifest = json.loads(path.read_text())
        manifest["providers"]["perplexity"]["executable"] = "/bin/sh"
        path.write_text(json.dumps(manifest))
    elif tamper == "inside_model":
        path = root / "host-manifest.json"
        path.write_text((host / "host-manifest.json").read_text())
        path.chmod(0o600)
    else:
        path.chmod(0o644)
    with pytest.raises(sandbox.BoundaryError):
        sandbox.load_host_manifest(path, root)


def test_never_modify_existing_v4_or_host_directory(prepared):
    root, host, repo, auth, _ = prepared
    before = (root / "home/auth.json").read_bytes()
    with pytest.raises(ValueError, match="new_separate"):
        full.prepare(root, host, repo, auth, as_of_date="2026-09-10")
    assert (root / "home/auth.json").read_bytes() == before


def test_provider_pins_and_minimal_host_environment(tmp_path, monkeypatch):
    specs = {}
    for name in bridge.HOST_PROVIDERS:
        code = tmp_path / name
        code.mkdir()
        executable = code / "runtime"
        executable.write_bytes(b"fixed-runtime-fixture")
        executable.chmod(0o700)
        (code / "server.py").write_text("# fixed read-provider fixture")
        specs[name] = {"executable": str(executable), "executable_sha256": bridge.file_sha256(executable),
                       "code_root": str(code), "entrypoint": "server.py", "code_tree_sha256": bridge.code_tree_sha256(code)}
    monkeypatch.setattr(bridge, "HOST_PROVIDERS", specs)
    assert bridge.verified_provider_pins() == specs
    configs = bridge.host_provider_configs(specs, ("perplexity", "yahoo_finance"),
                                          {"PERPLEXITY_API_KEY": "HOST_ONLY_CANARY", "BROKER_SECRET": "NEVER_INHERIT", "HTTPS_PROXY": "PRIVATE_PROXY"})
    assert configs["perplexity"]["env"]["PERPLEXITY_API_KEY"] == "HOST_ONLY_CANARY"
    assert "PERPLEXITY_API_KEY" not in configs["yahoo_finance"]["env"]
    assert "NEVER_INHERIT" not in repr(configs) and "PRIVATE_PROXY" not in repr(configs)
    assert all("npx" not in config["argv"] and "uv" not in config["argv"] for config in configs.values())
    with pytest.raises(bridge.BridgeError, match="credential"):
        bridge.host_provider_configs(specs, ("perplexity",), {})
    with pytest.raises(bridge.BridgeError, match="kr_auth_boundary_pending"):
        bridge.host_provider_configs(specs, ("kospi_kosdaq",), {})
    with pytest.raises(bridge.BridgeError, match="kr_public_diagnostic_retired_kis_only"):
        bridge.host_provider_configs(specs, ("kospi_kosdaq",), {
            "PRISM_MARKET_DATA_SOURCES": "kis", "PRISM_REPORT_DATA_SOURCES": "kis"}, kr_profile=bridge.KR_PUBLIC_DIAGNOSTIC)
    (tmp_path / "yahoo_finance/server.py").write_text("# modified provider")
    with pytest.raises(bridge.BridgeError, match="pin_mismatch"):
        bridge.verified_provider_pins(("yahoo_finance",))


def test_model_command_mounts_only_socket_not_host_manifest(tmp_path):
    address = tmp_path.parent / "m.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(address))
        address.chmod(0o600)
        try:
            command = sandbox.sandbox_command(tmp_path / "model", tmp_path / "codex", tmp_path / "gateway",
                                              tmp_path / "wrapper", ["exec"], read_sockets={"perplexity": address})
            index = command.index("/mcp-perplexity.sock")
            assert command[index - 2:index + 1] == ["--ro-bind", str(address), "/mcp-perplexity.sock"]
            assert "PRISM_PROBE_HOST_MANIFEST" not in command and "PERPLEXITY_API_KEY" not in command
            with pytest.raises(sandbox.BoundaryError, match="unsafe_read_socket"):
                sandbox.sandbox_command(tmp_path / "model", tmp_path / "codex", tmp_path / "gateway", tmp_path / "wrapper", ["exec"], read_sockets={"sqlite": address})
        finally:
            address.unlink()


@pytest.mark.parametrize("failure", [False, True])
def test_wrapper_closes_read_bridges_after_model_exit_or_launch_error(tmp_path, monkeypatch, failure):
    root = tmp_path / "model"
    root.mkdir(mode=0o700)
    (root / ".isolated-codex-probe").write_text("fixture")
    binary = tmp_path / "codex"
    binary.write_bytes(b"\x7fELF-fixture")
    binary.chmod(0o700)
    (tmp_path / "codex-code-mode-host").write_bytes(b"\x7fELF-fixture")
    (tmp_path / "codex-code-mode-host").chmod(0o700)
    monkeypatch.setattr(sandbox, "TRUSTED_CODEX_BINARY", str(binary))
    monkeypatch.setenv("PRISM_PROBE_HOST_MANIFEST", str(tmp_path / "private-host/host-manifest.json"))
    calls = []

    class Gateway:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def serve_forever(self): pass
        def shutdown(self): calls.append("gateway_closed")

    class ReadBridge:
        def __init__(self, address, *, server_name, **config):
            self.socket_path, self.name = address, server_name
            assert config["env"] == {"PERPLEXITY_API_KEY": "HOST_ONLY_CANARY"}
        def __enter__(self):
            calls.append("open:" + self.name)
            return self
        def __exit__(self, *a): calls.append("close:" + self.name)

    def configs(pins, names, environ, *, kr_profile=None, host_runtime_root=None):
        assert kr_profile is None
        assert host_runtime_root == tmp_path / "private-host"
        assert names == ("perplexity", "yahoo_finance")
        return {name: {"argv": ["/fixed/provider"], "env": {"PERPLEXITY_API_KEY": "HOST_ONLY_CANARY"}, "cwd": "/tmp"} for name in names}

    module = SimpleNamespace(ReadMcpBridge=ReadBridge, host_provider_configs=configs)
    monkeypatch.setattr(sandbox, "load_host_manifest", lambda *a: (module, {"providers": {}}))
    monkeypatch.setattr(sandbox, "Gateway", Gateway)

    def model_command(*a, read_sockets):
        assert set(read_sockets) == {"perplexity", "yahoo_finance"}
        return ["MODEL_NAMESPACE_WITH_SOCKET_MOUNTS_ONLY"]

    monkeypatch.setattr(sandbox, "sandbox_command", model_command)

    def popen(command, **kwargs):
        assert kwargs == {"env": {}, "close_fds": True}
        assert "CANARY" not in repr(command)
        if failure:
            raise OSError("launch failed")
        return SimpleNamespace(wait=lambda: 0, poll=lambda: 0)

    monkeypatch.setattr(sandbox.subprocess, "Popen", popen)
    if failure:
        with pytest.raises(OSError):
            sandbox.launch(root, binary, ["exec", "--profile", "us_trading"])
    else:
        assert sandbox.launch(root, binary, ["exec", "--profile", "us_trading"]) == 0
    assert calls.count("close:perplexity") == calls.count("close:yahoo_finance") == 1
    assert "gateway_closed" in calls


def test_retired_kr_public_stage_rejected_before_auth_read_or_staging(tmp_path, monkeypatch):
    root, host = tmp_path / "new-model", tmp_path / "new-host"
    monkeypatch.setattr(bridge, "verified_provider_pins", lambda **kw: pytest.fail("must not inspect providers"))
    monkeypatch.setattr(smoke, "prepare", lambda *a: pytest.fail("must not read auth or create stage"))
    with pytest.raises(ValueError, match="kr_public_diagnostic_retired_kis_only"):
        full.prepare(root, host, tmp_path, tmp_path / "absent-auth", as_of_date="2026-09-10", kr_public_diagnostic=True)
    assert not root.exists() and not host.exists()


@pytest.mark.parametrize("order", ["fdr,naver", "kis", "kis,krx", ""])
def test_retired_public_chain_never_constructs_kis_or_starts_provider(tmp_path, monkeypatch, order):
    import cores.market_data as market
    monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", order)
    monkeypatch.setenv("PRISM_REPORT_DATA_SOURCES", order)
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("provider construction, authentication or launch forbidden")

    monkeypatch.setattr(market.KisSource, "__init__", forbidden)
    monkeypatch.setattr(market.KisSource, "_trading", forbidden)
    monkeypatch.setattr(bridge, "verified_provider_pins", forbidden)
    monkeypatch.setattr(bridge.subprocess, "Popen", forbidden)
    monkeypatch.setattr(bridge.socket, "socket", forbidden)
    with pytest.raises(bridge.BridgeError, match="kr_public_diagnostic_retired_kis_only"):
        bridge.host_provider_configs(bridge.HOST_PROVIDERS, ("kospi_kosdaq",), {}, kr_profile=bridge.KR_PUBLIC_DIAGNOSTIC)
    # A saved manifest cannot bypass config validation by constructing the bridge directly.
    with pytest.raises(bridge.BridgeError, match="kr_public_diagnostic_retired_kis_only"):
        bridge.ReadMcpBridge(tmp_path / "unused.sock", server_name="kospi_kosdaq", argv=["/fixed/provider"], cwd="/tmp",
                             env={"PRISM_MARKET_DATA_SOURCES": order, "PRISM_REPORT_DATA_SOURCES": order},
                             source_profile=bridge.KR_PUBLIC_DIAGNOSTIC)
    assert forbidden_calls == []


def test_public_kr_tool_result_cannot_omit_approximation_and_source_warning():
    session = bridge._Session(None, SimpleNamespace(server_name="kospi_kosdaq", source_profile=bridge.KR_PUBLIC_DIAGNOSTIC))
    session.pending[1] = {"id": "client", "method": "tools/call", "at": 0}
    session.server_line(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": '{"MarketCap":100}'}]}}).encode())
    result = json.loads(bytes(session.to_client.parts[0]))["result"]
    assert "approximate" in result["content"][0]["text"]
    assert result["structuredContent"]["__probe_source"]["profile"] == bridge.KR_PUBLIC_DIAGNOSTIC
    assert "not_production_vendor_parity" in result["structuredContent"]["__probe_source"]["limitations"]


def test_public_profile_cannot_be_reenabled_with_mixed_source_orders(tmp_path):
    with pytest.raises(bridge.BridgeError, match="kr_public_diagnostic_retired_kis_only"):
        bridge.ReadMcpBridge(tmp_path / "unused.sock", server_name="kospi_kosdaq", argv=["/fixed/provider"], cwd="/tmp",
                             env={"PRISM_MARKET_DATA_SOURCES": "fdr,naver", "PRISM_REPORT_DATA_SOURCES": "kis"},
                             source_profile=bridge.KR_PUBLIC_DIAGNOSTIC)


@pytest.mark.parametrize("which", ["model", "host"])
def test_parent_traversal_is_rejected_before_staging_writes(tmp_path, monkeypatch, which):
    outside = tmp_path / "outside"
    outside.mkdir()
    root, host = tmp_path / "model", tmp_path / "host"
    if which == "model":
        root = outside / ".." / "model"
    else:
        host = outside / ".." / "model" / "hidden-host"
    monkeypatch.setattr(bridge, "verified_provider_pins", lambda: copy.deepcopy(bridge.HOST_PROVIDERS))
    monkeypatch.setattr(smoke, "prepare", lambda *a: pytest.fail("must reject traversal before any staging write"))
    with pytest.raises(ValueError, match="parent_traversal"):
        full.prepare(root, host, tmp_path, tmp_path / "unused-auth", as_of_date="2026-09-10")
    assert not (tmp_path / "model").exists()


def test_inside_model_host_manifest_cannot_escape_through_parent_alias(prepared):
    root, host, _, _, _ = prepared
    hidden = root / "hidden-host"
    hidden.mkdir(mode=0o700)
    for name in ("host-manifest.json", "read_bridge_host.py"):
        (hidden / name).write_bytes((host / name).read_bytes())
        (hidden / name).chmod(0o600)
    alias = host / ".." / root.name / "hidden-host" / "host-manifest.json"
    with pytest.raises(sandbox.BoundaryError, match="parent_traversal"):
        sandbox.load_host_manifest(alias, root)


def test_model_stage_parent_alias_is_rejected(prepared):
    root, host, _, _, _ = prepared
    alias = host / ".." / root.name
    with pytest.raises(sandbox.BoundaryError, match="parent_traversal"):
        sandbox.validate_stage(alias)
    with pytest.raises(sandbox.BoundaryError, match="parent_traversal"):
        sandbox._private_host_file(host / "host-manifest.json", alias)
