"""Explicit opt-in scaffold for bounded SEC EDGAR live evidence.

Task 9B.2 intentionally implements no concrete SEC network transport. Enabling
this smoke without a durable source-specific approval manifest must fail before
any request. A future approved transport task must validate the manifest hash,
SEC identification/User-Agent obligation, exact endpoint, terms, cost, rate,
call, credential, and validity scope before one bounded request.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.live_sec_edgar


def test_sec_edgar_live_evidence_requires_approved_concrete_transport() -> None:
    if os.environ.get("RUN_SEC_EDGAR_LIVE_SMOKE") != "1":
        pytest.skip(
            "set RUN_SEC_EDGAR_LIVE_SMOKE=1 only after source-specific SEC approval"
        )

    manifest_value = os.environ.get("SEC_EDGAR_APPROVAL_MANIFEST")
    if not manifest_value:
        pytest.fail(
            "SEC_EDGAR_APPROVAL_MANIFEST is required before any SEC EDGAR request"
        )
    manifest = Path(manifest_value)
    if not manifest.is_file():
        pytest.fail("SEC EDGAR approval manifest path does not exist")

    pytest.fail(
        "Task 9B.2 has no concrete SEC EDGAR live transport; no network request was made"
    )
