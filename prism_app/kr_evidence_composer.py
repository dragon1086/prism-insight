"""KR official-evidence precedence and supplemental fallback composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Protocol

from prism_app.live_kr_evidence import FMPIncomeEvidence
from prism_core.data.contracts import (
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    SecurityId,
)
from prism_core.data.providers.dart import DARTFetchResult
from prism_core.data.providers.kis_fundamentals import KISFundamentalFetchResult
from prism_core.data.providers.kind import KINDFetchResult


class SupplementalFundamentalsUnavailable(RuntimeError):
    """Sanitized signal that technical analysis must continue as REPORT_ONLY."""


class DARTPort(Protocol):
    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> DARTFetchResult: ...


class KINDPort(Protocol):
    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> KINDFetchResult: ...


class FMPPort(Protocol):
    async def fetch(self, *, symbol: str, as_of: datetime) -> FMPIncomeEvidence: ...


class KISFundamentalPort(Protocol):
    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> KISFundamentalFetchResult: ...


@dataclass(frozen=True)
class KREvidenceCascadeResult:
    fundamentals: tuple[FundamentalObservation, ...]
    evidence_items: tuple[EvidenceItem, ...]
    quality: DataQualityStatus
    hard_vetoes: tuple[str, ...]
    issues: tuple[str, ...]
    call_evidence: tuple[Mapping[str, str], ...]
    selected_provider: str | None


_RISK_VETO = {
    "TRADING_SUSPENSION": "OFFICIAL_TRADING_SUSPENSION",
    "MANAGEMENT_ISSUE": "OFFICIAL_MANAGEMENT_OR_DELISTING_RISK",
    "DELISTING_RISK": "OFFICIAL_MANAGEMENT_OR_DELISTING_RISK",
    "UNRESOLVED_CORPORATE_ACTION": "OFFICIAL_UNRESOLVED_CORPORATE_ACTION",
}


class KREvidenceComposer:
    """Apply KIS fundamentals first, with optional DART/KIND verification and FMP fallback."""

    def __init__(
        self,
        *,
        stock_code: str,
        security_id: SecurityId,
        dart: DARTPort,
        kind: KINDPort,
        fmp: FMPPort | None,
        kis: KISFundamentalPort | None = None,
    ) -> None:
        if len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError("stock_code must be a six-digit KR provider symbol")
        if not isinstance(security_id, SecurityId):
            raise TypeError("security_id must be SecurityId")
        self._stock_code = stock_code
        self._security_id = security_id
        self._dart = dart
        self._kind = kind
        self._fmp = fmp
        self._kis = kis
        self.last_result: KREvidenceCascadeResult | None = None

    async def fetch(self, *, symbol: str, as_of: datetime) -> FMPIncomeEvidence:
        if not symbol or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("symbol and timezone-aware as_of are required")
        issues: list[str] = []
        calls: list[Mapping[str, str]] = []
        vetoes: list[str] = []
        kis_result: KISFundamentalFetchResult | None = None
        kis_income: FMPIncomeEvidence | None = None
        if self._kis is not None:
            try:
                kis_result = await self._kis.fetch(
                    stock_code=self._stock_code,
                    security_id=self._security_id,
                    as_of=as_of,
                )
            except Exception:
                issues.append("KIS_FUNDAMENTALS_FETCH_FAILED")
            else:
                issues.extend(kis_result.issues)
                calls.extend(
                    {
                        "provider": "KIS",
                        "status": capability.status.value,
                        "schema": capability.category.value,
                    }
                    for capability in kis_result.capabilities
                )
                if (
                    kis_result.quality
                    in {DataQualityStatus.FRESH, DataQualityStatus.PARTIAL}
                    and kis_result.earnings_current is not None
                    and kis_result.earnings_previous is not None
                    and kis_result.earnings_current_period is not None
                    and kis_result.earnings_previous_period is not None
                    and kis_result.available_at is not None
                    and kis_result.ingested_at is not None
                ):
                    current_record = next(
                        (
                            item
                            for item in reversed(kis_result.fundamentals)
                            if item.metric == "net_income"
                            and item.period_end == kis_result.earnings_current_period
                        ),
                        None,
                    )
                    previous_record = next(
                        (
                            item
                            for item in kis_result.fundamentals
                            if item.metric == "net_income"
                            and item.period_end == kis_result.earnings_previous_period
                        ),
                        None,
                    )
                    if current_record is None or previous_record is None:
                        issues.append("KIS_FUNDAMENTAL_RESULT_INCONSISTENT")
                    else:
                        kis_income = FMPIncomeEvidence(
                            current=kis_result.earnings_current,
                            previous=kis_result.earnings_previous,
                            current_period=kis_result.earnings_current_period.isoformat(),
                            current_accepted_at=kis_result.available_at,
                            current_unit=current_record.unit,
                            previous_period=kis_result.earnings_previous_period.isoformat(),
                            previous_accepted_at=kis_result.available_at,
                            previous_unit=previous_record.unit,
                            ingested_at=kis_result.ingested_at,
                            response_hash=current_record.source_hash,
                            provider="KIS",
                            provider_symbol=self._stock_code,
                            source_record_prefix="kis:finance:income",
                            source_url=(
                                "https://github.com/koreainvestment/open-trading-api/tree/main/"
                                "examples_llm/domestic_stock/finance_income_statement"
                            ),
                        )
        try:
            dart = await self._dart.fetch(
                stock_code=self._stock_code,
                security_id=self._security_id,
                as_of=as_of,
            )
        except Exception:
            dart = DARTFetchResult(
                fundamentals=(), evidence_items=(), quality=DataQualityStatus.UNAVAILABLE,
                risk_flags=(), issues=("DART_FETCH_FAILED",), call_evidence=(),
            )
        issues.extend(dart.issues)
        calls.extend(dart.call_evidence)
        vetoes.extend(_risk_vetoes(dart.risk_flags))

        try:
            kind = await self._kind.fetch(
                stock_code=self._stock_code,
                security_id=self._security_id,
                as_of=as_of,
            )
        except Exception:
            kind = KINDFetchResult(
                evidence_items=(), quality=DataQualityStatus.UNAVAILABLE,
                risk_flags=(), issues=("KIND_FETCH_FAILED",), call_evidence=(),
            )
        issues.extend(kind.issues)
        calls.extend(kind.call_evidence)
        vetoes.extend(_risk_vetoes(kind.risk_flags))

        official_income, conflict = _select_official_income(dart.fundamentals)
        if conflict:
            if kis_income is None:
                issues.append("SEVERE_OFFICIAL_FILING_CONFLICT")
                vetoes.append("SEVERE_OFFICIAL_FILING_CONFLICT")
            else:
                issues.append("DART_SUPPLEMENT_CONFLICT")
            official_income = None

        fmp_income = None
        if self._kis is None and self._fmp is not None:
            try:
                fmp_income = await self._fmp.fetch(symbol=symbol, as_of=as_of)
            except Exception:
                issues.append("FMP_SUPPLEMENT_UNAVAILABLE")

        evidence_items = (
            *(kis_result.evidence_items if kis_result is not None else ()),
            *dart.evidence_items,
            *kind.evidence_items,
        )
        if kis_income is not None:
            assert kis_result is not None  # constructed only from this normalized result
            selected = kis_income
            quality = kis_result.quality
            provider = "KIS"
        elif official_income is not None:
            current, previous = official_income
            selected = FMPIncomeEvidence(
                current=current.value,
                previous=previous.value,
                current_period=current.period_end.isoformat(),
                current_accepted_at=current.timing.available_at,
                current_unit=current.unit,
                previous_period=previous.period_end.isoformat(),
                previous_accepted_at=previous.timing.available_at,
                previous_unit=previous.unit,
                ingested_at=max(current.timing.ingested_at, previous.timing.ingested_at),
                response_hash=current.source_hash,
                provider="DART",
                provider_symbol=self._stock_code,
                source_record_prefix=f"dart:receipt:{current.source_record_id}",
                source_url=(
                    "https://dart.fss.or.kr/dsaf001/main.do"
                    f"?rcpNo={current.source_record_id}"
                ),
            )
            quality = dart.quality
            provider = "DART"
        elif fmp_income is not None and not conflict:
            selected = fmp_income
            quality = DataQualityStatus.FRESH
            provider = "FMP"
        else:
            issues.append("MISSING_SUPPLEMENTAL_FUNDAMENTALS")
            vetoes.append("MISSING_SUPPLEMENTAL_FUNDAMENTALS")
            quality = DataQualityStatus.CONFLICT if conflict else DataQualityStatus.UNAVAILABLE
            self.last_result = KREvidenceCascadeResult(
                fundamentals=(
                    *(kis_result.fundamentals if kis_result is not None else ()),
                    *dart.fundamentals,
                ),
                evidence_items=evidence_items,
                quality=quality,
                hard_vetoes=tuple(sorted(set(vetoes))),
                issues=tuple(dict.fromkeys(issues)),
                call_evidence=tuple(calls),
                selected_provider=None,
            )
            raise SupplementalFundamentalsUnavailable(
                "KR supplemental fundamentals are unavailable"
            )

        self.last_result = KREvidenceCascadeResult(
            fundamentals=(
                *(kis_result.fundamentals if kis_result is not None else ()),
                *dart.fundamentals,
            ),
            evidence_items=evidence_items,
            quality=quality,
            hard_vetoes=tuple(sorted(set(vetoes))),
            issues=tuple(dict.fromkeys(issues)),
            call_evidence=tuple(calls),
            selected_provider=provider,
        )
        return selected


def _select_official_income(
    values: tuple[FundamentalObservation, ...],
) -> tuple[tuple[FundamentalObservation, FundamentalObservation] | None, bool]:
    by_period: dict[date, list[FundamentalObservation]] = {}
    for item in values:
        if item.metric == "net_income" and item.quality is DataQualityStatus.FRESH:
            by_period.setdefault(item.period_end, []).append(item)
    selected: list[FundamentalObservation] = []
    for period in sorted(by_period, reverse=True):
        records = by_period[period]
        highest = max(item.revision for item in records)
        revisions = tuple(item for item in records if item.revision == highest)
        if len({(item.value, item.unit) for item in revisions}) > 1:
            return None, True
        selected.append(max(revisions, key=lambda item: item.timing.available_at))
    if len(selected) < 2:
        return None, False
    return (selected[0], selected[1]), False


def _risk_vetoes(flags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                _RISK_VETO.get(item, "UNRECOGNIZED_OFFICIAL_RISK_FLAG")
                for item in flags
            }
        )
    )
