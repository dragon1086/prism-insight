"""Public market-data collection from immutable sanitized Evidence Packets only.

RECONSTRUCTED_REPLAY is not an observed-at-decision snapshot, backtest, holdout,
or promotion. Never fills Packet fields or imports trading/database code.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import re

CUTOFF = "2026-09-09T16:18:06.031436Z"
FIELDS = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
          "Adj Close": "adj_close", "Volume": "volume", "Dividends": "dividends",
          "Stock Splits": "stock_splits", "Capital Gains": "capital_gains"}
EXCHANGES = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE"}


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_missing")
    return parsed.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def read_packets(paths):
    packets, rows = [], []
    for path in paths:
        raw = Path(path).read_bytes()
        packet = json.loads(raw)
        if packet.get("packet_schema_version") != 3 or packet.get("analysis_contract_version") != "entry-quality-harness-v2":
            raise ValueError("unsupported_packet")
        if packet.get("market") not in {"US", "KR"} or stamp(packet["as_of"]) > stamp(CUTOFF):
            raise ValueError("invalid_packet_cutoff")
        if not re.fullmatch(r"[0-9a-f]{24}", packet["packet_id"]):
            raise ValueError("invalid_packet_id")
        packets.append({"packet_id": packet["packet_id"], "packet_sha256": hashlib.sha256(raw).hexdigest(),
                        "market": packet["market"], "as_of": packet["as_of"],
                        "packet_schema_version": 3, "analysis_contract_version": "entry-quality-harness-v2",
                        "candidate_count": len(packet["analysis_rows"])})
        for row in packet["analysis_rows"]:
            ticker, ref = row["ticker"], row["decision_ref"]
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker) or not re.fullmatch(r"[0-9a-f]{16}", ref):
                raise ValueError("invalid_row_identity")
            decision = stamp(row["decided_at"])
            if decision > stamp(CUTOFF):
                raise ValueError("decision_after_cutoff")
            outcome = row.get("outcomes", {})
            entry, closed = outcome.get("strategy_entry_at"), outcome.get("strategy_closed_at")
            if closed and (not entry or not decision <= stamp(entry) <= stamp(closed) <= stamp(CUTOFF)):
                raise ValueError("invalid_trade_chronology")
            rows.append({"packet_id": packet["packet_id"], "market": packet["market"], "ticker": ticker,
                         "decision_ref": ref, "decided_at": row["decided_at"],
                         "strategy_entry_at": entry, "strategy_closed_at": closed})
    if len({r["decision_ref"] for r in rows}) != len(rows):
        raise ValueError("duplicate_decision")
    return packets, rows


def requests_for(rows):
    requests = []
    groups = {}
    for row in rows:
        groups.setdefault((row["market"], row["ticker"]), []).append(row)
    for (market, ticker), group in sorted(groups.items()):
        start = min(stamp(r["decided_at"]) for r in group) - timedelta(days=180)
        requests.append({"market": market, "ticker": ticker, "interval": "1d",
                         "start": iso(start), "end": CUTOFF})
    for row in rows:
        if not row["strategy_closed_at"]:
            continue
        for interval in ("1m", "5m"):
            start, end = stamp(row["strategy_entry_at"]), stamp(row["strategy_closed_at"])
            # Short segments avoid a provider per-request one-minute range limit.
            while start < end:
                stop = min(start + timedelta(days=5), end)
                requests.append({"market": row["market"], "ticker": row["ticker"],
                                 "interval": interval, "start": iso(start), "end": iso(stop),
                                 "decision_ref": row["decision_ref"]})
                start = stop
    return requests


def _fetch_child(request, connection):
    # Child-local output suppression and cache: no auth keys, reusable cookie files,
    # prompts, or provider exception strings leave this boundary.
    import contextlib
    import io
    import logging
    import tempfile
    logging.disable(logging.CRITICAL)
    started = iso(datetime.now(timezone.utc))
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), tempfile.TemporaryDirectory() as cache:
            import yfinance as yf
            yf.set_tz_cache_location(cache)
            ticker = yf.Ticker(request["ticker"])
            frame = ticker.history(start=stamp(request["start"]), end=stamp(request["end"]),
                                   interval=request["interval"], auto_adjust=False, back_adjust=False,
                                   repair=False, actions=True, prepost=True, keepna=True,
                                   rounding=False, timeout=10, raise_errors=True)
            # Already-populated history metadata only: public metadata API can
            # fetch another changing 1h dataset, so do not call it here.
            metadata = getattr(ticker._price_history, "_history_metadata", {}) or {}
            raw_rows = []
            for when, row in frame.iterrows():
                if when.tzinfo is None:
                    raise ValueError("provider_timezone_missing")
                values = {key: (float(row[column]) if column in row and math.isfinite(float(row[column])) else None)
                          for column, key in FIELDS.items()}
                raw_rows.append({"provider_timestamp": when.isoformat(), **values})
            result = {"status": "received" if raw_rows else "empty", "raw_rows": raw_rows,
                      "exchange": metadata.get("exchangeName") if metadata.get("exchangeName") in EXCHANGES else None,
                      "yfinance_version": yf.__version__}
    except Exception:
        result = {"status": "provider_error", "raw_rows": [], "exchange": None}
    result.update(retrieval_started_at=started, retrieved_at=iso(datetime.now(timezone.utc)))
    connection.send(result)
    connection.close()


def fetch(request, timeout=40):
    if request["market"] != "US":
        return {"status": "market_mapping_unavailable", "raw_rows": [], "exchange": None,
                "retrieved_at": iso(datetime.now(timezone.utc))}
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_fetch_child, args=(request, send), daemon=True)
    process.start()
    send.close()
    try:
        if receive.poll(timeout):
            result = receive.recv()
        else:
            result = {"status": "timeout", "raw_rows": [], "exchange": None,
                      "retrieved_at": iso(datetime.now(timezone.utc))}
    except EOFError:
        result = {"status": "worker_error", "raw_rows": [], "exchange": None,
                  "retrieved_at": iso(datetime.now(timezone.utc))}
    finally:
        receive.close()
        process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
    return result


def normalize(request, response):
    import pandas_market_calendars as calendars
    interval = request["interval"]
    calendar_name = EXCHANGES.get(response.get("exchange"))
    result = {"request": request, **response, "calendar": calendar_name,
              "calendar_version": calendars.__version__, "source_sha256": sha(response),
              "bars": [], "exclusions": {}}
    exclusions = Counter()
    if not calendar_name:
        exclusions["EXCHANGE_CALENDAR_UNKNOWN"] = len(response["raw_rows"])
        result["exclusions"] = dict(exclusions)
        return result
    calendar = calendars.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=(stamp(request["start"]) - timedelta(days=2)).date(),
                                 end_date=stamp(request["end"]).date())
    seen = set()
    for raw in response["raw_rows"]:
        opened = stamp(raw["provider_timestamp"])
        local_date = opened.astimezone(calendar.tz).date().isoformat()
        if local_date not in schedule.index.strftime("%Y-%m-%d"):
            exclusions["NON_SESSION_DATE"] += 1
            continue
        session = schedule.loc[local_date]
        regular_open, regular_close = session["market_open"].to_pydatetime(), session["market_close"].to_pydatetime()
        closed = regular_close if interval == "1d" else opened + timedelta(minutes=int(interval[:-1]))
        if interval != "1d" and opened < stamp(request["start"]):
            exclusions["BAR_START_BEFORE_REQUEST_START"] += 1
            continue
        if closed > stamp(request["end"]) or closed > stamp(CUTOFF):
            exclusions["INCOMPLETE_AT_REQUEST_CUTOFF"] += 1
            continue
        if opened.isoformat() in seen:
            exclusions["DUPLICATE_PROVIDER_TIMESTAMP"] += 1
            continue
        seen.add(opened.isoformat())
        if any(raw.get(key) is None for key in ("open", "high", "low", "close", "volume")):
            exclusions["MISSING_OHLCV"] += 1
            continue
        if any(raw[key] <= 0 for key in ("open", "high", "low", "close")) or raw["volume"] < 0:
            exclusions["INVALID_OHLCV"] += 1
            continue
        regular = regular_open <= opened < regular_close
        result["bars"].append({**raw, "bar_close_at": iso(closed), "session_date": local_date,
                               "session": "regular" if interval == "1d" or regular else "extended"})
    result["exclusions"] = dict(exclusions)
    result["bars"].sort(key=lambda bar: bar["bar_close_at"])
    return result


def collect(paths, fetcher=fetch):
    packets, rows = read_packets(paths)
    requested = requests_for(rows)
    artifact = {"kind": "RECONSTRUCTED_REPLAY", "version": 1, "data_cutoff": CUTOFF,
                "created_at": iso(datetime.now(timezone.utc)), "packets": packets,
                "price_policy": "YAHOO_AS_DELIVERED_AUTO_ADJUST_FALSE_BACK_ADJUST_FALSE_REPAIR_FALSE",
                "corporate_action_caveat": "Provider OHLC can already reflect splits; not original unadjusted exchange tape. Raw dividend/split columns and adj_close retained, no invented action corrections.",
                "limitations": ["NOT_OBSERVED_AT_DECISION", "NO_PACKET_BACKFILL", "NO_PERFORMANCE_ANALYSIS",
                    "NO_INDEPENDENT_HOLDOUT", "ORIGINAL_STOP_THRESHOLDS_MISSING", "QUOTE_CADENCE_UNKNOWN",
                    "EXTENDED_HOURS_COMPLETENESS_UNKNOWN", "CURRENT_LISTING_SURVIVOR_COVERAGE_ONLY",
                    "ACTION_ANNOUNCEMENT_TIMES_UNKNOWN"],
                "automatic_shadow_forbidden": True, "automatic_live_forbidden": True,
                "datasets": [], "decisions": [], "closed_trades": []}
    for request in requested:
        artifact["datasets"].append(normalize(request, fetcher(request)))
    for row in rows:
        daily = next(d for d in artifact["datasets"] if d["request"]["ticker"] == row["ticker"] and d["request"]["market"] == row["market"] and d["request"]["interval"] == "1d")
        bars = [b for b in daily["bars"] if stamp(b["bar_close_at"]) <= stamp(row["decided_at"])]
        artifact["decisions"].append({"decision_ref": row["decision_ref"], "ticker": row["ticker"],
                                     "market": row["market"], "decided_at": row["decided_at"],
                                     "completed_daily_bars": len(bars), "eligible_60_bars": len(bars) >= 60,
                                     "last_completed_close_at": bars[-1]["bar_close_at"] if bars else None,
                                     "source_sha256": daily["source_sha256"]})
        if not row["strategy_closed_at"]:
            continue
        coverage = {}
        for interval in ("1m", "5m"):
            matching = [d for d in artifact["datasets"] if d["request"].get("decision_ref") == row["decision_ref"] and d["request"]["interval"] == interval]
            bars = sorted([b for d in matching for b in d["bars"]], key=lambda b: b["bar_close_at"])
            coverage[interval] = {"bars": len(bars), "regular_bars": sum(b["session"] == "regular" for b in bars),
                "extended_bars": sum(b["session"] == "extended" for b in bars),
                "first_bar_at": bars[0]["provider_timestamp"] if bars else None,
                "last_close_at": bars[-1]["bar_close_at"] if bars else None,
                "status": "PARTIAL_COVERAGE_NOT_CONTINUITY_PROOF" if bars else "DATA_UNAVAILABLE",
                "request_status_counts": dict(Counter(d["status"] for d in matching))}
        artifact["closed_trades"].append({**row, "research_role": "DISCOVERY" if row["ticker"] == "SNDK" else "ALREADY_SEEN_NOT_HOLDOUT",
                                          "intraday": coverage, "original_stop_threshold": None})
    artifact["artifact_sha256"] = sha(artifact)
    return artifact


def verify(artifact):
    expected = artifact.get("artifact_sha256")
    if expected != sha({k: v for k, v in artifact.items() if k != "artifact_sha256"}):
        raise ValueError("artifact_hash_mismatch")
    return expected


def saved_fetcher(artifact):
    """Replay frozen provider responses through current normalization, no network."""
    verify(artifact)
    allowed = {"status", "raw_rows", "exchange", "retrieval_started_at", "retrieved_at", "yfinance_version"}
    cache = {sha(dataset["request"]): {key: value for key, value in dataset.items() if key in allowed}
             for dataset in artifact["datasets"]}
    def replay(request):
        key = sha(request)
        if key not in cache:
            raise ValueError("request_not_in_saved_source")
        return cache[key]
    return replay


def summary(artifact):
    return {"status": "collected", "kind": artifact["kind"], "artifact_sha256": verify(artifact),
            "dataset_count": len(artifact["datasets"]), "decision_count": len(artifact["decisions"]),
            "eligible_60_bars": sum(r["eligible_60_bars"] for r in artifact["decisions"]),
            "closed_trade_count": len(artifact["closed_trades"]),
            "request_status_counts": dict(Counter(d["status"] for d in artifact["datasets"]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", action="append", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--replay-source", type=Path, help="Normalize frozen saved responses, never fetch again")
    args = parser.parse_args()
    try:
        if args.verify:
            artifact = json.loads(args.verify.read_text())
        else:
            if not args.packet or args.output is None or args.output.exists():
                raise ValueError("new_output_and_packet_required")
            if args.replay_source:
                source = json.loads(args.replay_source.read_text())
                artifact = collect(args.packet, saved_fetcher(source))
                artifact.pop("artifact_sha256")
                artifact["reprocessed_from_artifact_sha256"] = source["artifact_sha256"]
                artifact["artifact_sha256"] = sha(artifact)
            else:
                artifact = collect(args.packet)
            with args.output.open("x") as handle:
                json.dump(artifact, handle, sort_keys=True, indent=2, allow_nan=False)
        print(json.dumps(summary(artifact), sort_keys=True))
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "failed", "category": "collection_or_verification_failed"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
