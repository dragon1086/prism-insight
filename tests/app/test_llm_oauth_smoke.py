from __future__ import annotations

from pathlib import Path

import pytest

from cores.llm.ports import LLMBackend, LLMResult
from prism_app import llm_oauth_smoke
from prism_app.llm_oauth_smoke import OAuthSmokeResponse, run_oauth_smoke
from prism_app.oauth_llm import CHATGPT_OAUTH_DEFAULT_MODEL


class Backend(LLMBackend):
    name = "oauth-fixture"

    def __init__(self) -> None:
        self.spec = None
        self.user_input = None

    async def run(self, spec, user_input):
        self.spec = spec
        self.user_input = user_input
        return LLMResult(
            text="",
            structured=OAuthSmokeResponse(status="OK", transport="chatgpt_oauth"),
        )


class Runtime:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.exited = False

    async def __aenter__(self):
        return self.backend

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True


@pytest.mark.asyncio
async def test_oauth_smoke_uses_one_tool_free_structured_turn() -> None:
    backend = Backend()
    runtime = Runtime(backend)

    result = await run_oauth_smoke(runtime=runtime, model_id="gpt-5.2")

    assert result == {
        "stage": "PHASE1_OAUTH_LLM_SMOKE",
        "auth_mode": "chatgpt_oauth",
        "model_id": "gpt-5.2",
        "structured_schema": "OAuthSmokeResponse",
        "status": "OK",
        "tool_count": 0,
        "broker_called": False,
        "operational_readiness": False,
    }
    assert backend.spec.mcp_servers == ()
    assert backend.spec.params.max_iterations == 1
    assert backend.spec.output_schema is OAuthSmokeResponse
    assert runtime.exited is True


def test_oauth_smoke_cli_persists_only_sanitized_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    output = tmp_path / "oauth-smoke.json"

    async def fail(**kwargs):
        raise RuntimeError("secret provider detail")

    monkeypatch.setattr(llm_oauth_smoke, "run_oauth_smoke", fail)

    assert llm_oauth_smoke.main(["--output", str(output)]) == 2
    rendered = output.read_text(encoding="utf-8")
    assert "OAUTH_LLM_UNAVAILABLE" in rendered
    assert "secret provider detail" not in rendered
    assert "OAUTH_LLM_UNAVAILABLE" in capsys.readouterr().out


def test_oauth_smoke_uses_the_shared_oauth_default_model() -> None:
    assert CHATGPT_OAUTH_DEFAULT_MODEL == "gpt-5.4-mini"
    assert llm_oauth_smoke._parser().parse_args([]).model == CHATGPT_OAUTH_DEFAULT_MODEL
