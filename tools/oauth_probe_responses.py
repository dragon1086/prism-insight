"""OAuth/Codex probe for the *Responses API* path — the path production
trading actually uses (OpenAIResponsesLLM).

Run this ON db-server (where the ChatGPT OAuth token is valid):

    cd /root/prism-insight && python tools/oauth_probe_responses.py

Gating question: does the ChatGPT/Codex OAuth backend serve gpt-5.6-*
through the Responses API — plain AND with a function tool + reasoning
effort (the exact shape the trading agent sends)? If Codex rejects the
model, the OAuth/subscription path cannot run gpt-5.6 and we must either
map it in cores/chatgpt_proxy/api_translator.py or run those calls in
direct-API-key mode.

Exit code 0 = every gpt-5.6 probe returned OK; 1 = at least one failed
(so it is safe to gate a deploy on this script's exit status).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

REPO_ROOT = os.environ.get("PRISM_ROOT") or str(Path(__file__).resolve().parents[1])
if sys.path[0] != REPO_ROOT:
    sys.path.insert(0, REPO_ROOT)

SCRATCH_PORT = int(os.environ.get("PRISM_PROBE_PORT", "18799"))
PROMPT = "In one word, answer: is the sky blue? Reply yes or no."

# (model, reasoning_effort) — mirrors the production picks.
PROBES = [
    ("gpt-5.5", None),          # baseline: known-good through Codex today
    ("gpt-5.6-sol", "high"),    # trading pick
    ("gpt-5.6-terra", "medium"),  # report pick
    ("gpt-5.6-luna", "low"),    # translation pick
]

# A trivial function tool, so we exercise the "tool + reasoning" shape that
# the trading agent uses (the shape chat.completions rejects for gpt-5.6).
TOOL = {
    "type": "function",
    "name": "get_answer",
    "description": "Return the yes/no answer.",
    "parameters": {
        "type": "object",
        "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
        "required": ["answer"],
    },
}


async def _call(client, model, effort, with_tool):
    kwargs = dict(model=model, input=PROMPT, max_output_tokens=2000, store=False)
    if effort is not None:
        kwargs["reasoning"] = {"effort": effort}
    if with_tool:
        kwargs["tools"] = [TOOL]
    return await client.responses.create(**kwargs)


async def main() -> int:
    from cores.chatgpt_proxy import inject_env, start_proxy, stop_proxy

    inject_env(SCRATCH_PORT)
    print(f"[probe] OPENAI_BASE_URL={os.environ.get('OPENAI_BASE_URL')}")
    ok = await start_proxy(SCRATCH_PORT)
    print(f"[probe] start_proxy -> {ok}")
    if not ok:
        print("[probe] STOP: proxy failed to start (OAuth token expired? run "
              "`python -m cores.chatgpt_proxy.oauth_login`)")
        return 1

    from openai import AsyncOpenAI
    client = AsyncOpenAI()  # OPENAI_BASE_URL + placeholder key from inject_env

    failures = 0
    try:
        for with_tool in (False, True):
            label = "Responses + function tool" if with_tool else "Responses (plain)"
            print(f"\n=== {label} (trading uses tools) ===")
            for model, effort in PROBES:
                t = time.monotonic()
                try:
                    r = await _call(client, model, effort, with_tool)
                    dt = time.monotonic() - t
                    txt = (getattr(r, "output_text", "") or "")[:60]
                    print(f"  OK  {model:<16} effort={str(effort):<7} "
                          f"tool={with_tool!s:<5} {dt:6.1f}s  out={txt!r}")
                except Exception as e:  # noqa: BLE001
                    if model != "gpt-5.5":
                        failures += 1
                    print(f"  ERR {model:<16} effort={str(effort):<7} "
                          f"tool={with_tool!s:<5} {type(e).__name__}: {str(e)[:200]}")
    finally:
        await stop_proxy()

    print(f"\n[probe] done, proxy stopped. gpt-5.6 failures={failures}")
    if failures:
        print("[verdict] Codex/OAuth does NOT cleanly serve gpt-5.6 on the "
              "Responses path → do not deploy the swap as-is (map in "
              "api_translator or use direct-API-key mode).")
    else:
        print("[verdict] Codex/OAuth serves gpt-5.6 on the Responses path → "
              "safe to deploy the model swap.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
