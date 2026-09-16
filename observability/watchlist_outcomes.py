"""Bounded local outcome registry and immutable archives; no network/LLM/orders."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from observability.events import emit_event
from prism_core.watchlist_outcomes import VERSION, evaluate, identity, new_anchor

ROOT = Path(__file__).resolve().parents[1]
MAX_ANCHORS = 320


def _path(market, state_path=None):
    if market not in {"KR", "US"}:
        raise ValueError("unsupported_market")
    return Path(state_path) if state_path else ROOT / "runtime" / ("watch_outcomes_" + market.lower() + "_v1.json")


def _load(path, market):
    state = json.loads(path.read_text()) if path.exists() else {"market": market, "version": VERSION, "anchors": [], "outbox": []}
    if state["market"] != market or state["version"] != VERSION:
        raise ValueError("state_contract_mismatch")
    return state


def _atomic(path, obj):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
            name = file.name
            json.dump(obj, file, sort_keys=True, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _archive(directory, obj):
    directory.mkdir(parents=True, exist_ok=True)
    digest = identity(obj)
    path = directory / (digest + ".json")
    if not path.exists():
        _atomic(path, obj)
    elif json.loads(path.read_text()) != obj:
        raise ValueError("archive_integrity")
    return digest


def pending_symbols(market, state_path=None):
    try:
        anchors = _load(_path(market, state_path), market)["anchors"]
        return list(dict.fromkeys(a["ticker"] for a in sorted(anchors, key=lambda a: (a.get("last_attempt", ""), a["observed_at"], a["anchor_id"])) if not a["done"]))[:80]
    except Exception:  # noqa: BLE001 - research cannot block trading
        return []


def observe_outcomes(market, observations, frames, batch_ref, trade_date, state_path=None):
    """Fail open; persist snapshots/outbox before events, isolate market and legacy watch state."""
    try:
        path = _path(market, state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        archive = path.with_suffix(".archive")
        now = datetime.now(timezone.utc).isoformat()
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = _load(path, market)
            # Retirement records share the registry transaction. A crash between
            # registry replacement and archive creation cannot permit reenrollment.
            for record in state.get("retirements", []):
                _atomic(archive / (record["anchor_id"] + ".done.json"), record)
            state["retirements"] = []
            # An unavailable spool must not grow runtime memory without limit.
            if len(state["outbox"]) > 4000:
                state["outbox"] = [event for event in state["outbox"] if emit_event(
                    "watchlist.price_path", service="prism-watch-outcomes", market=market,
                    ticker=event["ticker"], event_id=event["event_id"], attributes=event) is None]
                _atomic(path, state)
                raise RuntimeError("outcome_delivery_backlog")
            digest = _archive(archive, frames)
            events, finished = [], []
            known = {a["anchor_id"] for a in state["anchors"]} | {
                e["anchor_id"] for e in state["outbox"] if e["status"] in {"ENROLLED", "EXCLUDED"}}
            for row in observations:
                for kind in (["BASELINE", "FIRST_READY"] if row["status"] == "READY" else ["BASELINE"]):
                    stamp = row.get("delivery_observation_payload", {}).get("observed_at", now)
                    anchor = new_anchor(market, row, kind, stamp, digest, batch_ref)
                    anchor["trade_date"] = trade_date
                    anchor["market_calendar_available"] = bool(frames.get("__market_days") and frames.get("__expected_completed_date"))
                    if kind == "FIRST_READY":
                        baseline_id = identity(VERSION, market, row["watch_id"], "BASELINE")
                        baseline = next((a for a in state["anchors"] if a["anchor_id"] == baseline_id), None)
                        baseline_file = archive / (baseline_id + ".done.json")
                        if baseline is None and baseline_file.exists():
                            baseline = json.loads(baseline_file.read_text())
                        if baseline and baseline.get("observed_local_date"):
                            anchor.update(baseline_anchor_id=baseline_id,
                                          baseline_observed_local_date=baseline["observed_local_date"])
                    tombstone = archive / (anchor["anchor_id"] + ".done.json")
                    if anchor["anchor_id"] in known or tombstone.exists():
                        continue
                    if len(state["anchors"]) >= MAX_ANCHORS:
                        events.append({**anchor, "status": "EXCLUDED", "reason": "registry_capacity"})
                        finished.append((tombstone, {"status": "EXCLUDED", "anchor_id": anchor["anchor_id"]}))
                        continue
                    state["anchors"].append(anchor)
                    known.add(anchor["anchor_id"])
                    events.append({**anchor, "status": "ENROLLED"})
            remaining = []
            requested = set(frames.get("__requested_symbols", [k for k in frames if not k.startswith("__")]))
            for anchor in state["anchors"]:
                if anchor["ticker"] not in requested or anchor["ticker"] in frames.get("__deferred_symbols", []):
                    updated, missing = evaluate(anchor, frames, {})
                    for event in missing:
                        event.update(status="MISSING", reason="collector_capacity_deferred", captured_at=now,
                                     input_snapshot_hash=digest, archive_ref=digest + ".json", trade_date=trade_date)
                    events.extend(missing)
                    if updated["done"]:
                        finished.append((archive / (anchor["anchor_id"] + ".done.json"), updated))
                    else:
                        remaining.append(anchor)
                    continue
                frozen = json.loads((archive / (anchor["seed_snapshot_hash"] + ".json")).read_text())
                updated, outcomes = evaluate(anchor, frames, frozen)
                if anchor.get("reference_snapshot_hash"):
                    reference_frames = json.loads((archive / (anchor["reference_snapshot_hash"] + ".json")).read_text())
                    _, checked = evaluate(anchor, frames, reference_frames)
                    if any(e["status"] == "BASIS_CHANGED" for e in checked):
                        updated["done"], outcomes = True, checked
                elif updated.get("reference_date"):
                    updated["reference_snapshot_hash"] = digest
                if anchor["ticker"] in requested:
                    updated["last_attempt"] = now
                for event in outcomes:
                    event.update(input_snapshot_hash=digest, archive_ref=digest + ".json", trade_date=trade_date,
                                 captured_at=now, data_source="kis_daily" if market == "KR" else "yfinance_daily")
                    events.append(event)
                if updated["done"]:
                    finished.append((archive / (anchor["anchor_id"] + ".done.json"), updated))
                else:
                    remaining.append(updated)
            state["anchors"] = remaining
            state["retirements"] = [record for _, record in finished]
            for event in events:
                event["outcome_id"] = identity(VERSION, event["anchor_id"], event.get("horizon"), event.get("measurement"))
                event["event_id"] = identity(VERSION, event["anchor_id"], event.get("horizon"), event["status"],
                                             event.get("asof"), event.get("reason"), event.get("input_snapshot_hash"),
                                             event.get("captured_at", event.get("observed_at")), event.get("measurement"))[:32]
                event.update(mode="SHADOW", trading_impact="none")
                if not any(e["event_id"] == event["event_id"] for e in state["outbox"]):
                    state["outbox"].append(event)
            # Do not silently drop events if transport is unavailable.
            _atomic(path, state)
            for tombstone, record in finished:
                _atomic(tombstone, record)
            state["retirements"] = []
            _atomic(path, state)
            unsent = []
            for event in state["outbox"]:
                response = emit_event("watchlist.price_path", service="prism-watch-outcomes", market=market,
                                      ticker=event["ticker"], event_id=event["event_id"], attributes=event)
                if response is None:
                    unsent.append(event)
            state["outbox"] = unsent
            _atomic(path, state)
            return events
    except Exception:  # noqa: BLE001 - optional evidence cannot affect control flow
        try:
            emit_event("watchlist.outcomes_unavailable", service="prism-watch-outcomes", market=market,
                       attributes={"batch_ref": batch_ref, "market": market, "trade_date": trade_date,
                                   "data_contract": VERSION, "status": "MISSING", "mode": "SHADOW",
                                   "trading_impact": "none", "reason": "outcome_capture_unavailable"})
        except Exception:  # noqa: BLE001 - even diagnostic I/O is optional
            return None
        return None
