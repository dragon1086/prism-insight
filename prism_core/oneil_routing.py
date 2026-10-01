"""Actual US batch routing for explicitly approved, newly owned campaigns.

SHADOW does not intercept the baseline. LIVE admission is fail-closed: once a
configured account selects this lane, an input/error never falls through to a
legacy full-size broker order. Strategy records remain independent of fills.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time

from prism_core.oneil_adaptive_policy import _hash, _num
from prism_core.oneil_config import ConfigurationError, load, require_live_approval
from prism_core.oneil_execution import OneilExecution


def _now():
    return datetime.now(timezone.utc).isoformat()


def selected_config(account_name):
    config = load()
    return config if config["mode"] == "LIVE" and account_name in config["accounts"] else None


def owned_campaign(account_id, symbol):
    config = load(protection_only=True)
    from pathlib import Path
    if not Path(config["live_db"]).exists():
        return None
    execution = OneilExecution(config["live_db"], mode="LIVE")
    owner = execution.owner_by_symbol(account_id, symbol)
    return (execution, owner) if owner else None


async def route_owned_entry(agent, account, ticker):
    """An owned campaign never enters the unrelated legacy pyramid path."""
    try:
        import sqlite3
        try:
            agent.cursor.execute("SELECT scenario FROM us_stock_holdings WHERE account_key=? AND ticker=?",
                                 (account["account_key"], ticker))
            rows = agent.cursor.fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
            rows = []  # Legacy initialization/tests may not yet have this table.
        for row in rows:
            saved = json.loads(row[0] or "{}")
            if isinstance(saved, dict) and saved.get("_oneil_execution"):
                return True
        return owned_campaign(account["account_key"], ticker) is not None
    except Exception:
        # A corrupt configuration cannot authorize a new full-size BUY.
        return True


def _portfolio_source(agent, campaign, now):
    """Read actual rows each time; observation timestamps never refresh old rows."""
    plan, context = campaign["plan"], campaign["context"]
    position_id = campaign["position_id"]
    portfolio_valid = True
    try:
        agent.cursor.execute("SELECT id,ticker,scenario,stop_loss FROM us_stock_holdings WHERE account_key=?", (campaign["account_id"],))
        rows = agent.cursor.fetchall()
    except Exception:
        rows, portfolio_valid = [], False
    positions, sectors = [], []
    owned_position = campaign.get("strategy_position_id") or context.get("source_position_id")
    current_stop = campaign.get("current_stop", plan["initial_stop"])
    for row in rows:
        row = dict(row)
        try:
            saved = json.loads(row["scenario"] or "{}")
            if not isinstance(saved, dict):
                raise ValueError("SCENARIO_UNAVAILABLE")
        except (ValueError, TypeError):
            saved, portfolio_valid = {}, False
        row_position = f"legacy:US:{row['id']}"
        if row_position == owned_position:
            marker = saved.get("_oneil_execution", {})
            if ((campaign["mode"] == "LIVE" and marker.get("campaign_id") != campaign["campaign_id"])
                    or saved.get("_decision_id") != plan["source_decision_ref"]):
                portfolio_valid = False
            else:
                current_stop = row["stop_loss"]
                row_position = position_id
        positions.append(dict(position_id=row_position, symbol=row["ticker"],
                              source_decision_ref=saved.get("_decision_id"), account_key=campaign["account_id"]))
        if "sector" in saved:
            sectors.append(saved["sector"])
    portfolio = dict(observed_at=now, source_ref=_hash([positions, sectors, now]),
                     account_key=campaign["account_id"], positions=positions, slots_used=len(positions),
                     max_slots=agent.max_slots, scenario_sectors=sectors,
                     max_same_sector=agent.MAX_SAME_SECTOR,
                     sector_concentration_ratio=agent.SECTOR_CONCENTRATION_RATIO,
                     minimum_holdings_for_ratio=4)
    return portfolio, portfolio_valid, owned_position, current_stop


async def collect_initial_envelope(agent, campaign, *, market_provider=None,
                                   quote_provider=None, intraday_provider=None, source="regular",
                                   protection_only=False):
    from prism_core.oneil_runtime_inputs import (
        IntradayProvider, current_gates, fetch_market_snapshot, fetch_quote, quote_input,
    )
    from prism_core.oneil_current_capture import capture_current_record
    plan, context = campaign["plan"], campaign["context"]
    position_id = campaign["position_id"]
    before = datetime.now(timezone.utc)
    as_of = before.replace(minute=before.minute // 5 * 5, second=0, microsecond=0).isoformat()
    intraday = market = raw_quote = None
    now, quote = _now(), None
    portfolio, portfolio_valid, owned_position, current_stop = _portfolio_source(agent, campaign, now)
    try:
        raw_quote = await asyncio.to_thread(quote_provider or fetch_quote, plan["symbol"])
        now = _now()
        quote = quote_input(plan=plan, position_id=position_id, response=raw_quote, now=now)
    except Exception:
        quote = None
    protective = protection_only or campaign.get("status") == "EXIT_PENDING" or quote is None
    if quote is not None:
        try:
            protective = protective or _num(quote["price"], True) <= _num(current_stop, True)
        except (ValueError, TypeError):
            protective = True
    if not protective:
        try:
            intraday = await asyncio.to_thread(intraday_provider or IntradayProvider(), plan["symbol"], as_of,
                                              "NASDAQ" if context.get("exchange") == "NASD" else "NYSE")
        except Exception:
            intraday = None
        try:
            market = await asyncio.to_thread(market_provider or fetch_market_snapshot)
        except Exception:
            market = None
        portfolio, portfolio_valid, owned_position, current_stop = _portfolio_source(agent, campaign, _now())
        try:
            raw_quote = await asyncio.to_thread(quote_provider or fetch_quote, plan["symbol"])
            now = _now()
            quote = quote_input(plan=plan, position_id=position_id, response=raw_quote, now=now)
        except Exception:
            now, quote = _now(), None
    gates = None
    try:
        if not portfolio_valid:
            raise ValueError("PORTFOLIO_UNAVAILABLE")
        gates = current_gates(plan=plan, position_id=position_id, scenario=context["scenario"],
                              quote=quote, portfolio=portfolio, market=market, now=now,
                              phase="ADD" if owned_position else "NEW")
    except Exception:
        gates = None
    identity = dict(symbol=plan["symbol"], position_id=position_id,
                    source_decision_ref=plan["source_decision_ref"], price_basis_ref=plan["setup"]["price_basis_ref"])
    stop = dict(identity, current_stop=current_stop, available_at=now,
                source_ref=_hash([campaign["campaign_id"], current_stop, now]))
    result = capture_current_record(plan=plan, position_id=position_id,
        setup_input=dict(status="OK", setup=plan["setup"]), intraday_input=intraday,
        quote=quote, gates=gates, stop=stop, now=now, source=source)
    if protective:
        result["reason_codes"].append("PROTECTION_ONLY")
        result["record_hash"] = _hash({key: value for key, value in result.items() if key != "record_hash"})
    return result


async def materialize_initial(agent, execution, reserved):
    """Serialize row creation/link recovery across batch and mechanical workers."""
    import fcntl
    def acquire():
        descriptor = os.open(str(execution.store.db_path) + ".materialize.lock",
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise ValueError("STRATEGY_MATERIALIZATION_BUSY")
                time.sleep(.02)
    pending = asyncio.create_task(asyncio.to_thread(acquire))
    try:
        descriptor = await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            os.close(await pending)
        except ValueError:
            pass
        raise
    try:
        return await _materialize_initial_unlocked(agent, execution, reserved)
    finally:
        os.close(descriptor)


async def _materialize_initial_unlocked(agent, execution, reserved):
    """Create exactly one strategy row before dispatching the reserved first BUY."""
    campaign = execution.snapshot(reserved["campaign"]["campaign_id"])
    if campaign.get("strategy_position_id"):
        return campaign
    context = campaign["context"]
    if hasattr(agent, "cursor"):
        agent.cursor.execute("SELECT id,scenario FROM us_stock_holdings WHERE account_key=? AND ticker=?",
                             (campaign["account_id"], campaign["symbol"]))
        existing = agent.cursor.fetchall()
        if existing:
            if len(existing) != 1:
                raise ValueError("STRATEGY_OWNERSHIP_AMBIGUOUS")
            row = dict(existing[0])
            marker = json.loads(row["scenario"] or "{}").get("_oneil_execution", {})
            if marker.get("campaign_id") != campaign["campaign_id"]:
                raise ValueError("EXISTING_STRATEGY_NOT_ADOPTED")
            return execution.link_strategy_position(campaign["campaign_id"], f"legacy:US:{row['id']}")
    scenario = deepcopy(context["scenario"])
    scenario["_oneil_execution"] = dict(owner=campaign["plan"]["policy_version"], campaign_id=campaign["campaign_id"],
                                         position_id=campaign["position_id"], initial_arm="INITIAL_POLICY_50")
    result = await agent._buy_stock_with_position(campaign["symbol"], context.get("company_name", campaign["symbol"]),
        float(reserved["intent"].limit_price), scenario, context.get("rank_change_msg", ""), is_add=False)
    if not result.success:
        execution.release_unsubmitted(campaign["campaign_id"], reserved["intent"].id, reason="STRATEGY_ROW_NOT_RECORDED")
        raise ValueError("STRATEGY_ROW_NOT_RECORDED")
    return execution.link_strategy_position(campaign["campaign_id"], f"legacy:US:{result.legacy_holding_id}")


async def route_initial(agent, *, account, ticker, company_name, scenario, current_price,
                        report_path, rank_change_msg="", broker_factory=None, envelope_provider=None):
    result = dict(handled=False, strategy_recorded=False, campaign_id=None, reason="BASELINE")
    try:
        config = selected_config(account["name"])
    except ConfigurationError:
        return dict(result, handled=True, reason="CONFIGURATION_UNAVAILABLE")
    if config is None:
        return result
    result["handled"] = True
    try:
        from prism_core.oneil_batch_setup import load_review_sidecar
        from observability.scenario_shadow import _adaptive_setup
        from prism_core.oneil_broker import OneilBroker
        from prism_core.oneil_dispatcher import dispatch_reserved
        from prism_core.oneil_runtime_inputs import fetch_quote
        budget = account["buy_amount_usd"]
        require_live_approval(config, account=account["name"], unit_budget=budget)
        execution = OneilExecution(config["live_db"], mode="LIVE")
        existing = execution.owner_by_symbol(account["account_key"], ticker)
        if existing:
            return dict(result, campaign_id=existing["campaign_id"], reason="OWNED_CAMPAIGN_WORKER_MANAGED")
        if await agent._is_ticker_in_holdings(ticker):
            return dict(result, reason="EXISTING_POSITION_NOT_ADOPTED")
        now = _now()
        listing = await asyncio.to_thread(fetch_quote, ticker)
        if listing.get("symbol") != ticker or listing.get("currency") != "USD":
            return dict(result, reason="LISTING_IDENTITY_UNAVAILABLE")
        exchange = {"NMS": "NASD", "NGM": "NASD", "NCM": "NASD", "NYQ": "NYSE", "ASE": "AMEX"}.get(listing.get("exchange"))
        if exchange is None:
            return dict(result, reason="LISTING_EXCHANGE_UNAVAILABLE")
        decision = scenario["_decision_id"]
        linked = await asyncio.to_thread(load_review_sidecar, report_path, symbol=ticker, decision_ref=decision, as_of=now)
        setup = _adaptive_setup(linked, ticker=ticker, decision_id=decision, current_price=current_price,
                                initial_stop=scenario["stop_loss"], captured_at=now)
        if setup.get("status") != "OK":
            return dict(result, reason="INITIAL_SETUP_UNAVAILABLE")
        factory = broker_factory or OneilBroker
        async with factory(account["name"], execution.store, exchange=exchange) as broker:
            account_snapshot = await broker.holdings(ticker)
        context = dict(scenario=deepcopy(scenario),
            account_name=account["name"], company_name=company_name, report_path=str(report_path),
            rank_change_msg=rank_change_msg, exchange=exchange,
            entry_slot_context=dict(max_slots=agent.max_slots))
        campaign = execution.claim_campaign(account_id=account["account_key"],
            position_id="oneil-initial:" + _hash([account["account_key"], decision]), plan=setup["plan"],
            unit_budget=budget, account_snapshot=account_snapshot, now=_now(), context=context)
        result["campaign_id"] = campaign["campaign_id"]
        envelope = await (envelope_provider or collect_initial_envelope)(agent, campaign)
        async with factory(account["name"], execution.store, exchange=exchange) as broker:
            current_account = await broker.holdings(ticker)
        reserved = execution.evaluate(campaign["campaign_id"], envelope, expected_revision=campaign["revision"],
                                      account_snapshot=current_account, evaluation_at=_now())
        if not reserved.get("intent"):
            return dict(result, reason=reserved["status"])
        require_live_approval(load(), account=account["name"], unit_budget=budget)
        await materialize_initial(agent, execution, reserved)
        result["strategy_recorded"] = True
        def authorize_add():
            latest = selected_config(account["name"])
            if latest is None or latest["live_db"] != config["live_db"]:
                return False
            require_live_approval(latest, account=account["name"], unit_budget=budget)
            return True
        await dispatch_reserved(execution, reserved, account_name=account["name"], broker_factory=factory,
                                authorize_add=authorize_add)
        return dict(result, reason="OWNED_INITIAL_DISPATCHED")
    except Exception:
        return dict(result, reason="OWNED_INITIAL_UNAVAILABLE")


def route_exit(agent, stock_data, reason):
    """Latch durable protection before strategy closure; callers skip legacy orders."""
    scenario = stock_data.get("scenario") or {}
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario)
        except ValueError:
            scenario = {}
    try:
        marker = scenario.get("_oneil_execution") if isinstance(scenario, dict) else None
        account_id = stock_data.get("account_key") or agent._account_scope()[0]
        if marker:
            stock_data["_oneil_owned_exit"] = True
            config = load(protection_only=True)
            execution = OneilExecution(config["live_db"], mode="LIVE")
            campaign = execution.snapshot(marker["campaign_id"])
            linked = campaign.get("strategy_position_id")
            if (campaign["account_id"] != account_id or campaign["symbol"] != stock_data["ticker"]
                    or (linked and linked != f"legacy:US:{stock_data.get('id')}")):
                raise ValueError("OWNED_EXIT_IDENTITY_UNAVAILABLE")
            owner = (execution, campaign)
        else:
            owner = owned_campaign(account_id, stock_data["ticker"])
        if not marker and owner is None:
            return False
        stock_data["_oneil_owned_exit"] = True
        if owner is None or (marker and marker.get("campaign_id") != owner[1]["campaign_id"]):
            raise ValueError("OWNED_EXIT_IDENTITY_UNAVAILABLE")
        execution, campaign = owner
        at = _now()
        price = str(_num(stock_data["current_price"], True))
        reference = dict(at=at, price=price, source_ref=_hash([
            campaign["campaign_id"], stock_data.get("id"), reason, price, at]))
        execution.request_exit(campaign["campaign_id"], reason=reason, reference=reference)
        return True
    except Exception:
        if stock_data.get("_oneil_owned_exit") or (isinstance(scenario, dict) and scenario.get("_oneil_execution")):
            stock_data["_oneil_owned_exit"] = True
            raise ValueError("OWNED_EXIT_LATCH_FAILED") from None
        # An optional ownership lookup cannot suppress an unrelated legacy exit.
        # Every materialized owned row has the durable marker checked above.
        return False


_UNSUBMITTED = frozenset({"NEVER_SUBMITTED_LOCAL_CAS", "PREFLIGHT_NOT_SUBMITTED"})


def strategy_entry_from_orders(orders):
    """Allocation-weighted strategy entry of an owned campaign.

    Each submitted BUY step raises the target allocation at its reserved limit
    price; like the legacy strategy row, the ledger is independent of broker
    fills. Steps released before submission never happened. Returns
    ``(entry_price, allocation)`` or None when no countable step exists.
    """
    steps = []
    for order in orders or []:
        intent = order.get("intent") or {}
        if intent.get("side") != "BUY" or order.get("cancellation_basis") in _UNSUBMITTED:
            continue
        steps.append((order.get("reserved_at") or "", _num(order["target_allocation"], True),
                      _num(intent["limit_price"], True)))
    deployed, cost_units = Decimal(0), Decimal(0)
    for _, target, price in sorted(steps, key=lambda step: step[0]):
        if target <= deployed:
            continue
        cost_units += (target - deployed) / price
        deployed = target
    if not deployed or deployed > 1:
        return None
    return deployed / cost_units, deployed


def owned_strategy_entry(scenario):
    """Strategy entry/allocation for an owned row's exit record, or None."""
    scenario = json.loads(scenario or "{}") if isinstance(scenario, str) else scenario
    marker = (scenario or {}).get("_oneil_execution") if isinstance(scenario, dict) else None
    if not marker:
        return None
    config = load(protection_only=True)
    state = OneilExecution(config["live_db"], mode="LIVE").snapshot(marker["campaign_id"])
    return strategy_entry_from_orders(state.get("orders"))


