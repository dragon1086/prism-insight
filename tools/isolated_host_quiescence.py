"""Read-only Linux host observation, not historical cleanup proof or approval.

No subprocess, signal, network, environment capture, CLI or model RPC. The
operator's own PID is excluded explicitly because it owns the lane/DB handles.
Any observed process churn or incomplete read aborts instead of claiming quiet.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

_NS = ("mnt", "pid", "net", "user")
_MAX_PROCESSES = 4096
_MAX_FDS = 65536


class QuiescenceRejected(ValueError):
    pass


def _read(path, maximum):
    with path.open("rb") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise QuiescenceRejected("proc_read_limit")
    return value


def _start(path, pid):
    value = _read(path / "stat", 8192)
    end = value.rfind(b")")
    tail = value[end + 2:].split()
    if (not value.startswith(str(pid).encode() + b" (") or end < len(str(pid)) + 2
            or value[end + 1:end + 2] != b" " or len(tail) < 20):
        raise QuiescenceRejected("proc_identity_invalid")
    ticks = int(tail[19])
    if ticks <= 0:
        raise QuiescenceRejected("proc_identity_invalid")
    return ticks


def _matches(value, roots):
    # Prefix boundary prevents /stage-other from matching /stage. Broader
    # substrings can only create conservative false-positive blocking.
    return any(re.search(re.escape(root) + rb"(?=$|[/\x00\s'\"])", value) for root in roots)


def _proc_path(value):
    return re.sub(rb"\\([0-7]{3})", lambda match: bytes([int(match[1], 8)]), value)


def _pids(proc):
    values = sorted(int(path.name) for path in proc.iterdir() if path.name.isdigit())
    if len(values) > _MAX_PROCESSES:
        raise QuiescenceRejected("proc_scan_limit")
    return values


def _collect(registered_roots, proc):
    """Private test seam; public collect always uses the actual /proc."""
    if sys.platform != "linux":
        raise QuiescenceRejected("linux_host_required")
    roots = tuple(path.encode() for path in registered_roots)
    started = time.monotonic()
    try:
        pids = _pids(proc)
        identities, scanned, fd_count = [], 0, 0
        starts = {}
        for pid in pids:
            if time.monotonic() - started > 5:
                raise QuiescenceRejected("proc_scan_deadline")
            if pid == os.getpid():
                continue
            path = proc / str(pid)
            start = _start(path, pid)
            starts[pid] = start
            sources = []
            if _matches(_read(path / "cmdline", 1024 * 1024), roots):
                sources.append("argv")
            mounts = _read(path / "mountinfo", 1024 * 1024).splitlines()
            for line in mounts:
                fields = line.split()
                if len(fields) < 10 or b"-" not in fields:
                    raise QuiescenceRejected("proc_mount_invalid")
                # mount root exposes bind source despite /arm or /app aliases.
                if any(_matches(_proc_path(field), roots) for field in fields[3:5]):
                    if "mount" not in sources:
                        sources.append("mount")
            fds = list((path / "fd").iterdir())
            fd_count += len(fds)
            if len(fds) > 4096 or fd_count > _MAX_FDS:
                raise QuiescenceRejected("proc_fd_limit")
            for descriptor in fds:
                if _matches(os.fsencode(os.readlink(descriptor)), roots) and "fd" not in sources:
                    sources.append("fd")
            if sources:
                namespaces = {}
                for name in _NS:
                    value = os.readlink(path / "ns" / name)
                    match = re.fullmatch(re.escape(name) + r":\[([0-9]+)\]", value)
                    if match is None:
                        raise QuiescenceRejected("proc_namespace_invalid")
                    namespaces[name] = int(match[1])
                identities.append({"pid": pid, "start_ticks": start, "namespaces": namespaces,
                                   "match_sources": sorted(sources)})
            if _start(path, pid) != start:
                raise QuiescenceRejected("proc_identity_changed")
            scanned += 1
        unix = _read(proc / "net" / "unix", 1024 * 1024).splitlines()
        if not unix or not unix[0].startswith(b"Num"):
            raise QuiescenceRejected("proc_socket_invalid")
        sockets = 0
        for line in unix[1:]:
            fields = line.split(None, 7)
            if len(fields) < 7:
                raise QuiescenceRejected("proc_socket_invalid")
            if len(fields) == 8 and _matches(_proc_path(fields[7]), roots):
                sockets += 1
        if _pids(proc) != pids or any(_start(proc / str(pid), pid) != start for pid, start in starts.items()):
            raise QuiescenceRejected("proc_snapshot_changed")
        if time.monotonic() - started > 5:
            raise QuiescenceRejected("proc_scan_deadline")
        result = {"schema_version": 1, "observed_at": time.time(), "scan_status": "COMPLETE",
                  "current_matches": len(identities), "matches": identities,
                  "scanned_process_count": scanned, "scanned_fd_count": fd_count,
                  "relevant_socket_count": sockets, "observer_pid": os.getpid(),
                  "registered_roots_sha256": hashlib.sha256(json.dumps(sorted(registered_roots)).encode()).hexdigest(),
                  "historical_start_identity": "NOT_CAPTURED", "historical_cleanup_receipt": "NOT_CAPTURED"}
        if not validate_receipt(result):
            raise QuiescenceRejected("proc_receipt_limit")
        return result
    except (OSError, ValueError, IndexError):
        raise QuiescenceRejected("proc_observation_incomplete") from None


def validate_receipt(value):
    fields = {"schema_version", "observed_at", "scan_status", "current_matches", "matches", "scanned_process_count",
              "scanned_fd_count", "relevant_socket_count", "observer_pid", "registered_roots_sha256",
              "historical_start_identity", "historical_cleanup_receipt"}
    if (type(value) is not dict or set(value) != fields or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["observed_at"]) not in {int, float} or not math.isfinite(value["observed_at"])
            or value["scan_status"] != "COMPLETE" or value["historical_start_identity"] != "NOT_CAPTURED"
            or value["historical_cleanup_receipt"] != "NOT_CAPTURED"
            or not isinstance(value["registered_roots_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["registered_roots_sha256"]) is None
            or any(type(value[k]) is not int or not 0 <= value[k] <= 1000000000 for k in
                   ("current_matches", "scanned_process_count", "scanned_fd_count", "relevant_socket_count", "observer_pid"))
            or value["current_matches"] > value["scanned_process_count"]
            or value["scanned_process_count"] > _MAX_PROCESSES or value["scanned_fd_count"] > _MAX_FDS
            or value["observer_pid"] <= 0
            or not isinstance(value["matches"], list) or len(value["matches"]) != value["current_matches"]):
        return False
    for match in value["matches"]:
        if (type(match) is not dict or set(match) != {"pid", "start_ticks", "namespaces", "match_sources"}
                or any(type(match[k]) is not int or not 0 < match[k] < 2**64 for k in ("pid", "start_ticks"))
                or type(match["namespaces"]) is not dict or set(match["namespaces"]) != set(_NS)
                or any(type(v) is not int or not 0 < v < 2**64 for v in match["namespaces"].values())
                or type(match["match_sources"]) is not list or not match["match_sources"]
                or any(not isinstance(v, str) or v not in {"argv", "mount", "fd"} for v in match["match_sources"])):
            return False
    if len({match["pid"] for match in value["matches"]}) != len(value["matches"]):
        return False
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()) <= 65536


def collect(original_registration, state_root):
    """Validate original host registration, then observe actual Linux /proc."""
    from tools.isolated_case_disposition import _validated_registration
    try:
        inputs, _ = _validated_registration(original_registration, state_root)
        env = json.loads(original_registration.binding.environment_json)
        paths = {Path(state_root), original_registration.binding.model_root, original_registration.binding.host_root,
                 original_registration.helper_source_root, Path(env["PRISM_PROBE_HOST_MANIFEST"]).parent}
        paths.update(Path(inputs[key]) for key in ("source_root", "runtime_root", "evidence_root", "arm_root"))
        paths.update(_provider_roots(original_registration, inputs, env))
        for path in paths:
            if path.resolve() != path or not path.is_dir() or path.stat().st_uid != os.getuid() or len(path.parts) < 3:
                raise QuiescenceRejected("registered_roots_invalid")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise QuiescenceRejected("registered_roots_unverified") from None
    return _collect(tuple(sorted(str(path) for path in paths)), Path("/proc"))


def _provider_roots(registration, inputs, env):
    """Providers may run outside every staged tree with only pipe descriptors.

    Verify the same pinned host manifest and selected executable/code trees as
    the real read bridge. A matching unrelated provider conservatively blocks.
    Do not include the shared Python interpreter as a process identity.
    """
    from tools.codex_probe_sandbox import load_host_manifest
    manifest_path = Path(env["PRISM_PROBE_HOST_MANIFEST"])
    module, _ = load_host_manifest(manifest_path, registration.binding.model_root)
    market = inputs["controls"]["market"]
    if market not in {"KR", "US"}:
        raise QuiescenceRejected("provider_scope_invalid")
    names = ("perplexity", "kospi_kosdaq" if market == "KR" else "yahoo_finance")
    pins = module.verified_provider_pins(names, host_runtime_root=manifest_path.parent)
    if set(pins) != set(names):
        raise QuiescenceRejected("provider_scope_invalid")
    return {Path(pins[name]["code_root"]) for name in names}
