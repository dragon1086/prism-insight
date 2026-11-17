#!/usr/bin/env python3
"""Broker-vs-ledger holdings reconcile: catch corporate actions before the sell loops go blind.

2026-11-16 Black Hills (BKH) became Bright Horizon Energy (HZN). KIS renamed the position the
same day; our tables kept BKH, the KIS quote for BKH froze at the 11/13 close (+0.00%) and the
hard-stop loop watched that frozen price for two sessions while HZN fell to the stop. This tool
compares every configured account's broker portfolio with the ledger's holdings and classifies:

  RENAME         one ledger ticker gone from the broker, one broker ticker unknown to the ledger, the
                 share count equals the previous run's count of the old ticker and the broker average
                 price is within RENAME_PRICE_TOL of our buy price -> 1:1 ticker change. Applied
                 automatically (tables + runtime state files), then reported.
  RENAME_RATIO   same pair but the share count changed, or no previous count is known, or prices differ
                 (merger with an exchange ratio such as 0.98-for-1, rename plus split): reported with the
                 ratio and the exact apply command; never applied automatically. A 0.98 ratio moves the
                 price only 2%, so the share count, not the price, decides between RENAME and RENAME_RATIO.
  SPLIT          same ticker, broker average price far from our buy price (split / reverse split):
                 reported with the ratio and the apply command.
  GONE           ledger ticker not at the broker and no unknown ticker to pair with (cash merger,
                 delisting, a sale outside the system): reported every run until resolved.
  OUTSIDE        broker ticker the ledger does not know (manual buy, spin-off shares): reported once.
  AMBIGUOUS      several missing/unknown tickers at once: reported, nothing applied.
  STALE_QUOTE    broker quote for a ledger holding shows 0.00% change and zero volume during the
                 session (dead symbol): reported.
  BROKER_UNAVAILABLE  the balance inquiry was not authoritative: nothing classified (MISSING).

Usage:
    python tools/holdings_reconcile.py --market US              # classify, apply 1:1 renames, alert
    python tools/holdings_reconcile.py --market KR --dry-run    # classify and print only
    python tools/holdings_reconcile.py --market US --apply-rename BKH HZN [--ratio 0.98] [--name "..."]

Only inquiry TRs are called; the tool never orders. Alerts go to the maintenance chat
(prism_core.ops_alert). Rollback: HOLDINGS_RECONCILE_ENABLED=false or remove the cron lines.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("holdings_reconcile")

RENAME_PRICE_TOL = 0.05     # |broker avg / our buy - 1| within this = the same shares, same cost
SPLIT_PRICE_TOL = 0.25      # beyond this on the same ticker = a split or a reverse split
STATE_PATH = ROOT / "runtime" / "holdings_reconcile_state.json"
MARKET_TZ = {"KR": "Asia/Seoul", "US": "America/New_York"}

# Tables that carry live positions by ticker; history tables (trading_history, order_intents,
# *_adjustment_log, watchlist_history) keep the old code on purpose: the trace stays readable.
# Literal statements only (no table or column names formatted into SQL): (table, count, update); the
# market-scoped ones take [old, market] / [new, old, market].
_SHARED = [
    ("loop_a_position_state", "select count(*) from loop_a_position_state where ticker = ? and market = ?",
     "update loop_a_position_state set ticker = ? where ticker = ? and market = ?"),
    ("loop_b_position_state", "select count(*) from loop_b_position_state where ticker = ? and market = ?",
     "update loop_b_position_state set ticker = ? where ticker = ? and market = ?"),
    ("positions", "select count(*) from positions where symbol = ? and market = ? and status = 'OPEN'",
     "update positions set symbol = ? where symbol = ? and market = ? and status = 'OPEN'"),
]
TABLES = {
    "KR": [("stock_holdings", "select count(*) from stock_holdings where ticker = ?",
            "update stock_holdings set ticker = ? where ticker = ?"),
           ("holding_decisions", "select count(*) from holding_decisions where ticker = ?",
            "update holding_decisions set ticker = ? where ticker = ?")] + _SHARED,
    "US": [("us_stock_holdings", "select count(*) from us_stock_holdings where ticker = ?",
            "update us_stock_holdings set ticker = ? where ticker = ?"),
           ("us_holding_decisions", "select count(*) from us_holding_decisions where ticker = ?",
            "update us_holding_decisions set ticker = ? where ticker = ?"),
           ("us_pending_orders", "select count(*) from us_pending_orders where ticker = ?",
            "update us_pending_orders set ticker = ? where ticker = ?")] + _SHARED,
}
HOLDINGS_TABLE = {"KR": "stock_holdings", "US": "us_stock_holdings"}
HOLDINGS_SQL = {
    "KR": {"name": "update stock_holdings set company_name = ? where ticker = ?",
           "scale": "update stock_holdings set buy_price = buy_price * ?, current_price = current_price * ?, "
                    "target_price = target_price * ?, stop_loss = stop_loss * ? where ticker = ?",
           "scenario": "select id, scenario from stock_holdings where ticker = ?",
           "save": "update stock_holdings set scenario = ? where id = ?"},
    "US": {"name": "update us_stock_holdings set company_name = ? where ticker = ?",
           "scale": "update us_stock_holdings set buy_price = buy_price * ?, current_price = current_price * ?, "
                    "target_price = target_price * ?, stop_loss = stop_loss * ? where ticker = ?",
           "scenario": "select id, scenario from us_stock_holdings where ticker = ?",
           "save": "update us_stock_holdings set scenario = ? where id = ?"},
}
RUNTIME_FILES = {
    "KR": ["core_hold_kr.json", "oneil_watchlist_state_kr_v1.json", "reentry_v3_state_kr.json"],
    "US": ["core_hold_us.json", "oneil_watchlist_state_v1.json", "us_eligibility_metadata.json",
           "reentry_v3_state_us.json"],
}
PRICE_COLUMNS = ("buy_price", "current_price", "target_price", "stop_loss")


# ------------------------------------------------------------------ classification (pure)
def classify(ledger, broker, *, quotes=None, session_open=False, previous=None):
    """ledger: {ticker: {"buy_price": float, "company_name": str}}; broker: {ticker: {"avg_price", "quantity",
    "stock_name"}} (quantity > 0 only); quotes: {ticker: {"change_rate", "volume"}} for ledger tickers;
    previous: {ticker: quantity} from the last run's broker snapshot (None on the first run).
    Returns a list of findings, each {"kind", "ticker", ...}."""
    previous = previous or {}
    missing = sorted(t for t in ledger if t not in broker)
    unknown = sorted(t for t in broker if t not in ledger)
    out = []
    if len(missing) == 1 and len(unknown) == 1:
        old, new = missing[0], unknown[0]
        ratio = _ratio(broker[new]["avg_price"], ledger[old]["buy_price"])
        prev_qty, new_qty = _num(previous.get(old)), _num(broker[new].get("quantity"))
        same_shares = prev_qty is not None and new_qty is not None and prev_qty == new_qty
        kind = "RENAME" if same_shares and ratio is not None and abs(ratio - 1) <= RENAME_PRICE_TOL else "RENAME_RATIO"
        out.append({"kind": kind, "ticker": old, "new_ticker": new, "ratio": ratio, "previous_quantity": prev_qty,
                    "name": broker[new].get("stock_name") or "", "quantity": broker[new].get("quantity"),
                    "buy_price": ledger[old]["buy_price"], "broker_avg_price": broker[new]["avg_price"]})
    elif missing or unknown:
        if len(missing) > 1 or len(unknown) > 1:
            out.append({"kind": "AMBIGUOUS", "ticker": ",".join(missing), "unknown": ",".join(unknown)})
        else:
            for t in missing:
                out.append({"kind": "GONE", "ticker": t, "buy_price": ledger[t]["buy_price"]})
            for t in unknown:
                out.append({"kind": "OUTSIDE", "ticker": t, "name": broker[t].get("stock_name") or "",
                            "quantity": broker[t].get("quantity"), "broker_avg_price": broker[t]["avg_price"]})
    for t in sorted(set(ledger) & set(broker)):
        ratio = _ratio(broker[t]["avg_price"], ledger[t]["buy_price"])
        prev_qty, new_qty = _num(previous.get(t)), _num(broker[t].get("quantity"))
        qty_changed = prev_qty is not None and new_qty is not None and prev_qty != new_qty
        if (ratio is not None and abs(ratio - 1) > SPLIT_PRICE_TOL) or qty_changed:
            out.append({"kind": "SPLIT", "ticker": t, "ratio": ratio, "buy_price": ledger[t]["buy_price"],
                        "broker_avg_price": broker[t]["avg_price"], "quantity": new_qty, "previous_quantity": prev_qty})
    if session_open and quotes:
        for t in sorted(set(ledger) & set(quotes)):
            q = quotes[t] or {}
            if _num(q.get("change_rate")) == 0 and _num(q.get("volume")) == 0:
                out.append({"kind": "STALE_QUOTE", "ticker": t, "price": q.get("current_price")})
    return out


def _ratio(broker_avg, buy_price):
    broker_avg, buy_price = _num(broker_avg), _num(buy_price)
    if not broker_avg or not buy_price:
        return None
    return round(broker_avg / buy_price, 4)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ apply
def apply_rename(market, old, new, *, db_path, runtime_dir, ratio=None, name=None, dry_run=False):
    """Rename a live position across the live tables and runtime state files. With ``ratio`` (broker
    average price / our buy price, e.g. 0.98 for a 0.98-for-1 merger or 2.0 for a 1:2 reverse split) the
    holding's price columns and scenario highest_price are scaled so returns, stop and target keep their
    meaning per new share. Returns {table_or_file: rows_or_tokens_changed}."""
    changed = {}
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        existing = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        for table, count_sql, update_sql in TABLES[market]:
            if table not in existing:
                continue
            params = [old, market] if "market = ?" in count_sql else [old]
            n = conn.execute(count_sql, params).fetchone()[0]
            if n and not dry_run:
                conn.execute(update_sql, [new, *params])
            changed[table] = n
        holdings, sql = HOLDINGS_TABLE[market], HOLDINGS_SQL[market]
        if changed.get(holdings) and not dry_run:
            if name:
                conn.execute(sql["name"], [name, new])
            if ratio and ratio > 0:
                conn.execute(sql["scale"], [ratio] * len(PRICE_COLUMNS) + [new])
                for row_id, scenario in conn.execute(sql["scenario"], [new]).fetchall():
                    try:
                        data = json.loads(scenario or "{}")
                    except ValueError:
                        continue
                    if _num(data.get("highest_price")):
                        data["highest_price"] = round(float(data["highest_price"]) * ratio, 6)
                    data["corporate_action"] = {"from": old, "to": new, "ratio": ratio,
                                                "at": datetime.now(ZoneInfo("UTC")).isoformat()}
                    conn.execute(sql["save"], [json.dumps(data, ensure_ascii=False), row_id])
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    token_old, token_new = json.dumps(old), json.dumps(new)
    for file_name in RUNTIME_FILES[market]:
        path = Path(runtime_dir) / file_name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        n = text.count(token_old)
        if n and not dry_run:
            replaced = text.replace(token_old, token_new)
            json.loads(replaced)          # never write a broken state file
            path.write_text(replaced, encoding="utf-8")
        changed[file_name] = n
    return changed


# ------------------------------------------------------------------ broker + ledger access
def _bootstrap_path(market):
    root, us, us_trading = str(ROOT), str(ROOT / "prism-us"), str(ROOT / "prism-us" / "trading")
    for path in ((root, us_trading, us) if market == "US" else (us, root)):
        sys.path.insert(0, path)


def _accounts(market):
    if market == "KR":
        from trading import kis_auth as ka
    else:
        sys.path.insert(0, str(ROOT / "trading"))
        import kis_auth as ka
    return list(ka.get_configured_accounts())


def _service(market, account_name):
    from prism_core.execution_service import ExecutionService
    return (ExecutionService.domestic if market == "KR" else ExecutionService.us)(account_name=account_name)


async def broker_view(market, account, *, with_quotes, service_factory=_service):
    """(authoritative, {ticker: row}, {ticker: quote}); never raises. A failed inquiry is MISSING."""
    try:
        async with service_factory(market, account["name"]) as service:
            if getattr(service, "account_key", None) != account["account_key"]:
                return False, {}, {}
            if market == "KR":
                ok, rows = await asyncio.to_thread(service._get_portfolio_checked)
            else:
                rows = await asyncio.to_thread(service.get_portfolio, True)
                ok = True
            key = "stock_code" if market == "KR" else "ticker"
            portfolio = {str(r[key]).upper(): r for r in rows if _num(r.get("quantity")) and float(r["quantity"]) > 0}
            quotes = {}
            if with_quotes:
                for ticker in with_quotes:
                    try:
                        quotes[ticker] = await asyncio.to_thread(service.get_current_price, ticker)
                    except Exception as error:  # noqa: BLE001 - a quote failure is MISSING
                        logger.warning("[HOLDINGS_RECONCILE][%s] quote %s failed: %s", market, ticker, type(error).__name__)
            return bool(ok), portfolio, quotes
    except Exception as error:  # noqa: BLE001 - an inquiry failure is MISSING, not a finding
        logger.warning("[HOLDINGS_RECONCILE][%s] balance inquiry unavailable: %s", market, type(error).__name__)
        return False, {}, {}


def ledger_view(market, account_key, db_path):
    """Holdings the broker should hold. A strategy entry the real account never bought (micro-split budget
    below one share: "simulator holding preserved") has an open position row without an entry intent; it
    is left out, otherwise it would read as GONE every day."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        query = ("select id, ticker, buy_price, company_name from stock_holdings where account_key = ?" if market == "KR"
                 else "select id, ticker, buy_price, company_name from us_stock_holdings where account_key = ?")
        rows = conn.execute(query, [account_key]).fetchall()
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        simulator = set()
        if "positions" in tables:
            simulator = {hid for hid, in conn.execute(
                "select legacy_holding_id from positions where market = ? and status = 'OPEN' and entry_intent_id is null",
                [market])}
    finally:
        conn.close()
    out = {}
    for hid, t, p, n in rows:
        if hid in simulator:
            logger.info("[HOLDINGS_RECONCILE][%s] %s is a simulator-only holding (no broker order): not compared", market, t)
            continue
        out[str(t).upper()] = {"buy_price": p, "company_name": n}
    return out


