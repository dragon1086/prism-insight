from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from prism_core.data.contracts import CorporateActionType, SecurityId
from prism_core.data.providers.fmp import FMPInstrument
from prism_core.data.providers.fmp_models import FMPApiKey
from prism_core.data.providers.fmp_splits_http import (
    FMPJSONResponse,
    FMPSplitCalendarHTTPTransport,
)


AS_OF = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)
RECEIVED_AT = datetime(2026, 7, 28, 4, 0, 1, tzinfo=timezone.utc)
AAPL_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000191"))
SPY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000192"))


class Requester:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.requests: list[dict[str, object]] = []
        self.rows = rows or [
            {
                "symbol": "AAPL",
                "date": "2026-07-01",
                "numerator": 2,
                "denominator": 1,
                "splitType": "Stock Split",
            },
            {
                "symbol": "MSFT",
                "date": "2026-07-02",
                "numerator": 3,
                "denominator": 1,
                "splitType": "Stock Split",
            },
            {
                "symbol": "AAPL",
                "date": "2026-07-28",
                "numerator": 4,
                "denominator": 1,
                "splitType": "Stock Split",
            },
        ]

    async def request(self, **kwargs: object) -> FMPJSONResponse:
        self.requests.append(kwargs)
        return FMPJSONResponse(
            status_code=200,
            body=json.dumps(self.rows).encode(),
            received_at=RECEIVED_AT,
            latency_ms=12,
        )


@pytest.mark.asyncio
async def test_split_calendar_binds_requested_symbols_and_completed_sessions() -> None:
    requester = Requester()
    transport = FMPSplitCalendarHTTPTransport(requester=requester, lookback_days=400)

    result = await transport.fetch(
        api_key=FMPApiKey("fixture-secret"),
        as_of=AS_OF,
        instruments=(
            FMPInstrument(AAPL_ID, "AAPL", datetime(1900, 1, 1, tzinfo=timezone.utc)),
            FMPInstrument(SPY_ID, "SPY", datetime(1900, 1, 1, tzinfo=timezone.utc)),
        ),
    )

    assert len(requester.requests) == 1
    assert requester.requests[0]["url"] == (
        "https://financialmodelingprep.com/stable/splits-calendar"
    )
    assert {item.value for item in result.covered_security_ids} == {
        AAPL_ID.value,
        SPY_ID.value,
    }
    assert result.latest_completed_session.isoformat() == "2026-07-27"
    assert result.excluded_future_dates == ("2026-07-28",)
    assert len(result.corporate_actions) == 1
    action = result.corporate_actions[0]
    assert action.security_id == AAPL_ID
    assert action.action_type is CorporateActionType.SPLIT
    assert str(action.ratio) == "2"
    assert action.effective_date.isoformat() == "2026-07-01"
    assert len(result.coverage_evidence) == 2
    assert all(item.kind == "corporate_action_coverage" for item in result.coverage_evidence)
    assert result.request_evidence["status_code"] == 200
    assert "fixture-secret" not in repr(result)


@pytest.mark.asyncio
async def test_split_coverage_identity_ignores_unrelated_market_rows() -> None:
    configured = {
        "symbol": "AAPL",
        "date": "2026-07-01",
        "numerator": 2,
        "denominator": 1,
        "splitType": "Stock Split",
    }
    first = FMPSplitCalendarHTTPTransport(
        requester=Requester([configured]), lookback_days=400
    )
    second = FMPSplitCalendarHTTPTransport(
        requester=Requester(
            [
                configured,
                {
                    "symbol": "MSFT",
                    "date": "2026-07-02",
                    "numerator": 3,
                    "denominator": 1,
                    "splitType": "Stock Split",
                },
            ]
        ),
        lookback_days=400,
    )
    instruments = (
        FMPInstrument(AAPL_ID, "AAPL", datetime(1900, 1, 1, tzinfo=timezone.utc)),
        FMPInstrument(SPY_ID, "SPY", datetime(1900, 1, 1, tzinfo=timezone.utc)),
    )

    one = await first.fetch(
        api_key=FMPApiKey("fixture-secret"), as_of=AS_OF, instruments=instruments
    )
    two = await second.fetch(
        api_key=FMPApiKey("fixture-secret"), as_of=AS_OF, instruments=instruments
    )

    assert [item.evidence_id for item in one.coverage_evidence] == [
        item.evidence_id for item in two.coverage_evidence
    ]
    assert [item.source_hash for item in one.corporate_actions] == [
        item.source_hash for item in two.corporate_actions
    ]
    assert one.request_evidence["raw_payload_hash"] != two.request_evidence[
        "raw_payload_hash"
    ]
