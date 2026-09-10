"""Command construction only; these fixtures do not prove OS isolation."""
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools import isolated_agent_namespace as ns


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def setup():
    with tempfile.TemporaryDirectory(prefix="ans-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        roots = {}
        for name in ("source", "runtime", "evidence", "arm", "host"):
            roots[name] = root / name
            roots[name].mkdir(mode=0o700)
        script = roots["source"] / "tools/isolated_trading_agent_case.py"
        script.parent.mkdir()
        script.write_text("# Fixed test script, never executed\n")
        binary = roots["runtime"] / "bin/python3.11"
        binary.parent.mkdir()
        binary.write_text("fixture, not an executable runtime")
        binary.chmod(0o700)
        case = roots["evidence"] / "case.json"
        case.write_text("{}")
        case.chmod(0o600)
        sockets, handles = {}, []
        for name in ("codex-invoke", "responses", "perplexity", "market"):
            handle = socket.socket(socket.AF_UNIX)
            path = roots["host"] / (name + ".sock")
            handle.bind(str(path))
            path.chmod(0o600)
            sockets[name] = path
            handles.append(handle)
        args = dict(source_root=roots["source"], runtime_root=roots["runtime"],
                    evidence_root=roots["evidence"], arm_root=roots["arm"], sockets=sockets,
                    script_hash=digest(script), case_filename="case.json", case_hash=digest(case),
                    source_files={str(script.relative_to(roots["source"])): digest(script)},
                    runtime_files={"bin/python3.11": digest(binary)}, evidence_files={"case.json": digest(case)},
                    controls={"market": "KR", "model": "gpt-6-astra", "effort": "xhigh", "timeout": 240})
        try:
            yield args
        finally:
            for handle in handles:
                handle.close()


def test_fixed_readonly_mounts_and_strict_environment(setup):
    args = ns.command(**setup)
    assert args[:2] == ["/usr/bin/bwrap", "--unshare-all"]
    assert "--clearenv" not in args  # Linux server bwrap 0.4 compatibility.
    assert args[-2:] == ["--case", "/evidence/case.json"]
    assert args.count("--bind") == 1
    assert args[args.index("--bind") + 1:args.index("--bind") + 3] == [str(setup["arm_root"]), "/arm"]
    assert "/usr" not in args and "/root" not in args
    assert ["--remount-ro", "/"] == args[args.index("--remount-ro"):args.index("--remount-ro") + 2]


@pytest.mark.parametrize("field,value", [("market", []), ("model", {}), ("effort", []),
                                         ("timeout", True), ("timeout", float("nan")), ("timeout", float("inf"))])
def test_invalid_control_is_safe_rejection(setup, field, value):
    setup["controls"][field] = value
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


@pytest.mark.parametrize("root", ["source", "runtime", "evidence"])
def test_unregistered_file_never_enters_mount(setup, root):
    (setup[root + "_root"] / "unexpected.py").write_text("# not registered")
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


@pytest.mark.parametrize("root", ["source", "runtime", "evidence"])
def test_symlink_never_enters_mount(setup, root):
    (setup[root + "_root"] / "leak").symlink_to(setup["arm_root"])
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


def test_source_content_pin_not_only_script(setup):
    path = setup["source_root"] / "library.py"
    path.write_text("# reviewed")
    setup["source_files"]["library.py"] = digest(path)
    ns.command(**setup)
    path.write_text("# changed")
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


def test_pin_does_not_authorize_secret_config(setup):
    path = setup["runtime_root"] / ".env"
    path.write_text("NOT_A_REAL_SECRET=fixture")
    setup["runtime_files"][".env"] = digest(path)
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


def test_wrong_case_hash_and_socket_alias_rejected(setup):
    setup["case_hash"] = "0" * 64
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)
    setup["case_hash"] = digest(setup["evidence_root"] / "case.json")
    setup["sockets"]["responses"] = setup["sockets"]["codex-invoke"]
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


