"""Bounded, fail-open US watch observation. No access to orders, holdings or LLMs."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from observability.events import emit_event
from observability.micro_split import shadow_enabled
from prism_core.oneil_watchlist import ACTIVE, POLICY_VERSION, advance

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "trading/config/oneil_watchlist_shadow.json"
STATE_PATH = ROOT / "runtime/oneil_watchlist_state_v1.json"


def _emit(event, **kwargs):
    return emit_event(event, service="prism-us-oneil-watchlist-shadow", **kwargs)


def _today():
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")


def enabled():
    try:
        policy = json.loads(POLICY_PATH.read_text())
        return (shadow_enabled() and policy == {
            "mode": "SHADOW", "market": "US", "policy_version": POLICY_VERSION, "enabled": True,
        } and os.getenv("ONEIL_WATCHLIST_SHADOW_ENABLED", "true").lower() not in {"0", "false", "off"})
    except (OSError, ValueError):
        return False


def _load(path):
    if not path.exists():
        return {"schema_version": 1, "policy_version": POLICY_VERSION, "watches": []}
    if path.stat().st_size > 1_000_000:
        raise ValueError("oversized_state")
    state = json.loads(path.read_text())
    if state.get("schema_version") != 1 or state.get("policy_version") != POLICY_VERSION:
        raise ValueError("state_version")
    return state


def _collect(tickers, trade_date):
    worker = ROOT / "tools/run_oneil_watchlist_shadow.py"
    result = subprocess.run([sys.executable, str(worker)],
                            input=json.dumps({"tickers": tickers, "trade_date": trade_date}),
                            text=True, capture_output=True, timeout=20, check=True, cwd=ROOT)
    return json.loads(result.stdout)


def _atomic(path, state):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".oneil-", delete=False) as file:
            name = file.name
            json.dump(state, file, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def observe_batch(final_results, trade_date, batch_ref, *, collector=None, state_path=None):
    """Observe even empty selection; optional subprocess has one fixed 20-second budget."""
    if not batch_ref or not enabled():
        return None
    if str(trade_date).replace("-", "") != _today():
        return None  # Offline overrides never contaminate prospective persistent state.
    path = Path(state_path) if state_path else STATE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = _load(path)
            # Invalidate old same-batch eligibility before optional I/O (including retries).
            for row in state["watches"]:
                row.pop("batch_ref", None)
            _atomic(path, state)
            seeds = [{"ticker": str(ticker), "trigger": str(trigger)}
                     for trigger, frame in final_results.items() for ticker in frame.index
                     if "contrarian" not in str(trigger).lower()]
            tickers = list(dict.fromkeys([w["ticker"] for w in state["watches"] if w["status"] in ACTIVE]
                                        + [s["ticker"] for s in seeds]))[:20]
            frames = (collector or _collect)(tickers + ["SPY"], trade_date) if tickers else {}
            if tickers and not frames.get("__expected_completed_date"):
                frames = {}  # Calendar availability is required, never guessed from provider rows.
            updated, observations = advance(state, seeds, frames, trade_date, batch_ref)
            for row in observations:
                public_row = {k: v for k, v in row.items() if not k.startswith("delivery_")}
                attributes = {**public_row, "watch_ref": row["watch_id"], "mode": "SHADOW",
                              "observed_at": datetime.now(timezone.utc).isoformat(),
                              "data_source": "yfinance_daily", "adjustment_policy": "auto_adjust_true_observed_now",
                              "research_kind": "ONEIL_WATCHLIST", "eligibility": "NOT_EVALUATED",
                              "trading_impact": "none", "observation_price_ref": row.get("input_hash"),
                              "ready_observation_event_id": row["observation_event_id"] if row["status"] == "READY" else None}
                row.setdefault("delivery_seed_payload", dict(attributes))
                if row["status"] == "READY":
                    row.setdefault("delivery_ready_payload", dict(attributes))
                old_payload = row.get("delivery_observation_payload", {})
                if old_payload.get("observation_event_id") != row["observation_event_id"]:
                    row["delivery_observation_payload"] = attributes
            # Persist immutable lifecycle payloads before delivery; next batch may retry them.
            _atomic(path, updated)
            for row in updated["watches"]:
                for kind, event, id_field in (("seed", "watchlist.shadow_seeded", "seed_event_id"),
                                              ("ready", "watchlist.shadow_ready", "ready_event_id"),
                                              ("observation", "watchlist.shadow_evaluated", "observation_event_id")):
                    payload = row.get("delivery_" + kind + "_payload")
                    event_id = row.get(id_field)
                    if payload and event_id and row.get("delivery_" + kind + "_sent") != event_id:
                        result = _emit(event, event_id=event_id, market="US", ticker=row["ticker"], attributes=payload,
                                       event_time=datetime.fromisoformat(payload["observed_at"]))
                        if result is not None:
                            row["delivery_" + kind + "_sent"] = event_id
            _atomic(path, updated)
            return observations
    except Exception:  # noqa: BLE001 - optional research cannot change batch success
        _emit("watchlist.shadow_unavailable", market="US", attributes={
            "batch_ref": batch_ref, "mode": "SHADOW", "status": "MISSING",
            "policy_version": POLICY_VERSION, "trading_impact": "none"})
        return None


def read_ready_context(market, ticker, batch_ref):
    try:
        if market != "US" or not batch_ref or not enabled():
            return None
        matches = [w for w in _load(STATE_PATH)["watches"]
                   if w["ticker"] == ticker and w.get("batch_ref") == batch_ref and w["status"] == "READY"]
        if len(matches) != 1:
            return None
        row = matches[0]
        if any(row.get("delivery_" + kind + "_sent") != row.get(id_field)
               for kind, id_field in (("seed", "seed_event_id"), ("ready", "ready_event_id"),
                                      ("observation", "observation_event_id"))):
            return None
        return {"market": market, "ticker": ticker, "batch_ref": batch_ref,
                "watch_ref": row["watch_id"], "watch_id": row["watch_id"],
                "seed_event_id": row["seed_event_id"], "ready_event_id": row["ready_event_id"],
                "ready_observation_event_id": row["observation_event_id"],
                "policy_version": POLICY_VERSION, "asof": row["asof"],
                "input_hash": row["input_hash"], "observation_price_ref": row["input_hash"]}
    except Exception:  # noqa: BLE001 - optional exact join fails closed
        return None
