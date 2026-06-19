"""
Strangler shim: wraps mcp_agent so domain code can call LLMBackend.run().

This adapter is the ONLY place in the codebase that imports mcp_agent.
It will be removed in Phase 5 once all call sites have migrated to the
openai-agents SDK backend.

Import guard: the module-level try/except means this file can be imported
(and tests collected) even when mcp_agent is not installed.  A clear
RuntimeError is raised at call time instead.
"""

from typing import Any

# --- SDK import guard ---------------------------------------------------
try:
    from mcp_agent.agents.agent import Agent
    from mcp_agent.workflows.llm.augmented_llm import RequestParams

    _mcp_agent_available = True
except ImportError:
    Agent = None  # type: ignore[assignment,misc]
    RequestParams = None  # type: ignore[assignment]
    _mcp_agent_available = False
# ------------------------------------------------------------------------

from cores.llm.ports import AgentSpec, LLMBackend, LLMResult


class McpAgentBackend(LLMBackend):
    """LLMBackend adapter that delegates to the mcp-agent framework.

    Must be run in the environment where mcp-agent is installed (db-server).
    Calling ``run()`` when mcp-agent is absent raises a clear RuntimeError;
    the constructor itself never fails.
    """

    name = "mcp_agent"

    async def run(self, spec: AgentSpec, user_input: Any) -> LLMResult:
        """Build an mcp_agent Agent, attach OpenAIResponsesLLM, run, return result.

        Raises:
            RuntimeError: if mcp-agent is not installed in the current environment.
        """
        if not _mcp_agent_available:
            raise RuntimeError(
                "McpAgentBackend requires the 'mcp-agent' package, which is not "
                "installed in this environment.  Run this workload on the db-server "
                "where mcp-agent is available, or switch to a different LLMBackend."
            )

        # Local import so it only resolves when mcp_agent is present.
        from cores.llm.openai_responses_llm import OpenAIResponsesLLM  # noqa: PLC0415

        agent = Agent(
            name=spec.name,
            instruction=spec.instructions,
            server_names=list(spec.mcp_servers),
        )

        async with agent:
            agent.attach_llm(OpenAIResponsesLLM)
            result_text: str = await agent.generate_str(
                message=user_input,
                request_params=RequestParams(
                    model=spec.model,
                    maxTokens=spec.params.max_tokens,
                    reasoning_effort=spec.params.reasoning_effort or "none",
                    max_iterations=spec.params.max_iterations,
                ),
            )

        return LLMResult(text=result_text)
