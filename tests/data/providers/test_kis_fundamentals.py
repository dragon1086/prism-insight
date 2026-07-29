from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.kis import ProviderTimeoutError
from prism_core.data.providers.kis_fundamentals import (
    KIS_FUNDAMENTAL_ENDPOINTS,
    KISFundamentalCategory,
    KISFundamentalPayload,
    KISFundamentalProvider,
    normalize_kis_fundamentals,
)
from prism_core.data.providers.kis_http import (
    KISHTTPResponse,
    KISHTTPTransport,
    KISMarketDataCredentials,
)


KST = ZoneInfo("Asia/Seoul")
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
RECEIVED_AT = datetime(2026, 7, 29, 23, 31, tzinfo=KST)
AS_OF = datetime(2026, 7, 29, 23, 32, tzinfo=KST)


def _payload(
    category: KISFundamentalCategory,
    rows: list[dict[str, str]],
) -> KISFundamentalPayload:
    body = json.dumps({"rt_cd": "0", "output": rows}, sort_keys=True).encode()
    return KISFundamentalPayload.from_wire(
        category=category,
        provider_symbol="005930",
        rows=rows,
        received_at=RECEIVED_AT,
        source_hash=hashlib.sha256(body).hexdigest(),
    )


def test_normalizes_kis_profitability_balance_growth_and_comparable_annual_earnings() -> None:
    payloads = (
        _payload(
            KISFundamentalCategory.BALANCE_SHEET,
            [
                {
                    "stac_yymm": "202512",
                    "cras": "100",
                    "fxas": "200",
                    "flow_lblt": "50",
                    "fix_lblt": "60",
                    "total_aset": "300",
                    "total_lblt": "110",
                    "total_cptl": "190",
                }
            ],
        ),
        _payload(
            KISFundamentalCategory.INCOME_STATEMENT,
            [
                {"stac_yymm": "202603", "sale_account": "90", "bsop_prti": "9", "thtr_ntin": "8"},
                {"stac_yymm": "202512", "sale_account": "400", "bsop_prti": "40", "thtr_ntin": "30"},
                {"stac_yymm": "202412", "sale_account": "350", "bsop_prti": "35", "thtr_ntin": "20"},
            ],
        ),
        _payload(
            KISFundamentalCategory.FINANCIAL_RATIO,
            [{"stac_yymm": "202512", "eps": "1200", "bps": "50000", "roe_val": "12.5"}],
        ),
        _payload(
            KISFundamentalCategory.PROFIT_RATIO,
            [
                {
                    "stac_yymm": "202512",
                    "cptl_ntin_rate": "8.1",
                    "self_cptl_ntin_inrt": "12.5",
                    "sale_totl_rate": "31.2",
                    "sale_ntin_rate": "7.5",
                }
            ],
        ),
        _payload(
            KISFundamentalCategory.STABILITY_RATIO,
            [
                {
                    "stac_yymm": "202512",
                    "lblt_rate": "57.9",
                    "bram_depn": "10.2",
                    "crnt_rate": "200.0",
                    "quck_rate": "150.0",
                }
            ],
        ),
        _payload(
            KISFundamentalCategory.GROWTH_RATIO,
            [
                {
                    "stac_yymm": "202512",
                    "grs": "14.3",
                    "bsop_prfi_inrt": "20.0",
                    "equt_inrt": "8.2",
                    "totl_aset_inrt": "9.1",
                }
            ],
        ),
    )

    result = normalize_kis_fundamentals(
        payloads=payloads,
        security_id=SECURITY_ID,
        as_of=AS_OF,
    )

    assert result.quality is DataQualityStatus.FRESH
    assert result.issues == ()
    assert {item.category: item.status for item in result.capabilities} == {
        category: "SUPPORTED" for category in KISFundamentalCategory
    }
    assert result.limitations == (
        "KIS_FILING_ACCEPTED_AT_UNAVAILABLE",
        "KIS_PROVIDER_AMOUNT_SCALE_UNSPECIFIED",
        "KIS_REVISION_ID_UNAVAILABLE",
    )
    assert result.earnings_current == Decimal("30")
    assert result.earnings_previous == Decimal("20")
    assert result.earnings_current_period.isoformat() == "2025-12-31"
    assert result.earnings_previous_period.isoformat() == "2024-12-31"
    by_metric = {item.metric: item for item in result.fundamentals}
    assert by_metric["net_income"].value == Decimal("30")
    assert by_metric["profitability.return_on_equity_percent"].value == Decimal("12.5")
    assert by_metric["balance_sheet.total_assets_provider_units"].value == Decimal("300")
    assert by_metric["balance_sheet.debt_ratio_percent"].value == Decimal("57.9")
    assert by_metric["growth.revenue_percent"].value == Decimal("14.3")
    assert all(item.provider == "KIS" for item in result.fundamentals)
    assert all(item.timing.available_at == RECEIVED_AT for item in result.fundamentals)
    assert all(item.timing.ingested_at == RECEIVED_AT for item in result.fundamentals)
    assert all(item.timing.as_of_date == AS_OF for item in result.fundamentals)
    assert {item.kind for item in result.evidence_items} == {
        "fundamental_balance_sheet",
        "fundamental_financial_ratio",
        "fundamental_growth",
        "fundamental_income_statement",
        "fundamental_profitability",
        "fundamental_stability",
    }


