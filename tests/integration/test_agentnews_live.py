"""Explicit opt-in public read-only AgentNews KR/US live smoke.

The smoke performs exactly two allowlisted public Markdown GETs. AgentNews is
untrusted research-priority context, never an order signal or final fact source.
"""

from __future__ import annotations

import asyncio
import hashlib
import os

import pytest

from prism_core.data import DataQualityStatus
from prism_core.data.providers.agentnews import AgentNewsProvider
from prism_core.data.providers.agentnews_models import (
    AgentNewsBoard,
    AgentNewsContentRole,
    AgentNewsTrust,
)


pytestmark = pytest.mark.live_agentnews


@pytest.mark.asyncio
async def test_agentnews_live_kr_us_markdown_provenance_and_bounds() -> None:
    if os.environ.get("RUN_AGENTNEWS_LIVE_SMOKE") != "1":
        pytest.skip(
            "set RUN_AGENTNEWS_LIVE_SMOKE=1 to enable the bounded public AgentNews smoke"
        )

    provider = AgentNewsProvider(
        max_attempts=2,
        connect_timeout_seconds=3,
        read_timeout_seconds=7,
        total_timeout_seconds=10,
        max_response_bytes=131_072,
    )

    for board in (AgentNewsBoard.KR, AgentNewsBoard.US):
        result = await asyncio.wait_for(provider.fetch_result(board), timeout=22)
        snapshot = result.snapshot
        evidence = result.attempts

        assert snapshot.url == board.url
        assert snapshot.source_updated_at is not None
        assert snapshot.source_updated_at <= snapshot.fetched_at
        assert snapshot.quality in {DataQualityStatus.FRESH, DataQualityStatus.STALE}
        assert snapshot.content_hash == hashlib.sha256(snapshot.raw_body).hexdigest()
        assert snapshot.raw_body.startswith(b"---\n")
        assert len(snapshot.raw_body) <= 131_072
        assert snapshot.role is AgentNewsContentRole.RESEARCH_PRIORITY_CONTEXT
        assert snapshot.trust is AgentNewsTrust.UNTRUSTED
        assert snapshot.material_claims_require_original_source is True
        assert snapshot.executable is False
        assert 1 <= len(evidence) <= 2
        assert all(attempt.url == board.url for attempt in evidence)
        assert evidence[-1].status_code == 200
        assert evidence[-1].content_hash == snapshot.content_hash
        assert evidence[-1].latency_ms is not None
        assert evidence[-1].latency_ms >= 0
        assert result.used_last_known_good is False