def registered(setup):
    case = {"case_id": "case1", "arm_id": "arm1", "profile_id": "profile1",
            "codex": {"request_id": "request1", "revision": "revision1", "settings_sha256": "1" * 64}}
    path = setup["evidence_root"] / "case.json"
    path.write_text(json.dumps(case))
    setup["case_hash"] = digest(path)
    setup["evidence_files"]["case.json"] = digest(path)
    marker = setup["arm_root"].parent / "host" / "registration.json"
    marker.write_text(json.dumps({"schema_version": 1, "case_sha256": digest(path),
                                 **{key: case[key] for key in ("case_id", "arm_id", "profile_id")}, **case["codex"]}))
    marker.chmod(0o600)
    setup.update(execute_registered=True, registration_file=marker, registration_sha256=digest(marker))
    return marker


def test_registered_marker_is_readonly_and_fixed_flag_only(setup):
    marker = registered(setup)
    args = ns.command(**setup)
    index = args.index(str(marker))
    assert args[index - 1:index + 2] == ["--ro-bind", str(marker), "/parent-case.json"]
    assert args[-1] == "--execute-registered"


def test_registration_wrong_case_or_writable_arm_never_authorizes_execution(setup):
    marker = registered(setup)
    data = json.loads(marker.read_text())
    data["case_id"] = "wrong"
    marker.write_text(json.dumps(data))
    setup["registration_sha256"] = digest(marker)
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)
    data["case_id"] = "case1"
    target = setup["arm_root"] / "registration.json"
    target.write_text(json.dumps(data))
    target.chmod(0o600)
    setup.update(registration_file=target, registration_sha256=digest(target))
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


def test_partial_registration_is_rejected(setup):
    setup["registration_sha256"] = "1" * 64
    with pytest.raises(ns.NamespaceRejected):
        ns.command(**setup)


@pytest.mark.skipif(sys.platform != "linux" or sys.version_info[:2] != (3, 11), reason="Linux staged Python 3.11 namespace")
@pytest.mark.parametrize("registered_mode", [False, True])
def test_real_harmless_namespace_has_only_registered_mounts(setup, registered_mode):
    from tools.prepare_codex_probe_stage import stage_runtime

    # Only the installed standard library, no production config/dependencies.
    stage_runtime(setup["runtime_root"].parent, [])
    script = setup["source_root"] / "tools/isolated_trading_agent_case.py"
    outside = setup["arm_root"].parent / "host-canary.txt"
    outside.write_text("synthetic-host-canary")
    if registered_mode:
        registered(setup)
    script.write_text('''import json, os, socket
from pathlib import Path
assert "CANARY_SECRET" not in os.environ
assert not Path("/root").exists()
assert not Path(%r).exists()
if %r:
    assert json.loads(Path("/parent-case.json").read_text())["case_id"] == "case1"
for target in ("/evidence/case.json", "/app/src/tools/isolated_trading_agent_case.py", "/app/runtime/bin/python3.11", "/parent-case.json", "/outside"):
    try:
        with open(target, "a") as output: output.write("forbidden")
    except OSError:
        pass
    else:
        raise AssertionError("write escaped arm")
for name in ("codex-invoke", "responses", "perplexity", "market"):
    with socket.socket(socket.AF_UNIX) as client:
        client.connect("/" + name + ".sock")
with socket.socket() as client:
    client.settimeout(.1)
    assert client.connect_ex(("192.0.2.1", 443)) != 0
Path("/tmp/scratch").write_text("scratch")
Path("/arm/result.json").write_text(json.dumps({"mount_canary": True}))
''' % (str(outside), registered_mode))
    setup["script_hash"] = digest(script)
    for name in ("source", "runtime"):
        root = setup[name + "_root"]
        setup[name + "_files"] = {str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()}
    # Listening sockets are inert local canaries, not model/API proxies.
    listeners = []
    try:
        for path in setup["sockets"].values():
            path.unlink()
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(path))
            path.chmod(0o600)
            listener.listen(1)
            listeners.append(listener)
        result = subprocess.run(ns.command(**setup), env={}, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads((setup["arm_root"] / "result.json").read_text()) == {"mount_canary": True}
        assert outside.read_text() == "synthetic-host-canary"
    finally:
        for listener in listeners:
            listener.close()
