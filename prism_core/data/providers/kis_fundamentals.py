"""KIS-primary Korean fundamental normalization with conservative PIT timing.

KIS finance responses expose a settlement year-month but no filing acceptance or
revision timestamp.  The adapter therefore treats the live response receipt as
``available_at`` and never backdates availability to the reported period.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from prism_core.data.contracts import (
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    ObservationTime,
    SecurityId,
)


KST = ZoneInfo("Asia/Seoul")


class KISFundamentalCategory(str, Enum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    FINANCIAL_RATIO = "financial_ratio"
    PROFIT_RATIO = "profit_ratio"
    STABILITY_RATIO = "stability_ratio"
    GROWTH_RATIO = "growth_ratio"


class KISFundamentalCapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class KISFundamentalEndpoint:
    path: str
    tr_id: str
    evidence_kind: str


KIS_FUNDAMENTAL_ENDPOINTS: Mapping[
    KISFundamentalCategory, KISFundamentalEndpoint
] = MappingProxyType(
    {
        KISFundamentalCategory.BALANCE_SHEET: KISFundamentalEndpoint(
            "/uapi/domestic-stock/v1/finance/balance-sheet",
            "FHKST66430100",
            "fundamental_balance_sheet",
        ),
        KISFundamentalCategory.INCOME_STATEMENT: KISFundamentalEndpoint(
            "/uapi/domestic-stock/v1/finance/income-statement",
            "FHKST66430200",
            "fundamental_income_statement",
        ),
        KISFundamentalCategory.FINANCIAL_RATIO: KISFundamentalEndpoint(
            "/uapi/domestic-stock/v1/finance/financial-ratio",
            "FHKST66430300",
            "fundamental_financial_ratio",
        ),
        KISFundamentalCategory.PROFIT_RATIO: KISFundamentalEndpoint(
            "/uapi/domestic-stock/v1/finance/profit-ratio",
            "FHKST66430400",
            "fundamental_profitability",
        ),
        KISFundamentalCategory.STABILITY_RATIO: KISFundamentalEndpoint(
            "/uapi/domestic-stock/v1/finance/stability-ratio",
            "FHKST66430600",
            "fundamental_stability",
        ),
        KISFundamentalCategory.GROWTH_RATIO: KISFundamentalEndpoint(
            "/uapi/domestic-stock/v1/finance/growth-ratio",
            "FHKST66430800",
            "fundamental_growth",
        ),
    }
)


@dataclass(frozen=True)
class KISFundamentalRow:
    period: str
    values: Mapping[str, str]

    def __post_init__(self) -> None:
        _period_end(self.period)
        copied = dict(self.values)
        if "stac_yymm" in copied:
            raise ValueError("row values must not duplicate the settlement period")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in copied.items()):
            raise TypeError("KIS fundamental row values must be strings")
        object.__setattr__(self, "values", MappingProxyType(copied))


@dataclass(frozen=True)
class KISFundamentalPayload:
    category: KISFundamentalCategory
    provider_symbol: str
    endpoint: str
    tr_id: str
    rows: tuple[KISFundamentalRow, ...]
    received_at: datetime
    source_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.category, KISFundamentalCategory):
            raise TypeError("category must be KISFundamentalCategory")
        expected = KIS_FUNDAMENTAL_ENDPOINTS[self.category]
        if (self.endpoint, self.tr_id) != (expected.path, expected.tr_id):
            raise ValueError("KIS fundamental endpoint identity is inconsistent")
        if len(self.provider_symbol) != 6 or not self.provider_symbol.isdigit():
            raise ValueError("provider_symbol must be a six-digit KIS symbol")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        if len(self.source_hash) != 64 or any(char not in "0123456789abcdef" for char in self.source_hash):
            raise ValueError("source_hash must be a lowercase SHA-256 digest")
        if not self.rows:
            raise ValueError("KIS fundamental payload must contain rows")

    @classmethod
    def from_wire(
        cls,
        *,
        category: KISFundamentalCategory,
        provider_symbol: str,
        rows: list[dict[str, str]],
        received_at: datetime,
        source_hash: str,
    ) -> "KISFundamentalPayload":
        expected = KIS_FUNDAMENTAL_ENDPOINTS[category]
        normalized_rows = []
        for raw in rows:
            period = raw.get("stac_yymm", "")
            normalized_rows.append(
                KISFundamentalRow(
                    period=period,
                    values={key: value for key, value in raw.items() if key != "stac_yymm"},
                )
            )
        return cls(
            category=category,
            provider_symbol=provider_symbol,
            endpoint=expected.path,
            tr_id=expected.tr_id,
            rows=tuple(normalized_rows),
            received_at=received_at,
            source_hash=source_hash,
        )


@dataclass(frozen=True)
class KISFundamentalFetchResult:
    fundamentals: tuple[FundamentalObservation, ...]
    evidence_items: tuple[EvidenceItem, ...]
    quality: DataQualityStatus
    issues: tuple[str, ...]
    earnings_current: Decimal | None
    earnings_previous: Decimal | None
    earnings_current_period: date | None
    earnings_previous_period: date | None
    available_at: datetime | None
    ingested_at: datetime | None
    capabilities: tuple["KISFundamentalCapabilityEvidence", ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class KISFundamentalCapabilityEvidence:
    category: KISFundamentalCategory
    status: KISFundamentalCapabilityStatus
    endpoint: str
    tr_id: str
    received_at: datetime | None
    source_hash: str | None


_KIS_LIMITATIONS = (
    "KIS_FILING_ACCEPTED_AT_UNAVAILABLE",
    "KIS_PROVIDER_AMOUNT_SCALE_UNSPECIFIED",
    "KIS_REVISION_ID_UNAVAILABLE",
)


_FIELD_MAP: Mapping[KISFundamentalCategory, Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        KISFundamentalCategory.BALANCE_SHEET: MappingProxyType(
            {
                "cras": ("balance_sheet.current_assets_provider_units", "KIS_REPORTED_AMOUNT"),
                "fxas": ("balance_sheet.fixed_assets_provider_units", "KIS_REPORTED_AMOUNT"),
                "flow_lblt": ("balance_sheet.current_liabilities_provider_units", "KIS_REPORTED_AMOUNT"),
                "fix_lblt": ("balance_sheet.noncurrent_liabilities_provider_units", "KIS_REPORTED_AMOUNT"),
                "total_aset": ("balance_sheet.total_assets_provider_units", "KIS_REPORTED_AMOUNT"),
                "total_lblt": ("balance_sheet.total_liabilities_provider_units", "KIS_REPORTED_AMOUNT"),
                "total_cptl": ("balance_sheet.total_equity_provider_units", "KIS_REPORTED_AMOUNT"),
            }
        ),
        KISFundamentalCategory.INCOME_STATEMENT: MappingProxyType(
            {
                "sale_account": ("income_statement.revenue_provider_units", "KIS_REPORTED_AMOUNT"),
                "sale_totl_prfi": ("income_statement.gross_profit_provider_units", "KIS_REPORTED_AMOUNT"),
                "bsop_prti": ("income_statement.operating_profit_provider_units", "KIS_REPORTED_AMOUNT"),
                "op_prfi": ("income_statement.ordinary_profit_provider_units", "KIS_REPORTED_AMOUNT"),
            }
        ),
        KISFundamentalCategory.FINANCIAL_RATIO: MappingProxyType(
            {
                "eps": ("financial.earnings_per_share_provider_units", "KIS_REPORTED_AMOUNT_PER_SHARE"),
                "bps": ("financial.book_value_per_share_provider_units", "KIS_REPORTED_AMOUNT_PER_SHARE"),
                "roe_val": ("financial.return_on_equity_percent", "PERCENT"),
                "rsrv_rate": ("financial.retention_ratio_percent", "PERCENT"),
            }
        ),
        KISFundamentalCategory.PROFIT_RATIO: MappingProxyType(
            {
                "cptl_ntin_rate": ("profitability.return_on_assets_percent", "PERCENT"),
                "self_cptl_ntin_inrt": ("profitability.return_on_equity_percent", "PERCENT"),
                "sale_totl_rate": ("profitability.gross_margin_percent", "PERCENT"),
                "sale_ntin_rate": ("profitability.net_margin_percent", "PERCENT"),
            }
        ),
        KISFundamentalCategory.STABILITY_RATIO: MappingProxyType(
            {
                "lblt_rate": ("balance_sheet.debt_ratio_percent", "PERCENT"),
                "bram_depn": ("balance_sheet.borrowings_dependency_percent", "PERCENT"),
                "crnt_rate": ("balance_sheet.current_ratio_percent", "PERCENT"),
                "quck_rate": ("balance_sheet.quick_ratio_percent", "PERCENT"),
            }
        ),
        KISFundamentalCategory.GROWTH_RATIO: MappingProxyType(
            {
                "grs": ("growth.revenue_percent", "PERCENT"),
                "bsop_prfi_inrt": ("growth.operating_profit_percent", "PERCENT"),
                "equt_inrt": ("growth.equity_percent", "PERCENT"),
                "totl_aset_inrt": ("growth.total_assets_percent", "PERCENT"),
            }
        ),
    }
)


class KISFundamentalTransport(Protocol):
    async def fetch_fundamental_payloads(
        self, *, symbol: str
    ) -> tuple[KISFundamentalPayload, ...]: ...


class KISFundamentalProvider:
    """Thin application-facing adapter over the bounded KIS finance transport."""

    def __init__(self, *, transport: KISFundamentalTransport) -> None:
        self._transport = transport
        self._prefetched: dict[
            str,
            tuple[tuple[KISFundamentalPayload, ...], tuple[str, ...]],
        ] = {}

    async def prefetch(self, *, stock_code: str) -> None:
        """Freeze raw live receipts before the caller fixes its decision clock."""

        payloads = await self._transport.fetch_fundamental_payloads(symbol=stock_code)
        self._prefetched[stock_code] = (
            payloads,
            tuple(getattr(self._transport, "fundamental_issues", ())),
        )

    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> KISFundamentalFetchResult:
        prefetched = self._prefetched.pop(stock_code, None)
        if prefetched is None:
            payloads = await self._transport.fetch_fundamental_payloads(symbol=stock_code)
            fetch_issues = tuple(
                getattr(self._transport, "fundamental_issues", ())
            )
        else:
            payloads, fetch_issues = prefetched
        return normalize_kis_fundamentals(
            payloads=payloads,
            security_id=security_id,
            as_of=as_of,
            fetch_issues=fetch_issues,
        )


def normalize_kis_fundamentals(
    *,
    payloads: tuple[KISFundamentalPayload, ...],
    security_id: SecurityId,
    as_of: datetime,
    fetch_issues: tuple[str, ...] = (),
) -> KISFundamentalFetchResult:
    """Normalize one prospectively fetched KIS finance bundle.

    The KIS endpoint has no filing publication timestamp, so receipt time is the
    earliest supported availability.  Historical use before receipt fails closed.
    """

    if not isinstance(security_id, SecurityId):
        raise TypeError("security_id must be SecurityId")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not payloads:
        return KISFundamentalFetchResult(
            fundamentals=(),
            evidence_items=(),
            quality=DataQualityStatus.UNAVAILABLE,
            issues=fetch_issues or ("KIS_FUNDAMENTALS_UNAVAILABLE",),
            earnings_current=None,
            earnings_previous=None,
            earnings_current_period=None,
            earnings_previous_period=None,
            available_at=None,
            ingested_at=None,
            capabilities=_capabilities(payloads),
            limitations=_KIS_LIMITATIONS,
        )
    symbols = {payload.provider_symbol for payload in payloads}
    categories = {payload.category for payload in payloads}
    if len(symbols) != 1 or len(categories) != len(payloads):
        raise ValueError("KIS fundamental bundle must contain unique categories for one symbol")
    if any(payload.received_at > as_of for payload in payloads):
        return KISFundamentalFetchResult(
            fundamentals=(),
            evidence_items=(),
            quality=DataQualityStatus.UNAVAILABLE,
            issues=("KIS_FUNDAMENTALS_UNAVAILABLE_AT_AS_OF",),
            earnings_current=None,
            earnings_previous=None,
            earnings_current_period=None,
            earnings_previous_period=None,
            available_at=None,
            ingested_at=None,
            capabilities=_capabilities(payloads),
            limitations=_KIS_LIMITATIONS,
        )

    symbol = next(iter(symbols))
    fundamentals: list[FundamentalObservation] = []
    evidence_items: list[EvidenceItem] = []
    issues: list[str] = list(fetch_issues)
    income_values: list[tuple[date, Decimal, KISFundamentalPayload]] = []
    for payload in sorted(payloads, key=lambda item: item.category.value):
        expected_fields = _FIELD_MAP[payload.category]
        valid_periods: list[date] = []
        for row in payload.rows:
            period_end = _period_end(row.period)
            if period_end > payload.received_at.astimezone(KST).date():
                issues.append(f"KIS_FUTURE_PERIOD:{payload.category.value}:{row.period}")
                continue
            valid_periods.append(period_end)
            for wire_name, (metric, unit) in expected_fields.items():
                value = _decimal_or_none(row.values.get(wire_name))
                if value is None:
                    continue
                fundamentals.append(
                    _fundamental(
                        security_id=security_id,
                        payload=payload,
                        metric=metric,
                        period_end=period_end,
                        value=value,
                        unit=unit,
                        as_of=as_of,
                    )
                )
            if payload.category is KISFundamentalCategory.INCOME_STATEMENT:
                net_income = _decimal_or_none(row.values.get("thtr_ntin"))
                if net_income is not None:
                    income_values.append((period_end, net_income, payload))
        if valid_periods:
            evidence_items.append(
                _evidence(
                    security_id=security_id,
                    payload=payload,
                    observed_at=datetime.combine(max(valid_periods), time.min, tzinfo=KST),
                    as_of=as_of,
                )
            )
        else:
            issues.append(f"KIS_NO_PIT_ROWS:{payload.category.value}")

    missing_categories = sorted(
        category.value for category in KISFundamentalCategory if category not in categories
    )
    classified_missing = {
        issue.rsplit(":", 1)[-1]
        for issue in fetch_issues
        if issue.startswith(
            (
                "KIS_CAPABILITY_UNAVAILABLE:",
                "KIS_ENDPOINT_TRANSIENT:",
                "KIS_SCHEMA_INVALID:",
            )
        )
    }
    issues.extend(
        f"KIS_CAPABILITY_UNAVAILABLE:{name}"
        for name in missing_categories
        if name not in classified_missing
    )
    pair = _latest_comparable_annual_pair(income_values)
    if pair is None:
        issues.append("KIS_COMPARABLE_ANNUAL_EARNINGS_UNAVAILABLE")
        current = previous = None
        current_period = previous_period = None
    elif pair[1][1] == 0:
        issues.append("KIS_EARNINGS_TREND_UNDEFINED_ZERO_BASE")
        current = previous = None
        current_period = previous_period = None
    else:
        current_item, previous_item = pair
        current_period, current, current_payload = current_item
        previous_period, previous, previous_payload = previous_item
        for period_end, value, payload in (previous_item, current_item):
            fundamentals.append(
                _fundamental(
                    security_id=security_id,
                    payload=payload,
                    metric="net_income",
                    period_end=period_end,
                    value=value,
                    unit="KIS_REPORTED_AMOUNT",
                    as_of=as_of,
                )
            )
        del current_payload, previous_payload

    fundamentals.sort(key=lambda item: (item.period_end, item.metric, item.source_record_id))
    evidence_items.sort(key=lambda item: item.kind)
    quality = DataQualityStatus.FRESH if not issues else DataQualityStatus.PARTIAL
    available_at = max(payload.received_at for payload in payloads)
    return KISFundamentalFetchResult(
        fundamentals=tuple(fundamentals),
        evidence_items=tuple(evidence_items),
        quality=quality,
        issues=tuple(sorted(set(issues))),
        earnings_current=current,
        earnings_previous=previous,
        earnings_current_period=current_period,
        earnings_previous_period=previous_period,
        available_at=available_at,
        ingested_at=available_at,
        capabilities=_capabilities(payloads),
        limitations=_KIS_LIMITATIONS,
    )


def _capabilities(
    payloads: tuple[KISFundamentalPayload, ...],
) -> tuple[KISFundamentalCapabilityEvidence, ...]:
    by_category = {payload.category: payload for payload in payloads}
    return tuple(
        KISFundamentalCapabilityEvidence(
            category=category,
            status=(
                KISFundamentalCapabilityStatus.SUPPORTED
                if category in by_category
                else KISFundamentalCapabilityStatus.UNAVAILABLE
            ),
            endpoint=KIS_FUNDAMENTAL_ENDPOINTS[category].path,
            tr_id=KIS_FUNDAMENTAL_ENDPOINTS[category].tr_id,
            received_at=(
                None if category not in by_category else by_category[category].received_at
            ),
            source_hash=(
                None if category not in by_category else by_category[category].source_hash
            ),
        )
        for category in KISFundamentalCategory
    )


def _latest_comparable_annual_pair(
    values: list[tuple[date, Decimal, KISFundamentalPayload]],
) -> tuple[
    tuple[date, Decimal, KISFundamentalPayload],
    tuple[date, Decimal, KISFundamentalPayload],
] | None:
    by_period = {item[0]: item for item in values}
    for current_period in sorted(by_period, reverse=True):
        previous_period = date(
            current_period.year - 1,
            current_period.month,
            calendar.monthrange(current_period.year - 1, current_period.month)[1],
        )
        if previous_period in by_period:
            return by_period[current_period], by_period[previous_period]
    return None


def _period_end(value: str) -> date:
    if len(value) != 6 or not value.isdigit():
        raise ValueError("stac_yymm must use YYYYMM format")
    year = int(value[:4])
    month = int(value[4:])
    if not 1 <= month <= 12:
        raise ValueError("stac_yymm month is invalid")
    return date(year, month, calendar.monthrange(year, month)[1])


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None or not value.strip() or value.strip() in {"-", "--"}:
        return None
    try:
        parsed = Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _timing(
    *, payload: KISFundamentalPayload, period_end: date, as_of: datetime
) -> ObservationTime:
    observed_at = datetime.combine(period_end, time.min, tzinfo=KST)
    return ObservationTime(
        observed_at=observed_at,
        available_at=payload.received_at,
        ingested_at=payload.received_at,
        as_of_date=as_of,
    )


def _fundamental(
    *,
    security_id: SecurityId,
    payload: KISFundamentalPayload,
    metric: str,
    period_end: date,
    value: Decimal,
    unit: str,
    as_of: datetime,
) -> FundamentalObservation:
    return FundamentalObservation(
        security_id=security_id,
        provider="KIS",
        provider_symbol=payload.provider_symbol,
        source_record_id=(
            f"KIS:finance:{payload.category.value}:{payload.provider_symbol}:"
            f"{period_end.isoformat()}:{metric}"
        ),
        source_hash=payload.source_hash,
        revision=0,
        timing=_timing(payload=payload, period_end=period_end, as_of=as_of),
        quality=DataQualityStatus.FRESH,
        metric=metric,
        # KIS supplies a settlement year-month but not a period start.  Preserve
        # that limitation instead of fabricating annual/quarterly duration.
        period_start=period_end,
        period_end=period_end,
        value=value,
        unit=unit,
    )


def _evidence(
    *,
    security_id: SecurityId,
    payload: KISFundamentalPayload,
    observed_at: datetime,
    as_of: datetime,
) -> EvidenceItem:
    spec = KIS_FUNDAMENTAL_ENDPOINTS[payload.category]
    identity = (
        f"KIS:{payload.category.value}:{payload.provider_symbol}:{payload.source_hash}"
    )
    return EvidenceItem.model_validate(
        {
            "security_id": security_id,
            "provider": "KIS",
            "provider_symbol": payload.provider_symbol,
            "source_record_id": (
                f"KIS:finance:{payload.category.value}:{payload.provider_symbol}:"
                f"{payload.received_at.isoformat()}"
            ),
            "source_hash": payload.source_hash,
            "revision": 0,
            "timing": ObservationTime(
                observed_at=observed_at,
                available_at=payload.received_at,
                ingested_at=payload.received_at,
                as_of_date=as_of,
            ),
            "quality": DataQualityStatus.FRESH,
            "evidence_id": uuid5(NAMESPACE_URL, identity),
            "kind": spec.evidence_kind,
            "title": f"KIS {payload.category.value} {payload.provider_symbol}",
            "source_url": (
                "https://github.com/koreainvestment/open-trading-api/tree/main/"
                f"examples_llm/domestic_stock/finance_{payload.category.value}"
            ),
            "content_hash": payload.source_hash,
        }
    )
