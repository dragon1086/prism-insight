"""Read-only, no-model MCP schema audit; never invoke provider tools.

Full schemas stay in an optional mode-0600 artifact, not LLM input/stdout.
Only named report servers are launched, and package launchers run offline.
Availability means initialize/list_tools worked, NOT provider data access.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from contextlib import closing
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVERS = ("perplexity", "firecrawl", "kospi_kosdaq", "yahoo_finance", "time", "webresearch", "deepsearch")
TOKEN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,120}$")


def identifier(value):
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else "REDACTED"


def compact_tool(tool):
    """Omit descriptions/defaults/examples: they can be huge or contain secrets."""
    schema = tool.get("inputSchema") or {}
    return {
        "name": identifier(tool.get("name")),
        "parameters": {identifier(k): identifier(v.get("type", "union"))
                       for k, v in schema.get("properties", {}).items()
                       if isinstance(v, dict)},
        "required": [identifier(k) for k in schema.get("required", [])],
        "structured_output_schema": bool(tool.get("outputSchema")),
        "response_size_guarantee": "none_in_mcp_schema",
    }


async def collect_session(session):
    initialized = await session.initialize()
    collected = []
    cursor = None
    seen = set()
    for _ in range(100):
        page = await session.list_tools(cursor=cursor)
        collected.extend(t.model_dump(mode="json", exclude_none=True) for t in page.tools)
        cursor = page.nextCursor
        if not cursor:
            return {"server_version": identifier(initialized.serverInfo.version), "tools": collected}
        if cursor in seen:
            raise ValueError("repeated_cursor")
        seen.add(cursor)
    raise ValueError("page_limit")


async def inspect_server(name, operation, timeout=30):
    try:
        detail = await asyncio.wait_for(operation(), timeout)
        return {"server": name, "availability": "schema_reachable",
                "data_access_verified": False, **detail}
    except asyncio.TimeoutError:
        return {"server": name, "availability": "timeout", "tools": []}
    except Exception as exc:  # noqa: BLE001 — audit boundary must sanitize every provider failure
        # Exception messages frequently include launch args, headers or env.
        return {"server": name, "availability": "unavailable",
                "error_type": type(exc).__name__, "tools": []}


async def list_registered(spec):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client

    command = Path(spec.command).name
    args = list(spec.args)
    if command in {"npx", "uv"}:
        args.insert(0, "--offline")
    elif spec.name not in {"time", "kospi_kosdaq"}:
        raise ValueError("unsupported_launcher")
    params = StdioServerParameters(command=spec.command, args=args,
                                   env={**get_default_environment(), **spec.env}, cwd=spec.cwd or str(ROOT))
    # Subprocess stderr can contain credentials; it must not enter the transcript.
    with closing(await asyncio.to_thread(open, os.devnull, "w")) as sink:
        async with (stdio_client(params, errlog=sink) as (read, write),
                    ClientSession(read, write) as session):
            return await collect_session(session)


def summary(records):
    return [{**{k: v for k, v in r.items() if k != "tools"},
             "tools": [compact_tool(t) for t in r.get("tools", [])]} for r in records]


async def run(names, config, env_file):
    sys.path.insert(0, str(ROOT))
    if env_file:
        from dotenv import dotenv_values
        # Read only relevant fields; never export arbitrary dotenv launcher overrides.
        allowed = {"FIRECRAWL_API_KEY", "PERPLEXITY_API_KEY", "ARCHIVE_API_KEY"}
        for key, value in dotenv_values(env_file).items():
            if key in allowed and value:
                os.environ.setdefault(key, value)
    os.environ.setdefault("PRISM_MCP_PYTHON", sys.executable)
    os.environ.setdefault("PRISM_REPO_ROOT", str(ROOT))
    from cores.llm.config_loader import load_report_mcp_registry
    registry = load_report_mcp_registry(config)
    results = []
    for name in names:
        if name not in registry.names():
            results.append({"server": name, "availability": "not_registered", "tools": []})
            continue
        spec = registry.get(name)
        result = await inspect_server(name, partial(list_registered, spec))
        # Package version only; never persist full command/env or resolved config.
        result["package_versions"] = [a for a in spec.args if isinstance(a, str)
                                      and re.fullmatch(r"@?[\w/-]+(?:@|==)\d[\w.+-]*", a)]
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servers", nargs="+", choices=SERVERS, default=list(SERVERS))
    parser.add_argument("--config", type=Path, default=ROOT / "cores/llm/mcp_servers.yaml")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = asyncio.run(run(args.servers, args.config, args.env_file))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL prevents overwriting/following an existing sensitive file/symlink.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(results, file, ensure_ascii=False, indent=2)
    print(json.dumps(summary(results), ensure_ascii=False))


if __name__ == "__main__":
    main()
