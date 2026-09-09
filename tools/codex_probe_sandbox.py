#!/root/.pyenv/versions/3.11.11/bin/python3.11 -I
"""Linux-only isolated Codex executable wrapper, not a production launcher.

Set PRISM_PROBE_ROOT and PRISM_PROBE_CODEX_BINARY on the wrapper's host process.
The root must be a dedicated staged directory containing no symlinks, hardlinks,
devices or sockets. It is mounted read-only at its original absolute path. Only
ephemeral /tmp and HOME are writable. Stage auth as a private regular file; never
mount a production home, repository, database, or credential directory.

Network is a new namespace. A host UNIX gateway accepts CONNECT to explicitly
listed public service domains on port 443 only. It resolves once and connects to the
validated public IP, preventing private-service access and DNS rebinding. This
does not prohibit transmission of supplied fixture data to the model service.
Only the explicitly listed read-only research/market service domains are enabled.
"""
from __future__ import annotations

import ipaddress
import hashlib
import importlib.util
import json
import os
from contextlib import ExitStack
from pathlib import Path
import select
import signal
import socket
import socketserver
import stat
import subprocess  # nosec B404 - fixed, validated namespace execution without a shell
import sys
import tempfile
import threading

TRUSTED_CODEX_BINARY = "/opt/prism-codex-astra/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
TRUSTED_HOST_PYTHON = "/root/.pyenv/versions/3.11.11/bin/python3.11"
DISABLED_FEATURES = frozenset({
    "shell_tool", "shell_snapshot", "shell_snapshot_v2", "shell_zsh_fork",
    "apps", "enable_mcp_apps", "hooks", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "in_app_browser", "image_generation",
    "view_image", "multi_agent", "multi_agent_v2", "code_mode",
    "code_mode_only", "code_mode_prewarm", "code_mode_interrupt", "goals", "artifact",
    "standalone_web_search", "web_search_cached", "web_search_request",
    "skill_mcp_dependency_install", "skill_search", "external_agent_memory_import",
})
DOMAINS = frozenset({"chatgpt.com", "auth.openai.com", "api.openai.com",
    "api.perplexity.ai", "query1.finance.yahoo.com", "query2.finance.yahoo.com",
    "fc.yahoo.com", "finance.yahoo.com"})
MAX_HEADER = 8192
IDLE_SECONDS = 120
MAX_CONNECTIONS = 16
_PRIVATE_TMPFS = "/tmp"  # nosec B108 - a new tmpfs inside the model namespace


class BoundaryError(ValueError):
    pass


class ParentLease:
    """Read-only inherited pipe; sole writer stays in the host supervisor."""
    def __init__(self, fd, parent_pid):
        import fcntl
        if (type(fd) is not int or fd < 3 or type(parent_pid) is not int or parent_pid <= 1
                or not stat.S_ISFIFO(os.fstat(fd).st_mode)
                or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY):
            raise BoundaryError("invalid_parent_lease")
        self.fd, self.parent_pid = fd, parent_pid
        self.check()

    def alive(self):
        # No writes are allowed by the protocol; readable means EOF or invalid
        # data, both fail closed. A pipe lease avoids PID-reuse assumptions.
        return os.getppid() == self.parent_pid and not select.select([self.fd], [], [], 0)[0]

    def check(self):
        if not self.alive():
            raise BoundaryError("parent_lease_lost")


def wait_owned_child(child, lease, stopped):
    """Only direct still-owned Popen handles; never signal arbitrary groups."""
    while True:
        if stopped.is_set() or not lease.alive():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
            return 2
        try:
            return child.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            continue


