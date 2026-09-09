"""Boundary tests use only local sockets and fixture files, never models."""
import os
from pathlib import Path
import socket
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from tools import codex_probe_sandbox as sandbox


@pytest.fixture
def short_root():
    with tempfile.TemporaryDirectory(prefix="probe-", dir="/tmp") as directory:
        yield Path(directory).resolve()


@pytest.mark.parametrize("authority", [
    "localhost:443", "127.0.0.1:443", "169.254.169.254:443", "10.0.0.1:443",
    "chatgpt.com:80", "chatgpt.com.evil.test:443", "user@chatgpt.com:443",
    "chatgpt.com.:443", "CHATGPT.COM:443", "[::1]:443", "example.com:443",
])
def test_destination_denylist_cannot_bypass_allowlist(authority):
    with pytest.raises(sandbox.BoundaryError):
        sandbox.connect_target(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())


def test_known_model_authority_allowed():
    assert sandbox.connect_target(b"CONNECT chatgpt.com:443 HTTP/1.1\r\n\r\n") == "chatgpt.com"


def test_wrapper_uses_pinned_isolated_host_interpreter():
    assert Path(sandbox.__file__).read_text().splitlines()[0] == "#!" + sandbox.TRUSTED_HOST_PYTHON + " -I"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_dns_rebinding_or_private_answers_fail_closed(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ])
    with pytest.raises(sandbox.BoundaryError, match="nonpublic_destination"):
        sandbox.public_addresses("chatgpt.com")


def test_numeric_public_address_is_retained(monkeypatch):
    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answer)
    assert sandbox.public_addresses("chatgpt.com") == answer


def test_gateway_rejects_before_any_outbound_connection(short_root, monkeypatch):
    path = str(short_root / "g.sock")
    monkeypatch.setattr(sandbox, "public_addresses", lambda _: pytest.fail("must reject before DNS"))
    with sandbox.Gateway(path) as gateway:
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(path)
                client.sendall(b"CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n")
                assert client.recv(1024).startswith(b"HTTP/1.1 403")
        finally:
            gateway.shutdown()


def test_bwrap_mounts_and_namespaces_are_explicit(tmp_path):
    root = tmp_path / "staged"
    command = sandbox.sandbox_command(root, Path("/opt/codex"), Path("/tmp/g.sock"),
                                      Path("/src/runner.py"), ["exec", "--json", "-"])
    assert "--unshare-all" in command
    assert "--share-net" not in command
    assert "--die-with-parent" in command
    assert "--clearenv" not in command  # Unsupported by deployed bwrap 0.4.0.
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--proc") + 1] == "/proc"
    sources = [command[i + 1] for i, arg in enumerate(command) if arg == "--ro-bind"]
    assert not {"/", "/root", "/etc", "/run", "/home"}.intersection(sources)
    assert "--bind" not in command
    assert command[-3:] == ["exec", "--json", "-"]
    assert str(root / "runtime/bin/python3.11") in command
    assert command[command.index("PYTHONHOME") + 1] == str(root / "runtime")


def make_stage(tmp_path):
    root = tmp_path.resolve() / "stage"
    root.mkdir(mode=0o700)
    (root / ".isolated-codex-probe").write_text("fixture")
    return root


def test_stage_requires_marker(tmp_path):
    with pytest.raises(sandbox.BoundaryError, match="stage_marker_missing"):
        sandbox.validate_stage(tmp_path.resolve())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "world_writable", "socket"])
def test_stage_rejects_unsafe_inodes(short_root, kind):
    root = make_stage(short_root)
    source = root / "data"
    source.write_text("fixture")
    sock = None
    if kind == "symlink":
        (root / "alias").symlink_to(source)
    elif kind == "hardlink":
        os.link(source, root / "alias")
    elif kind == "world_writable":
        source.chmod(0o666)
    else:
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(root / "socket"))
    try:
        with pytest.raises(sandbox.BoundaryError):
            sandbox.validate_stage(root)
    finally:
        if sock:
            sock.close()


def test_rhel_ca_bundle_is_bound_as_a_file(monkeypatch, tmp_path):
    rhel = "/etc/pki/tls/certs/ca-bundle.crt"
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == rhel)
    command = sandbox.sandbox_command(tmp_path, tmp_path / "binary", tmp_path / "socket",
                                      tmp_path / "runner", ["exec"])
    source = str(Path(rhel).resolve())
    index = command.index(source)
    assert command[index - 1:index + 2] == ["--ro-bind", source, "/ca-bundle.crt"]
    assert command[command.index("SSL_CERT_FILE") + 1] == "/ca-bundle.crt"


