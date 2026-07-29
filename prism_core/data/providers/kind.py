"""KIND/KRX evidence port with an explicit unavailable implementation.

No undocumented web endpoint is embedded here.  A concrete adapter can replace
this port only after a bounded official endpoint/export and its terms are approved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol

from prism_core.data.contracts import DataQualityStatus, EvidenceItem, SecurityId


@dataclass(frozen=True)
class KINDFetchResult:
    evidence_items: tuple[EvidenceItem, ...]
    quality: DataQualityStatus
    risk_flags: tuple[str, ...]
    issues: tuple[str, ...]
    call_evidence: tuple[Mapping[str, str], ...]


class KINDPort(Protocol):
    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> KINDFetchResult: ...


class UnavailableKINDAdapter:
    """Fail-closed declaration that no approved KIND transport is configured."""

    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> KINDFetchResult:
        del stock_code, security_id, as_of
        return KINDFetchResult(
            evidence_items=(),
            quality=DataQualityStatus.UNAVAILABLE,
            risk_flags=(),
            issues=("KIND_CAPABILITY_UNAVAILABLE",),
            call_evidence=(),
        )
