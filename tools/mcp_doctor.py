#!/usr/bin/env python3
"""Report whether every MCP server the report path needs can actually start.

Why this exists: report sections silently degrade to "Analysis failed: ..."
when an MCP server cannot launch, and the cause is usually environmental — a
missing binary, a path that only exists on one machine, an unset key. The
failure looks identical in the report no matter which of those it was, and it
only shows up after a full (slow, paid) generation.

This resolves the config exactly the way the report path does, then checks each
server without launching it, so the same command can be run on every host and
the outputs compared.

**Never prints a secret.** Env vars are reported by name and set/unset only —
a lesson from leaking two credentials into a chat log by printing a resolved
registry object.

Usage:
    python tools/mcp_doctor.py            # human-readable
    python tools/mcp_doctor.py --json     # machine-diffable across hosts

Exit code is non-zero if any server is unusable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cores.llm import config_loader  # noqa: E402
from cores.llm.config_loader import (  # noqa: E402
    load_mcp_registry,
    load_report_mcp_registry,
)

_ENV_REF = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
# A filesystem path, as opposed to a URL, an npm package spec, or a flag.
# Being strict here matters: this output is meant to be diffed across hosts,
# and a false positive is indistinguishable from a real breakage.
_FILE_SUFFIXES = (".js", ".py", ".sqlite", ".db", ".json", ".yaml", ".yml")
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

OK = "OK"
MISSING_COMMAND = "MISSING_COMMAND"
MISSING_PATH = "MISSING_PATH"
UNSET_ENV = "UNSET_ENV"
ABSOLUTE_PATH = "ABSOLUTE_PATH"


@dataclass
class ServerReport:
    name: str
    command: str
    command_found: bool
    problems: list[str] = field(default_factory=list)
    paths: list[dict] = field(default_factory=list)
    env: list[dict] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.problems


def _check_env(spec_env: dict, raw_env: dict | None = None) -> list[dict]:
    """Report env var names and whether a value resolves — never the value.

    ``spec_env`` has already been interpolated by the loader, so ``${VAR}``
    references are indistinguishable from literals by the time the registry
    exists. ``raw_env`` is the same block read straight from the YAML, which is
    what tells the two apart — and telling them apart is the whole point of
    migrating credentials out of the config file.
    """

    checked: list[dict] = []
    raw_env = raw_env or {}
    for key, resolved in sorted((spec_env or {}).items()):
        declared = str(raw_env.get(key, resolved) or "")
        reference = _ENV_REF.match(declared)
        if reference:
            var = reference.group(1)
            checked.append(
                {
                    "key": key,
                    "source": f"${{{var}}}",
                    "set": bool(os.environ.get(var)),
                }
            )
        else:
            # A credential written into the config file: machine-local, drifts
            # between hosts, and one `git add -A` away from being committed.
            checked.append(
                {
                    "key": key,
                    "source": "inline",
                    "set": bool(str(resolved or "").strip()),
                }
            )
    return checked


def _resolve_config_path(label: str) -> Path | None:
    """Mirror the loader's search order so the raw YAML can be read too."""

    if label == "report":
        override = os.environ.get("REPORT_MCP_CONFIG")
        if override:
            return Path(override)
        if config_loader._LEGACY_CONFIG.exists():
            return config_loader._LEGACY_CONFIG
    override = os.environ.get("PRISM_MCP_CONFIG")
    if override:
        return Path(override)
    if config_loader._NATIVE_CONFIG.exists():
        return config_loader._NATIVE_CONFIG
    if config_loader._LEGACY_CONFIG.exists():
        return config_loader._LEGACY_CONFIG
    return None


def _raw_servers(path: Path | None) -> dict:
    """Return the uninterpolated ``servers`` block, in either YAML shape."""

    if path is None or not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if "servers" in raw:
        return raw.get("servers") or {}
    return ((raw.get("mcp") or {}).get("servers")) or {}


def _looks_like_path(text: str) -> bool:
    if text.startswith("-") or _URL.match(text):
        return False
    if text.startswith("@"):
        return False  # npm scoped package, e.g. @scope/name@latest
    if text.startswith("/") or text.startswith("./") or text.startswith("../"):
        return True
    return text.endswith(_FILE_SUFFIXES)


def _base_dir(args, project_root: Path) -> Path:
    """Where relative args resolve from.

    Some servers are launched with `--directory X`, which moves the base; the
    sqlite entry's `../stock_tracking_db.sqlite` only makes sense relative to
    that, not to the repo root.
    """

    items = [str(a) for a in args or ()]
    for flag in ("--directory", "--cwd"):
        if flag in items:
            index = items.index(flag)
            if index + 1 < len(items):
                return project_root / items[index + 1]
    return project_root


