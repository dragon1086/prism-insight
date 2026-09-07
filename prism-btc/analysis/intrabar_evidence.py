"""Independent event-ledger checks and preregistered exploratory comparison.

Rebuilds lot inventory, weighted entry, cash, fees and funding from economic
receipts, not the broker's internal Parent objects. No exchange/network access.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from analysis.confidence_allocation_study import block_bounds

LOT = .001
DAY_MS = 86_400_000
ARTIFACTS = ("events.jsonl", "decisions.jsonl", "closed_parents.json",
             "daily_nav.jsonl", "summary.json", "manifest.json")


def _same(actual, expected, name):
    if not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7):
        raise ValueError(name)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(name)
    return value


def verify_ledger(events, summary, config, bars, funding, marks, initial_cash=10_000.):
    """Check full specified time window, including omitted settlement receipts.

    bars/funding/marks are timestamp dictionaries sliced to the case window.
    Funding timestamps in this contract are 5m opens with observed mark opens.
    """
    parents, used, grouped = {}, defaultdict(int), defaultdict(list)
    previous_ts, previous_seq = -1, 0
    for event in events:
        ts = _integer(event["ts"], "invalid_event_timestamp")
        seq = _integer(event["seq"], "invalid_event_sequence", 1)
        if ts < previous_ts or seq != previous_seq + 1:
            raise ValueError("nonchronological_or_missing_event")
        previous_ts, previous_seq = ts, seq
        grouped[ts].append(event)
    fees = realized = funding_cost = 0.
    closures, settled, entry_lots, exit_lots = set(), set(), 0, 0
    for ts in sorted(set(grouped) | set(funding)):
        rows = grouped.get(ts, [])
        charged = [e for e in rows if e["kind"] == "funding"]
        if ts in funding:
            expected = {pid: p for pid, p in parents.items() if p["lots"]}
            if len(charged) != len(expected) or {e["parent_id"] for e in charged} != set(expected):
                raise ValueError("funding_receipt_coverage")
            seen_nonfunding = False
            for event in rows:
                if event["kind"] == "funding" and seen_nonfunding:
                    raise ValueError("funding_not_before_other_events")
                seen_nonfunding |= event["kind"] != "funding"
        elif charged:
            raise ValueError("unexpected_funding_timestamp")
        for event in rows:
            kind, pid = event["kind"], event.get("parent_id")
            if kind == "entry_submit":
                if pid in parents:
                    raise ValueError("duplicate_parent")
                n = _integer(event["accepted_lots"], "invalid_accepted_lots", 1)
                chunks = {c["index"]: _integer(c["lots"], "invalid_child_lots") for c in event["children"]}
                if len(chunks) != len(event["children"]) or sum(chunks.values()) != n:
                    raise ValueError("child_plan_quantity")
                parents[pid] = {"side": event["side"], "lane": event["lane"], "lots": 0,
                    "filled": 0, "exited": 0, "entry": 0., "fees": 0., "funding": 0.,
                    "realized": 0., "initial_risk": 0., "stop": event["stop"],
                    "tp1": event["tp1"], "children": chunks, "filled_children": set(),
                    "accepted": n, "closed": False}
            elif kind == "fill":
                if pid not in parents or parents[pid]["closed"]:
                    raise ValueError("fill_without_active_parent")
                p = parents[pid]
                n = _integer(event["lots"], "noninteger_or_dust_fill", 1)
                price = event["price"]
                if not math.isfinite(price) or price <= 0:
                    raise ValueError("invalid_fill_price")
                _same(price * 10, round(price * 10), "non_tick_fill")
                _same(event["qty"], n * LOT, "quantity_unit_mismatch")
                if event["side"] != p["side"] or event["lane"] != p["lane"] or type(event["entry"]) is not bool:
                    raise ValueError("fill_parent_identity")
                sign = 1 if p["side"] == "long" else -1
                action = "BUY" if (sign == 1) == event["entry"] else "SELL"
                if event["action"] != action:
                    raise ValueError("fill_action_direction")
                bar_ts = event["bar_open_ts"]
                if bar_ts not in bars or not bar_ts <= ts <= bar_ts + 300_000:
                    raise ValueError("fill_outside_bar")
                used[bar_ts] += n
                capacity = math.floor(bars[bar_ts]["prior_volume"] * config["participation"] / LOT + 1e-12)
                if used[bar_ts] > capacity:
                    raise ValueError("shared_liquidity_exceeded")
                fee = n * LOT * price * config["maker_fee" if event["reason"] in ("limit", "tp1") else "taker_fee"]
                _same(event["fee"], fee, "fee_rate_mismatch")
                pnl = 0.
                if event["entry"]:
                    child = event["child"]
                    if child not in p["children"] or n > p["children"][child]:
                        raise ValueError("overfilled_or_cancelled_child")
                    p["children"][child] -= n
                    p["filled_children"].add(child)
                    p["entry"] = (p["entry"] * p["lots"] + price * n) / (p["lots"] + n)
                    p["lots"] += n
                    p["filled"] += n
                    p["initial_risk"] += n * LOT * abs(price - p["stop"])
                    entry_lots += n
                else:
                    if n > p["lots"]:
                        raise ValueError("oversold_parent")
                    pnl = sign * n * LOT * (price - p["entry"])
                    p["lots"] -= n
                    p["exited"] += n
                    exit_lots += n
                _same(event["realized_price"], pnl, "realized_basis_mismatch")
                if event["remaining_lots"] != p["lots"]:
                    raise ValueError("remaining_lots_mismatch")
                _same(event["initial_risk"], p["initial_risk"], "initial_risk_money_unit")
                p["realized"] += pnl
                p["fees"] += fee
                realized += pnl
                fees += fee
            elif kind in ("cancel_ack", "child_skip", "entry_reject") and pid in parents and "child" in event:
                p = parents[pid]
                child = event["child"]
                if event["cancelled_lots"] != p["children"][child]:
                    raise ValueError("cancelled_quantity_mismatch")
                p["children"][child] = 0
            elif kind == "entry_settled":
                p = parents[pid]
                if pid in settled or any(p["children"].values()) or p["filled"] != event["filled_lots"]:
                    raise ValueError("premature_entry_settlement")
                quota = p["filled"] // 3 if p["tp1"] is not None else 0
                if event["tp1_quota"] != quota:
                    raise ValueError("tp_quota_mismatch")
                settled.add(pid)
            elif kind == "funding":
                p = parents[pid]
                rate, mark = funding[ts]["rate"], marks[ts]["open"]
                if event["lots"] != p["lots"]:
                    raise ValueError("funding_quantity_mismatch")
                _same(event["rate"], rate, "funding_rate_mismatch")
                _same(event["mark"], mark, "funding_mark_mismatch")
                cost = (1 if p["side"] == "long" else -1) * p["lots"] * LOT * mark * rate
                _same(event["amount"], cost, "funding_sign_or_amount")
                p["funding"] += cost
                funding_cost += cost
            elif kind == "parent_close":
                p = parents[pid]
                if pid in closures or p["lots"] or any(p["children"].values()):
                    raise ValueError("nonterminal_parent_close")
                for key, actual in (("fees", p["fees"]), ("funding", p["funding"]),
                    ("realized_price", p["realized"]), ("initial_risk", p["initial_risk"]),
                    ("net_pnl", p["realized"] - p["fees"] - p["funding"])):
                    _same(event[key], actual, "closed_parent_" + key)
                if event["filled_lots"] != p["filled"] or event["exited_lots"] != p["exited"]:
                    raise ValueError("closed_parent_quantity")
                p["closed"] = True
                closures.add(pid)
    expected_settled = {pid for pid, p in parents.items() if not any(p["children"].values())}
    expected_closed = {pid for pid in expected_settled if parents[pid]["filled"] and not parents[pid]["lots"]}
    if settled != expected_settled or closures != expected_closed:
        raise ValueError("settlement_receipt_coverage")
    execution = summary["execution"]
    for key, expected in (("closed_parents", len(closures)), ("entry_lots", entry_lots),
            ("exit_lots", exit_lots), ("filled_parents", sum(p["filled"] > 0 for p in parents.values())),
            ("multi_child_parents", sum(len(p["filled_children"]) > 1 for p in parents.values()))):
        if execution[key] != expected:
            raise ValueError("execution_summary_" + key)
    final, mark = summary["final"], summary["final_mark"]
    unreal = math.fsum((1 if p["side"] == "long" else -1) * p["lots"] * LOT * (mark - p["entry"])
                      for p in parents.values())
    cash = initial_cash + realized - fees - funding_cost
    for key, value in (("cash", cash), ("nav", cash + unreal), ("unrealized", unreal),
                       ("realized_price", realized), ("fees", fees), ("funding", funding_cost)):
        _same(final[key], value, "final_" + key)
    if {p["id"]: p["lots"] for p in summary["open_positions"]} != {
            pid: p["lots"] for pid, p in parents.items() if p["lots"]}:
        raise ValueError("open_position_coverage")
    _same(summary["net_return"], final["nav"] / initial_cash - 1, "return_identity")
    _same(math.fsum(r["interval_nav_change"] for r in summary["regimes"].values()),
          final["nav"] - initial_cash, "regime_nav_identity")
    return {"status": "PASS", "parents": len(parents), "closed_parents": len(closures),
            "entry_lots": entry_lots, "exit_lots": exit_lots, "held_lots": entry_lots - exit_lots,
            "multi_child_parents": sum(len(p["filled_children"]) > 1 for p in parents.values()),
            "fees": fees, "funding": funding_cost, "realized_price": realized,
            "final_cash": cash, "final_unrealized": unreal, "final_nav": cash + unreal,
            "scheduled_funding_events": len(funding), "liquidity_intervals_used": len(used)}


def compare_results(results):
    """results is {case_id: {case, summary, daily}}; registry completeness required."""
    from analysis.intrabar_revalidation import registry
    planned = registry()
    if set(results) != {c["id"] for c in planned}:
        raise ValueError("incomplete_registry")
    indexed = {(c["phase"], c["scope"], c["main_mode"], c["profile"], c["path"]): results[c["id"]]
               for c in planned}

    def get(scope, mode, profile="BASE", path="OHLC"):
        return indexed[("evaluation", scope, mode, profile, path)]

    differences, times = {}, None
    for scope in ("main", "joint"):
        for path in ("OHLC", "OLHC"):
            a, b = get(scope, "confirmed", path=path), get(scope, "single", path=path)
            grid = [r["timestamp_ms"] for r in a["daily"]]
            if grid != [r["timestamp_ms"] for r in b["daily"]] or (times is not None and times != grid):
                raise ValueError("unpaired_daily_grid")
            times = grid
            differences[f"{scope}:{path}:confirmed-single"] = [x["return"] - y["return"]
                                                              for x, y in zip(a["daily"], b["daily"])]
    stats = block_bounds(differences, times, draws=2000, seed=20260907)
    checks, contrasts = {}, {}
    for profile in ("BASE", "STRESS"):
        for path in ("OHLC", "OLHC"):
            a, b = get("joint", "confirmed", profile, path)["summary"], get("joint", "single", profile, path)["summary"]
            key = f"{profile}:{path}"
            checks[key + ":return_higher"] = a["net_return"] > b["net_return"]
            checks[key + ":mdd_not_worse"] = a["max_drawdown"] <= b["max_drawdown"]
            contrasts[key] = {"net_return_difference": a["net_return"] - b["net_return"],
                              "mdd_difference": a["max_drawdown"] - b["max_drawdown"]}
            if profile == "BASE":
                checks[key + ":two_years_better"] = sum(a["yearly_returns"][y] > b["yearly_returns"][y]
                                                          for y in ("2023", "2024", "2025")) >= 2
                checks[key + ":adjusted_lower_positive"] = stats["adjusted_lower_bounds"][f"joint:{path}:confirmed-single"] > 0
                checks[key + ":multi_child_identifiable"] = a["execution"]["multi_child_parents"] > 0
    identifiable = all(v for k, v in checks.items() if k.endswith(":multi_child_identifiable"))
    return {"exploratory_verdict": "UNIDENTIFIABLE" if not identifiable else
            "SUPPORTED_WITHIN_MODEL" if all(checks.values()) else "NOT_SUPPORTED",
            "checks": checks, "contrasts": contrasts, "statistics": stats,
            "profitability_status": "INSUFFICIENT", "auto_activate": False}


def verify_daily(rows, summary, initial_cash=10_000.):
    previous = initial_cash
    years, year_base, last_ts = {}, initial_cash, None
    for row in rows:
        ts = row["timestamp_ms"]
        if ts % DAY_MS or (last_ts is not None and ts - last_ts != DAY_MS):
            raise ValueError("nonconsecutive_daily_grid")
        _same(row["return"], row["nav"] / previous - 1, "daily_return_identity")
        date = datetime.fromtimestamp((ts - 1) / 1000, timezone.utc)
        years[str(date.year)] = row["nav"] / year_base - 1
        if date.month == 12 and date.day == 31:
            year_base = row["nav"]
        previous, last_ts = row["nav"], ts
    _same(previous, summary["final"]["nav"], "daily_final_nav")
    if set(years) != set(summary["yearly_returns"]):
        raise ValueError("yearly_coverage")
    for year, value in years.items():
        _same(summary["yearly_returns"][year], value, "yearly_return_identity")


def compare_repeats(first, second, case_ids):
    pairs = {}
    for case in case_ids:
        pairs[case] = {}
        for name in ARTIFACTS:
            left, right = (Path(root) / case / name for root in (first, second))
            a, b = (hashlib.sha256(p.read_bytes()).hexdigest() for p in (left, right))
            if a != b:
                raise ValueError(f"repeat_mismatch:{case}:{name}")
            pairs[case][name] = a
    return {"status": "PASS", "case_count": len(pairs), "artifact_pairs": len(pairs) * len(ARTIFACTS), "hashes": pairs}


def read_lines(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream]
