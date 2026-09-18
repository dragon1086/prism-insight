"""Responses API evaluator-optimizer workflow for Telegram summaries."""

from __future__ import annotations

from typing import Any

from mcp_agent.workflows.evaluator_optimizer.evaluator_optimizer import (
    EvaluationResult,
    QualityRating,
)

from cores.llm.agent_bridge import ensure_openai_agents_configured, spec_from_mcp_agent
from cores.llm.backends.openai_agents_backend import OpenAIAgentsBackend
from cores.llm.mcp_registry import McpServerRegistry
from cores.llm.ports import AgentSpec, LLMParams


_backend: OpenAIAgentsBackend | None = None


def _get_backend() -> OpenAIAgentsBackend:
    global _backend
    if _backend is None:
        ensure_openai_agents_configured()
        _backend = OpenAIAgentsBackend(McpServerRegistry({}))
    return _backend


def _evaluation_prompt(original_request: str, response: str, iteration: int) -> str:
    return f"""Evaluate the Telegram summary against the original request and its supplied KIS evidence.

Original request:
{original_request}

Current response (iteration {iteration + 1}):
{response}

Return the structured evaluation required by your instructions.
"""


def _refinement_prompt(
    original_request: str,
    response: str,
    evaluation: EvaluationResult,
) -> str:
    focus = ", ".join(evaluation.focus_areas) or "none"
    return f"""Improve the Telegram summary using the evaluator feedback while preserving the supplied KIS evidence.

Original request:
{original_request}

Current response:
{response}

Evaluator feedback:
{evaluation.feedback}

Focus areas: {focus}

Return only the revised Telegram summary.
"""


async def run_telegram_summary_workflow(
    *,
    optimizer: Any,
    evaluator: Any,
    message: str,
    model: str,
    reasoning_effort: str,
    backend: OpenAIAgentsBackend | None = None,
    max_refinements: int = 3,
    max_tokens: int = 6000,
) -> str:
    """Generate, evaluate, and refine a Telegram summary via Responses API."""
    active_backend = backend or _get_backend()
    params = LLMParams(
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        max_iterations=2,
    )
    optimizer_spec = spec_from_mcp_agent(optimizer, model=model, params=params)
    evaluator_base = spec_from_mcp_agent(evaluator, model=model, params=params)
    evaluator_spec = AgentSpec(
        name=evaluator_base.name,
        instructions=evaluator_base.instructions,
        model=evaluator_base.model,
        mcp_servers=evaluator_base.mcp_servers,
        output_schema=EvaluationResult,
        params=evaluator_base.params,
    )

    response = (await active_backend.run(optimizer_spec, message)).text
    best_response = response
    best_rating = QualityRating.POOR

    for iteration in range(max_refinements + 1):
        evaluated = await active_backend.run(
            evaluator_spec,
            _evaluation_prompt(message, response, iteration),
        )
        evaluation = evaluated.structured
        if not isinstance(evaluation, EvaluationResult):
            raise RuntimeError("Telegram summary evaluator returned no structured result")

        if evaluation.rating.value > best_rating.value:
            best_rating = evaluation.rating
            best_response = response

        if (
            evaluation.rating.value >= QualityRating.EXCELLENT.value
            or not evaluation.needs_improvement
            or iteration >= max_refinements
        ):
            break

        response = (
            await active_backend.run(
                optimizer_spec,
                _refinement_prompt(message, response, evaluation),
            )
        ).text

    return best_response
