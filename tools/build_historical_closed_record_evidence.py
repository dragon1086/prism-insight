"""Output-only, account-scoped historical closed records; never prospective facts.

Reuses the reviewed historical query projection and timestamp parser, but never
calls its event emitter, state writer, candidate query, or production spool path.
Run only after operator review against explicitly supplied read-only source DB.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.backfill_observability import _ACTUAL_QUERIES, _ACTUAL_TABLES, _parse_time

CONTRACT = "historical-closed-record-evidence-v1"
CUTOFF = datetime.fromisoformat("2026-09-09T16:18:06.031436+00:00")
TRIGGERS = frozenset({"거래량 급증 상위주", "갭 상승 모멘텀 상위주", "일중 상승률 상위주",
    "마감 강도 상위주", "시총 대비 집중 자금 유입 상위주", "거래량 증가 상위 횡보주",
    "Volume Surge Top", "Gap Up Momentum Top", "Intraday Rise Top", "Closing Strength Top",
    "Value-to-Cap Ratio Top", "Macro Sector Leader"})
EXIT_KINDS = frozenset({"stop", "hard_stop", "trend_exit", "target", "ai", "manual", "trailing", "timeout"})
MODES = frozenset({"morning", "afternoon", "topdown", "bottomup"})
READ_COLUMNS = frozenset({"id", "account_key", "ticker", "buy_price", "buy_date", "sell_price",
                         "sell_date", "profit_rate", "holding_days", "trigger_type", "trigger_mode", "exit_kind"})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def reference(key, *parts):
    return hmac.new(key, canonical(parts), hashlib.sha256).hexdigest()


def utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if not positive or value > 0 else None


def authorizer(action, first, second, _database, _source):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and (first == "sqlite_master" or (
        first in _ACTUAL_TABLES.values() and second in READ_COLUMNS
    )):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first in {"table_info", "query_only"} and (
        first != "query_only" or second is None
    ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_TRANSACTION and first in {"BEGIN", "ROLLBACK"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def read_only_connection(path):
    connection = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise ValueError("query_only_unavailable")
    connection.set_authorizer(authorizer)
    connection.row_factory = sqlite3.Row
    return connection


def query_for(market, has_scope):
    # Narrow the existing approved projection: do not retrieve raw scenario,
    # company/account names or sector text. Scope key stays internal for HMAC only.
    scope = "account_key" if has_scope else "NULL AS account_key"
    return (_ACTUAL_QUERIES[market]
            .replace("id, ticker, company_name,", f"id, {scope}, ticker,")
            .replace("holding_days, scenario, trigger_type,", "holding_days, trigger_type,")
            .replace("trigger_mode, sector, exit_kind", "trigger_mode, exit_kind"))


def record_from_row(row, market, namespace, key, source_timezone):
    identifier = row["id"]
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        raise ValueError("invalid_original_primary_key")
    record_ref = reference(key, "original-row", namespace, market, _ACTUAL_TABLES[market], identifier)
    reasons = []
    entry, closed = (_parse_time(row[field], default_timezone=source_timezone) for field in ("buy_date", "sell_date"))
    if closed is None:
        return None, "CLOSE_TIME_CUTOFF_UNPROVABLE"
    if closed is not None and closed > CUTOFF:
        return None, "AFTER_FROZEN_CUTOFF"
    if entry is None or closed is None or closed < entry:
        reasons.append("INVALID_OR_MISSING_ORIGINAL_TIMES")
    entry_price, exit_price = (finite(row[field], positive=True) for field in ("buy_price", "sell_price"))
    original_return = finite(row["profit_rate"])
    if entry_price is None or exit_price is None or original_return is None:
        reasons.append("INVALID_OR_MISSING_ORIGINAL_PRICES_OR_RETURN")
    ticker = row["ticker"]
    pattern = r"[0-9]{6}" if market == "KR" else r"[A-Z][A-Z0-9.-]{0,9}"
    if not isinstance(ticker, str) or not re.fullmatch(pattern, ticker):
        ticker = None
        reasons.append("INVALID_TICKER")
    scope = row["account_key"]
    scope_known = isinstance(scope, str) and bool(scope.strip()) and scope.strip().upper() not in {
        "UNKNOWN", "MISSING", "[REDACTED]"
    }
    # Strip only for placeholder recognition; genuine opaque keys retain bytes.
    book = reference(key, "legacy-book", namespace, market, scope) if scope_known else None
    if book is None:
        reasons.append("LEGACY_BOOK_ID_UNAVAILABLE")
    trigger = row["trigger_type"] if row["trigger_type"] in TRIGGERS else None
    if trigger is None:
        reasons.append("TRIGGER_MISSING_OR_UNRECOGNIZED")
    value = {"source_record_ref": record_ref, "source_table": _ACTUAL_TABLES[market],
             "source_kind": "LEGACY_ACCOUNT_SCOPED_HISTORY_UNRECONCILED",
             "ingestion_mode": "backfill", "legacy_book_ref": book,
             "book_scope_status": "PSEUDONYMOUS_LEGACY_SCOPE" if book else "UNKNOWN_UNGROUPABLE",
             "canonical_strategy_book_verified": False, "market": market, "ticker": ticker,
             "recorded_entry_at": utc(entry) if entry else None, "recorded_exit_at": utc(closed) if closed else None,
             "recorded_entry_price": entry_price, "recorded_exit_price": exit_price,
             "recorded_return_pct": original_return,
             "recorded_holding_days": finite(row["holding_days"]), "trigger_type": trigger,
             "trigger_mode": row["trigger_mode"] if row["trigger_mode"] in MODES else None,
             "exit_kind": row["exit_kind"] if row["exit_kind"] in EXIT_KINDS else None,
             "original_decision_ref": None, "original_position_ref": None,
             "decision_at": None, "observed_at": None, "regime": None, "policy_version": None,
             "original_stop": None, "stop_history": None,
             "timezone_provenance": {"naive_source_timezone": str(source_timezone),
                 "basis": "OPERATOR_DECLARED_NOT_INFERRED_FROM_MARKET",
                 "entry_had_offset": bool(row["buy_date"] and re.search(r"(?:Z|[+-]\d\d:\d\d)$", str(row["buy_date"]))),
                 "exit_had_offset": bool(row["sell_date"] and re.search(r"(?:Z|[+-]\d\d:\d\d)$", str(row["sell_date"])))},
             "quality_reasons": reasons,
             "missing_lineage": ["ORIGINAL_DECISION_LINK", "ORIGINAL_POSITION_LINK", "DECISION_TIME",
                                 "POLICY_VERSION", "REGIME", "ORIGINAL_STOP_HISTORY"]}
    value["source_projection_sha256"] = digest(value)
    return value, None


def build(db, markets, namespace, key, source_timezone, *, now=None):
    if len(key) != 32 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", namespace):
        raise ValueError("invalid_pseudonym_key_or_namespace")
    if not markets or set(markets) - set(_ACTUAL_TABLES):
        raise ValueError("invalid_market")
    began = now or datetime.now(timezone.utc)
    rows, excluded, unavailable = [], Counter(), []
    connection = read_only_connection(db)
    deadline = time.monotonic() + 30
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        connection.execute("BEGIN")
        for market in sorted(set(markets)):
            table = _ACTUAL_TABLES[market]
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not columns:
                unavailable.append({"market": market, "reason": "SOURCE_TABLE_MISSING"})
                continue
            for raw in connection.execute(query_for(market, "account_key" in columns)):
                if len(rows) >= 100_000:
                    raise ValueError("export_row_limit")
                record, reason = record_from_row(raw, market, namespace, key, source_timezone)
                if record:
                    rows.append(record)
                if reason:
                    excluded[reason] += 1
        connection.execute("ROLLBACK")
    finally:
        connection.close()
    rows.sort(key=lambda row: row["source_record_ref"])
    if len({r["source_record_ref"] for r in rows}) != len(rows):
        raise ValueError("duplicate_original_primary_key")
    books = Counter((row["market"], row["legacy_book_ref"]) for row in rows if row["legacy_book_ref"])
    content = {"analysis_contract_version": CONTRACT, "packet_schema_version": 1,
        "source_kind": "LEGACY_ACCOUNT_SCOPED_HISTORY_UNRECONCILED", "source_namespace": namespace,
        "source_fingerprint_basis": "CONSISTENT_READ_TRANSACTION_ALLOWLISTED_COLUMN_PROJECTION",
        "pseudonym_key_fingerprint": hashlib.sha256(key).hexdigest(),
        "data_cutoff": utc(CUTOFF), "ingestion_mode": "backfill", "prospective": False,
        "independent_holdout": False, "canonical_strategy_book_verified": False,
        "automatic_shadow_forbidden": True, "automatic_live_forbidden": True,
        "candidate_universe_exported": False, "broker_fill_filter_applied": False,
        "records": rows, "excluded_counts": dict(excluded), "unavailable_sources": unavailable,
        "legacy_books": [{"market": market, "legacy_book_ref": book, "record_count": count}
                         for (market, book), count in sorted(books.items())],
        "ungroupable_record_count": sum(r["legacy_book_ref"] is None for r in rows),
        "quality_counts": dict(Counter(reason for row in rows for reason in row["quality_reasons"])),
        "limitations": ["Not canonical strategy sample counts or cross-book performance",
            "No reconstructed candidates, gates, policy, regime or observed_at",
            "Closed-record source completeness and mutation history not independently proven",
            "Recorded returns retained without recalculation or broker fill filtering",
            "Source timestamps and naive timezone declaration require operator review"]}
    content["logical_source_sha256"] = digest(content)
    content["retrieval_started_at"] = utc(began)
    content["retrieved_at"] = utc(now or datetime.now(timezone.utc))
    content["artifact_sha256"] = digest(content)
    return content


def validate_output(db, output_root, output):
    # Path.absolute() preserves '..'; lexical is_relative_to is insufficient.
    # Reject traversal before checking original components for symlinks.
    if ".." in output_root.parts or ".." in output.parts:
        raise ValueError("output_parent_traversal_forbidden")
    db, root, target = db.resolve(strict=True), output_root.absolute(), output.absolute()
    if not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("unsafe_output_root")
    if root.is_relative_to(db.parent) or not target.is_relative_to(root) or target.suffix != ".json":
        raise ValueError("unsafe_output_location")
    if target.exists() or target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise ValueError("output_must_be_new_regular_file")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--market", action="append", required=True, choices=["KR", "US"])
    parser.add_argument("--source-namespace", required=True)
    parser.add_argument("--naive-source-timezone", required=True, choices=["Asia/Seoul", "UTC", "America/New_York"])
    parser.add_argument("--pseudonym-key-file", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = validate_output(args.db, args.output_root, args.output)
        if args.pseudonym_key_file.stat().st_mode & 0o077:
            raise ValueError("pseudonym_key_permissions")
        key = args.pseudonym_key_file.read_bytes()
        artifact = build(args.db, args.market, args.source_namespace, key, ZoneInfo(args.naive_source_timezone))
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(artifact, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        print(json.dumps({"status": "exported", "contract": CONTRACT, "record_count": len(artifact["records"]),
                          "legacy_book_count": len(artifact["legacy_books"]),
                          "logical_source_sha256": artifact["logical_source_sha256"],
                          "artifact_sha256": artifact["artifact_sha256"]}))
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
        print(json.dumps({"status": "failed", "category": "historical_source_export_failed"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
