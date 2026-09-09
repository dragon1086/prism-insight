"""Fixed agent-side mount plan; no execution, credentials or model invocation.

Only a supervisor supplies validated paths and launches this command with env={}
under its whole-case lease. This is the agent sibling, NOT the model namespace.
The actual inner-case script, parent lifecycle and end-to-end proof remain gates.
"""
import hashlib
import math
import os
from pathlib import Path
import stat


class NamespaceRejected(ValueError):
    pass


def _path(value, *, directory=False, private=False):
    try:
        path = Path(value)
    except TypeError:
        raise NamespaceRejected("invalid_path") from None
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise NamespaceRejected("noncanonical_path")
    try:
        info = path.stat()
    except OSError:
        raise NamespaceRejected("path_unavailable") from None
    if (info.st_uid != os.getuid() or info.st_mode & (0o077 if private else 0o022)
            or (directory and not stat.S_ISDIR(info.st_mode))):
        raise NamespaceRejected("unsafe_path_permissions")
    if not directory and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise NamespaceRejected("aliased_file")
    return path


def validate_tree(root, expected, *, python_only=False):
    """Check an exact supervisor-registered public inventory, never self-adopt.

    Hashes prove identity, not that content is non-secret. The trusted staging
    step must derive these inventories from reviewed source/distributions and
    sanitized evidence, never inventory an arbitrary host tree for approval.
    """
    root = _path(root, directory=True)
    if not isinstance(expected, dict) or not expected:
        raise NamespaceRejected("registered_inventory_required")
    observed = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise NamespaceRejected("symlink_in_tree")
        if path.is_dir():
            _path(path, directory=True)
        else:
            path = _path(path)
            if not path.is_file() or (python_only and path.suffix != ".py"):
                raise NamespaceRejected("source_tree_contains_noncode")
            if path.name in {".env", "auth.json", "mcp_agent.secrets.yaml", "kis_devlp.yaml"} or path.name.startswith(".env."):
                raise NamespaceRejected("credential_config_in_tree")
            observed[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        raise NamespaceRejected("tree_inventory_mismatch")
    return root


def validate_python_source(root, script_hash, expected):
    root = validate_tree(root, expected, python_only=True)
    script = root / "tools/isolated_trading_agent_case.py"
    if not script.is_file() or hashlib.sha256(script.read_bytes()).hexdigest() != script_hash:
        raise NamespaceRejected("fixed_case_script_pin_mismatch")
    return root


def command(*, source_root, runtime_root, evidence_root, arm_root, sockets, script_hash,
            case_filename, case_hash, controls, source_files, runtime_files, evidence_files):
    """Construct one fixed command. No caller argv, arbitrary env or endpoints."""
    source = validate_python_source(source_root, script_hash, source_files)
    runtime = validate_tree(runtime_root, runtime_files)
    evidence = _path(evidence_root, directory=True, private=True)
    validate_tree(evidence, evidence_files)
    arm = _path(arm_root, directory=True, private=True)
    roots = (source, runtime, evidence, arm)
    if any(a.is_relative_to(b) for i, a in enumerate(roots) for j, b in enumerate(roots) if i != j):
        raise NamespaceRejected("overlapping_roots")
    if (not isinstance(case_filename, str) or Path(case_filename).name != case_filename
            or not case_filename.endswith(".json")):
        raise NamespaceRejected("invalid_case_filename")
    case = _path(evidence / case_filename, private=True)
    if not case.is_file() or hashlib.sha256(case.read_bytes()).hexdigest() != case_hash:
        raise NamespaceRejected("case_pin_mismatch")
    required = {"codex-invoke", "responses", "perplexity", "market"}
    if not isinstance(sockets, dict) or set(sockets) != required:
        raise NamespaceRejected("fixed_socket_set_required")
    socket_paths = {}
    for name, value in sockets.items():
        path = _path(value, private=True)
        if not stat.S_ISSOCK(path.stat().st_mode) or any(path.is_relative_to(root) for root in roots):
            raise NamespaceRejected("unsafe_socket_mount")
        socket_paths[name] = path
    if len(set(socket_paths.values())) != len(required):
        raise NamespaceRejected("socket_alias")
    allowed_controls = {"market", "model", "effort", "timeout"}
    if (not isinstance(controls, dict) or set(controls) != allowed_controls
            or any(not isinstance(controls[key], str) for key in ("market", "model", "effort"))
            or controls["market"] not in {"KR", "US"}
            or controls["model"] not in {"gpt-6-astra", "gpt-5.6-sol"}
            or controls["effort"] not in {"low", "medium", "high", "xhigh", "max", "ultra"}
            or type(controls["timeout"]) not in {int, float}
            or not math.isfinite(controls["timeout"]) or not 0 < controls["timeout"] <= 600):
        raise NamespaceRejected("invalid_pinned_controls")
    python = _path(runtime / "bin/python3.11")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise NamespaceRejected("staged_python_missing")
    # bwrap 0.4 lacks --clearenv: the supervisor MUST pass env={} to Popen.
    args = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--cap-drop", "ALL",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home/agent"]
    for public in ("/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/share/zoneinfo"):
        if Path(public).is_dir():
            args += ["--ro-bind", public, public]
    args += ["--ro-bind", str(source), "/app/src", "--ro-bind", str(runtime), "/app/runtime",
             "--ro-bind", str(evidence), "/evidence", "--bind", str(arm), "/arm"]
    for name in sorted(socket_paths):
        args += ["--ro-bind", str(socket_paths[name]), "/" + name + ".sock"]
    env = {"PATH": "/app/runtime/bin", "HOME": "/home/agent", "TZ": "Asia/Seoul",
           "PYTHONHOME": "/app/runtime", "LD_LIBRARY_PATH": "/app/runtime/lib",
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
           "PRISM_KR_CODEX_FAST_TRADING": "1", "PRISM_US_CODEX_FAST_TRADING": "1",
           "PRISM_KR_CODEX_FAST_SELL": "1", "PRISM_US_CODEX_FAST_SELL": "1"}
    for side in ("BUY", "SELL"):
        env.update({f"PRISM_{side}_CODEX_MODEL": controls["model"], f"PRISM_{side}_CODEX_EFFORT": controls["effort"],
                    f"PRISM_{side}_CODEX_TIMEOUT": str(controls["timeout"])})
    for key, value in sorted(env.items()):
        args += ["--setenv", key, value]
    args += ["--chdir", "/arm", "--remount-ro", "/", "--", "/app/runtime/bin/python3.11",
             "/app/src/tools/isolated_trading_agent_case.py", "--case", "/evidence/" + case_filename]
    return args
