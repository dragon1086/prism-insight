"""Keep a short copy of what the trading AI's tools answered, for later audits.

The BUY/SELL decision AI calls read-only tools (filings, news, prices, the
portfolio DB) and the decision cites them, but the backend used to keep only the
tool name and arguments, so the evidence audit could not check claims such as
"no tender offer or delisting" (EVIDENCE_NOT_RETAINED). Each decision now appends
one JSON line per call to ``logs/tool_evidence/<market>_<YYYYMMDD>.jsonl``: the
tool, its arguments, its status, up to ``MAX_URLS`` source links and the first
``MAX_EXCERPT_CHARS`` characters of the reply. Files older than ``KEEP_DAYS``
are deleted on write. Recording never raises; set ``TOOL_EVIDENCE_LOG_ENABLED=false``
to turn it off.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

KEEP_DAYS = 30
MAX_EXCERPT_CHARS = 1500
MAX_URLS = 10
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "logs" / "tool_evidence"

_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
# Secrets that a tool may echo back: query-string keys and Telegram bot tokens.
_SECRET_PARAM = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret|key|appkey|appsecret)=([^&\s\"']+)")
_BOT_TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")


def _redact(text: str) -> str:
    text = _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=REDACTED", text)
    return _BOT_TOKEN.sub("<redacted-token>", text)


def _enabled() -> bool:
    return os.getenv("TOOL_EVIDENCE_LOG_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _directory() -> Path:
    return Path(os.getenv("TOOL_EVIDENCE_LOG_DIR") or DEFAULT_DIR)


def _prune(directory: Path, now: float) -> None:
    cutoff = now - KEEP_DAYS * 86400
    for path in directory.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _row(call, *, market: str, ticker: str, decision: str, model: str, recorded_at: str) -> dict:
    text = _redact(str(getattr(call, "result_text", "") or ""))
    arguments = getattr(call, "arguments", None)
    try:
        arguments = json.loads(_redact(json.dumps(arguments, ensure_ascii=False, default=str)))
    except (TypeError, ValueError):
        arguments = None
    error = getattr(call, "error", None)
    return {
        "recorded_at": recorded_at,
        "market": market,
        "ticker": ticker,
        "decision": decision,
        "model": model,
        "server": getattr(call, "server", ""),
        "tool": getattr(call, "tool", ""),
        "arguments": arguments,
        "status": getattr(call, "status", None),
        "error": _redact(str(error))[:300] if error else None,
        "result_chars": len(text),
        "urls": list(dict.fromkeys(_URL.findall(text)))[:MAX_URLS],
        "excerpt": text[:MAX_EXCERPT_CHARS],
    }


def record(*, market: str, ticker: str, decision: str, result, model: str = "") -> int:
    """Append one line per tool call of ``result``; return the number written (0 on any failure)."""
    if not _enabled():
        return 0
    calls = list(getattr(result, "mcp_calls", None) or [])
    if not calls:
        return 0
    try:
        now = time.time()
        moment = datetime.now().astimezone()
        recorded_at = moment.isoformat(timespec="seconds")
        rows = [_row(call, market=market, ticker=str(ticker or "?"), decision=decision, model=model,
                     recorded_at=recorded_at) for call in calls]
        directory = _directory()
        directory.mkdir(parents=True, exist_ok=True)
        _prune(directory, now)
        path = directory / f"{market.lower()}_{moment.strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        logger.info("[TOOL_EVIDENCE] recorded market=%s ticker=%s decision=%s calls=%d",
                    market, ticker or "?", decision, len(rows))
        return len(rows)
    except Exception as exc:  # noqa: BLE001 — evidence capture must never block trading
        logger.warning("[TOOL_EVIDENCE] record failed market=%s ticker=%s: %s",
                       market, ticker or "?", type(exc).__name__)
        return 0
