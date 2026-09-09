"""Prepare a private, no-order diagnostic runtime from installed dependencies.

Never opens the production trading DB. The first fixture is deliberately a
synthetic time/SQLite smoke, NOT production parity. All generated files stay in
a new operator-selected directory; existing directories are never overwritten.
Run on db-server with its Python 3.11 interpreter, after reviewing the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import sysconfig

from packaging.requirements import Requirement

try:
    from .codex_probe_sandbox import DISABLED_FEATURES
except ImportError:  # Standalone staged bootstrap; no production package import.
    from codex_probe_sandbox import DISABLED_FEATURES


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def copy_regular(source, target):
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise ValueError("source_file_missing")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o700 if os.access(source, os.X_OK) else 0o600)


def dependencies(names):
    """Installed distribution closure only: no network/pip or extras guessed."""
    pending, found = list(names), {}
    while pending:
        name = pending.pop()
        dist = metadata.distribution(name)
        key = dist.metadata["Name"].lower().replace("_", "-")
        if key in found:
            continue
        found[key] = dist
        for requirement in dist.requires or []:
            requirement = Requirement(requirement)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return found


def stage_runtime(root, names):
    if sys.version_info[:2] != (3, 11):
        raise ValueError("python_311_required")
    runtime = root / "runtime"
    copy_regular(sys.executable, runtime / "bin/python3.11")
    lib = Path(sysconfig.get_path("stdlib"))
    excluded = {"site-packages", "__pycache__", "test", "tests", "ensurepip", "idlelib", "tkinter", "turtledemo"}
    for source in lib.rglob("*"):
        relative = source.relative_to(lib)
        if source.is_file() and not excluded.intersection(relative.parts) and source.suffix != ".pyc":
            copy_regular(source, runtime / "lib/python3.11" / relative)
    for source in (Path(sys.base_prefix) / "lib").glob("libpython3.11.so*"):
        copy_regular(source, runtime / "lib" / source.name)
    site = Path(sysconfig.get_path("purelib")).resolve()
    distributions = dependencies(names)
    for dist in distributions.values():
        for relative in dist.files or []:
            source = Path(dist.locate_file(relative)).absolute()
            # Ignore entry-point scripts, editable paths and bytecode.
            if not source.is_relative_to(site) or ".." in relative.parts or source.suffix == ".pyc":
                continue
            if source.is_file():
                copy_regular(source, runtime / "lib/python3.11/site-packages" / source.relative_to(site))
    return {name: dist.version for name, dist in sorted(distributions.items())}


def write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)


def write_smoke_fixture(root):
    """Real MCP connectivity only; no security-price or trading decision claim."""
    schema = {"type": "object", "properties": {"smoke": {"type": "boolean"}}, "required": ["smoke"]}
    system = 'Use only the configured time and SQLite read tools. Return exactly {"smoke":true} after both succeed. No other actions.'
    cases = []
    for market, zone in (("KR", "Asia/Seoul"), ("US", "America/New_York")):
        user = f'Call get_current_time for {zone}; list SQLite tables and read fixture_metadata. This is a SYNTHETIC no-order connectivity smoke, not a trading or production-parity evaluation.'
        cases.append({"market": market, "action": "SELL", "system_prompt": system,
                      "user_prompt": user, "system_sha256": sha(system), "user_sha256": sha(user),
                      "schema": schema, "schema_sha256": sha(json.dumps(schema, sort_keys=True, separators=(",", ":"))),
                      "deviations": ["fixture_portfolio", "missing_ancillary_load"]})
    write_private(root / "smoke-fixture.json", json.dumps({"version": 1, "cases": cases}))


def prepare(root, repo, auth):
    if ".." in root.parts:
        raise ValueError("parent_traversal_rejected")
    if root.exists() or root.is_symlink() or any(p.is_symlink() for p in root.parents):
        raise ValueError("new_canonical_stage_required")
    root.mkdir(mode=0o700, parents=True)
    write_private(root / ".isolated-codex-probe", "diagnostic-only-v1\n")
    versions = stage_runtime(root, ["mcp", "yfinance", "python-dotenv", "packaging"])
    # Use the exact production time implementation, not an invented clock.
    copy_regular(repo / "cores/llm/time_mcp_server.py", root / "mcp/time_server.py")
    # Existing SQLite server package: its database is an isolated fixture only.
    for source in (repo / "sqlite/src/mcp_server_sqlite").glob("*.py"):
        copy_regular(source, root / "mcp/mcp_server_sqlite" / source.name)
    write_private(root / "mcp/sqlite_main.py", "from mcp_server_sqlite import main\nmain()\n")
    database = root / "portfolio.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE fixture_metadata (mode TEXT NOT NULL)")
        db.execute("INSERT INTO fixture_metadata VALUES ('SYNTHETIC_SMOKE_NO_ORDERS')")
    database.chmod(0o600)
    copy_regular(auth, root / "home/auth.json")
    write_private(root / "home/config.toml", '''model = "gpt-6-astra"
service_tier = "fast"
cli_auth_credentials_store = "file"
web_search = "disabled"
[features]
fast_mode = true
code_mode_host = true
''' + "".join(f"{name} = false\n" for name in sorted(DISABLED_FEATURES)))
    # Smoke uses real time/SQLite only, with no invented market/news results.
    tools = {
        "time": ["get_current_time"],
        "sqlite": ["list_tables", "describe_table", "read_query"],
    }
    # Do not create fake market MCP results. A full profile must be supplied by
    # the subsequent parity stage; a minimal smoke profile is used explicitly.
    py = str(root / "runtime/bin/python3.11")
    profile = []
    for name in ("time", "sqlite"):
        args = [str(root / "mcp" / ("time_server.py" if name == "time" else "sqlite_main.py"))]
        if name == "sqlite":
            args += ["--db-path", str(database)]
        profile.extend([
            f"[mcp_servers.{name}]", f"command = {json.dumps(py)}",
            f"args = {json.dumps(args)}", f"enabled_tools = {json.dumps(tools[name])}",
            'env = { PYTHONHOME = ' + json.dumps(str(root / "runtime"))
            + ', LD_LIBRARY_PATH = ' + json.dumps(str(root / "runtime/lib"))
            + ', PYTHONPATH = ' + json.dumps(str(root / "mcp")) + ' }',
            'default_tools_approval_mode = "approve"', "required = true",
            "startup_timeout_sec = 30", "tool_timeout_sec = 30", "",
        ])
    for name in ("kr_trading", "us_trading"):
        write_private(root / "home" / (name + ".config.toml"), "\n".join(profile))
    manifest = {
        "mode": "SYNTHETIC_SMOKE_NO_ORDERS", "production_parity": False,
        "installed_distributions": versions, "market_and_news_tools": "NOT_STAGED",
        "source_time_sha256": sha((root / "mcp/time_server.py").read_text()),
        "production_database_opened": False,
    }
    write_private(root / "stage-manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    write_smoke_fixture(root)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--auth", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.run_root.absolute(), args.repo.resolve(), args.auth.resolve())
        print(json.dumps({"status": "prepared", **result}))
    except (OSError, ValueError, metadata.PackageNotFoundError):
        print(json.dumps({"status": "failed", "category": "stage_preparation_failed"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
