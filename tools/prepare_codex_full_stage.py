"""Prepare NEW read-tool diagnostic stage + separate private host manifest.

Never changes an existing smoke stage or production repository. No providers,
models, order adapters or notification transports are started. Provider source
and executable pins are verified before staging; no uv/npx/package downloads.
The caller supplies PERPLEXITY_API_KEY only to the later host wrapper process.
KR's vetted read provider may itself load the production repo environment; its
host filesystem is trusted, not a secret-filesystem sandbox.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import codex_probe_mcp_bridge as bridge
from tools import codex_probe_sandbox as sandbox
from tools import prepare_codex_probe_stage as smoke


def prepare(root, host_root, repo, auth, *, as_of_date, kr_public_diagnostic=False):
    if any(".." in Path(path).parts for path in (root, host_root)):
        raise ValueError("parent_traversal_rejected")
    root, host_root, repo = Path(root).absolute(), Path(host_root).absolute(), Path(repo).resolve()
    anchor = date.fromisoformat(as_of_date)
    if type(kr_public_diagnostic) is not bool:
        raise ValueError("explicit_kr_diagnostic_flag_required")
    if (root.exists() or host_root.exists() or root.is_relative_to(host_root) or host_root.is_relative_to(root)
            or any(path.is_symlink() for base in (root, host_root) for path in (base, *base.parents))
            or any(host_root.is_relative_to(Path(exposed)) for exposed in ("/usr", "/lib", "/lib64"))):
        raise ValueError("new_separate_canonical_stages_required")
    pins = bridge.verified_provider_pins(prepare_node_snapshot=True)
    result = smoke.prepare(root, repo, Path(auth).resolve())
    host_root.mkdir(mode=0o700, parents=True)
    bridge.snapshot_node_runtime(host_root)
    smoke.copy_regular(Path(bridge.__file__), host_root / "read_bridge_host.py")
    (host_root / "read_bridge_host.py").chmod(0o600)
    smoke.write_private(root / "mcp/read_bridge_client.py", bridge.client_source())
    smoke.copy_regular(Path(sandbox.__file__), root / "wrapper.py")
    (root / "wrapper.py").chmod(0o700)
    runtime = root / "runtime"
    for market, profile in (("KR", "kr_trading"), ("US", "us_trading")):
        path = root / "home" / (profile + ".config.toml")
        contents = path.read_text()
        for name in ("perplexity", "kospi_kosdaq" if market == "KR" else "yahoo_finance"):
            contents += "\n" + "\n".join([
                f"[mcp_servers.{name}]", f"command = {json.dumps(str(runtime / 'bin/python3.11'))}",
                "args = " + json.dumps([str(root / "mcp/read_bridge_client.py"), "--client", f"/mcp-{name}.sock"]),
                "env = { PYTHONHOME = " + json.dumps(str(runtime)) + ", LD_LIBRARY_PATH = " + json.dumps(str(runtime / "lib")) + " }",
                "enabled_tools = " + json.dumps(sorted(bridge.TOOLS[name])),
                'default_tools_approval_mode = "approve"', "required = true",
                "startup_timeout_sec = 30", "tool_timeout_sec = 120", "",
            ])
        smoke.write_private(path, contents)
    manifest = {"version": 1, "stage_root": str(root), "providers": pins,
                "kr_profile": bridge.KR_PUBLIC_DIAGNOSTIC if kr_public_diagnostic else None,
                "bridge_sha256": bridge.file_sha256(host_root / "read_bridge_host.py"),
                "client_sha256": bridge.file_sha256(root / "mcp/read_bridge_client.py")}
    smoke.write_private(host_root / "host-manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    schema = {"type": "object", "properties": {"diagnostic": {"type": "string"}, "missing": {"type": "array"}}, "required": ["diagnostic", "missing"]}
    system = ("This is a READ-TOOL BACKEND DIAGNOSTIC, not a trading decision or agent-parity test. "
              "Use only the configured read MCP tools. Never write data, submit orders, send messages, use shell/files or fabricate missing data. "
              'Return exactly one JSON object with diagnostic="read_tool_probe" and missing=[names of unavailable reads].')
    cases = []
    # Default KR remains blocked. Public opt-in is tool-contract diagnostics,
    # NOT the production KIS vendor/auth path or exact historical market cap.
    markets = ("US", "KR") if kr_public_diagnostic else ("US",)
    for market in markets:
        market_read = (f"get_stock_ohlcv for ticker 005930 from {(anchor - timedelta(days=30)).strftime('%Y%m%d')} to {anchor.strftime('%Y%m%d')}"
                       if market == "KR" else "get_historical_stock_prices for AAPL, period 1mo, interval 1d")
        zone, domain, company = ("Asia/Seoul", "samsung.com", "Samsung Electronics") if market == "KR" else ("America/New_York", "sec.gov", "Apple")
        # This is model-facing diagnostic prose, never a constructed SQL query.
        user = (f"Call get_current_time for {zone}. List isolated SQLite tables and SELECT mode FROM fixture_metadata. "  # nosec B608  # nosemgrep
                f"Call {market_read}. Call perplexity_ask with a bounded user message locating the latest official financial filing for {company}, "
                f"search_domain_filter=[\"{domain}\"], search_recency_filter=year. State which reads are unavailable, never invent observations.")
        cases.append({"market": market, "action": "BUY", "system_prompt": system, "user_prompt": user, "schema": schema,
                      "system_sha256": smoke.sha(system), "user_sha256": smoke.sha(user),
                      "schema_sha256": smoke.sha(json.dumps(schema, sort_keys=True, separators=(",", ":"))),
                      "deviations": ["fixture_portfolio", "synthetic_report", "missing_ancillary_load", "diagnostic_backend_only", "no_fallback", "isolated_sqlite"]})
        if market == "KR":
            cases[-1]["deviations"].append("market_data_source_order_deviation_no_kis")
    smoke.write_private(root / "read-tool-fixture.json", json.dumps({"version": 1, "cases": cases}, ensure_ascii=False))
    result.update(mode="READ_TOOL_BACKEND_DIAGNOSTIC", market_and_news_tools="HOST_READ_BRIDGES",
                  production_parity=False, host_manifest_sha256=bridge.file_sha256(host_root / "host-manifest.json"),
                  host_credential_env_required=["PERPLEXITY_API_KEY"],
                  provider_boundary="TRUSTED_HOST_PROVIDER_MAY_READ_HOST_SECRETS_NOT_MODEL_SANDBOX",
                  provider_deviations=["fixed_python_instead_of_pyenv_shim", "new_process_per_connection",
                                       "minimal_host_environment", "private_provider_cwd_and_cache",
                                       "read_only_provider_filesystem", "installed_dependencies_trusted_not_fully_hashed",
                                       "pinned_node_private_runtime_copy"],
                  preparation_reference_date=as_of_date, case_count=len(cases), markets=list(markets),
                  kr_status="public_diagnostic_configured_not_executed" if kr_public_diagnostic else "auth_boundary_pending",
                  kr_production_auth_status="auth_boundary_pending", us_status="configured_not_executed")
    if kr_public_diagnostic:
        result["provider_deviations"].append("market_data_source_order_deviation_no_kis")
        result["kr_public_limitations"] = list(bridge.KR_PUBLIC_LIMITATIONS)
    smoke.write_private(root / "stage-manifest.json", json.dumps(result, sort_keys=True, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--host-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--auth", type=Path, required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--kr-public-diagnostic", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps({"status": "prepared", **prepare(args.run_root, args.host_root, args.repo, args.auth,
                                                         as_of_date=args.as_of_date, kr_public_diagnostic=args.kr_public_diagnostic)}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "failed", "category": "full_stage_preparation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
