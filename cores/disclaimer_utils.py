"""Shared disclaimer/warning text helpers.

Extracted from `telegram_ai_bot.py` so the regex can be unit-tested without
pulling in the full telegram-bot dependency stack (gh #263).
"""

from __future__ import annotations

import re

# Trailing disclaimer-like lines the LLM tends to append on its own (gh #263).
# Matches a final block of one or more lines starting with ⚠️/⚡/❗ that mention
# 투자/investment + a responsibility/reference/advice/risk noun. Stripped before
# the canonical disclaimer is appended so users do not see two warnings.
_TRAILING_DISCLAIMER_RE = re.compile(
    r"(?:\n[ \t]*[⚠⚡❗‼️]+[^\n]*?"
    r"(?:투자\s*(?:참고|판단|권유|결정|책임|위험|유의)"
    r"|investment[^\n]*?(?:reference|decision|responsibility|advice|risk|caution|disclaim)"
    r"|not\s+(?:a\s+)?(?:financial|investment)\s+advice"
    r")[^\n]*)+\s*\Z",
    re.IGNORECASE,
)


def strip_trailing_disclaimer(text: str) -> str:
    """Remove any disclaimer-like trailing block emitted by the LLM (gh #263).

    Used before appending the bot's canonical disclaimer so users do not see
    two near-identical investment warnings.

    Returns ``text`` unchanged when it is empty or ``None``.
    """
    if not text:
        return text
    return _TRAILING_DISCLAIMER_RE.sub("", text).rstrip()