def validate_snapshot(path, expected_sha256, model_root, agent_root, host_root):
    path, model_root, agent_root, host_root = map(Path, (path, model_root, agent_root, host_root))
    if (any(".." in p.parts or not p.is_absolute() or p.resolve() != p for p in (path, model_root, agent_root, host_root))
            or path.parent != host_root or host_root.is_relative_to(agent_root) or host_root.is_relative_to(model_root)
            or agent_root.is_relative_to(host_root) or model_root.is_relative_to(host_root)):
        raise BoundaryError("unsafe_snapshot")
    path = _private_host_file(path, model_root)
    if (stat.S_IMODE(path.stat().st_mode) != 0o400 or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64 or path.stat().st_size > 256 * 1024 * 1024
            or any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))):
        raise BoundaryError("unsafe_snapshot")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        first = stream.read(1024 * 1024)
        if not first.startswith(b"SQLite format 3\0"):
            raise BoundaryError("unsafe_snapshot")
        digest.update(first)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise BoundaryError("snapshot_hash_mismatch")
    return path


def _private_host_file(path: Path, root: Path) -> Path:
    if any(".." in Path(candidate).parts for candidate in (path, root)):
        raise BoundaryError("parent_traversal_rejected")
    path = path.absolute()
    if (any(path.is_relative_to(exposed) for exposed in (root, Path("/usr"), Path("/lib"), Path("/lib64")))
            or any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()):
        raise BoundaryError("unsafe_host_manifest")
    info, directory = path.stat(), path.parent.stat()
    if info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077 or directory.st_uid != os.getuid() or directory.st_mode & 0o077:
        raise BoundaryError("unsafe_host_manifest")
    return path


def load_host_manifest(path: Path, root: Path):
    """Root-operator private manifest; never model-mounted or model-configurable."""
    try:
        path = _private_host_file(path, root)
        if path.name != "host-manifest.json":
            raise BoundaryError("invalid_host_manifest")
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise BoundaryError("invalid_host_manifest")
        manifest = json.loads(raw)
        expected = {"version", "stage_root", "bridge_sha256", "client_sha256", "providers", "kr_profile"}
        if not isinstance(manifest, dict) or set(manifest) != expected or manifest["version"] != 1 or manifest["stage_root"] != str(root):
            raise BoundaryError("invalid_host_manifest")
        if manifest["kr_profile"] not in {None, "KR_PUBLIC_DIAGNOSTIC"}:
            raise BoundaryError("invalid_host_manifest")
        bridge_file = _private_host_file(path.parent / "read_bridge_host.py", root)
        client_file = root / "mcp/read_bridge_client.py"
        if (hashlib.sha256(bridge_file.read_bytes()).hexdigest() != manifest["bridge_sha256"]
                or not client_file.is_file() or client_file.is_symlink()
                or hashlib.sha256(client_file.read_bytes()).hexdigest() != manifest["client_sha256"]):
            raise BoundaryError("bridge_code_mismatch")
        spec = importlib.util.spec_from_file_location("prism_probe_host_bridge", bridge_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if manifest["providers"] != module.HOST_PROVIDERS:
            raise BoundaryError("unapproved_provider_manifest")
        _private_host_file(path.parent / "node-runtime", root)
        module.verified_node_runtime(path.parent)
        if client_file.read_text() != module.client_source():
            raise BoundaryError("bridge_client_mismatch")
        return module, manifest
    except BoundaryError:
        raise
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        raise BoundaryError("invalid_host_manifest") from None


def _read_provider_names(args):
    if args.count("--profile") != 1:
        raise BoundaryError("explicit_read_profile_required")
    index = args.index("--profile")
    profile = args[index + 1] if index + 1 < len(args) else None
    if profile not in {"kr_trading", "us_trading"}:
        raise BoundaryError("explicit_read_profile_required")
    return ("perplexity", "kospi_kosdaq" if profile == "kr_trading" else "yahoo_finance")


def connect_target(header: bytes) -> str:
    """Parse exactly one CONNECT authority; never accept a URL or userinfo."""
    try:
        first = header.decode("ascii").split("\r\n", 1)[0].split(" ")
    except UnicodeDecodeError:
        raise BoundaryError("invalid_connect") from None
    if len(first) != 3 or first[0] != "CONNECT" or first[2] not in {"HTTP/1.0", "HTTP/1.1"}:
        raise BoundaryError("invalid_connect")
    host, separator, port = first[1].rpartition(":")
    if not separator or host not in DOMAINS or port != "443":
        raise BoundaryError("destination_denied")
    return host


def public_addresses(host: str):
    if host not in DOMAINS:
        raise BoundaryError("destination_denied")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise BoundaryError("dns_failed")
    for family, kind, protocol, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast or ip.is_unspecified:
            raise BoundaryError("nonpublic_destination")
        if getattr(ip, "ipv4_mapped", None) is not None and not ip.ipv4_mapped.is_global:
            raise BoundaryError("nonpublic_destination")
    return addresses


def tunnel(left, right):
    """Bounded idle time and memory; no stream contents are recorded."""
    while True:
        ready, _, _ = select.select([left, right], [], [], IDLE_SECONDS)
        if not ready:
            return
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            (right if source is left else left).sendall(data)


class GatewayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.slots.acquire(blocking=False):
            return
        try:
            self.request.settimeout(IDLE_SECONDS)
            header = bytearray()
            # Avoid over-reading any tunneled TLS bytes.
            while not header.endswith(b"\r\n\r\n"):
                byte = self.request.recv(1)
                if not byte or len(header) >= MAX_HEADER:
                    raise BoundaryError("invalid_header")
                header.extend(byte)
            host = connect_target(bytes(header))
            addresses = public_addresses(host)
            connected = None
            for family, kind, protocol, _, address in addresses:
                candidate = socket.socket(family, kind, protocol)
                candidate.settimeout(10)
                try:
                    candidate.connect(address)  # Already validated numeric IP.
                    connected = candidate
                    break
                except OSError:
                    candidate.close()
            if connected is None:
                raise BoundaryError("connect_failed")
            with connected:
                connected.settimeout(IDLE_SECONDS)
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                tunnel(self.request, connected)
        except (BoundaryError, OSError, ValueError):
            try:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
        finally:
            self.server.slots.release()


class Gateway(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address):
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, GatewayHandler)

    def handle_error(self, request, client_address):
        # Default handler writes raw exception details to stderr.
        pass


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.slots.acquire(blocking=False):
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
                upstream.settimeout(IDLE_SECONDS)
                self.request.settimeout(IDLE_SECONDS)
                upstream.connect("/gateway.sock")
                tunnel(self.request, upstream)
        except OSError:
            pass
        finally:
            self.server.slots.release()