class _SequenceRequester:
    def __init__(self, responses: list[KISHTTPResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []

    async def request(self, **kwargs: object) -> KISHTTPResponse:
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _http_response(body: dict[str, object]) -> KISHTTPResponse:
    return KISHTTPResponse(
        status_code=200,
        body=json.dumps(body, separators=(",", ":")).encode(),
        received_at=RECEIVED_AT,
    )


@pytest.mark.asyncio
async def test_http_transport_fetches_only_the_six_official_kis_finance_endpoints() -> None:
    requester = _SequenceRequester(
        [
            _http_response({"access_token": "fixture-token", "expires_in": 3600}),
            *[
                _http_response(
                    {
                        "rt_cd": "0",
                        "msg_cd": "MCA00000",
                        "output": [{"stac_yymm": "202512", "thtr_ntin": "30"}],
                    }
                )
                for _ in KISFundamentalCategory
            ],
        ]
    )
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=(),
        requester=requester,
        clock=lambda: RECEIVED_AT,
    )

    payloads = await transport.fetch_fundamental_payloads(symbol="005930")

    assert tuple(item.category for item in payloads) == tuple(KISFundamentalCategory)
    finance_requests = requester.requests[1:]
    assert [request["method"] for request in finance_requests] == ["GET"] * 6
    assert [request["url"] for request in finance_requests] == [
        "https://openapi.koreainvestment.com:9443" + KIS_FUNDAMENTAL_ENDPOINTS[category].path
        for category in KISFundamentalCategory
    ]
    assert [request["headers"]["tr_id"] for request in finance_requests] == [
        KIS_FUNDAMENTAL_ENDPOINTS[category].tr_id
        for category in KISFundamentalCategory
    ]
    assert all(
        request["params"]
        == {
            "FID_DIV_CLS_CODE": "0",
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": "005930",
        }
        for request in finance_requests
    )
    serialized = repr(finance_requests).lower()
    assert "account" not in serialized
    assert "cano" not in serialized
    assert "order" not in serialized
    assert all(item.received_at == RECEIVED_AT for item in payloads)
    assert all(len(item.source_hash) == 64 for item in payloads)


@pytest.mark.asyncio
async def test_http_transport_records_one_sanitized_capability_gap_and_keeps_other_data() -> None:
    responses = [_http_response({"access_token": "fixture-token", "expires_in": 3600})]
    for category in KISFundamentalCategory:
        if category is KISFundamentalCategory.GROWTH_RATIO:
            responses.append(
                _http_response(
                    {
                        "rt_cd": "1",
                        "msg_cd": "CAPABILITY_DENIED",
                        "msg1": "secret provider detail",
                    }
                )
            )
        else:
            responses.append(
                _http_response(
                    {
                        "rt_cd": "0",
                        "output": [{"stac_yymm": "202512", "thtr_ntin": "30"}],
                    }
                )
            )
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=(),
        requester=_SequenceRequester(responses),
        clock=lambda: RECEIVED_AT,
    )

    payloads = await transport.fetch_fundamental_payloads(symbol="005930")

    assert len(payloads) == 5
    assert transport.fundamental_issues == (
        "KIS_CAPABILITY_UNAVAILABLE:growth_ratio",
    )
    assert "secret" not in repr(transport.fundamental_issues).lower()


