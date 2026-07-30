"""Bounded actual ChatGPT OAuth LLM smoke for the Phase 1 backend seam."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from cores.llm.ports import AgentSpec, LLMBackend, LLMParams
from prism_app.oauth_llm import CHATGPT_OAUTH_DEFAULT_MODEL, ChatGPTOAuthRuntime


class OAuthSmokeResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: Literal["OK"]
    transport: Literal["chatgpt_oauth"]


class OAuthRuntime(Protocol):
    async def __aenter__(self) -> LLMBackend: ...

    async def __aexit__(self, exc_type, exc, traceback) -> None: ...


async def run_oauth_smoke(
    *, runtime: OAuthRuntime | None = None, model_id: str
) -> dict[str, object]:
    """Make one tool-free structured call and retain no raw response or token data."""

    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id must be non-empty")
    selected_runtime = runtime or ChatGPTOAuthRuntime()
    spec = AgentSpec(
        name="phase1_oauth_transport_smoke",
        instructions=(
            "Return exactly the requested structured status. Do not infer market facts, "
            "use tools, or include additional content."
        ),
        model=model_id,
        mcp_servers=(),
        output_schema=OAuthSmokeResponse,
        params=LLMParams(
            max_tokens=128,
            reasoning_effort="none",
            temperature=None,
            parallel_tool_calls=False,
            max_iterations=1,
        ),
    )
    async with selected_runtime as backend:
        result = await backend.run(
            spec,
            '{"status":"OK","transport":"chatgpt_oauth"}',
        )
    if not isinstance(result.structured, OAuthSmokeResponse):
        raise RuntimeError("OAuth LLM returned an incompatible structured response")
    return {
        "stage": "PHASE1_OAUTH_LLM_SMOKE",
        "auth_mode": "chatgpt_oauth",
        "model_id": model_id,
        "structured_schema": "OAuthSmokeResponse",
        "status": result.structured.status,
        "tool_count": 0,
        "broker_called": False,
        "operational_readiness": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded, tool-free ChatGPT OAuth LLM smoke."
    )
    parser.add_argument("--model", default=CHATGPT_OAUTH_DEFAULT_MODEL)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    prior_auth_mode = os.environ.get("PRISM_OPENAI_AUTH_MODE")
    if prior_auth_mode is None:
        os.environ["PRISM_OPENAI_AUTH_MODE"] = "chatgpt_oauth"
    try:
        try:
            payload = asyncio.run(run_oauth_smoke(model_id=args.model))
            exit_code = 0
        except Exception:  # noqa: BLE001 - external details and tokens are always redacted
            payload = {
                "stage": "PHASE1_OAUTH_LLM_SMOKE",
                "auth_mode": "chatgpt_oauth",
                "model_id": args.model,
                "status": "OAUTH_LLM_UNAVAILABLE",
                "tool_count": 0,
                "broker_called": False,
                "operational_readiness": False,
            }
            exit_code = 2
    finally:
        if prior_auth_mode is None:
            os.environ.pop("PRISM_OPENAI_AUTH_MODE", None)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