class Relay(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self):
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(("127.0.0.1", 0), RelayHandler)

    def handle_error(self, request, client_address):
        pass


def validate_stage(root: Path) -> Path:
    if ".." in root.parts:
        raise BoundaryError("parent_traversal_rejected")
    root = root.absolute()
    if not root.is_dir() or root == Path("/") or any(p.is_symlink() for p in (root, *root.parents)):
        raise BoundaryError("unsafe_stage")
    # Explicit marker prevents accidentally passing a production repository.
    if not (root / ".isolated-codex-probe").is_file():
        raise BoundaryError("stage_marker_missing")
    for path in (root, *root.rglob("*")):
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise BoundaryError("unsafe_stage_inode")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise BoundaryError("unsafe_stage_inode")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise BoundaryError("unsafe_stage_permissions")
    return root


def sandbox_command(root: Path, binary: Path, gateway: Path, runner: Path, args: list[str], *, read_sockets=None, snapshot=None):
    """Only specific runtime mounts; never mount /, /root, /etc or /run."""
    if ".." in root.parts:
        raise BoundaryError("parent_traversal_rejected")
    command = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent",
               "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev",
               "--tmpfs", _PRIVATE_TMPFS, "--dir", "/home/probe", "--dir", "/etc"]
    for runtime in ("/usr", "/lib", "/lib64"):
        if Path(runtime).exists():
            command += ["--ro-bind", runtime, runtime]
    # Mount the actual public CA file, not a directory whose symlinks may point
    # outside the namespace (RHEL uses /etc/pki; Debian uses ca-certificates.crt).
    ca = next((Path(p).resolve() for p in (
        "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/certs/ca-certificates.crt",
        "/etc/ssl/cert.pem") if Path(p).is_file()), None)
    if ca is None:
        raise BoundaryError("public_ca_bundle_missing")
    command += ["--ro-bind", str(ca), "/ca-bundle.crt"]
    for name, source in sorted((read_sockets or {}).items()):
        if name not in {"perplexity", "kospi_kosdaq", "yahoo_finance"}:
            raise BoundaryError("unsafe_read_socket")
        info = Path(source).lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BoundaryError("unsafe_read_socket")
        command += ["--ro-bind", str(source), f"/mcp-{name}.sock"]
    command += ["--ro-bind", str(root), str(root)]
    if snapshot is not None:
        target = root / "portfolio.sqlite"
        if not target.is_file() or target.is_symlink():
            raise BoundaryError("snapshot_target_missing")
        command += ["--ro-bind", str(snapshot), str(target)]
    command += [
                "--ro-bind", str(binary), "/codex",
                "--ro-bind", str(binary.with_name("codex-code-mode-host")), "/codex-code-mode-host",
                "--ro-bind", str(runner), "/runner.py",
                "--ro-bind", str(gateway), "/gateway.sock",
                "--setenv", "PATH", "/usr/bin:/bin",
                "--setenv", "HOME", "/home/probe",
                "--setenv", "CODEX_HOME", str(root / "home"),
                "--setenv", "PYTHONHOME", str(root / "runtime"),
                "--setenv", "LD_LIBRARY_PATH", str(root / "runtime/lib"),
                "--setenv", "SSL_CERT_FILE", "/ca-bundle.crt",
                "--chdir", _PRIVATE_TMPFS, "--", str(root / "runtime/bin/python3.11"), "/runner.py", "--inner", *args]
    return command