def _check_args(args, project_root: Path) -> list[dict]:
    base = _base_dir(args, project_root)
    resolved: list[dict] = []
    for arg in args or ():
        text = str(arg)
        if not _looks_like_path(text):
            continue
        candidate = Path(text)
        absolute = candidate.is_absolute()
        full = candidate if absolute else (base / candidate)
        resolved.append(
            {
                "arg": text,
                "absolute": absolute,
                "base": None if absolute else str(base),
                "exists": full.exists(),
            }
        )
    return resolved


def inspect(registry, project_root: Path, raw_servers: dict | None = None) -> list[ServerReport]:
    raw_servers = raw_servers or {}
    reports: list[ServerReport] = []
    for name in sorted(registry.names()):
        spec = registry.get(name)
        command_found = shutil.which(spec.command) is not None
        raw_env = ((raw_servers.get(name) or {}).get("env")) or {}
        report = ServerReport(
            name=name,
            command=spec.command,
            command_found=command_found,
            paths=_check_args(spec.args, project_root),
            env=_check_env(dict(spec.env or {}), raw_env),
        )
        if not command_found:
            report.problems.append(MISSING_COMMAND)
        for path in report.paths:
            if not path["exists"]:
                report.problems.append(MISSING_PATH)
            elif path["absolute"]:
                # Exists here, but an absolute path will not survive a move to
                # another host — the exact failure this tool was written for.
                report.problems.append(ABSOLUTE_PATH)
        for entry in report.env:
            if not entry["set"]:
                report.problems.append(UNSET_ENV)
        reports.append(report)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-diffable output")
    parser.add_argument(
        "--all",
        action="store_true",
        help="also inspect the native registry, not just the report path",
    )
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    sources = [("report", load_report_mcp_registry)]
    if args.all:
        sources.append(("native", load_mcp_registry))

    payload = {
        "host": os.uname().nodename,
        "project_root": str(project_root),
        "config": {
            "native": str(config_loader._NATIVE_CONFIG),
            "native_exists": config_loader._NATIVE_CONFIG.exists(),
            "legacy": str(config_loader._LEGACY_CONFIG),
            "legacy_exists": config_loader._LEGACY_CONFIG.exists(),
        },
        "registries": {},
    }

    unhealthy = 0
    for label, loader in sources:
        try:
            registry = loader()
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            payload["registries"][label] = {"error": str(exc)}
            unhealthy += 1
            continue
        config_path = _resolve_config_path(label)
        reports = inspect(registry, project_root, _raw_servers(config_path))
        unhealthy += sum(1 for r in reports if not r.healthy)
        payload["registries"][label] = {
            "source": str(config_path) if config_path else None,
            "servers": [
                {
                    "name": r.name,
                    "command": r.command,
                    "command_found": r.command_found,
                    "paths": r.paths,
                    "env": r.env,
                    "problems": sorted(set(r.problems)),
                }
                for r in reports
            ]
        }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 1 if unhealthy else 0

    print(f"host: {payload['host']}")
    print(f"root: {payload['project_root']}")
    cfg = payload["config"]
    print(
        f"config: native={'y' if cfg['native_exists'] else 'n'} "
        f"legacy={'y' if cfg['legacy_exists'] else 'n'}"
    )
    if cfg["legacy_exists"]:
        print("  ! report path prefers the legacy config (machine-local)")
    for label, data in payload["registries"].items():
        print(f"\n[{label}] source={data.get('source')}")
        if "error" in data:
            print(f"  ERROR: {data['error']}")
            continue
        for server in data["servers"]:
            mark = "ok " if not server["problems"] else "FAIL"
            print(f"  {mark} {server['name']:<14} command={server['command']}", end="")
            if not server["command_found"]:
                print(" (not on PATH)", end="")
            print()
            for path in server["paths"]:
                flag = "exists" if path["exists"] else "MISSING"
                kind = "abs" if path["absolute"] else "rel"
                print(f"        arg[{kind}] {flag}: {path['arg']}")
            for entry in server["env"]:
                state = "set" if entry["set"] else "UNSET"
                print(f"        env {entry['key']} <- {entry['source']} [{state}]")
            if server["problems"]:
                print(f"        problems: {', '.join(sorted(set(server['problems'])))}")

    print(f"\nunhealthy servers: {unhealthy}")
    return 1 if unhealthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
