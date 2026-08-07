"""Channel-neutral batch campaign events published to a local durable queue.

The analysis orchestrators publish one screening or rest event per market/session/date.
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
COLLECTING = "COLLECTING"
COMPLETED = "COMPLETED"
SKIPPED = "SKIPPED"
_VALID_STATUSES = frozenset({COLLECTING, COMPLETED, SKIPPED})
_VALID_MARKETS = frozenset({"KR", "US"})
_VALID_SESSIONS = frozenset({"MORNING", "AFTERNOON"})
_VALID_REGIMES = frozenset({"UNKNOWN", "UPTREND", "UNDER_PRESSURE", "CORRECTION"})
REPORT_READY = "REPORT_READY"
DECISION_READY = "DECISION_READY"
PORTFOLIO_SNAPSHOT = "PORTFOLIO_SNAPSHOT"


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
    display_message: Optional[str] = None,
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

    if normalized_status in {COLLECTING, COMPLETED}:
        normalized_candidates = []
        for candidate in candidates or ():
            normalized = _normalized_candidate(candidate)
            if normalized is not None:
                normalized_candidates.append(normalized)
            if len(normalized_candidates) == 5:
                break
        if not normalized_candidates:
            raise ValueError("screening campaign requires at least one candidate")
        event["candidates"] = normalized_candidates
        rendered_message = str(display_message or "").strip()
        if rendered_message:
            event["display_message"] = rendered_message
    else:
        reason = str(skip_reason or "").strip()
        if not reason:
            raise ValueError("skipped campaign requires skip_reason")
        event["skip_reason"] = reason

    return event


def _story_base(
    *,
    market: str,
    session: str,
    trade_date: Any,
    regime: Optional[str],
    stage: str,
    occurred_at: Optional[datetime],
) -> Dict[str, Any]:
    normalized_market = str(_enum_value(market) or "").strip().upper()
    normalized_session = str(_enum_value(session) or "").strip().upper()
    normalized_regime = str(_enum_value(regime) or "UNKNOWN").strip().upper()
    if normalized_market not in _VALID_MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if normalized_session not in _VALID_SESSIONS:
        raise ValueError(f"unsupported session: {session}")
    if normalized_regime not in _VALID_REGIMES:
        raise ValueError(f"unsupported campaign regime: {regime}")
    normalized_date = _normalized_trade_date(trade_date)
    campaign_id = campaign_id_for(
        normalized_market, normalized_session, normalized_date
    )
    timestamp = occurred_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": f"BATCH_CAMPAIGN_{stage}",
        "campaign_id": campaign_id,
        "market": normalized_market,
        "session": normalized_session,
        "trade_date": normalized_date,
        "regime": normalized_regime,
        "occurred_at": timestamp.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def build_batch_report_event(
    *,
    market: str,
    session: str,
    trade_date: Any,
    regime: Optional[str],
    ticker: str,
    company_name: str,
    summary: str,
    artifact_path: str | Path,
    occurred_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    event = _story_base(
        market=market,
        session=session,
        trade_date=trade_date,
        regime=regime,
        stage=REPORT_READY,
        occurred_at=occurred_at,
    )
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_name = str(company_name or normalized_ticker).strip()
    normalized_summary = str(summary or "").strip()
    normalized_path = str(artifact_path or "").strip()
    if not normalized_ticker or not normalized_name:
        raise ValueError("report event requires ticker and company_name")
    if not normalized_summary:
        raise ValueError("report event requires summary")
    if not normalized_path.lower().endswith(".pdf"):
        raise ValueError("report event requires a PDF artifact_path")
    event.update(
        {
            "event_id": f"{event['campaign_id']}:report:{normalized_ticker}",
            "ticker": normalized_ticker,
            "company_name": normalized_name,
            "message": normalized_summary,
            "artifact_path": normalized_path,
        }
    )
    return event


def _build_text_story_event(
    *,
    market: str,
    session: str,
    trade_date: Any,
    regime: Optional[str],
    stage: str,
    suffix: str,
    message: str,
    occurred_at: Optional[datetime],
) -> Dict[str, Any]:
    event = _story_base(
        market=market,
        session=session,
        trade_date=trade_date,
        regime=regime,
        stage=stage,
        occurred_at=occurred_at,
    )
    normalized_message = str(message or "").strip()
    if not normalized_message:
        raise ValueError(f"{suffix} event requires message")
    event["event_id"] = f"{event['campaign_id']}:{suffix}"
    event["message"] = normalized_message
    return event


def build_batch_decision_event(
    *, decision_key: str | None = None, **fields: Any
) -> Dict[str, Any]:
    event = _build_text_story_event(**fields, stage=DECISION_READY, suffix="decision")
    if decision_key is not None:
        normalized_key = str(decision_key).strip().lower()
        if not normalized_key or not normalized_key.replace("-", "").isalnum():
            raise ValueError(
                "decision_key must contain only letters, numbers, or hyphens"
            )
        event["event_id"] = f"{event['campaign_id']}:decision:{normalized_key}"
    return event


def build_batch_portfolio_event(**fields: Any) -> Dict[str, Any]:
    return _build_text_story_event(
        **fields, stage=PORTFOLIO_SNAPSHOT, suffix="portfolio"
    )


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


async def publish_batch_event_best_effort(
    event: Mapping[str, Any],
) -> Optional[str]:
    """Publish an already-built story event without affecting the batch."""
    publisher: Optional[BatchCampaignPublisher] = None
    try:
        publisher = BatchCampaignPublisher()
        await publisher.connect()
        return await publisher.publish(event)
    except Exception as exc:
        logger.warning("Batch story hook failed (ignored): %s", exc)
        return None
    finally:
        if publisher is not None:
            await publisher.disconnect()


async def publish_batch_reports_best_effort(
    *,
    market: str,
    session: str,
    trade_date: Any,
    regime: Optional[str],
    pdf_paths: Sequence[Any],
    message_paths: Sequence[Any],
) -> int:
    """Publish only PDF reports that have a matching Telegram summary."""
    summaries: dict[str, tuple[str, str]] = {}
    for raw_path in message_paths:
        path = Path(str(raw_path))
        stem = path.stem.removesuffix("_telegram")
        if "_" not in stem:
            continue
        ticker, company_name = stem.split("_", 1)
        try:
            message = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Could not read campaign summary %s: %s", path, exc)
            continue
        if message:
            summaries[ticker.upper()] = (company_name, message)

    published = 0
    for raw_path in pdf_paths:
        path = Path(str(raw_path))
        parts = path.stem.split("_")
        ticker = parts[0].upper() if parts else ""
        matched = summaries.get(ticker)
        if matched is None or not path.is_file():
            logger.warning("Skipping Kakao report without complete artifacts: %s", path)
            continue
        company_name, message = matched
        try:
            event = build_batch_report_event(
                market=market,
                session=session,
                trade_date=trade_date,
                regime=regime,
                ticker=ticker,
                company_name=company_name,
                summary=message,
                artifact_path=path.resolve(),
            )
        except Exception as exc:
            logger.warning("Could not build Kakao report event for %s: %s", path, exc)
            continue
        published += int(await publish_batch_event_best_effort(event) is not None)
    return published


async def publish_batch_tracking_story_best_effort(
    *,
    market: str,
    session: str,
    trade_date: Any,
    regime: Optional[str],
    messages: Sequence[tuple[Optional[str], str]],
) -> int:
    """Publish the tracking agent's actual decision and portfolio messages."""
    decision_messages = [
        str(message).strip()
        for message_type, message in messages
        if message_type == "analysis" and str(message).strip()
    ]
    portfolio_messages = [
        str(message).strip()
        for message_type, message in messages
        if message_type == "portfolio" and str(message).strip()
    ]
    common = {
        "market": market,
        "session": session,
        "trade_date": trade_date,
        "regime": regime,
        "occurred_at": None,
    }
    events = []
    if decision_messages:
        events.extend(
            build_batch_decision_event(
                **common,
                decision_key=f"{index:02d}",
                message=message,
            )
            for index, message in enumerate(decision_messages, 1)
        )
    else:
        events.append(
            build_batch_decision_event(
                **common,
                message="이번 배치에서 새로 실행된 가상 매수·매도는 없습니다.",
            )
        )
    if portfolio_messages:
        events.append(
            build_batch_portfolio_event(
                **common, message="\n\n".join(portfolio_messages)
            )
        )

    published = 0
    for event in events:
        published += int(await publish_batch_event_best_effort(event) is not None)
    return published