def mark_owned_strategy_exit(stock_data):
    """Call only after the independent strategy history/delete commit succeeds."""
    if not stock_data.get("_oneil_owned_exit"):
        return
    scenario = stock_data.get("scenario") or {}
    scenario = json.loads(scenario) if isinstance(scenario, str) else scenario
    marker = scenario.get("_oneil_execution") or {}
    config = load(protection_only=True)
    execution = OneilExecution(config["live_db"], mode="LIVE")
    state = execution.snapshot(marker["campaign_id"])
    if (state["account_id"] != stock_data["account_key"] or state["symbol"] != stock_data["ticker"]
            or state.get("strategy_position_id") != f"legacy:US:{stock_data['id']}"):
        raise ValueError("STRATEGY_EXIT_MARK_IDENTITY_MISMATCH")
    if state.get("strategy_exit_recorded"):
        return
    reference = state["strategy_exit_reference"]
    execution.mark_strategy_exit(state["campaign_id"], at=reference["at"], source_ref=reference["source_ref"])


async def finalize_owned_strategy(agent, execution, cid, price, reason):
    """Close only the exact strategy row, independently of broker settlement."""
    state = execution.snapshot(cid)
    position = state.get("strategy_position_id")
    if not position or not position.startswith("legacy:US:"):
        return False
    row_id = int(position.removeprefix("legacy:US:"))
    agent.cursor.execute("SELECT * FROM us_stock_holdings WHERE id=? AND ticker=? AND account_key=?",
                         (row_id, state["symbol"], state["account_id"]))
    row = agent.cursor.fetchone()
    if row is None:
        return False
    stock = dict(row)
    scenario = json.loads(stock["scenario"] or "{}")
    marker = scenario.get("_oneil_execution") or {}
    if (marker.get("campaign_id") != cid
            or scenario.get("_decision_id") != state["plan"]["source_decision_ref"]):
        raise ValueError("STRATEGY_FINALIZE_IDENTITY_MISMATCH")
    stock["current_price"] = float(_num(price, True))
    # sell_stock itself only writes strategy history; its callers own broker
    # effects. Calling it here never invokes update_holdings/legacy execution.
    return await agent.sell_stock(stock, reason)
