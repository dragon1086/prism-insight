#!/usr/bin/env python3
"""Read-only KR PENDING-ledger activation preflight for issue #412.

This command never places/amends/cancels orders and opens SQLite in read-only
mode. It reports readiness only; it never changes a persisted state or feature
gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prism_core.positions import PositionStore, account_fingerprint  # noqa: E402
from tools.feature_status import _cron_get_all_inline_env  # noqa: E402


GATE = "POSITION_PENDING_KR_ENABLED"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSEY = frozenset({"0", "false", "no", "off", ""})
_BLOCKING_POSITION_STATUSES = (
    "PENDING_ENTRY",
    "PENDING_EXIT",
    "EXIT_UNKNOWN",
    "ENTRY_FAILED",
)


def _normalized_gate_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().strip('"').strip("'").lower()


def evaluate_gate_sources(
    *,
    process_value: Any,
    dotenv_value: Any,
    crontab_text: str,
) -> dict[str, Any]:
    """Conservatively require every effective source to be unset/false."""

    values = [
        ("process_env", _normalized_gate_value(process_value)),
        ("dotenv", _normalized_gate_value(dotenv_value)),
    ]
    cron_values = [
        _normalized_gate_value(value) or ""
        for value in _cron_get_all_inline_env(crontab_text, GATE)
    ]
    values.extend(("crontab_inline", value) for value in cron_values)

    enabled_sources = [
        {"source": source, "value": value}
        for source, value in values
        if value in _TRUTHY
    ]
    invalid_values = [
        {"source": source, "value": value}
        for source, value in values
        if value is not None and value not in _TRUTHY | _FALSEY
    ]
    blockers = []
    if enabled_sources:
        blockers.append("position_pending_kr_gate_enabled")
    if invalid_values:
        blockers.append("position_pending_kr_gate_invalid")
    return {
        "status": "blocked" if blockers else "ready",
        "gate": GATE,
        "process_value": _normalized_gate_value(process_value),
        "dotenv_value": _normalized_gate_value(dotenv_value),
        "cron_values": cron_values,
        "enabled_sources": enabled_sources,
        "invalid_values": invalid_values,
        "blockers": blockers,
    }


def _read_dotenv_gate(path: Path) -> tuple[bool, str | None, str | None]:
    if not path.exists():
        return True, None, None
    try:
        value = None
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, candidate = line.partition("=")
                normalized_key = key.strip()
                if normalized_key.startswith("export "):
                    normalized_key = normalized_key.removeprefix("export ").strip()
                if normalized_key == GATE:
                    value = candidate.strip()
        return True, value, None
    except OSError as error:
        return False, None, type(error).__name__


def _read_crontab() -> tuple[bool, str, str | None]:
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as error:
        return False, "", type(error).__name__
    if result.returncode == 0:
        return True, result.stdout, None
    if "no crontab for" in result.stderr.lower():
        return True, "", None
    return False, "", "CrontabReadError"


def _read_only_connection(db_path: str | Path) -> sqlite3.Connection:
    uri = Path(db_path).expanduser().resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _position_status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts = {status: 0 for status in _BLOCKING_POSITION_STATUSES}
    placeholders = ",".join("?" for _ in _BLOCKING_POSITION_STATUSES)
    rows = connection.execute(
        f"SELECT status, COUNT(*) FROM positions "
        f"WHERE market='KR' AND status IN ({placeholders}) GROUP BY status",
        _BLOCKING_POSITION_STATUSES,
    ).fetchall()
    counts.update({str(status): int(count) for status, count in rows})
    return counts


def _pyramided_holdings(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT account_key, ticker, COUNT(*) AS row_count
           FROM stock_holdings
           GROUP BY account_key, ticker
           HAVING COUNT(*) > 1
           ORDER BY account_key, ticker"""
    ).fetchall()
    return [
        {
            "account_ref": account_fingerprint(account_id),
            "symbol": str(symbol).upper(),
            "row_count": int(row_count),
        }
        for account_id, symbol, row_count in rows
    ]


