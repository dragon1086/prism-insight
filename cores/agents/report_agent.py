"""SDK-neutral report agent definition."""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ReportAgent:
    """Describe one report agent without constructing an SDK-specific object."""

    name: str
    instruction: str
    server_names: tuple[str, ...] = ()

    def __init__(
        self,
        name: str,
        instruction: str,
        server_names: Iterable[str] | None = None,
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "server_names", tuple(server_names or ()))
