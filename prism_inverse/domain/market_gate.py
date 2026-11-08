"""② Market gate: is this a time to hold inverse exposure on this underlying?"""

from __future__ import annotations

from typing import Mapping, Optional

from prism_inverse.domain.models import LeaderState
from prism_inverse.domain.policy import InversePolicy


def broken_weight(leaders: Mapping[str, LeaderState], weights: Mapping[str, float]) -> Optional[float]:
    """Share of an index held in currently broken leaders; ``None`` without constituent weights."""
    if not weights:
        return None
    return sum(w for code, w in weights.items() if code in leaders and leaders[code].is_broken)


def gate(pulse_state: Optional[str], weight: Optional[float], policy: InversePolicy) -> tuple[bool, str]:
    if pulse_state is None:
        return False, "MISSING_PULSE"
    if pulse_state not in policy.gate_states:
        return False, f"PULSE_{pulse_state}"
    if policy.gate_min_leader_weight is not None:
        if weight is None:
            return False, "MISSING_LEADER_WEIGHT"
        if weight < policy.gate_min_leader_weight:
            return False, "LEADER_WEIGHT_LOW"
    return True, "GATE_ON"
