"""Validate isolated production-context fixtures; never run an unverified toolset.

This is a diagnostic backend harness, NOT agent-path or end-to-end SHADOW.
Execution requires the reviewed Linux OS-isolated wrapper, a dedicated staged
root, and the pinned server Codex binary. Configuration validation alone cannot
prove a safe effective built-in toolset. No legacy fallback is imported.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
import tomllib

DEVIATIONS = frozenset({
    "fixture_portfolio", "missing_journal", "missing_trend", "missing_rank",
    "synthetic_report", "missing_ancillary_load", "isolated_sqlite",
    "diagnostic_backend_only", "no_fallback",
    "market_data_source_order_deviation_no_kis",
})
READ_TOOLS = {
    "time": {"get_current_time"},
    "sqlite": {"list_tables", "describe_table", "read_query"},
    "perplexity": {"perplexity_ask"},
    "kospi_kosdaq": {"get_stock_ohlcv", "get_stock_market_cap",
                     "get_stock_trading_volume", "get_index_ohlcv", "get_ticker_name"},
    "yahoo_finance": {"get_historical_stock_prices", "get_stock_info",
        "get_yahoo_finance_news", "get_stock_actions", "get_financial_statement",
        "get_holder_info", "get_option_expiration_dates", "get_option_chain",
        "get_recommendations"},
}
MANDATORY_DEVIATIONS = {"diagnostic_backend_only", "no_fallback", "isolated_sqlite"}
BLOCKER = "runtime_tool_isolation_unverified"


class ProbeRejected(ValueError):
    """Only fixed safe category strings cross the diagnostic boundary."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_digest(value: dict) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _local_file(path: Path, root: Path) -> Path:
    # Reject symlink components and hardlinks: resolving under root alone does not
    # prevent a production inode from being reachable through a local alias.
    if any(".." in candidate.parts for candidate in (path, root)):
        raise ProbeRejected("unsafe_path")
    path = path.absolute()
    if not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
        raise ProbeRejected("unsafe_path")
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ProbeRejected("unsafe_file")
    return path


def load_fixture(path: Path, root: Path) -> list[dict]:
    try:
        raw = _local_file(path, root).read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise ProbeRejected("fixture_too_large")
        bundle = json.loads(raw)
        if set(bundle) != {"version", "cases"} or bundle["version"] != 1:
            raise ProbeRejected("invalid_fixture")
        cases = bundle["cases"]
        if not isinstance(cases, list) or not 1 <= len(cases) <= 64:
            raise ProbeRejected("invalid_cases")
        keys = {"market", "action", "system_prompt", "user_prompt", "schema",
                "system_sha256", "user_sha256", "schema_sha256", "deviations"}
        for case in cases:
            if not isinstance(case, dict) or set(case) != keys:
                raise ProbeRejected("invalid_case")
            if case["market"] not in {"KR", "US"} or case["action"] not in {"SELL", "BUY"}:
                raise ProbeRejected("invalid_cell")
            for field in ("system", "user"):
                prompt = case[field + "_prompt"]
                if not isinstance(prompt, str) or not prompt or digest(prompt) != case[field + "_sha256"]:
                    raise ProbeRejected("prompt_hash_mismatch")
            schema = case["schema"]
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ProbeRejected("invalid_schema")
            if schema_digest(schema) != case["schema_sha256"]:
                raise ProbeRejected("schema_hash_mismatch")
            deviations = case["deviations"]
            if not isinstance(deviations, list) or any(not isinstance(d, str) or d not in DEVIATIONS for d in deviations):
                raise ProbeRejected("invalid_deviation")
        return cases
    except ProbeRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ProbeRejected("invalid_fixture") from None


