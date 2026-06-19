"""
Provider-agnostic port layer for LLM access (Phase 1 — anti-corruption layer).

Domain code depends ONLY on these types; no mcp_agent or openai SDK imports here.
All concrete SDK adapters live in cores/llm/backends/ and are never imported by
domain modules directly.
"""

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class LLMParams:
    """Immutable LLM tuning parameters, SDK-independent.

    reasoning_effort: one of None, "none", "low", "medium", "high".
      None means "use the model default / omit the parameter".
    stop_sequences: tuple so the dataclass stays hashable/frozen.
    """

    max_tokens: int = 8000
    reasoning_effort: Optional[str] = None
    temperature: Optional[float] = None
    max_iterations: int = 10
    stop_sequences: tuple = ()


@dataclass(frozen=True)
class AgentSpec:
    """Everything needed to describe a single agent invocation.

    mcp_servers: logical server names that map to McpServerRegistry entries.
    output_schema: optional type hint for structured JSON output (used by
      backends that support it; ignored otherwise).
    """

    name: str
    instructions: str
    model: str
    mcp_servers: tuple = ()
    output_schema: Optional[type] = None
    params: LLMParams = field(default_factory=LLMParams)


@dataclass
class LLMResult:
    """Normalised output from any LLM backend.

    text: the primary string output.
    structured: parsed structured object when output_schema was given.
    response_id: opaque backend ID (e.g. Responses API previous_response_id).
    usage: token-count dict if the backend surfaces it, else None.
    raw: the unmodified backend response object for debugging / migration.
    """

    text: str = ""
    structured: Any = None
    response_id: Optional[str] = None
    usage: Optional[dict] = None
    raw: Any = None


class LLMBackend(abc.ABC):
    """Single seam that domain code calls.

    Subclass in cores/llm/backends/ for each concrete SDK.
    The ``name`` class attribute identifies the backend in logs/config.
    """

    name: str = "base"

    @abc.abstractmethod
    async def run(self, spec: AgentSpec, user_input: Any) -> LLMResult:
        """Execute the agent described by *spec* against *user_input*.

        Returns a normalised LLMResult regardless of the underlying SDK.
        Raises RuntimeError if the backend SDK is not installed.
        """