def test_valid_stage_is_read_only_input(tmp_path):
    root = make_stage(tmp_path)
    assert sandbox.validate_stage(root) == root


def test_launch_clears_inherited_environment_without_new_bwrap_flag(tmp_path, monkeypatch):
    root = make_stage(tmp_path)
    binary = root / "codex-elf-fixture"
    binary.write_bytes(b"\x7fELFfixture")
    binary.chmod(0o700)
    host = root / "codex-code-mode-host"
    host.write_bytes(b"\x7fELFfixture-host")
    host.chmod(0o700)
    monkeypatch.setattr(sandbox, "TRUSTED_CODEX_BINARY", str(binary))
    monkeypatch.setenv("PRODUCTION_ENV_CANARY", "PRIVATE_CANARY")
    class FakeGateway:
        def __init__(self, _):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def serve_forever(self):
            pass
        def shutdown(self):
            pass
    class FakeChild:
        def wait(self):
            return 0
        def poll(self):
            return 0
    def spawn(command, **kwargs):
        assert kwargs["env"] == {}
        assert kwargs["close_fds"] is True
        assert "--clearenv" not in command
        assert "PRODUCTION_ENV_CANARY" not in str(command)
        return FakeChild()
    monkeypatch.setattr(sandbox, "Gateway", FakeGateway)
    monkeypatch.setattr(sandbox.subprocess, "Popen", spawn)
    assert sandbox.launch(root, binary, ["exec"]) == 0


def test_missing_code_host_cannot_launch(tmp_path, monkeypatch):
    root = make_stage(tmp_path)
    binary = root / "codex"
    binary.write_bytes(b"\x7fELFfixture")
    binary.chmod(0o700)
    monkeypatch.setattr(sandbox, "TRUSTED_CODEX_BINARY", str(binary))
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(sandbox.BoundaryError, match="unsafe_code_mode_host"):
        sandbox.launch(root, binary, ["exec"])


def test_safe_failure_does_not_emit_paths_or_environment(monkeypatch, capsys):
    monkeypatch.setenv("PRISM_PROBE_ROOT", "/SECRET_ACCOUNT_CANARY")
    monkeypatch.setenv("PRISM_PROBE_CODEX_BINARY", "/SECRET_TOKEN_CANARY")
    assert sandbox.main(["exec"]) == 2
    output = capsys.readouterr()
    assert "CANARY" not in output.err + output.out
    assert output.err.strip() == "probe_sandbox_boundary_failed"


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None,
                    reason="Linux bwrap namespace test; no model or external network")
def test_linux_namespace_denies_host_files_network_and_reaps_on_cancel(tmp_path, monkeypatch):
    from cores.llm.codex_oauth_fast_backend import _terminate_owned_process
    secret = tmp_path / "host-only-canary"
    secret.write_text("PRIVATE_CANARY")
    monkeypatch.setenv("PRODUCTION_ENV_CANARY", "PRIVATE_CANARY")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        code = (
            "import os,socket,signal,time; "
            "assert 'PRODUCTION_ENV_CANARY' not in os.environ; "
            "assert not os.path.exists(%r); " % str(secret) +
            "s=socket.socket(); s.settimeout(0.5); "
            "assert s.connect_ex(('127.0.0.1',%d)) != 0; " % listener.getsockname()[1] +
            "assert len([n for n in os.listdir('/proc') if n.isdigit()]) <= 3; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('isolated_ready',flush=True); time.sleep(60)"
        )
        command = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent",
                   "--cap-drop", "ALL", "--proc", "/proc",
                   "--dev", "/dev", "--tmpfs", "/tmp"]
        for runtime in ("/usr", "/lib", "/lib64"):
            if Path(runtime).exists():
                command += ["--ro-bind", runtime, runtime]
        interpreter = Path("/usr/bin/python3").resolve(strict=True)
        assert interpreter.is_relative_to(Path("/usr"))
        command += ["--", str(interpreter), "-c", code]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True, close_fds=True, env={})
        try:
            assert select.select([process.stdout], [], [], 5)[0], "namespace startup exceeded bound"
            assert process.stdout.readline().strip() == "isolated_ready"
            started = time.monotonic()
            _terminate_owned_process(process)
            assert process.poll() is not None
            assert time.monotonic() - started < 3
            assert secret.read_text() == "PRIVATE_CANARY"
        finally:
            if process.poll() is None:
                _terminate_owned_process(process)