def inner(args):
    # Preserve ephemeral CLI cache/state without permitting writes to staged auth,
    # fixtures or SQLite. Individual auth/config copies live in private tmpfs.
    import shutil
    source_home = Path(os.environ["CODEX_HOME"])
    temporary_home = Path(tempfile.mkdtemp(prefix="codex-home-"))
    for name in ("auth.json", "config.toml", "kr_trading.config.toml", "us_trading.config.toml"):
        source = source_home / name
        if source.is_file():
            shutil.copyfile(source, temporary_home / name)
    os.environ["CODEX_HOME"] = str(temporary_home)
    with Relay() as relay:
        thread = threading.Thread(target=relay.serve_forever, daemon=True)
        thread.start()
        proxy = "http://127.0.0.1:" + str(relay.server_address[1])
        env = dict(os.environ, HTTPS_PROXY=proxy, HTTP_PROXY=proxy, ALL_PROXY=proxy,
                   https_proxy=proxy, http_proxy=proxy, all_proxy=proxy, NO_PROXY="", no_proxy="")
        try:
            return subprocess.call(["/codex", *args], env=env, close_fds=True)  # nosec B603
        finally:
            relay.shutdown()


def launch(root: Path, binary: Path, args: list[str]) -> int:
    fields = ("PRISM_PROBE_PARENT_FD", "PRISM_PROBE_PARENT_PID", "PRISM_PROBE_SNAPSHOT",
              "PRISM_PROBE_SNAPSHOT_SHA256", "PRISM_PROBE_AGENT_ROOT", "PRISM_PROBE_SNAPSHOT_ROOT")
    if any(key in os.environ for key in fields):
        if not all(key in os.environ for key in fields):
            raise BoundaryError("incomplete_diagnostic_binding")
        try:
            lease = ParentLease(int(os.environ[fields[0]]), int(os.environ[fields[1]]))
        except (ValueError, OSError):
            raise BoundaryError("invalid_parent_lease") from None
        snapshot = validate_snapshot(os.environ[fields[2]], os.environ[fields[3]], root,
                                     os.environ[fields[4]], os.environ[fields[5]])
        try:
            return _launch(root, binary, args, lease=lease, snapshot=snapshot)
        finally:
            os.close(lease.fd)
    return _launch(root, binary, args)


