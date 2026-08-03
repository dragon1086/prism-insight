"""Channel-neutral batch campaign events published to a local durable queue.

The analysis orchestrators publish one terminal event per market/session/date.
Consumers (Kakao today, other channels later) decide independently whether and
where to deliver it. Publishing is deliberately fail-open: queue failures
must never fail analysis or trading.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
COMPLETED = "COMPLETED"
SKIPPED = "SKIPPED"
_VALID_STATUSES = frozenset({COMPLETED, SKIPPED})
_VALID_MARKETS = frozenset({"KR", "US"})
_VALID_SESSIONS = frozenset({"MORNING", "AFTERNOON"})
_VALID_REGIMES = frozenset({"UNKNOWN", "UPTREND", "UNDER_PRESSURE", "CORRECTION"})


def _enum_value(value: Any) -> Any:
    """Accept plain strings and string-valued Enum members without coupling."""
    return getattr(value, "value", value)


def _normalized_trade_date(value: Any) -> str:
    """Return ``YYYY-MM-DD`` for date, datetime, YYYYMMDD, or ISO input."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    raw = str(value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    return date.fromisoformat(raw).isoformat()


def campaign_id_for(market: str, session: str, trade_date: Any) -> str:
    """Build the stable business identifier used for consumer deduplication."""
    normalized_market = str(_enum_value(market) or "").strip().upper()
    normalized_session = str(_enum_value(session) or "").strip().upper()
    if normalized_market not in _VALID_MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if normalized_session not in _VALID_SESSIONS:
        raise ValueError(f"unsupported session: {session}")
    normalized_date = _normalized_trade_date(trade_date)
    return f"{normalized_market.lower()}-{normalized_session.lower()}-{normalized_date}"


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_candidate(candidate: Any) -> Optional[Dict[str, Any]]:
    if isinstance(candidate, Mapping):
        ticker = (
            candidate.get("ticker") or candidate.get("code") or candidate.get("symbol")
        )
        company_name = candidate.get("company_name") or candidate.get("name") or ticker
        score = candidate.get("score")
        if score is None:
            score = candidate.get("buy_score")
        if score is None:
            score = candidate.get("risk_reward_ratio")
        rationale = candidate.get("rationale") or candidate.get("trigger_type")
    else:
        ticker = candidate
        company_name = candidate
        score = None
        rationale = None

    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return None

    return {
        "ticker": normalized_ticker,
        "company_name": str(company_name or normalized_ticker).strip(),
        "score": _optional_float(score),
        "rationale": str(rationale).strip() if rationale else None,
    }


def select_reported_candidates(
    candidates: Sequence[Any], report_paths: Sequence[Any]
) -> list[Any]:
    """Keep candidates whose report artifact was generated successfully.

    Both orchestrators name reports ``{ticker}_...``.  Filtering on those
    artifacts prevents a partially failed batch from advertising failed
    candidates as completed.
    """
    reported_tickers = {
        Path(str(path)).name.split("_", 1)[0].strip().upper()
        for path in report_paths
        if str(path).strip()
    }
    selected = []
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            ticker = (
                candidate.get("ticker")
                or candidate.get("code")
                or candidate.get("symbol")
            )
        else:
            ticker = candidate
        if str(ticker or "").strip().upper() in reported_tickers:
            selected.append(candidate)
    return selected


def build_batch_campaign_event(
    *,
    market: str,
    session: str,
    trade_date: Any,
    regime: Optional[str],
    status: str,
    candidates: Optional[Sequence[Any]] = None,
    skip_reason: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build and validate the canonical JSON-compatible campaign payload."""
    normalized_status = str(_enum_value(status) or "").strip().upper()
    if normalized_status not in _VALID_STATUSES:
        raise ValueError(f"unsupported campaign status: {status}")

    normalized_market = str(_enum_value(market) or "").strip().upper()
    normalized_session = str(_enum_value(session) or "").strip().upper()
    normalized_regime = str(_enum_value(regime) or "UNKNOWN").strip().upper()
    if normalized_regime not in _VALID_REGIMES:
        raise ValueError(f"unsupported campaign regime: {regime}")
    normalized_date = _normalized_trade_date(trade_date)
    timestamp = occurred_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)

    event: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": f"BATCH_CAMPAIGN_{normalized_status}",
        "campaign_id": campaign_id_for(
            normalized_market, normalized_session, normalized_date
        ),
        "market": normalized_market,
        "session": normalized_session,
        "trade_date": normalized_date,
        "regime": normalized_regime,
        "status": normalized_status,
        "occurred_at": timestamp.isoformat().replace("+00:00", "Z"),
    }

    if normalized_status == COMPLETED:
        normalized_candidates = []
        for candidate in candidates or ():
            normalized = _normalized_candidate(candidate)
            if normalized is not None:
                normalized_candidates.append(normalized)
            if len(normalized_candidates) == 5:
                break
        if not normalized_candidates:
            raise ValueError("completed campaign requires at least one candidate")
        event["candidates"] = normalized_candidates
    else:
        reason = str(skip_reason or "").strip()
        if not reason:
            raise ValueError("skipped campaign requires skip_reason")
        event["skip_reason"] = reason

    return event


class BatchCampaignPublisher:
    """Local queue publisher isolated from Kakao and trading-signal transports."""

    DEFAULT_DATABASE_PATH = "prism_campaign_queue.sqlite"

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        queue: SQLiteBatchCampaignQueue | None = None,
    ) -> None:
        configured_path = (
            database_path
            or os.environ.get("PRISM_CAMPAIGN_QUEUE_PATH")
            or self.DEFAULT_DATABASE_PATH
        )
        self.database_path = Path(configured_path)
        self._queue = queue
        self._owns_queue = queue is None

    async def __aenter__(self) -> "BatchCampaignPublisher":
        await self.connect()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        try:
            if self._queue is None:
                self._queue = SQLiteBatchCampaignQueue(self.database_path)
        except Exception as exc:  # fail-open by contract
            logger.warning(
                "Batch campaign local queue connection failed (ignored): %s",
                exc,
            )
            self._queue = None

    async def disconnect(self) -> None:
        queue = self._queue
        if queue is None or not self._owns_queue:
            return
        self._queue = None
        try:
            queue.close()
        except Exception as exc:
            logger.warning(
                "Batch campaign local queue close failed (ignored): %s",
                exc,
            )

    async def publish(self, event: Mapping[str, Any]) -> Optional[str]:
        if self._queue is None:
            return None
        try:
            campaign_id = self._queue.enqueue(event)
            logger.info(
                "Batch campaign queued locally: %s (created=%s)",
                event.get("campaign_id"),
                campaign_id is not None,
            )
            return campaign_id
        except Exception as exc:  # fail-open by contract
            logger.warning("Batch campaign publish failed (ignored): %s", exc)
            return None


async def publish_batch_campaign_best_effort(**event_fields: Any) -> Optional[str]:
    """Build and publish an event without propagating any failure to callers."""
    publisher: Optional[BatchCampaignPublisher] = None
    try:
        event = build_batch_campaign_event(**event_fields)
        publisher = BatchCampaignPublisher()
        await publisher.connect()
        return await publisher.publish(event)
    except Exception as exc:  # includes invalid input and unavailable local queue
        logger.warning("Batch campaign hook failed (ignored): %s", exc)
        return None
    finally:
        if publisher is not None:
            await publisher.disconnect()