def validate_home(home: Path, root: Path, markets: set[str], *, smoke: bool = False) -> None:
    """Reject unsafe configuration, but do not claim an effective-tool audit.

    MCP command binaries/packages still need review; a read-tool name is not a
    security boundary for an arbitrary server implementation. The separate
    OS-isolated wrapper remains mandatory even if this validation passes.
    """
    try:
        config = tomllib.loads(_local_file(home / "config.toml", root).read_text())
        if set(config) - {"model", "service_tier", "cli_auth_credentials_store", "features", "web_search"}:
            raise ProbeRejected("unsafe_root_config")
        if config.get("cli_auth_credentials_store") != "file":
            raise ProbeRejected("unsafe_auth_store")
        features = config.get("features", {})
        if not isinstance(features, dict) or any(v is not False for k, v in features.items() if k not in {"fast_mode", "code_mode_host"}):
            raise ProbeRejected("unsafe_features")
        if features.get("code_mode_host") is not True:
            raise ProbeRejected("code_mode_host_required")
        if features.get("shell_tool") is not False:
            raise ProbeRejected("shell_disable_missing")
        from tools.codex_probe_sandbox import DISABLED_FEATURES
        if config.get("web_search") != "disabled" or any(features.get(name) is not False for name in DISABLED_FEATURES):
            raise ProbeRejected("capability_disable_missing")
        # Auth symlink is intentionally allowed; never read or emit credentials.
        if not (home / "auth.json").is_file():
            raise ProbeRejected("missing_auth")
        for market in markets:
            profile = "kr_trading" if market == "KR" else "us_trading"
            config = tomllib.loads(_local_file(home / (profile + ".config.toml"), root).read_text())
            if set(config) != {"mcp_servers"}:
                raise ProbeRejected("unsafe_profile")
            servers = config["mcp_servers"]
            expected = {"sqlite", "time", "perplexity", "kospi_kosdaq" if market == "KR" else "yahoo_finance"}
            if smoke:
                expected = {"sqlite", "time"}
            if set(servers) != expected:
                raise ProbeRejected("unsafe_servers")
            for name, server in servers.items():
                allowed = {"command", "args", "cwd", "env", "env_vars", "enabled_tools",
                           "default_tools_approval_mode", "required", "startup_timeout_sec", "tool_timeout_sec"}
                if set(server) - allowed or set(server.get("enabled_tools", [])) != READ_TOOLS[name]:
                    raise ProbeRejected("unsafe_tools")
                if server.get("default_tools_approval_mode") != "approve" or server.get("required") is not True:
                    raise ProbeRejected("unsafe_tool_policy")
                if name in {"perplexity", "kospi_kosdaq", "yahoo_finance"}:
                    expected_env = {"PYTHONHOME": str(root / "runtime"), "LD_LIBRARY_PATH": str(root / "runtime/lib")}
                    if (server.get("command") != str(root / "runtime/bin/python3.11")
                            or server.get("args") != [str(root / "mcp/read_bridge_client.py"), "--client", f"/mcp-{name}.sock"]
                            or server.get("env") != expected_env or "env_vars" in server):
                        raise ProbeRejected("unsafe_bridge_client")
                if name == "sqlite":
                    args = server.get("args", [])
                    if args.count("--db-path") != 1:
                        raise ProbeRejected("isolated_database_missing")
                    database = Path(args[args.index("--db-path") + 1])
                    if not database.is_absolute():
                        raise ProbeRejected("unsafe_database")
                    _local_file(database, root)
    except ProbeRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        raise ProbeRejected("invalid_config") from None


def _manifest(cases: list[dict]) -> dict:
    return {"mode": "diagnostic_only", "execution_enabled": False,
            "parity": "incomplete", "blocker": BLOCKER,
            "cases": [{"index": i, "market": c["market"], "action": c["action"],
                       "system_sha256": c["system_sha256"], "user_sha256": c["user_sha256"],
                       "schema_sha256": c["schema_sha256"],
                       "system_bytes": len(c["system_prompt"].encode()),
                       "user_bytes": len(c["user_prompt"].encode()),
                       "deviations": sorted(set(c["deviations"]) | MANDATORY_DEVIATIONS)}
                      for i, c in enumerate(cases)]}


async def _schedule_diagnostic(cases, *, invoke, concurrency: int = 2) -> list[dict]:
    """Invoke an async diagnostic backend adapter, never fallback.
    All SELL positions finish sequentially before bounded parallel BUY begins.
    """
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ProbeRejected("invalid_concurrency")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(index, case):
        async with semaphore:
            started = time.monotonic()
            record = {"index": index, "market": case["market"], "action": case["action"],
                      "category": "backend_error", "successful_mcp_calls": 0,
                      "successful_mcp_calls_by_server": {},
                      "mcp_count_basis": "transport_completed_status_only_not_financial_data_accuracy"}
            try:
                result = await invoke(case)
                parsed = json.loads(result.text)
                if not isinstance(parsed, dict):
                    record["category"] = "invalid_json_object"
                else:
                    calls = result.mcp_calls
                    safe_calls = all(c.server in READ_TOOLS and c.tool in READ_TOOLS[c.server] for c in calls)
                    count = sum(c.status == "completed" and not c.error for c in calls)
                    record["successful_mcp_calls"] = count
                    record["failed_mcp_calls"] = len(calls) - count
                    record["successful_mcp_servers"] = sorted({
                        c.server for c in calls if c.server in READ_TOOLS and c.tool in READ_TOOLS[c.server]
                        and c.status == "completed" and not c.error})
                    record["successful_mcp_calls_by_server"] = {
                        server: sum(c.server == server and c.tool in READ_TOOLS[server] and c.status == "completed" and not c.error for c in calls)
                        for server in record["successful_mcp_servers"]}
                    missing = parsed.get("missing")
                    record["model_declared_missing_count"] = len(missing) if isinstance(missing, list) else 0
                    record["category"] = "ok" if safe_calls and count else "missing_or_unsafe_mcp"
                    # JSON object validation is not schema or trading-risk validation.
                    record["schema_validated"] = False
                latency = result.latency_s
                if isinstance(latency, (float, int)) and math.isfinite(latency) and latency >= 0:
                    record["backend_latency_s"] = round(latency, 3)
            except (json.JSONDecodeError, TypeError):
                record["category"] = "invalid_json_object"
            except Exception:
                # Exception text can contain prompts, stderr or account details.
                record["category"] = "backend_error"
            record["elapsed_s"] = round(time.monotonic() - started, 3)
            return record

    records = []
    for i, case in enumerate(cases):
        if case["action"] == "SELL":
            records.append(await one(i, case))
    records.extend(await asyncio.gather(*(one(i, c) for i, c in enumerate(cases) if c["action"] == "BUY")))
    return sorted(records, key=lambda r: r["index"])


