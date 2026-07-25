"""Explicit opt-in scaffold for bounded FRED/ALFRED/ECOS live evidence.

Task 9B.3 intentionally implements no concrete network transport. Enabling this
smoke without a durable source-specific approval manifest must fail before any
request. A future approved transport task must validate the manifest hash,
source/capability, exact endpoint, terms/license, credential or key scope, cost,
rate/call bounds, and validity before one bounded request.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.live_macro_official


def test_macro_official_live_evidence_requires_approved_concrete_transport() -> None:
    if os.environ.get("RUN_MACRO_OFFICIAL_LIVE_SMOKE") != "1":
        pytest.skip(
            "set RUN_MACRO_OFFICIAL_LIVE_SMOKE=1 only after source-specific approval"
        )

    manifest_value = os.environ.get("MACRO_OFFICIAL_APPROVAL_MANIFEST")
    if not manifest_value:
        pytest.fail(
            "MACRO_OFFICIAL_APPROVAL_MANIFEST is required before any macro request"
        )
    manifest = Path(manifest_value)
    if not manifest.is_file():
        pytest.fail("macro official approval manifest path does not exist")

    pytest.fail(
        "Task 9B.3 has no concrete macro live transport; no network request was made"
    )
