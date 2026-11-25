#!/usr/bin/env python3
"""Export the re-entry watch list for the public dashboard (read-only; no orders, no state writes).

Only a distance bucket is published, never the trigger price: the dashboard is public and an exact level
would let others buy ahead of the rule. Watches the LIVE rule can no longer buy (ended, virtual position
open, another rule's record) and held tickers are left out.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from observability import reentry_v3_shadow as V3  # noqa: E402
from observability.reentry_shadow import _atomic  # noqa: E402
from prism_core import reentry_campaign as C  # noqa: E402
from tools import run_reentry_shadow as collectors  # noqa: E402
from tools.run_reentry_v3_shadow import KR_CACHE_DIR  # noqa: E402

NEAR, MID = 0.03, 0.10
HOLDINGS = {"KR": "stock_holdings", "US": "us_stock_holdings"}


def proximity(price, level):
    """near / mid / far from the rule's level, or None when either value is missing."""
    if not price or not level:
        return None
    gap = abs(price / level - 1)
    return "near" if gap <= NEAR else "mid" if gap <= MID else "far"


def buyable(watches, rule, held):
    """One ACTIVE watch per ticker that the LIVE rule may still buy; a stop exit wins over a skip."""
    picked = {}
    for watch in watches:
        gate = ((watch.get("ledger") or {}).get("next") or {}).get(rule) or {}
        if watch["status"] != "ACTIVE" or watch["ticker"] in held or gate.get("eligible") is not True:
            continue
        rank = (watch["source"] == C.STOP_EXIT, watch["enrolled_at"])
        if watch["ticker"] not in picked or rank > picked[watch["ticker"]][0]:
            picked[watch["ticker"]] = (rank, watch, gate)
    return [(watch, gate) for _, watch, gate in picked.values()]


def item(watch, gate, rule, rows):
    closes = [row["close"] for row in rows or []]
    window = gate.get("window") or {}
    if rule == "MA20R":
        level = sum(closes[-C.MA20R_AVG_BARS:]) / C.MA20R_AVG_BARS if len(closes) >= C.MA20R_AVG_BARS else None
    else:
        level = window.get("R") or watch["setup"]["L"]
    left = C.HORIZON - watch["ledger"]["elapsed"]
    if window.get("sessions_left") is not None:
        left = min(left, window["sessions_left"])
    return {"ticker": watch["ticker"], "name": watch["row"].get("company_name") or watch["ticker"],
            "source": "stop_exit" if watch["source"] == C.STOP_EXIT else "skipped",
            "since": watch["setup"]["anchor_date"], "sessions_left": left,
            "proximity": proximity(closes[-1] if closes else None, level)}


def build(market, state, expected, held, bars_fn):
    """The payload; ``items`` is None (unknown, not empty) when the state is not from the expected session."""
    out = {"market": market, "as_of": state["last_completed"],
           "generated_at": datetime.now(timezone.utc).isoformat(), "items": None}
    if state["last_completed"] != expected:
        return out
    rule = C.policy_for(market)["primary_rule"]
    rows = buyable(state["watches"], rule, held)
    bars = bars_fn([watch["ticker"] for watch, _ in rows]) if rows else {}
    items = [item(watch, gate, rule, bars.get(watch["ticker"])) for watch, gate in rows]
    order = {"near": 0, "mid": 1, "far": 2, None: 3}
    out["items"] = sorted(items, key=lambda i: (order[i["proximity"]], i["sessions_left"], i["ticker"]))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    state = json.loads(V3.paths(args.market)["state"].read_text(encoding="utf-8"))
    expected = collectors.completed_session(args.market)
    db = sqlite3.connect(f"file:{V3.DB_PATH}?mode=ro", uri=True)
    try:
        held = {ticker for (ticker,) in db.execute(f"SELECT ticker FROM {HOLDINGS[args.market]}")}
    finally:
        db.close()

    def bars(tickers):
        if args.market == "US":
            return collectors.collect_us(tickers, expected)
        cache = KR_CACHE_DIR / f"{expected}.json"       # written by the close run just before this
        return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}

    payload = build(args.market, state, expected, held, bars)
    _atomic(Path(args.out), payload)
    Path(args.out).chmod(0o644)
    items = payload["items"]
    print(json.dumps({"market": args.market, "as_of": payload["as_of"], "expected": expected,
                      "items": None if items is None else len(items),
                      "unpriced": None if items is None else sum(i["proximity"] is None for i in items)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
