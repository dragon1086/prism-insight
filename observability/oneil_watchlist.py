"""Bounded, fail-open US watch observation. No access to orders, holdings or LLMs."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess  # nosec B404 - fixed repository worker, JSON stdin, no shell
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from observability.events import emit_event
from observability.micro_split import shadow_enabled
from prism_core.oneil_watchlist import ACTIVE, advance, contrarian, policy_version

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "trading/config/oneil_watchlist_shadow.json"
STATE_PATH = ROOT / "runtime/oneil_watchlist_state_v1.json"
KR_POLICY_PATH = ROOT / "trading/config/oneil_watchlist_kr_shadow.json"
KR_STATE_PATH = ROOT / "runtime/oneil_watchlist_state_kr_v1.json"


def _emit(event, **kwargs):
    return emit_event(event, service="prism-" + kwargs.get("market", "US").lower() + "-oneil-watchlist-shadow", **kwargs)


def _today(market="US"):
    return datetime.now(ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")).strftime("%Y%m%d")


def enabled(market="US"):
    try:
        policy = json.loads((KR_POLICY_PATH if market == "KR" else POLICY_PATH).read_text())
        return ((shadow_enabled() if market == "US" else shadow_enabled(market=market)) and policy == {
            "mode": "SHADOW", "market": market, "policy_version": policy_version(market), "enabled": True,
        } and os.getenv("ONEIL_WATCHLIST_SHADOW_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"})
    except (OSError, ValueError):
        return False


def _load(path, market="US"):
    if not path.exists():
        return {"schema_version": 1, "policy_version": policy_version(market), "watches": []}
    if path.stat().st_size > 1_000_000:
        raise ValueError("oversized_state")
    state = json.loads(path.read_text())
    if (state.get("schema_version") != 1 or state.get("policy_version") != policy_version(market)
            or state.get("market", market) != market):
        raise ValueError("state_version")
    return state


def _collect(tickers, trade_date):
    worker = ROOT / "tools/run_oneil_watchlist_shadow.py"
    # Neither the executable nor script path comes from candidate data.
    result = subprocess.run([sys.executable, str(worker)],  # nosec B603  # nosemgrep
                            input=json.dumps({"tickers": tickers, "trade_date": trade_date}),
                            text=True, capture_output=True, timeout=20, check=True, cwd=ROOT,
                            shell=False, close_fds=True)
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


def observe_batch(final_results, trade_date, batch_ref, *, collector=None, state_path=None, market="US",
                  regime_context=None):
    """Observe even empty selection; optional subprocess has one fixed 20-second budget."""
    if not batch_ref or not (enabled() if market == "US" else enabled(market)):
        return None
    if str(trade_date).replace("-", "") != (_today() if market == "US" else _today(market)):
        return None  # Offline overrides never contaminate prospective persistent state.
    path = Path(state_path) if state_path else (KR_STATE_PATH if market == "KR" else STATE_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = _load(path, market)
            # Invalidate old same-batch eligibility before optional I/O (including retries).
            for row in state["watches"]:
                row.pop("batch_ref", None)
            _atomic(path, state)
            seeds = [{"ticker": str(ticker), "trigger": str(trigger)}
                     for trigger, frame in final_results.items() for ticker in frame.index
                     if not contrarian(str(trigger))]
            tickers = list(dict.fromkeys([w["ticker"] for w in state["watches"] if w["status"] in ACTIVE]
                                        + [s["ticker"] for s in seeds]))[:20]
            try:
                from observability.watchlist_outcomes import pending_symbols
                pending = pending_symbols(market)[:80]
            except Exception:  # noqa: BLE001 - optional research
                pending = []
            pending = [s for s in pending if s not in tickers]
            if pending:
                pending_offset = (int(str(trade_date).replace("-", "")) * 6) % len(pending)
                pending = pending[pending_offset:] + pending[:pending_offset]
            active_symbols = [w["ticker"] for w in state["watches"] if w["status"] in ACTIVE]
            if pending:
                # Reserve outcome capacity even in weak markets with one batch/day.
                offset = int(str(trade_date).replace("-", "")) % max(1, len(active_symbols))
                rotated = active_symbols[offset:] + active_symbols[:offset]
                new = [s["ticker"] for s in seeds if s["ticker"] not in active_symbols]
                watch_budget = 20 - min(6, len(pending))
                selected = list(dict.fromkeys(new + rotated))[:watch_budget]
                outcome_selected = pending[:20 - len(selected)]
                symbols = []
                # Wall-clock fairness too: a slow first active request must not
                # repeatedly consume the entire deadline before any outcome read.
                while selected or outcome_selected:
                    symbols.extend(outcome_selected[:1])
                    outcome_selected = outcome_selected[1:]
                    symbols.extend(selected[:2])
                    selected = selected[2:]
            else:
                symbols = tickers
            if market == "KR" and collector is None:
                from tools.run_oneil_watchlist_shadow import collect_kr_bounded
                collector = collect_kr_bounded
            frames = (collector or _collect)(symbols + (["SPY"] if market == "US" else []), trade_date) if symbols else {}
            attempted = frames.get("__attempted_symbols", symbols)
            frames["__requested_symbols"] = list(attempted)
            frames["__deferred_symbols"] = list(dict.fromkeys(
                [s for s in tickers + pending + symbols if s not in attempted]))
            if tickers and not frames.get("__expected_completed_date"):
                frames = {}  # Calendar availability is required, never guessed from provider rows.
            updated, observations = advance(state, seeds, frames, trade_date, batch_ref, market=market)
            for row in observations:
                if row.get("setup_initialized"):
                    row.setdefault("setup_observed_at", datetime.now(timezone.utc).isoformat())
                    row.setdefault("setup_batch_ref", batch_ref)
                if row["status"] == "MISSING" and row["ticker"] in frames.get("__deferred_symbols", []):
                    row["reason"] = "collection_deferred_budget"
                if isinstance(regime_context, dict):
                    row["regime_context"] = {key: value for key, value in regime_context.items()
                                             if key in {"market_regime", "primary_trend_regime", "effective_entry_regime",
                                                        "swing_state", "reference_date"}
                                             and isinstance(value, (str, int, float, bool, type(None)))}
                    row["regime_context"]["source"] = "batch_macro_context"
                public_row = {k: v for k, v in row.items() if not k.startswith("delivery_")}
                attributes = {**public_row, "watch_ref": row["watch_id"], "mode": "SHADOW",
                              "observed_at": datetime.now(timezone.utc).isoformat(),
                              "data_source": "kis_daily" if market == "KR" else "yfinance_daily",
                              "currency": "KRW" if market == "KR" else "USD",
                              "benchmark": row.get("benchmark", "SPY" if market == "US" else None),
                              "adjustment_policy": "kis_adjusted_true_observed_now" if market == "KR" else "auto_adjust_true_observed_now",
                              "research_kind": "ONEIL_WATCHLIST", "eligibility": "NOT_EVALUATED",
                              "outcome_collection_expected": True, "outcome_contract_version": "watch_price_path_v1",
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
                        result = _emit(event, event_id=event_id, market=market, ticker=row["ticker"], attributes=payload,
                                       event_time=datetime.fromisoformat(payload["observed_at"]))
                        if result is not None:
                            row["delivery_" + kind + "_sent"] = event_id
            _atomic(path, updated)
            try:
                from observability.watchlist_outcomes import observe_outcomes
                observe_outcomes(market, observations, frames, batch_ref, trade_date)
            except Exception:  # noqa: BLE001 - outcomes never change watch/production decisions
                _emit("watchlist.outcomes_unavailable", market=market, attributes={
                    "batch_ref": batch_ref, "mode": "SHADOW", "trading_impact": "none",
                    "reason": "outcome_hook_unavailable"})
            return observations
    except Exception:  # noqa: BLE001 - optional research cannot change batch success
        _emit("watchlist.shadow_unavailable", market=market, attributes={
            "batch_ref": batch_ref, "mode": "SHADOW", "status": "MISSING",
            "policy_version": policy_version(market), "trading_impact": "none"})
        return None


def read_ready_context(market, ticker, batch_ref):
    try:
        if market not in {"US", "KR"} or not batch_ref or not (enabled() if market == "US" else enabled(market)):
            return None
        matches = [w for w in _load(KR_STATE_PATH if market == "KR" else STATE_PATH, market)["watches"]
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
                "policy_version": policy_version(market), "asof": row["asof"],
                "input_hash": row["input_hash"], "observation_price_ref": row["input_hash"]}
    except Exception:  # noqa: BLE001 - optional exact join fails closed
        return None
