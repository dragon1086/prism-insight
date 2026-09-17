"""Optional descriptive market evidence; never mutates trading regime or scores."""
import hashlib
import json
import math
import os
import subprocess  # nosec B404 - fixed local collector, shell disabled, date validated below
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ETF_LABELS = {"SPY": "benchmark", "IWF": "growth proxy", "IWD": "value proxy",
              "RSP": "equal-weight proxy", "IWM": "small-cap proxy", "XLK": "Technology",
              "XLV": "Healthcare", "XLF": "Financial Services", "XLY": "Consumer Cyclical",
              "XLP": "Consumer Defensive", "XLE": "Energy", "XLI": "Industrials",
              "XLB": "Basic Materials", "XLRE": "Real Estate", "XLU": "Utilities",
              "XLC": "Communication Services"}


def enabled():
    override = os.getenv("REPORT_MARKET_CONTEXT_ENABLED")
    if override is not None:
        return override.lower() == "true"
    try:
        config = json.loads((ROOT / "runtime/report_research_config.json").read_text())
        return isinstance(config, dict) and config.get("market_context_enabled") is True
    except (OSError, ValueError, TypeError):
        return False


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def snapshot_participation(snapshot, market, asof, expected_count=None, previous=None):
    """Snapshot advance/decline counts, not whole-market MA breadth or cash flows."""
    columns = ("등락률", "ChangeRate", "change_pct", "change_percent", "Change")
    column = next((key for key in columns if key in snapshot.columns), None)
    values = [_number(value) for value in snapshot[column]] if column else []
    if not column and previous is not None:
        close = "Close" if "Close" in snapshot.columns else "종가"
        if close in snapshot.columns and close in previous.columns:
            for ticker in snapshot.index:
                current = _number(snapshot.loc[ticker, close])
                prior = _number(previous.loc[ticker, close]) if ticker in previous.index else None
                values.append((current / prior - 1) * 100 if current and prior and prior > 0 else None)
    valid = [value for value in values if value is not None]
    denominator = max(len(snapshot), expected_count or 0)
    return {"contract": "market_snapshot_participation_v1", "market": market,
            "asof": str(asof), "scope": "existing_batch_snapshot_universe",
            "source": "existing_validated_batch_snapshot",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "timing": "snapshot_may_be_intraday", "universe_count": denominator,
            "valid_count": len(valid), "missing_count": denominator - len(valid),
            "advance": sum(value > 0 for value in valid), "decline": sum(value < 0 for value in valid),
            "unchanged": sum(value == 0 for value in valid),
            "status": "AVAILABLE" if valid else "UNKNOWN", "is_cash_flow": False,
            "is_full_market_ma_breadth": False}


def optional_participation(snapshot, previous, market, asof, expected_count=None):
    if not enabled():
        return None
    try:
        return snapshot_participation(snapshot, market, asof, expected_count, previous)
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"contract": "market_snapshot_participation_v1", "market": market, "status": "UNKNOWN"}


def build_etf_packet(series, reference_date, market_days=None):
    """Align each comparison to benchmark sessions strictly before analysis date."""
    cutoff = datetime.strptime(reference_date, "%Y%m%d").replace(tzinfo=timezone.utc).strftime("%Y-%m-%d")
    cleaned = {}
    for symbol, rows in series.items():
        cleaned[symbol] = {str(row[0])[:10]: _number(row[1]) for row in rows
                           if str(row[0])[:10] < cutoff and _number(row[1]) is not None and float(row[1]) > 0}
    dates = sorted(day for day in market_days if day < cutoff) if market_days is not None else sorted(cleaned.get("SPY", {}))
    output = []
    for symbol, label in ETF_LABELS.items():
        values = cleaned.get(symbol, {})
        row = {"symbol": symbol, "label": label, "returns_pct": {}, "relative_spy_pp": {}, "windows": {}}
        for horizon in (5, 20, 60):
            window = dates[-horizon - 1:]
            if len(window) != horizon + 1 or any(day not in values or day not in cleaned.get("SPY", {}) for day in window):
                continue
            start, end = window[0], window[-1]
            absolute = (values[end] / values[start] - 1) * 100
            benchmark = (cleaned["SPY"][end] / cleaned["SPY"][start] - 1) * 100
            row["returns_pct"][str(horizon)] = round(absolute, 3)
            row["relative_spy_pp"][str(horizon)] = round(absolute - benchmark, 3)
            row["windows"][str(horizon)] = {"start": start, "end": end}
        row["status"] = "AVAILABLE" if row["returns_pct"] else "UNKNOWN"
        output.append(row)
    return {"contract": "market_etf_rotation_v1", "market": "US", "reference_date": reference_date,
            "price_asof": max(cleaned.get("SPY", {}), default=None),
            "expected_price_asof": dates[-1] if dates else None, "source": "yfinance_adjusted_daily",
            "captured_at": datetime.now(timezone.utc).isoformat(), "rows": output,
            "status": "AVAILABLE" if any(row["returns_pct"] for row in output) else "UNKNOWN", "is_cash_flow": False,
            "calendar_verified": market_days is not None,
            "input_sha256": hashlib.sha256(json.dumps(cleaned, sort_keys=True).encode()).hexdigest(),
            "scope": "fixed_16_etf_proxies_not_constituent_breadth"}


def prefetch_us_context(reference_date):
    if not enabled():
        return None
    try:
        if not isinstance(reference_date, str) or len(reference_date) != 8 or not reference_date.isascii() or not reference_date.isdigit():
            raise ValueError("invalid_analysis_date")
        datetime.strptime(reference_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        # Interpreter and collector are locally fixed; only YYYYMMDD crosses this boundary.
        run = subprocess.run(  # nosec B603 B607  # nosemgrep
                             [sys.executable, str(ROOT / "tools/run_market_intelligence_prefetch.py"),
                              "--date", reference_date], capture_output=True, text=True, timeout=20, check=True)
        packet = json.loads(run.stdout)
        if not isinstance(packet, dict) or packet.get("contract") != "market_etf_rotation_v1":
            raise ValueError("unexpected_packet_contract")
        return packet
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"contract": "market_etf_rotation_v1", "status": "UNKNOWN", "reference_date": reference_date}


def render_context(packet):
    if not packet:
        return ""
    lines = ["## Quantitative market context (descriptive; not a new regime or score)",
             "ETF price returns are NOT net fund flows or constituent breadth. Do not double-count VIX.",
             f"Source: {packet.get('source', 'UNKNOWN')}; completed price date: {packet.get('price_asof', 'UNKNOWN')}; status: {packet.get('status')}",
             "Symbol | 5/20/60 session return % | excess vs SPY percentage points"]
    for row in packet.get("rows", []):
        absolute = "/".join(str(row["returns_pct"].get(str(day), "?")) for day in (5, 20, 60))
        relative = "/".join(str(row["relative_spy_pp"].get(str(day), "?")) for day in (5, 20, 60))
        lines.append(f"{row['symbol']} ({row['label']}) | {absolute} | {relative}")
    return "\n".join(lines)[:2500]