def _launch(root: Path, binary: Path, args: list[str], *, lease=None, snapshot=None) -> int:
    root = validate_stage(root)
    if str(binary) != TRUSTED_CODEX_BINARY:
        raise BoundaryError("untrusted_binary_path")
    binary = binary.resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK) or binary.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BoundaryError("unsafe_binary")
    with binary.open("rb") as executable:
        if executable.read(4) != b"\x7fELF":
            raise BoundaryError("binary_not_elf")
    host = binary.with_name("codex-code-mode-host")
    if not host.is_file() or host.is_symlink() or not os.access(host, os.X_OK) or host.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BoundaryError("unsafe_code_mode_host")
    with host.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise BoundaryError("unsafe_code_mode_host")
    # This wrapper is exclusively a backend executable, never a general shell.
    if not args or args[0] != "exec":
        raise BoundaryError("invalid_invocation")
    host_module = host_configs = None
    host_path = os.environ.get("PRISM_PROBE_HOST_MANIFEST")
    if host_path:
        host_module, manifest = load_host_manifest(Path(host_path), root)
        try:
            host_configs = host_module.host_provider_configs(manifest["providers"], _read_provider_names(args), os.environ,
                                                             kr_profile=manifest.get("kr_profile"), host_runtime_root=Path(host_path).parent)
        except ValueError:
            raise BoundaryError("host_provider_preflight_failed") from None
    with tempfile.TemporaryDirectory(prefix="prism-probe-gateway-") as directory:
        gateway_path = Path(directory) / "gateway.sock"
        with Gateway(str(gateway_path)) as gateway, ExitStack() as bridges:
            sockets = {}
            for name, config in (host_configs or {}).items():
                bridge = bridges.enter_context(host_module.ReadMcpBridge(
                    Path(directory) / f"mcp-{name}.sock", server_name=name, **config,
                    **({"parent_alive": lease.alive} if lease is not None else {})))
                sockets[name] = bridge.socket_path
            thread = threading.Thread(target=gateway.serve_forever, daemon=True)
            thread.start()
            child = None
            stopped = threading.Event()
            old_handlers = {}
            def stop(signum, _frame):
                stopped.set()
                if child is not None:
                    child.terminate()
            try:
                command = sandbox_command(root, binary, gateway_path, Path(__file__).resolve(), args, read_sockets=sockets,
                                          **({"snapshot": snapshot} if snapshot is not None else {}))
                # bwrap 0.4.0 has no --clearenv. Host secrets/manifest path never
                # enter model env; only sandbox_command's explicit variables do.
                # Backend-generated CLI arguments cannot select the executable
                # or bypass the enclosing OS mounts/network/capability limits.
                for signum in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[signum] = signal.signal(signum, stop)
                if lease is not None:
                    lease.check()
                child = subprocess.Popen(command, env={}, close_fds=True)  # nosec B603  # nosemgrep
                if lease is not None:
                    lease.check()  # parent may have died during Popen/assignment
                    return wait_owned_child(child, lease, stopped)
                return child.wait()
            finally:
                if child is not None and child.poll() is None:
                    child.kill()
                    child.wait()
                for signum, handler in old_handlers.items():
                    signal.signal(signum, handler)
                gateway.shutdown()


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    try:
        if args and args[0] == "--inner":
            return inner(args[1:])
        return launch(Path(os.environ["PRISM_PROBE_ROOT"]),
                      Path(os.environ["PRISM_PROBE_CODEX_BINARY"]), args)
    except (ValueError, OSError, KeyError):
        print("probe_sandbox_boundary_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
