"""One report-pipeline model contract shared by KR and US entry points."""

from __future__ import annotations

import os
import re


REPORT_MODEL = os.environ.get("REPORT_MODEL", "gpt-5.6-luna")
REPORT_EFFORT = os.environ.get("REPORT_EFFORT", "low")
REPORT_AUX_MODEL = os.environ.get("REPORT_AUX_MODEL", REPORT_MODEL)
REPORT_AUX_EFFORT = os.environ.get("REPORT_AUX_EFFORT", "low")


def report_model_slug(model: str | None = None) -> str:
    """Return a stable filename-safe slug that reflects the actual model."""
    value = str(model or REPORT_MODEL).strip().lower()
    value = re.sub(r"[^a-z0-9.]+", "-", value).strip("-")
    return value or "unknown-model"


__all__ = [
    "REPORT_AUX_EFFORT",
    "REPORT_AUX_MODEL",
    "REPORT_EFFORT",
    "REPORT_MODEL",
    "report_model_slug",
]