def _execution_adapter(args, root: Path):
    # Direct script execution needs the trusted repository module path, not cwd.
    repository = str(Path(__file__).resolve().parents[1])
    if repository not in sys.path:
        sys.path.insert(0, repository)
    from tools import codex_probe_sandbox as sandbox
    if sys.platform != "linux" or args.sandbox_wrapper is None:
        raise ProbeRejected("isolated_launcher_required")
    if os.environ.get("PRISM_PROBE_ROOT") != str(root):
        raise ProbeRejected("sandbox_root_mismatch")
    if os.environ.get("PRISM_PROBE_CODEX_BINARY") != sandbox.TRUSTED_CODEX_BINARY:
        raise ProbeRejected("sandbox_binary_mismatch")
    host_python = Path(sandbox.TRUSTED_HOST_PYTHON)
    if os.environ.get("PATH", "").split(os.pathsep)[0] != str(host_python.parent):
        raise ProbeRejected("untrusted_host_python_path")
    try:
        selected = host_python.parent / "python3"
        if not host_python.is_file() or selected.resolve(strict=True) != host_python.resolve(strict=True):
            raise ProbeRejected("untrusted_host_python")
        if not os.access(host_python, os.X_OK) or host_python.stat().st_mode & 0o022:
            raise ProbeRejected("untrusted_host_python")
    except OSError:
        raise ProbeRejected("untrusted_host_python") from None
    if args.codex_home.absolute() != root / "home":
        raise ProbeRejected("sandbox_home_mismatch")
    try:
        sandbox.validate_stage(root)
        wrapper = _local_file(args.sandbox_wrapper, root)
        if wrapper.read_bytes() != Path(sandbox.__file__).read_bytes() or not os.access(wrapper, os.X_OK):
            raise ProbeRejected("untrusted_wrapper")
    except sandbox.BoundaryError:
        raise ProbeRejected("unsafe_stage") from None
    kr_public_diagnostic = False
    if not getattr(args, "smoke", False):
        host_manifest = os.environ.get("PRISM_PROBE_HOST_MANIFEST")
        if not host_manifest:
            raise ProbeRejected("host_manifest_required")
        try:
            _, host_settings = sandbox.load_host_manifest(Path(host_manifest), root)
            kr_public_diagnostic = host_settings.get("kr_profile") == "KR_PUBLIC_DIAGNOSTIC"
        except sandbox.BoundaryError:
            raise ProbeRejected("invalid_host_manifest") from None
    from cores.llm.codex_oauth_fast_backend import generate_codex_fast_async
    _configure_backend_logging()
    async def invoke(case):
        return await generate_codex_fast_async(
            system_prompt=case["system_prompt"], user_prompt=case["user_prompt"],
            model=args.model, reasoning_effort=args.effort, timeout=args.timeout,
            codex_bin=str(wrapper), codex_home=str(root / "home"),
            mcp_profile="kr_trading" if case["market"] == "KR" else "us_trading",
            require_mcp_calls=True,
        )
    invoke.kr_public_diagnostic = kr_public_diagnostic
    return invoke