def session_open(market, now=None):
    now = now or datetime.now(ZoneInfo(MARKET_TZ[market]))
    hm = now.hour * 60 + now.minute
    window = (9 * 60, 15 * 60 + 30) if market == "KR" else (9 * 60 + 30, 16 * 60)
    return now.weekday() < 5 and window[0] <= hm < window[1]


# ------------------------------------------------------------------ run
KIND_KO = {"RENAME": "종목코드 변경(1:1) 자동 반영", "RENAME_RATIO": "종목코드 변경 + 교환 비율(수동 확인)",
           "SPLIT": "분할·병합 의심(수동 확인)", "GONE": "증권사에 없는 보유(현금 합병·상장폐지·외부 매도 의심)",
           "OUTSIDE": "시스템 밖 보유(수동 매수·분할 상장 배정)", "AMBIGUOUS": "여러 종목 불일치(수동 확인)",
           "STALE_QUOTE": "시세 정지(변동 0%·거래량 0, 코드 변경·거래 정지 의심)",
           "BROKER_UNAVAILABLE": "증권사 잔고 조회 실패(판정 보류)"}


def render(market, account_name, finding):
    kind, t = finding["kind"], finding.get("ticker", "")
    line = f"[{market}·{account_name}] {KIND_KO[kind]}: {t}"
    qty = f"{_fmt(finding.get('previous_quantity'))}→{_fmt(finding.get('quantity'))}주"
    if kind in ("RENAME", "RENAME_RATIO"):
        line += f" → {finding['new_ticker']} {finding.get('name') or ''} {qty}, 매입가 {finding['buy_price']} vs 증권사 평균 {finding['broker_avg_price']} (비율 {finding['ratio']})"
    elif kind == "SPLIT":
        line += f" 매입가 {finding['buy_price']} vs 증권사 평균 {finding['broker_avg_price']} (비율 {finding['ratio']}, {qty})"
    elif kind == "OUTSIDE":
        line += f" {finding.get('name') or ''} {finding.get('quantity') or ''}주 평균 {finding['broker_avg_price']}"
    elif kind == "AMBIGUOUS":
        line += f" / 증권사에만: {finding.get('unknown')}"
    elif kind == "STALE_QUOTE":
        line += f" 현재가 {finding.get('price')}"
    if kind == "RENAME_RATIO":
        line += (f"\n  적용: python tools/holdings_reconcile.py --market {market} --apply-rename {t} {finding['new_ticker']}"
                 f" --ratio {finding['ratio']} --name \"{finding.get('name') or ''}\"")
    elif kind == "SPLIT":
        line += f"\n  적용: python tools/holdings_reconcile.py --market {market} --apply-rename {t} {t} --ratio {finding['ratio']}"
    if finding.get("applied") is not None:
        line += f"\n  반영: {json.dumps(finding['applied'], ensure_ascii=False)}"
    return line


