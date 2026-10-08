"""Put a candidate's screening facts on its decision trace (fail-open).

Screening runs before the report exists, so the screening ledger
(``runtime/candidate_ledger.sqlite``) cannot know the ``report:<pdf>`` decision
id that names the position trace.  When ``candidate.evaluated`` is emitted both
keys are known: the report file name carries the trade date and session, which
are exactly the ledger's (market, trade_date, mode, ticker) key.  This module
reads that row and appends ``screening.candidate_linked`` on the decision's
trace, timestamped at the screening run, so one trace reads screening →
analysis → entry → holds → exit in time order.  Read-only; never raises.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observability.events import emit_event

EVENT_TYPE = "screening.candidate_linked"
_ROOT = Path(__file__).resolve().parents[1]
_REPORT_KEY = re.compile(r"_(\d{8})_(morning|afternoon)_")
_COLUMNS = (
    "trigger_type", "rank_in_trigger", "candidates_in_trigger", "composite_score", "final_score",
    "rs_score", "extension_score", "close", "volume", "amount", "change_rate", "selected",
    "selection_channel", "selected_trigger", "market_regime", "above_ma50", "ma50_rising",
    "ma20_gt_ma50", "above_ma200", "vol_ratio_50", "updown_vol_ratio_50", "vol_dryup_10_50",
    "pocket_pivot", "created_at",
)


def screening_key(decision_id: Any) -> tuple[str, str] | None:
    """(trade_date YYYY-MM-DD, session) from a ``report:<pdf>`` decision id."""
    match = _REPORT_KEY.search(str(decision_id or "")) if str(decision_id or "").startswith("report:") else None
    if not match:
        return None
    day = match.group(1)
    return f"{day[:4]}-{day[4:6]}-{day[6:]}", match.group(2)


def _ledger_path() -> Path:
    configured = os.getenv("CANDIDATE_LEDGER_DB", "").strip()
    return Path(configured) if configured else _ROOT / "runtime" / "candidate_ledger.sqlite"


def _screened_at(rows: list[dict[str, Any]]) -> datetime | None:
    stamps = [row.get("created_at") for row in rows if row.get("created_at")]
    if not stamps:
        return None
    parsed = datetime.fromisoformat(min(stamps))
    # Naive rows are server-local wall time (candidate_ledger uses datetime.now()).
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.astimezone().astimezone(timezone.utc)


def emit_screening_link(market: str, ticker: str, decision_id: str, trace_id: str) -> dict[str, Any] | None:
    """Append the matching screening rows to ``trace_id``; ``None`` when unmatched."""
    try:
        key = screening_key(decision_id)
        path = _ledger_path()
        if key is None or not path.exists():
            return None
        normalized_market, normalized_ticker = str(market).upper(), str(ticker).upper()
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = [
                {column: row[column] for column in _COLUMNS}
                for row in conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM candidate_ledger "  # nosec B608 - fixed column list
                    "WHERE market = ? AND trade_date = ? AND mode = ? AND upper(ticker) = ? "
                    "ORDER BY rank_in_trigger",
                    (normalized_market, key[0], key[1], normalized_ticker),
                )
            ]
        if not rows:
            return None
        best = next((row for row in rows if row.get("selected")), rows[0])
        return emit_event(
            EVENT_TYPE,
            service=f"prism-{normalized_market.lower()}-screening",
            event_id=hashlib.sha256(f"screening-link|{decision_id}".encode()).hexdigest()[:32],
            market=normalized_market,
            ticker=normalized_ticker,
            trace_id=trace_id,
            decision_id=decision_id,
            event_time=_screened_at(rows),
            attributes={
                "trade_date": key[0],
                "mode": key[1],
                "selected": bool(best.get("selected")),
                "selection_channel": best.get("selection_channel"),
                "selected_trigger": best.get("selected_trigger"),
                "market_regime": best.get("market_regime"),
                "trigger_count": len(rows),
                "triggers": [{k: v for k, v in row.items() if k != "created_at"} for row in rows[:10]],
            },
        )
    except Exception:  # noqa: BLE001 - observation must never affect a decision
        return None


__all__ = ["EVENT_TYPE", "emit_screening_link", "screening_key"]
