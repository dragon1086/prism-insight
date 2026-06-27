"""
Model registry: maps logical role names to (model_id, LLMParams).

Keeps model selection out of domain code so swapping models is config-only.
Default roles reflect real production usage (KR side).
"""

from cores.llm.ports import LLMParams

# Production-derived defaults.  model_id strings are plain — no SDK coupling.
_DEFAULT_MAPPING: dict[str, tuple[str, LLMParams]] = {
    "sell_decision": (
        "gpt-5.5",
        LLMParams(max_tokens=30000),
    ),
    "trading": (
        "gpt-5.5",
        LLMParams(max_tokens=30000),
    ),
    "journal": (
        "gpt-5.4-mini",
        LLMParams(reasoning_effort="none", max_tokens=16000),
    ),
    "summary": (
        "gpt-5.4-mini",
        LLMParams(reasoning_effort="none", max_tokens=16000),
    ),
}


class ModelRegistry:
    """Maps logical role strings to ``(model_id, LLMParams)`` pairs.

    Construct via ``from_mapping()`` with a plain dict, or use the
    class-level defaults that mirror real production usage.

    Example::

        reg = ModelRegistry.from_mapping({
            "sell_decision": ("gpt-5.5", LLMParams(max_tokens=30000)),
        })
        model_id, params = reg.resolve("sell_decision")
    """

    def __init__(self, mapping: dict[str, tuple[str, LLMParams]]) -> None:
        self._mapping: dict[str, tuple[str, LLMParams]] = dict(mapping)

    @classmethod
    def from_mapping(cls, mapping: dict[str, tuple[str, LLMParams]]) -> "ModelRegistry":
        """Construct from a caller-supplied dict (merged over defaults)."""
        merged = dict(_DEFAULT_MAPPING)
        merged.update(mapping)
        return cls(merged)

    @classmethod
    def defaults(cls) -> "ModelRegistry":
        """Return a registry pre-loaded with the production defaults."""
        return cls(dict(_DEFAULT_MAPPING))

    def resolve(self, role: str) -> tuple[str, LLMParams]:
        """Return ``(model_id, LLMParams)`` for *role*.

        Raises:
            KeyError: with a clear message listing available roles.
        """
        try:
            return self._mapping[role]
        except KeyError:
            available = ", ".join(sorted(self._mapping))
            raise KeyError(
                f"Unknown model role {role!r}. Available roles: {available}"
            ) from None

    def roles(self) -> list[str]:
        """Return all registered role names."""
        return list(self._mapping)