def _fmt(value):
    return "?" if value is None else (str(int(value)) if float(value).is_integer() else str(value))


def _dedupe_key(market, account_key, finding):
    return f"{market}|{account_key}|{finding['kind']}|{finding.get('ticker')}|{finding.get('new_ticker', '')}"


async def run(market, *, db_path, runtime_dir, state_path=STATE_PATH, dry_run=False, accounts=None,
              service_factory=_service, now=None, sender=None):
    accounts = _accounts(market) if accounts is None else accounts
    state = _load_state(state_path)
    today = (now or datetime.now(ZoneInfo(MARKET_TZ[market]))).strftime("%Y-%m-%d")
    report, alerts = [], []
    for account in accounts:
        ledger = ledger_view(market, account["account_key"], db_path)
        open_now = session_open(market, now)
        ok, broker, quotes = await broker_view(market, account, with_quotes=sorted(ledger) if open_now else None,
                                               service_factory=service_factory)
        snapshot_key = f"snapshot|{market}|{account['account_key']}"
        previous = (state.get(snapshot_key) or {}).get("quantities")
        findings = classify(ledger, broker, quotes=quotes, session_open=open_now, previous=previous) if ok else \
            ([{"kind": "BROKER_UNAVAILABLE", "ticker": ",".join(sorted(ledger))}] if ledger else [])
        if ok:
            state[snapshot_key] = {"date": today, "quantities": {t: _num(r.get("quantity")) for t, r in broker.items()}}
        for finding in findings:
            if finding["kind"] == "RENAME" and not dry_run:
                finding["applied"] = apply_rename(market, finding["ticker"], finding["new_ticker"], db_path=db_path,
                                                  runtime_dir=runtime_dir, name=finding.get("name") or None)
            line = render(market, account["name"], finding)
            report.append(line)
            key = _dedupe_key(market, account["account_key"], finding)
            once = finding["kind"] in ("OUTSIDE",)
            if state.get(key) if once else state.get(key) == today:
                continue
            alerts.append(line)
            state[key] = today
    for line in report:
        logger.info("%s", line)
    if alerts and not dry_run:
        text = "[주의] 보유 종목 대조 (증권사 vs 장부)\n" + "\n".join(alerts)
        (sender or _send)(text)
    if not dry_run:
        _save_state(state_path, state)
    return report, alerts


