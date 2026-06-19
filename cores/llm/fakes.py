"""
In-process fake backend for domain unit tests — zero network, zero SDK deps.

Usage::

    from cores.llm.fakes import FakeLLMBackend
    from cores.llm.ports import LLMResult

    backend = FakeLLMBackend([LLMResult(text="buy"), LLMResult(text="hold")])
    result = await backend.run(spec, "analyse AAPL")
    assert result.text == "buy"
    assert backend.calls[0] == (spec, "analyse AAPL")
"""

from collections import deque
from typing import Any, Callable, List, Union

from cores.llm.ports import AgentSpec, LLMBackend, LLMResult


class FakeLLMBackend(LLMBackend):
    """Scripted LLM backend for deterministic unit tests.

    Pass either:
    - a list of ``LLMResult`` objects that are returned in order, or
    - a callable ``(spec, user_input) -> LLMResult`` for dynamic responses.

    Each call is recorded in ``.calls`` as a ``(AgentSpec, user_input)`` tuple.
    Raises ``IndexError`` if the scripted queue is exhausted (list mode).
    """

    name = "fake"

    def __init__(
        self,
        responses: Union[List[LLMResult], Callable[[AgentSpec, Any], LLMResult]],
    ) -> None:
        if callable(responses):
            self._callable: Callable[[AgentSpec, Any], LLMResult] = responses
            self._queue: deque[LLMResult] = deque()
        else:
            self._callable = None  # type: ignore[assignment]
            self._queue = deque(responses)
        self.calls: list[tuple[AgentSpec, Any]] = []

    async def run(self, spec: AgentSpec, user_input: Any) -> LLMResult:
        """Return the next scripted result and record the call."""
        self.calls.append((spec, user_input))
        if self._callable is not None:
            return self._callable(spec, user_input)
        if not self._queue:
            raise IndexError(
                "FakeLLMBackend: scripted response queue is empty. "
                "Add more LLMResult entries to the responses list."
            )
        return self._queue.popleft()
