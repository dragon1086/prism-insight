#!/usr/bin/env python3
"""Read-only PRISM ClickStack lookup for trade reviews (2026-10-18).

One ``trace_id`` ties a decision end to end (docs/PRISM_OBSERVABILITY.md "Trace linkage"):
screening -> candidate.evaluated -> entry -> fill -> every holding.evaluated -> exit.

    clickstack_query.py ticker LLY --days 60     # recent traces for a ticker, newest first
    clickstack_query.py trace <trace_id>         # one decision's events in time order
    clickstack_query.py events --days 7          # event counts (coverage check)
    clickstack_query.py sql "SELECT ..."         # ad-hoc read-only query, JSON lines

The query runs on prism-backend, where ClickHouse listens on 127.0.0.1:18123, reached from
here through db-server (``PRISM_DB_SSH``, default ssh alias ``prism-db``) and the backend
host in db-server's /etc/prism-observability/tunnel.env. It logs in as the SELECT-only
``prism_reader`` account from the backend's /etc/prism-observability/reader.env; credentials never
leave the backend. Every request is ``readonly=1`` with
time and row limits. ``--local`` queries ``CLICKHOUSE_HTTP_ENDPOINT`` directly (on backend).

For cohort statistics or SHADOW/LIVE judgments use the Evidence Packet workflow
(skills/prism-entry-quality-analysis) instead of ad-hoc counts from this tool.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess  # nosec B404 - fixed ssh/bash argv below, no shell, query via stdin
import sys
from typing import Any

LIMITS = {"readonly": "1", "max_execution_time": "30", "max_result_rows": "20000"}

# Runs on prism-backend with the observability env loaded. Kept stdlib-only.
REMOTE = r'''
import http.client, json, os, sys, urllib.parse
def run(payload):
    url = urllib.parse.urlsplit(os.getenv("CLICKHOUSE_HTTP_ENDPOINT", "http://127.0.0.1:18123"))
    if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("ClickHouse endpoint must be local HTTP")
    query = dict(payload["limits"])
    query.update({"param_" + k: str(v) for k, v in payload["params"].items()})
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=60)
    try:
        conn.request("POST", "/?" + urllib.parse.urlencode(query),
                     body=(payload["sql"] + "\nFORMAT JSONEachRow").encode("utf-8"),
                     headers={"Content-Type": "text/plain; charset=utf-8",
                              "X-ClickHouse-User": os.getenv("CLICKHOUSE_USER", "prism_reader"),
                              "X-ClickHouse-Key": os.getenv("CLICKHOUSE_PASSWORD", "")})
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        conn.close()
    if response.status != 200:
        raise SystemExit("ClickHouse HTTP %s: %s" % (response.status, body[:400]))
    sys.stdout.write(body)
'''

READER_ENV = "/etc/prism-observability/reader.env"


def build_program(sql: str, params: dict[str, Any]) -> str:
    payload = {"sql": sql, "params": params, "limits": LIMITS}
    return REMOTE + "\nrun(json.loads(%r))\n" % json.dumps(payload, ensure_ascii=False)


def run_query(sql: str, params: dict[str, Any] | None = None, *, local: bool = False) -> list[dict]:
    program = build_program(sql, params or {})
    if local:
        command = ["bash", "-c", f"set -a; . {READER_ENV}; set +a; python3 -"]
    else:
        backend = f"set -a; . {READER_ENV}; set +a; python3 -"
        hop = ". /etc/prism-observability/tunnel.env; ssh -o BatchMode=yes root@$PRISM_BACKEND_HOST " + shlex.quote(backend)
        target = os.getenv("PRISM_DB_SSH", "prism-db")
        # an ssh destination only: a leading "-" or spaces would turn it into ssh options
        if not re.fullmatch(r"[A-Za-z0-9_.@][A-Za-z0-9_.@-]*", target):
            raise SystemExit(f"invalid PRISM_DB_SSH: {target!r}")
        command = ["ssh", "-o", "BatchMode=yes", target, hop]
    result = subprocess.run(command, input=program, text=True, capture_output=True, timeout=180)  # nosec B603  # nosemgrep
    if result.returncode:
        noise = ("post-quantum", "store now", "openssh.com/pq")
        err = "\n".join(l for l in result.stderr.splitlines() if not any(n in l for n in noise))
        raise SystemExit(f"clickstack query failed: {err.strip()[-800:]}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


# Fields worth reading first in a trade review, by where they live in the event attributes.
KEY_FIELDS = (
    "selected_trigger", "trigger_type", "market_regime", "effective_entry_regime", "decision",
    "buy_score", "adjusted_score", "min_score", "gate_reason", "slots_used", "slots_max", "price",
    "status", "reason_code", "confirmed_fill_price", "buy_price", "sell_price", "current_price",
    "profit_rate_pct", "profit_rate", "holding_days", "stop_loss", "target_price", "should_sell",
    "sell_reason", "exit_reason", "reason", "rationale", "mode", "target_pct",
    # re-entry signals / near misses / forward outcomes (decision_snapshot_v1, candidate ledger)
    "trigger", "decision_price", "level", "dist_level_pct", "rr", "rr_floor", "dist_ma50_pct",
    "ma50_slope_10d_pct", "rs_60d_pctp", "max_gain_pct", "max_loss_pct", "ledger_mode", "mfe_10s", "mae_10s",
    "mfe_pct", "mae_pct",
)


def _flatten(attrs: dict) -> dict:
    flat: dict = {}
    for key, value in attrs.items():
        if isinstance(value, dict) and key in ("decision_context", "fill_provenance", "exit_context", "snapshot"):
            for sub, inner in value.items():
                flat.setdefault(sub, inner)
        elif not isinstance(value, (dict, list)):
            flat.setdefault(key, value)
    return flat


def summarize(body: dict, width: int = 220) -> str:
    flat = _flatten(body.get("attributes") or {})
    parts = []
    for key in KEY_FIELDS:
        value = flat.get(key)
        if value is None or value == "":
            continue
        text = str(value).replace("\n", " ")
        if width and len(text) > width:
            text = text[:width] + "…"
        parts.append(f"{key}={text}")
    return " · ".join(parts)


def cmd_trace(args) -> None:
    rows = run_query(
        "SELECT toString(toTimeZone(Timestamp, 'Asia/Seoul')) AS kst, LogAttributes['event.name'] AS event, Body"
        " FROM otel_logs WHERE TraceId = {trace:String} ORDER BY Timestamp",
        {"trace": args.trace_id}, local=args.local)
    if not rows:
        print("no events for this trace_id (180-day retention; linkage exists for decisions after 2026-10-08)")
        return
    first = json.loads(rows[0]["Body"])
    print(f"trace {args.trace_id} · {first.get('market')} {first.get('ticker')} · {len(rows)} events (KST)")
    for row in rows:
        body = json.loads(row["Body"])
        if args.raw:
            print(json.dumps(body, ensure_ascii=False))
            continue
        print(f"{row['kst'][:19]}  {row['event']:<34} {summarize(body, 0 if args.full else 220)}")


def cmd_ticker(args) -> None:
    rows = run_query(
        "SELECT TraceId AS trace, any(LogAttributes['prism.market']) AS market,"
        " toString(toTimeZone(min(Timestamp), 'Asia/Seoul')) AS first_kst,"
        " toString(toTimeZone(max(Timestamp), 'Asia/Seoul')) AS last_kst,"
        " arrayStringConcat(arrayDistinct(arrayMap(x -> x.2, arraySort(groupArray((Timestamp, LogAttributes['event.name']))))), ' > ') AS path"
        " FROM otel_logs WHERE LogAttributes['prism.ticker'] = {ticker:String}"
        " AND TimestampTime >= now() - toIntervalDay({days:UInt16})"
        " AND Timestamp <= now() + INTERVAL 5 MINUTE AND TraceId != ''"
        " GROUP BY TraceId"
        # watchlist/micro-split side events get their own one-event traces; hide them unless --all
        " HAVING {all:UInt8} OR countIf(LogAttributes['event.name'] IN"
        " ('candidate.evaluated', 'entry.executed', 'holding.evaluated', 'exit.executed')"
        " OR startsWith(LogAttributes['event.name'], 'reentry')) > 0"
        " ORDER BY min(Timestamp) DESC LIMIT {limit:UInt16}",
        {"ticker": args.ticker, "days": args.days, "limit": args.limit, "all": int(args.all)}, local=args.local)
    if not rows:
        print(f"no traced events for {args.ticker} in {args.days} days")
    for row in rows:
        print(f"{row['first_kst'][:16]} ~ {row['last_kst'][:16]}  {row['market']}  {row['trace']}\n    {row['path']}")


def cmd_events(args) -> None:
    rows = run_query(
        "SELECT LogAttributes['event.name'] AS event, LogAttributes['prism.market'] AS market,"
        " countIf(Timestamp <= now() + INTERVAL 5 MINUTE) AS n,"
        " countIf(Timestamp > now() + INTERVAL 5 MINUTE) AS future,"
        " toString(toTimeZone(maxIf(Timestamp, Timestamp <= now() + INTERVAL 5 MINUTE), 'Asia/Seoul')) AS last_kst FROM otel_logs"
        " WHERE TimestampTime >= now() - toIntervalDay({days:UInt16}) AND startsWith(event, {prefix:String})"
        " GROUP BY event, market ORDER BY event, market",
        {"days": args.days, "prefix": args.prefix}, local=args.local)
    for row in rows:
        last = row['last_kst'][:16] if row['n'] else '-'
        future = f"  future={row['future']}" if row['future'] else ""
        print(f"{row['event']:<40} {row['market'] or '-':<4} {row['n']:>7}  last {last}{future}")


def cmd_sql(args) -> None:
    head = args.query.lstrip().split(None, 1)[0].upper() if args.query.strip() else ""
    if head not in {"SELECT", "WITH", "DESCRIBE", "DESC", "SHOW", "EXPLAIN"}:
        raise SystemExit("only read queries (SELECT/WITH/DESCRIBE/SHOW/EXPLAIN) are allowed")
    for row in run_query(args.query, local=args.local):
        print(json.dumps(row, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", action="store_true", help="query CLICKHOUSE_HTTP_ENDPOINT directly (run on prism-backend)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("trace"); p.add_argument("trace_id")
    p.add_argument("--full", action="store_true", help="do not truncate reasons"); p.add_argument("--raw", action="store_true", help="print event JSON")
    p.set_defaults(func=cmd_trace)
    p = sub.add_parser("ticker"); p.add_argument("ticker"); p.add_argument("--days", type=int, default=60); p.add_argument("--limit", type=int, default=20)
    p.add_argument("--all", action="store_true", help="include side-event traces (watchlist, micro-split plans)")
    p.set_defaults(func=cmd_ticker)
    p = sub.add_parser("events"); p.add_argument("--days", type=int, default=7); p.add_argument("--prefix", default="")
    p.set_defaults(func=cmd_events)
    p = sub.add_parser("sql"); p.add_argument("query"); p.set_defaults(func=cmd_sql)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
