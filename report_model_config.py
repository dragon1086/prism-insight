"""One report-pipeline model contract shared by KR and US entry points."""

from __future__ import annotations

import os
import re

REPORT_MODEL = os.environ.get("REPORT_MODEL", "gpt-5.6-luna")
REPORT_EFFORT = os.environ.get("REPORT_EFFORT", "medium")
REPORT_AUX_MODEL = os.environ.get("REPORT_AUX_MODEL", REPORT_MODEL)
REPORT_AUX_EFFORT = os.environ.get("REPORT_AUX_EFFORT", "low")
# Filing tables mix comparative periods and hierarchical accounting scopes, so
# only the three tool-free DART writers use a different model than the sections.
# Blind A/B 2026-09-26 on frozen 252990 + 017670 DART packets: gpt-6-luna/medium
# produced 3 HIGH numeric errors; gpt-6-luna/high, gpt-6-sol/low and
# gpt-6-sol/medium produced 0 HIGH. luna/high chosen at ~1/20 the cost of sol.
# Report stages never use astra.
DART_REPORT_MODEL = os.environ.get("DART_REPORT_MODEL", "gpt-6-luna")
DART_REPORT_EFFORT = os.environ.get("DART_REPORT_EFFORT", "high")


def report_model_slug(model: str | None = None) -> str:
    """Return a stable filename-safe slug that reflects the actual model."""
    value = str(model or REPORT_MODEL).strip().lower()
    value = re.sub(r"[^a-z0-9.]+", "-", value).strip("-")
    return value or "unknown-model"


__all__ = [
    "DART_REPORT_EFFORT",
    "DART_REPORT_MODEL",
    "REPORT_AUX_EFFORT",
    "REPORT_AUX_MODEL",
    "REPORT_EFFORT",
    "REPORT_MODEL",
    "report_model_slug",
]