def _load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _send(text):
    from prism_core.ops_alert import send_ops_alert_sync
    return send_ops_alert_sync(text)


def enabled():
    raw = (os.getenv("HOLDINGS_RECONCILE_ENABLED") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite"))
    parser.add_argument("--runtime-dir", default=str(ROOT / "runtime"))
    parser.add_argument("--apply-rename", nargs=2, metavar=("OLD", "NEW"))
    parser.add_argument("--ratio", type=float, default=None, help="broker average price / our buy price")
    parser.add_argument("--name", default=None, help="new company name for the holdings row")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    if args.apply_rename:
        old, new = (s.upper() for s in args.apply_rename)
        changed = apply_rename(args.market, old, new, db_path=args.db, runtime_dir=args.runtime_dir,
                               ratio=args.ratio, name=args.name, dry_run=args.dry_run)
        print(json.dumps({"market": args.market, "from": old, "to": new, "ratio": args.ratio, "dry_run": args.dry_run,
                          "changed": changed}, ensure_ascii=False))
        return 0
    if not enabled():
        logger.info("holdings reconcile disabled (HOLDINGS_RECONCILE_ENABLED)")
        return 0
    _bootstrap_path(args.market)
    report, alerts = asyncio.run(run(args.market, db_path=args.db, runtime_dir=args.runtime_dir, dry_run=args.dry_run))
    print(json.dumps({"market": args.market, "dry_run": args.dry_run, "findings": len(report), "alerted": len(alerts)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