def _accepted_kr_sells(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT bo.id, bo.broker_order_id, oi.id, oi.account_id, oi.symbol
           FROM broker_orders AS bo
           JOIN order_intents AS oi ON oi.id = bo.intent_id
           WHERE oi.market='KR' AND oi.side='SELL' AND bo.accepted=1
           ORDER BY bo.submitted_at, bo.id"""
    ).fetchall()
    return [
        {
            "broker_record_id": str(broker_record_id),
            "broker_order_id": (
                str(broker_order_id).strip() if broker_order_id is not None else ""
            ),
            "intent_id": str(intent_id),
            "account_id": str(account_id),
            "account_ref": account_fingerprint(account_id),
            "symbol": str(symbol).upper(),
        }
        for broker_record_id, broker_order_id, intent_id, account_id, symbol in rows
    ]


def _normalized_open_sells(
    inquiries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for account_id, result in inquiries.items():
        for order in result.get("orders", []):
            if str(order.get("side", "")).upper() != "SELL":
                continue
            orders.append(
                {
                    "account_id": str(account_id),
                    "account_ref": account_fingerprint(account_id),
                    "broker_order_id": str(order.get("order_no", "")).strip(),
                    "symbol": str(order.get("ticker", "")).upper(),
                    "unfilled_qty": int(order.get("unfilled_qty", 0)),
                }
            )
    return orders


def _kis_inquiry_audit(
    inquiries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "account_ref": account_fingerprint(account_id),
            "authoritative": bool(result.get("authoritative")),
            "open_order_count": len(result.get("orders", [])),
            "error_type": result.get("error_type"),
        }
        for account_id, result in sorted(inquiries.items())
    ]


def _safe_broker_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "account_id"}


def _broker_order_audit(
    accepted: list[dict[str, Any]],
    open_sells: list[dict[str, Any]],
) -> dict[str, Any]:
    accepted_with_id = [item for item in accepted if item["broker_order_id"]]
    missing_id = [item for item in accepted if not item["broker_order_id"]]
    accepted_keys = {
        (item["account_id"], item["broker_order_id"], item["symbol"])
        for item in accepted_with_id
    }
    open_keys = {
        (item["account_id"], item["broker_order_id"], item["symbol"])
        for item in open_sells
    }
    accepted_not_open = [
        item
        for item in accepted_with_id
        if (item["account_id"], item["broker_order_id"], item["symbol"])
        not in open_keys
    ]
    open_not_accepted = [
        item
        for item in open_sells
        if (item["account_id"], item["broker_order_id"], item["symbol"])
        not in accepted_keys
    ]
    return {
        "accepted_sell_count": len(accepted),
        "current_open_sell_count": len(open_sells),
        "matched": sum(
            (item["account_id"], item["broker_order_id"], item["symbol"]) in open_keys
            for item in accepted_with_id
        ),
        "accepted_without_broker_order_id": [
            _safe_broker_item(item) for item in missing_id
        ],
        "accepted_not_currently_open": [
            _safe_broker_item(item) for item in accepted_not_open
        ],
        "open_sell_without_accepted_ledger": [
            _safe_broker_item(item) for item in open_not_accepted
        ],
        "current_open_sells": [_safe_broker_item(item) for item in open_sells],
    }


def audit_database(
    db_path: str | Path,
    kis_inquiries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Audit persisted KR state without making any database changes."""

    unknown_kis = (
        any(not bool(result.get("authoritative")) for result in kis_inquiries.values())
        or not kis_inquiries
    )
    with _read_only_connection(db_path) as connection:
        position_counts = _position_status_counts(connection)
        comparator = PositionStore(connection).compare_legacy_positions("KR")
        pyramids = _pyramided_holdings(connection)
        accepted = _accepted_kr_sells(connection)
        broker_audit = None
        if not unknown_kis:
            broker_audit = _broker_order_audit(
                accepted,
                _normalized_open_sells(kis_inquiries),
            )

    blockers: list[str] = []
    if any(position_counts.values()):
        blockers.append("blocking_position_states")
    if not comparator["matches"]:
        blockers.append("legacy_position_comparator_mismatch")
    if comparator["counts"]["failed_exit_linked_open_positions"]:
        blockers.append("failed_exit_linked_open_positions")
    if pyramids:
        blockers.append("pyramided_kr_holdings")
    if broker_audit is not None:
        if broker_audit["accepted_without_broker_order_id"]:
            blockers.append("accepted_sell_missing_broker_order_id")
        if broker_audit["accepted_not_currently_open"]:
            blockers.append("accepted_sell_not_currently_open")
        if broker_audit["open_sell_without_accepted_ledger"]:
            blockers.append("open_sell_without_accepted_ledger")
        if broker_audit["current_open_sell_count"]:
            blockers.append("current_open_sell_orders")

    unknowns = ["kis_open_orders"] if unknown_kis else []
    status = "unknown" if unknowns else "blocked" if blockers else "ready"
    return {
        "status": status,
        "database": str(Path(db_path).expanduser().resolve()),
        "kis_inquiries": _kis_inquiry_audit(kis_inquiries),
        "position_status_counts": position_counts,
        "comparator": comparator,
        "pyramided_holdings": pyramids,
        "broker_order_audit": broker_audit,
        "blockers": blockers,
        "unknowns": unknowns,
    }


async def inquire_kis_open_sells() -> dict[str, dict[str, Any]]:
    """Read authoritative open SELLs for every configured active KR account."""

    from prism_core.execution_service import ExecutionService
    from trading import domestic_stock_trading as domestic

    default_mode = str(domestic.ka.getEnv().get("default_mode", "demo")).lower()
    server = "vps" if default_mode == "demo" else "prod"
    accounts = domestic.ka.get_configured_accounts(svr=server, market="kr")
    results: dict[str, dict[str, Any]] = {}
    for account in accounts:
        account_id = str(account["account_key"])
        try:
            async with ExecutionService.domestic(
                account_name=account["name"]
            ) as trader:
                authoritative, rows = await asyncio.to_thread(
                    trader.get_revisable_orders_checked
                )
            orders = [
                {
                    "order_no": str(row.get("order_no", "")).strip(),
                    "ticker": str(row.get("stock_code", "")).upper(),
                    "side": (
                        "SELL"
                        if str(row.get("sll_buy_dvsn_cd", "")).strip() == "01"
                        else "BUY"
                    ),
                    "unfilled_qty": int(row.get("psbl_qty", 0)),
                }
                for row in rows
                if int(row.get("psbl_qty", 0)) > 0
            ]
            results[account_id] = {
                "authoritative": authoritative,
                "orders": orders,
            }
        except Exception as error:
            results[account_id] = {
                "authoritative": False,
                "orders": [],
                "error_type": type(error).__name__,
            }
    return results


def combine_reports(*reports: dict[str, Any]) -> dict[str, Any]:
    statuses = {report.get("status") for report in reports}
    status = (
        "unknown"
        if "unknown" in statuses
        else "blocked"
        if "blocked" in statuses
        else "ready"
    )
    return {
        "status": status,
        "gate": reports[0] if reports else None,
        "database": reports[1] if len(reports) > 1 else None,
    }


def exit_code(report: dict[str, Any]) -> int:
    return {"ready": 0, "blocked": 1}.get(str(report.get("status")), 2)


def _unknown_report(component: str, error_type: str) -> dict[str, Any]:
    return {
        "status": "unknown",
        "component": component,
        "error_type": error_type,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=(
            os.getenv("STOCK_TRACKING_DB")
            or str(PROJECT_ROOT / "stock_tracking_db.sqlite")
        ),
    )
    parser.add_argument(
        "--env-file",
        default=str(PROJECT_ROOT / ".env"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env_ok, dotenv_value, env_error = _read_dotenv_gate(Path(args.env_file))
    cron_ok, crontab_text, cron_error = _read_crontab()
    if env_ok and cron_ok:
        gate_report = evaluate_gate_sources(
            process_value=os.environ.get(GATE),
            dotenv_value=dotenv_value,
            crontab_text=crontab_text,
        )
    else:
        gate_report = _unknown_report(
            "gate_sources",
            env_error or cron_error or "GateSourceReadError",
        )

    try:
        kis_inquiries = asyncio.run(inquire_kis_open_sells())
        database_report = audit_database(args.db_path, kis_inquiries)
    except Exception as error:
        database_report = _unknown_report("database_or_kis_audit", type(error).__name__)

    report = combine_reports(gate_report, database_report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
