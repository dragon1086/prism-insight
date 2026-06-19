"""
MCP server registry: adapter-agnostic catalogue of MCP server definitions.

Reads the ``mcp.servers`` section of mcp_agent.config.yaml (or any equivalent
dict) and exposes server specs as plain dataclasses with no SDK coupling.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class McpServerSpec:
    """Immutable description of a single MCP server entry.

    Mirrors the mcp_agent.config.yaml ``mcp.servers.<name>`` shape::

        perplexity:
          command: uvx
          args: [mcp-server-perplexity-ask]
          env: {PERPLEXITY_API_KEY: "..."}
          read_timeout_seconds: 120
    """

    name: str
    command: str
    args: tuple = ()
    env: dict = field(default_factory=dict, compare=False, hash=False)
    read_timeout_seconds: Optional[int] = None


class McpServerRegistry:
    """Catalogue of MCP server specs, keyed by logical name.

    Build from the raw ``mcp.servers`` dict parsed from YAML::

        import yaml
        with open("mcp_agent.config.yaml") as f:
            cfg = yaml.safe_load(f)
        registry = McpServerRegistry.from_yaml_dict(cfg)
        spec = registry.get("perplexity")
    """

    def __init__(self, specs: dict[str, McpServerSpec]) -> None:
        self._specs: dict[str, McpServerSpec] = dict(specs)

    @classmethod
    def from_yaml_dict(cls, yaml_dict: dict) -> "McpServerRegistry":
        """Parse the top-level config dict (i.e. the full YAML document).

        Expects the shape ``{"mcp": {"servers": {<name>: {command, args?, env?, ...}}}}``.
        Both ``args`` and ``env`` are optional; missing fields default to empty.

        Raises:
            KeyError: if the ``mcp.servers`` path is absent from *yaml_dict*.
            ValueError: if a server entry is missing the required ``command`` field.
        """
        try:
            servers_dict: dict = yaml_dict["mcp"]["servers"]
        except KeyError as exc:
            raise KeyError(
                f"Expected 'mcp.servers' in config dict; missing key: {exc}"
            ) from exc

        specs: dict[str, McpServerSpec] = {}
        for name, entry in servers_dict.items():
            if "command" not in entry:
                raise ValueError(
                    f"MCP server {name!r} is missing required field 'command'."
                )
            raw_args = entry.get("args", [])
            specs[name] = McpServerSpec(
                name=name,
                command=entry["command"],
                args=tuple(raw_args),
                env=dict(entry.get("env", {})),
                read_timeout_seconds=entry.get("read_timeout_seconds"),
            )

        return cls(specs)

    def get(self, name: str) -> McpServerSpec:
        """Return the spec for *name*.

        Raises:
            KeyError: with a clear message if *name* is not registered.
        """
        try:
            return self._specs[name]
        except KeyError:
            available = ", ".join(sorted(self._specs))
            raise KeyError(
                f"MCP server {name!r} not found. Registered servers: {available}"
            ) from None

    def names(self) -> list[str]:
        """Return all registered server names."""
        return list(self._specs)