@pytest.mark.asyncio
async def test_http_transport_isolates_one_malformed_finance_category() -> None:
    responses = [_http_response({"access_token": "fixture-token", "expires_in": 3600})]
    for category in KISFundamentalCategory:
        period = "not-a-period" if category is KISFundamentalCategory.GROWTH_RATIO else "202512"
        responses.append(
            _http_response({"rt_cd": "0", "output": [{"stac_yymm": period}]})
        )
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=(),
        requester=_SequenceRequester(responses),
        clock=lambda: RECEIVED_AT,
    )

    payloads = await transport.fetch_fundamental_payloads(symbol="005930")
    result = normalize_kis_fundamentals(
        payloads=payloads,
        security_id=SECURITY_ID,
        as_of=AS_OF,
        fetch_issues=transport.fundamental_issues,
    )

    assert len(payloads) == 5
    assert transport.fundamental_issues == (
        "KIS_SCHEMA_INVALID:growth_ratio",
    )
    assert "KIS_SCHEMA_INVALID:growth_ratio" in result.issues
    assert "KIS_CAPABILITY_UNAVAILABLE:growth_ratio" not in result.issues


@pytest.mark.asyncio
async def test_http_transport_labels_timeout_as_transient_not_capability_denial() -> None:
    class TimeoutRequester:
        def __init__(self, responses: list[KISHTTPResponse | Exception]) -> None:
            self.responses = responses

        async def request(self, **_: object) -> KISHTTPResponse:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    responses: list[KISHTTPResponse | Exception] = [
        _http_response({"access_token": "fixture-token", "expires_in": 3600}),
        ProviderTimeoutError("fixture timeout"),
        *[
            _http_response({"rt_cd": "0", "output": [{"stac_yymm": "202512"}]})
            for _ in range(5)
        ],
    ]
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=(),
        requester=TimeoutRequester(responses),
        clock=lambda: RECEIVED_AT,
    )

    result = await KISFundamentalProvider(transport=transport).fetch(
        stock_code="005930",
        security_id=SECURITY_ID,
        as_of=AS_OF,
    )

    assert sum(item.status == "SUPPORTED" for item in result.capabilities) == 5
    assert transport.fundamental_issues == (
        "KIS_ENDPOINT_TRANSIENT:timeout:balance_sheet",
    )
    assert "KIS_ENDPOINT_TRANSIENT:timeout:balance_sheet" in result.issues
    assert "KIS_CAPABILITY_UNAVAILABLE:balance_sheet" not in result.issues


class _FundamentalTransport:
    def __init__(self, payloads: tuple[KISFundamentalPayload, ...]) -> None:
        self.payloads = payloads
        self.symbols: list[str] = []

    async def fetch_fundamental_payloads(
        self, *, symbol: str
    ) -> tuple[KISFundamentalPayload, ...]:
        self.symbols.append(symbol)
        return self.payloads


@pytest.mark.asyncio
async def test_provider_exposes_normalized_kis_result_through_the_app_port() -> None:
    payloads = tuple(
        _payload(
            category,
            (
                [
                    {"stac_yymm": "202512", "thtr_ntin": "30"},
                    {"stac_yymm": "202412", "thtr_ntin": "20"},
                ]
                if category is KISFundamentalCategory.INCOME_STATEMENT
                else [{"stac_yymm": "202512"}]
            ),
        )
        for category in KISFundamentalCategory
    )
    transport = _FundamentalTransport(payloads)
    provider = KISFundamentalProvider(transport=transport)

    result = await provider.fetch(
        stock_code="005930",
        security_id=SECURITY_ID,
        as_of=AS_OF,
    )

    assert transport.symbols == ["005930"]
    assert result.earnings_current == Decimal("30")
    assert result.earnings_previous == Decimal("20")
    assert result.quality is DataQualityStatus.FRESH


