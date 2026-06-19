"""Manual LIVE verification of the openai-agents backend.

Proves the Phase 2 migration target end-to-end against BOTH auth paths:
  --auth api    : openai-agents Runner (Responses) -> real OpenAI API (uses OPENAI_API_KEY env or secrets.yaml)
  --auth proxy  : openai-agents Runner (Responses) -> ChatGPT OAuth proxy /v1/responses -> Codex backend

NO trading, NO telegram, NO DB writes. Safe to run. Only effect: one (or a few)
LLM calls + optional read-only MCP tool calls.

PREREQUISITES:
  api mode   : OPENAI_API_KEY in .env / environment, or openai.api_key in mcp_agent.secrets.yaml.
  proxy mode : a valid ChatGPT OAuth token at ~/.config/prism-insight/chatgpt_auth.json
               (create via `python -m cores.chatgpt_proxy.oauth_login`).
  MCP        : the chosen server's command on PATH (uvx/npx/node/uv) + its key set in environment.
               MCP config loaded from cores/llm/mcp_servers.yaml (override with --config).
  Deps in .venv: openai>=2.9, openai-agents==0.7.0, aiohttp, pyyaml, python-dotenv.

USAGE (graduated):
  # API key path
  .venv/bin/python tools/verify_openai_agents_live.py --auth api
  .venv/bin/python tools/verify_openai_agents_live.py --auth api --mcp time
  .venv/bin/python tools/verify_openai_agents_live.py --auth api --mcp yahoo_finance \
      --prompt "What is the latest closing price of AAPL?"
  # OAuth proxy path
  .venv/bin/python tools/verify_openai_agents_live.py --auth proxy --mcp time
  # Override MCP config
  .venv/bin/python tools/verify_openai_agents_live.py --auth api --mcp time --config /path/to/config.yaml

Exit code 0 = PASS, non-zero = FAIL.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml

# Load .env early so env-based secrets and ${VAR} interpolation work.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOKEN_PATH = Path.home() / ".config" / "prism-insight" / "chatgpt_auth.json"
SECRETS_PATH = ROOT / "mcp_agent.secrets.yaml"


def _load_openai_key_from_secrets() -> str | None:
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    if SECRETS_PATH.exists():
        data = yaml.safe_load(SECRETS_PATH.read_text()) or {}
        key = (data.get("openai") or {}).get("api_key")
        if key and "example" not in key.lower():
            return key
    return None


def _disable_tracing() -> None:
    try:
        from agents import set_tracing_disabled

        set_tracing_disabled(True)
    except Exception:
        pass


async def _setup_api() -> bool:
    """Configure SDK to hit the real OpenAI API. Returns True on success."""
    key = _load_openai_key_from_secrets()
    if not key:
        print("[FAIL] No OpenAI API key in OPENAI_API_KEY env or mcp_agent.secrets.yaml (openai.api_key).")
        return False
    os.environ["OPENAI_API_KEY"] = key
    os.environ.pop("OPENAI_BASE_URL", None)  # ensure we hit real api.openai.com
    _disable_tracing()
    # Responses is the agents-SDK default; be explicit + bind a clean client.
    from agents import set_default_openai_api, set_default_openai_client, set_default_openai_key
    from openai import AsyncOpenAI

    set_default_openai_client(AsyncOpenAI(api_key=key))
    set_default_openai_api("responses")
    set_default_openai_key(key)
    print(f"[info] auth=api  endpoint=api.openai.com  key=set({len(key)} chars)")
    return True


async def _setup_proxy() -> bool:
    """Start the OAuth proxy and point the SDK at it. Returns True on success."""
    if not TOKEN_PATH.exists():
        print(f"[FAIL] ChatGPT OAuth token not found at {TOKEN_PATH}.")
        print("       Run: python -m cores.chatgpt_proxy.oauth_login")
        return False
    from cores.chatgpt_proxy import inject_env, start_proxy
    from cores.llm.backends.openai_agents_backend import configure_openai_agents_for_proxy

    _disable_tracing()
    inject_env()
    started = await start_proxy()
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    print(f"[info] auth=proxy  started={started}  base_url={base_url}")
    configure_openai_agents_for_proxy(
        base_url, os.environ.get("OPENAI_API_KEY", "chatgpt-oauth-placeholder")
    )
    return True


async def _teardown_proxy() -> None:
    try:
        from cores.chatgpt_proxy import stop_proxy

        await stop_proxy()
    except Exception:
        pass


async def _run(args: argparse.Namespace) -> int:
    from cores.llm.backends.openai_agents_backend import OpenAIAgentsBackend
    from cores.llm.ports import AgentSpec, LLMParams

    if args.auth == "api":
        if not await _setup_api():
            return 2
    else:
        if not await _setup_proxy():
            return 2

    try:
        registry = None
        if args.mcp:
            from cores.llm.config_loader import load_mcp_registry
            try:
                registry = load_mcp_registry(args.config)
            except FileNotFoundError as exc:
                print(f"[FAIL] MCP requested but config not found: {exc}")
                return 3
            missing = [n for n in args.mcp if n not in registry.names()]
            if missing:
                print(f"[FAIL] MCP server(s) not in config: {missing}")
                print(f"       Registered: {registry.names()}")
                return 4

        spec = AgentSpec(
            name="live-verify",
            instructions="You are a verification assistant. Answer briefly and factually.",
            model=args.model,
            mcp_servers=tuple(args.mcp),
            params=LLMParams(
                max_tokens=args.max_tokens,
                reasoning_effort=args.reasoning,
                max_iterations=args.max_iters,
            ),
        )

        backend = OpenAIAgentsBackend(registry)
        print(f"[info] running: model={args.model} mcp={list(args.mcp)} reasoning={args.reasoning}")
        result = await backend.run(spec, args.prompt)

        print("\n===== RESULT =====")
        print("text        :", (result.text or "").strip()[:1500])
        print("response_id :", result.response_id)
        print("usage       :", result.usage)
        print("==================")
        if not (result.text or "").strip():
            print("[WARN] empty text — check proxy logs / Codex field rejections.")
            return 5
        print(f"[PASS] openai-agents ({args.auth}) -> Responses roundtrip OK"
              + (f" + MCP {list(args.mcp)}" if args.mcp else ""))
        return 0
    finally:
        if args.auth == "proxy":
            await _teardown_proxy()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth", choices=["api", "proxy"], default="proxy", help="auth path to verify")
    ap.add_argument("--mcp", nargs="*", default=[], help="MCP server names from mcp_agent.config.yaml")
    ap.add_argument("--model", default="gpt-5.4-mini")
    ap.add_argument("--prompt", default="In one sentence, confirm you can respond.")
    ap.add_argument("--reasoning", default="none", help="none|low|medium|high")
    ap.add_argument("--max-tokens", type=int, default=2000, dest="max_tokens")
    ap.add_argument("--max-iters", type=int, default=5, dest="max_iters")
    ap.add_argument("--config", default=None, help="MCP config YAML path (default: auto-detect via load_mcp_registry search order)")
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
