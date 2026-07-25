"""Explicit opt-in scaffold for KR official-source live evidence.

Task 9B.1 intentionally implements no concrete KRX, KIND, or DART network
transport. Enabling this smoke without a durable source-specific approval must
fail before any request. A later source-approved transport task must replace the
terminal failure with one bounded exact-endpoint assertion.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.live_kr_official


def test_kr_official_live_evidence_requires_approved_concrete_transport() -> None:
    if os.environ.get("RUN_KR_OFFICIAL_LIVE_SMOKE") != "1":
        pytest.skip(
            "set RUN_KR_OFFICIAL_LIVE_SMOKE=1 only after source-specific approval"
        )

    manifest_value = os.environ.get("KR_OFFICIAL_APPROVAL_MANIFEST")
    if not manifest_value:
        pytest.fail(
            "KR_OFFICIAL_APPROVAL_MANIFEST is required before any KR official request"
        )
    manifest = Path(manifest_value)
    if not manifest.is_file():
        pytest.fail("KR official approval manifest path does not exist")

    pytest.fail(
        "Task 9B.1 has no concrete KR official live transport; no network request was made"
    )
