"""Compatibility import for the canonical strategy prompt contracts.

The unshadowed implementation lives under ``prism_core`` so both the root KR
package and the separately rooted ``prism-us/cores`` package consume one contract.
"""

from prism_core.llm.trade_plan_prompts import (
    SWING_V1_PROMPT_VERSION,
    TREND_V1_PROMPT_VERSION,
    TradePlanPromptContract,
    get_trade_plan_prompt_contract,
)

__all__ = [
    "SWING_V1_PROMPT_VERSION",
    "TREND_V1_PROMPT_VERSION",
    "TradePlanPromptContract",
    "get_trade_plan_prompt_contract",
]