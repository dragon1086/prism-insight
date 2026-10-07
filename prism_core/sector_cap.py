"""Plain-language note for a sector-concentration entry block (KR/US, display only).

The cap itself lives in ``check_sector_diversity`` (KR tracking/helpers.py, US
prism-us/us_stock_tracking_agent.py): 3 names per sector, and once 4+ names are
held, one sector may not reach 30% of the held names. A hold message used to say
only "섹터 집중 (Technology)" next to an AI rationale arguing for entry (SNDK,
2026-10-08), so it read like a contradiction. This note states which limit hit
and the counts at decision time. It never decides anything and never raises.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

MIN_HOLDINGS_FOR_RATIO_CHECK = 4


def sector_cap_note(cursor, table: str, sector: str, *, max_same: int, ratio: float,
                    account_key: str | None = None, same_sector=None) -> str:
    """'Technology 2/4종목(50%) 보유, 한 업종 30% 한도' — '' when unknown or not capped."""
    if table not in ("stock_holdings", "us_stock_holdings") or not sector:
        return ""
    same_sector = same_sector or (lambda held, new: held.lower() == new.lower())
    try:
        if account_key:
            cursor.execute(f"SELECT scenario FROM {table} WHERE account_key = ?", (account_key,))  # nosec B608  # nosemgrep
        else:
            cursor.execute(f"SELECT scenario FROM {table}")  # nosec B608  # nosemgrep
        sectors = []
        for (raw,) in cursor.fetchall():
            try:
                held = json.loads(raw or "{}").get("sector")
            except (TypeError, ValueError, AttributeError):
                continue
            if held:
                sectors.append(held)
        same = sum(1 for held in sectors if same_sector(held, sector))
        total = len(sectors)
    except Exception as exc:  # noqa: BLE001 — display only
        logger.warning("sector cap note unavailable for %s: %s", sector, type(exc).__name__)
        return ""
    if same >= max_same:
        return f"{sector} {same}종목 보유, 업종당 {max_same}종목 한도"
    if total >= MIN_HOLDINGS_FOR_RATIO_CHECK and same / total >= ratio:
        return f"{sector} {same}/{total}종목({same / total * 100:.0f}%) 보유, 한 업종 {ratio * 100:.0f}% 한도"
    return ""


def deferred_decision_line(ai_entered: bool, raw_decision: str) -> str:
    """'결정:' value: make an AI entry that a rule blocked explicit."""
    return "AI는 진입 판단 → 규칙으로 보류" if ai_entered else raw_decision