@pytest.mark.asyncio
async def test_provider_prefetch_freezes_raw_receipt_before_live_decision_as_of() -> None:
    payloads = tuple(
        _payload(
            category,
            (
                [
                    {"stac_yymm": "202512", "thtr_ntin": "30"},
                    {"stac_yymm": "202412", "thtr_ntin": "20"},
                ]
                if category is KISFundamentalCategory.INCOME_STATEMENT
                else [{"stac_yymm": "202512"}]
            ),
        )
        for category in KISFundamentalCategory
    )
    transport = _FundamentalTransport(payloads)
    provider = KISFundamentalProvider(transport=transport)

    await provider.prefetch(stock_code="005930")
    result = await provider.fetch(
        stock_code="005930",
        security_id=SECURITY_ID,
        as_of=AS_OF,
    )

    assert transport.symbols == ["005930"]
    assert result.quality is DataQualityStatus.FRESH
    assert result.available_at == RECEIVED_AT


@pytest.mark.asyncio
async def test_provider_prefetch_freezes_transport_issues_with_each_symbol_bundle() -> None:
    payloads = tuple(
        _payload(
            category,
            (
                [
                    {"stac_yymm": "202512", "thtr_ntin": "30"},
                    {"stac_yymm": "202412", "thtr_ntin": "20"},
                ]
                if category is KISFundamentalCategory.INCOME_STATEMENT
                else [{"stac_yymm": "202512"}]
            ),
        )
        for category in KISFundamentalCategory
    )

    class StatefulTransport:
        fundamental_issues: tuple[str, ...] = ()

        async def fetch_fundamental_payloads(
            self, *, symbol: str
        ) -> tuple[KISFundamentalPayload, ...]:
            category = "growth_ratio" if symbol == "005930" else "balance_sheet"
            self.fundamental_issues = (f"KIS_ENDPOINT_TRANSIENT:timeout:{category}",)
            return payloads

    provider = KISFundamentalProvider(transport=StatefulTransport())

    await provider.prefetch(stock_code="005930")
    await provider.prefetch(stock_code="000660")
    result = await provider.fetch(
        stock_code="005930",
        security_id=SECURITY_ID,
        as_of=AS_OF,
    )

    assert "KIS_ENDPOINT_TRANSIENT:timeout:growth_ratio" in result.issues
    assert "KIS_ENDPOINT_TRANSIENT:timeout:balance_sheet" not in result.issues


def test_future_settlement_row_is_excluded_with_visible_pit_issue() -> None:
    payloads = tuple(
        _payload(
            category,
            (
                [
                    {"stac_yymm": "202612", "thtr_ntin": "999"},
                    {"stac_yymm": "202512", "thtr_ntin": "30"},
                    {"stac_yymm": "202412", "thtr_ntin": "20"},
                ]
                if category is KISFundamentalCategory.INCOME_STATEMENT
                else [{"stac_yymm": "202512"}]
            ),
        )
        for category in KISFundamentalCategory
    )

    result = normalize_kis_fundamentals(
        payloads=payloads,
        security_id=SECURITY_ID,
        as_of=AS_OF,
    )

    assert result.quality is DataQualityStatus.PARTIAL
    assert "KIS_FUTURE_PERIOD:income_statement:202612" in result.issues
    assert result.earnings_current == Decimal("30")
    assert all(
        item.timing.observed_at <= item.timing.available_at
        for item in result.evidence_items
    )


def test_empty_bundle_preserves_sanitized_transport_failure_classification() -> None:
    result = normalize_kis_fundamentals(
        payloads=(),
        security_id=SECURITY_ID,
        as_of=AS_OF,
        fetch_issues=("KIS_ENDPOINT_TRANSIENT:rate_limit:income_statement",),
    )

    assert result.issues == (
        "KIS_ENDPOINT_TRANSIENT:rate_limit:income_statement",
    )


def test_zero_prior_annual_earnings_is_an_explicit_undefined_trend_gap() -> None:
    payloads = tuple(
        _payload(
            category,
            (
                [
                    {"stac_yymm": "202512", "thtr_ntin": "30"},
                    {"stac_yymm": "202412", "thtr_ntin": "0"},
                ]
                if category is KISFundamentalCategory.INCOME_STATEMENT
                else [{"stac_yymm": "202512"}]
            ),
        )
        for category in KISFundamentalCategory
    )

    result = normalize_kis_fundamentals(
        payloads=payloads,
        security_id=SECURITY_ID,
        as_of=AS_OF,
    )

    assert result.quality is DataQualityStatus.PARTIAL
    assert result.earnings_current is None
    assert result.earnings_previous is None
    assert "KIS_EARNINGS_TREND_UNDEFINED_ZERO_BASE" in result.issues