def _configure_backend_logging():
    """Enable only the backend's fixed stage record, never root-wide verbosity."""
    categories = {"start", "first_event", "mcp_started", "mcp_completed", "mcp_error",
        "agent_message", "model_final", "turn_completed", "turn_error", "success", "timeout", "cancelled",
        "launch_error", "process_io_error", "nonzero_exit", "missing_final",
        "missing_successful_mcp", "output_limit", "cleanup_unconfirmed", "cleanup_completed",
        "unsupported_platform", "incomplete_stdin"}
    enums = {"category": categories, "last_stage": categories,
             "model": {"gpt-6-astra", "gpt-5.6-sol", "invalid"},
             "effort": {"low", "medium", "high", "xhigh", "max", "ultra", "default", "invalid"},
             "profile": {"kr_trading", "us_trading", "none", "invalid"},
             "server": set(READ_TOOLS) | {"none", "other"},
             "tool": set().union(*READ_TOOLS.values()) | {"none", "other"},
             "stdin_closed": {"0", "1"}}
    numeric = {"timeout_s", "elapsed_s", "rc", "mcp_started", "mcp_completed", "mcp_errors", "mcp_pending", "tool_s"}
    byte_counts = {"stdin_total_bytes", "stdin_sent_bytes"}
    class SafeStageFilter(logging.Filter):
        def filter(self, record):
            try:
                message = record.getMessage()
                if not message.startswith("[CODEX_FAST] ") or record.exc_info:
                    return False
                pairs = [part.split("=", 1) for part in message.split()[1:]]
                fields = dict(pairs)
                if len(fields) != len(pairs) or set(fields) != set(enums) | numeric | byte_counts | {"request_id"}:
                    return False
                if any(fields[key] not in values for key, values in enums.items()):
                    return False
                request = fields["request_id"]
                if len(request) != 32 or any(c not in "0123456789abcdef" for c in request):
                    return False
                if any(not value.isascii() or not value.isdecimal() or len(value) > 20 for value in (fields[key] for key in byte_counts)):
                    return False
                if int(fields["stdin_sent_bytes"]) > int(fields["stdin_total_bytes"]):
                    return False
                return all((key == "rc" and fields[key] == "None") or math.isfinite(float(fields[key])) for key in numeric)
            except (ValueError, TypeError, KeyError):
                return False
    logger = logging.getLogger("cores.llm.codex_oauth_fast_backend")
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(SafeStageFilter())
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)


def main(argv=None) -> int:
    repository = str(Path(__file__).resolve().parents[1])
    if repository not in sys.path:
        sys.path.insert(0, repository)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Synthetic time/SQLite only; never production parity")
    parser.add_argument("--sandbox-wrapper", type=Path)
    parser.add_argument("--model", choices=["gpt-5.6-sol", "gpt-6-astra"], default="gpt-6-astra")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max", "ultra"], default="medium")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        root = args.run_root.absolute()
        if not root.is_dir() or root.is_symlink() or any(p.is_symlink() for p in root.parents):
            raise ProbeRejected("unsafe_run_root")
        cases = load_fixture(args.fixture, root)
        validate_home(args.codex_home.absolute(), root, {c["market"] for c in cases}, smoke=args.smoke)
        manifest = _manifest(cases)
        if args.smoke:
            manifest.update(mode="synthetic_smoke", parity="not_parity")
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600 or not 1 <= args.concurrency <= 4:
            raise ProbeRejected("invalid_budget")
        if args.execute and args.validate_only:
            raise ProbeRejected("conflicting_mode")
        manifest["status"] = "validated_only"
        if args.execute:
            invoke = _execution_adapter(args, root)
            if not args.smoke and any(case["market"] == "KR" for case in cases) and not getattr(invoke, "kr_public_diagnostic", False):
                raise ProbeRejected("kr_auth_boundary_pending")
            if getattr(invoke, "kr_public_diagnostic", False):
                manifest["kr_profile"] = "KR_PUBLIC_DIAGNOSTIC"
                manifest["kr_production_vendor_parity"] = False
                for case in manifest["cases"]:
                    if case["market"] == "KR":
                        case["deviations"] = sorted(set(case["deviations"]) | {"market_data_source_order_deviation_no_kis"})
            records = asyncio.run(_schedule_diagnostic(cases, invoke=invoke, concurrency=args.concurrency))
            for record in records:
                expected = {"time", "sqlite"}
                if not args.smoke:
                    expected |= {"perplexity", "kospi_kosdaq" if record["market"] == "KR" else "yahoo_finance"}
                record["missing_read_servers"] = sorted(expected - set(record.get("successful_mcp_servers", [])))
                if record["category"] == "ok" and (record["missing_read_servers"] or record.get("failed_mcp_calls") or record.get("model_declared_missing_count")):
                    record["category"] = "incomplete_read_tools"
            manifest.update(status="finished", execution_enabled=True, blocker=None,
                            outcomes=records, model=args.model, effort=args.effort,
                            timeout_s=args.timeout, concurrency=args.concurrency)
        print(json.dumps(manifest, sort_keys=True))
        return 1 if args.execute and any(r["category"] != "ok" for r in records) else 0
    except ProbeRejected as exc:
        print(json.dumps({"status": "rejected", "category": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
