"""B3 micro-split LIVE (KR/US): fractional first entry and in-slot adds on one holding row.

The holding row's ``scenario["micro_split"]`` is the single source of truth for
the strategy position: current slot allocation (0..1), the unit amount of one
slot, and every leg (allocation, price, time). The row's ``buy_price`` stays the
initial entry; trading_history records the cost-weighted entry of the legs. Strategy records stay independent of broker
fills, like the legacy full-slot entry. OFF unless MICRO_SPLIT_LIVE_ENABLED.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from decimal import ROUND_DOWN, Decimal

from prism_core.slot_weight import slot_fraction

SCENARIO_KEY = "micro_split"
CONTRACT = "micro-split-live-v1"


def live_enabled(market):
    flag = os.getenv("MICRO_SPLIT_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    markets = {m.strip().upper() for m in os.getenv("MICRO_SPLIT_LIVE_MARKETS", "KR,US").split(",") if m.strip()}
    return flag and str(market).upper() in markets


def plan_adds_enabled(market):
    """Scenario-based (add_plan) LIVE adds; on with micro-split LIVE.

    ``MICRO_SPLIT_LIVE_ADDS_ENABLED=false`` is the emergency kill switch for adds
    only (the fractional first entry stays LIVE). The fixed +2%/+4% ladder never
    places LIVE orders since 2026-10-02 (docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md).
    """
    flag = os.getenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    return flag and live_enabled(market)


def _dec(value):
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("positive finite number required")
    return number


def scaled_cash(unit_amount, allocation, market):
    """Order notional for an allocation of one slot (KRW whole won, USD cents), rounded down."""
    quantum = Decimal(1) if str(market).upper() == "KR" else Decimal("0.01")
    value = (_dec(unit_amount) * _dec(allocation)).quantize(quantum, rounding=ROUND_DOWN)
    if value <= 0:
        raise ValueError("allocation below one currency unit")
    return int(value) if quantum == 1 else float(value)


# Conviction tilt (2026-10-04, user-approved design 2): top setups start one step bigger.
# The B3 plan (and its plan_hash, the B3 virtual ledger) keeps the volatility-based initial;
# the tilt is stored explicitly on the holding's micro_split block, which every LIVE consumer
# reads (order cash, slots, messages, dashboard, journal, signals, add rails).
CONVICTION_TRIGGERS = {"KR": frozenset({"일중 상승률 상위주", "갭 상승 모멘텀 상위주"}),
                       "US": frozenset({"Intraday Rise Top", "Gap Up Momentum Top"})}
CONVICTION_MIN_SCORE = Decimal(8)
CONVICTION_STEP = Decimal("0.20")
CONVICTION_CAP = Decimal("0.80")
CONVICTION_REASON = "CONVICTION_TOP_SETUP"


def conviction_tilt_enabled():
    """Kill switch MICRO_SPLIT_CONVICTION_TILT (default on)."""
    return os.getenv("MICRO_SPLIT_CONVICTION_TILT", "true").strip().lower() not in {"0", "false", "no", "off"}


def conviction_trigger(market, trigger_type):
    """True when the trigger is in the market's strong set (score not checked)."""
    return str(trigger_type or "").strip() in CONVICTION_TRIGGERS.get(str(market).upper(), frozenset())


def conviction_tilt(base, *, market, buy_score, trigger_type):
    """{"base", "tilted", "reason", ...} for a top setup, else None.

    Top setup: buy_score >= 8 AND a strong trigger. Tilted = min(0.80, base + 0.20); a base
    already at the cap gets no record (nothing changes).
    """
    if not conviction_tilt_enabled() or not conviction_trigger(market, trigger_type):
        return None
    if isinstance(buy_score, bool):
        return None
    try:
        score = Decimal(str(buy_score))
    except ArithmeticError:
        return None
    if not score.is_finite() or score < CONVICTION_MIN_SCORE:
        return None
    base = _dec(base)
    tilted = min(CONVICTION_CAP, base + CONVICTION_STEP)
    if tilted <= base:
        return None
    return {"base": str(base), "tilted": str(tilted), "reason": CONVICTION_REASON,
            "buy_score": float(score), "trigger_type": str(trigger_type).strip()}


def entry_record(*, plan, unit_amount, market, entered_at, tilt=None):
    """The micro_split block stored on the new holding's scenario.

    With a conviction ``tilt`` the first leg and the allocation are the tilted value and the
    tilt is recorded next to the B3 plan hash (which still describes the base initial).
    """
    initial = tilt["tilted"] if tilt else plan["initial_nominal"]
    block = {"contract": CONTRACT, "policy_version": plan["policy_version"], "plan_hash": plan["plan_hash"],
             "market": str(market).upper(), "unit_amount": str(_dec(unit_amount)), "allocation": str(_dec(initial)),
             "legs": [{"kind": "INITIAL", "allocation": str(_dec(initial)),
                       "price": str(_dec(plan["entry_reference"])), "at": entered_at}]}
    if tilt:
        block["conviction_tilt"] = dict(tilt)
    return block


def record(scenario):
    block = (scenario or {}).get(SCENARIO_KEY) if isinstance(scenario, dict) else None
    return block if isinstance(block, dict) and block.get("contract") == CONTRACT else None


def allocation(scenario):
    """Current slot allocation of a micro-split holding, or None for legacy rows."""
    block = record(scenario)
    if block is None:
        return None
    try:
        value = _dec(block["allocation"])
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    return float(value) if value <= 1 else None


def weighted_entry(legs):
    """Cost-weighted entry: allocation-weighted harmonic mean of leg prices."""
    total = sum((_dec(leg["allocation"]) for leg in legs), Decimal(0))
    units = sum((_dec(leg["allocation"]) / _dec(leg["price"]) for leg in legs), Decimal(0))
    return total / units


def apply_add(scenario, *, delta, price, at, bar_end, intent_id=None, extra=None):
    """Return (new_scenario, new_buy_price) after an in-slot add; never above one slot.

    ``extra`` (scenario id, lens, plan hash, session) is recorded on the add leg.
    """
    updated = deepcopy(scenario)
    block = record(updated)
    if block is None:
        raise ValueError("not a micro-split holding")
    target = _dec(block["allocation"]) + _dec(delta)
    if target > 1:
        raise ValueError("add would exceed one slot")
    if any(leg.get("bar_end") == bar_end for leg in block["legs"]):
        raise ValueError("bar already used for an add")
    block["legs"].append(dict(extra or {}, kind="ADD", allocation=str(_dec(delta)), price=str(_dec(price)), at=at,
                              bar_end=bar_end, intent_id=intent_id))
    block["allocation"] = str(target)
    return updated, float(weighted_entry(block["legs"]))


def display(scenario):
    """Short Korean label for messages/dashboard, e.g. '비중 35%' or '비중 80% (35%→80%)'."""
    block = record(scenario)
    if block is None:
        return None
    current = round(float(block["allocation"]) * 100)
    first = round(float(block["legs"][0]["allocation"]) * 100)
    return f"비중 {current}%" if current == first else f"비중 {current}% ({first}%→{current}%)"


def prepare_entry(agent, *, market, ticker, current_price, scenario, decision_ref, account, logger=None,
                  trigger_type=None):
    """Size a LIVE first entry: (plan, cash_amount, scenario_with_record) or (None, None, scenario).

    Called with the refreshed execution price, before the strategy row and the
    broker order. When LIVE is off or the plan cannot be built (no ATR/stop),
    the legacy full-slot entry is unchanged and the reason is logged. A top setup
    (scenario buy_score >= 8 and a strong ``trigger_type``) starts with the conviction
    tilt; callers pass no trigger type for entries outside the regular batch (re-entry).
    """
    if not live_enabled(market):
        return None, None, scenario
    try:
        from observability.b3_ae_capture import build_plan
        unit = (account or {}).get("buy_amount_krw" if str(market).upper() == "KR" else "buy_amount_usd")
        plan = build_plan(agent, market=str(market).upper(), ticker=ticker, entry_price=current_price,
                          stop_loss=scenario.get("stop_loss"), decision_ref=decision_ref)
        tilt = conviction_tilt(plan["initial_nominal"], market=market, buy_score=scenario.get("buy_score"),
                               trigger_type=trigger_type)
        initial = tilt["tilted"] if tilt else plan["initial_nominal"]
        cash = scaled_cash(unit, initial, market)
        updated = dict(scenario)
        updated[SCENARIO_KEY] = entry_record(plan=plan, unit_amount=unit, market=market,
                                             entered_at=plan["created_at"], tilt=tilt)
        attach_buy_add_plan(updated, market=market, ticker=ticker, raw=updated.pop("add_plan", None), logger=logger)
        if logger:
            logger.warning("[MICRO_SPLIT][%s] %s initial=%s b3_initial=%s tilt=%s cash=%s", market, ticker, initial,
                           plan["initial_nominal"], (tilt or {}).get("reason"), cash)
        return plan, cash, updated
    except Exception as error:  # noqa: BLE001 - legacy full slot when B3 cannot size the entry
        if logger:
            logger.warning("[MICRO_SPLIT][%s] %s unavailable, legacy full slot: %s", market, ticker, error)
        return None, None, scenario


def attach_buy_add_plan(scenario, *, market, ticker, raw, logger=None):
    """Validate the BUY agent's ``add_plan`` and store it on the new micro_split block (in place).

    An invalid or missing plan leaves the position without adds until the next
    holdings review provides one. With a conviction tilt the targets (written for the
    volatility initial, since the prompt never mentions the tilt) are rebased by
    (tilted - base) before the usual validation.
    """
    from prism_core import add_plan

    block = scenario[SCENARIO_KEY]
    created = block["legs"][0]["at"]
    tilt = block.get("conviction_tilt") or {}
    rebase = _dec(tilt["tilted"]) - _dec(tilt["base"]) if tilt.get("tilted") else None
    if raw is None:
        plan, issues = None, [{"id": None, "reason": "PLAN_MISSING"}]
    else:
        plan, issues = add_plan.validate_plan(
            raw, market=market, source="BUY", created_at=created, valid_for=add_plan.buy_valid_for(market, created),
            allocation=block["allocation"], last_step=block["allocation"], rebase=rebase)
    add_plan.store(block, plan, issues, raw=raw, source="BUY", created_at=created)
    _emit_planned(market=market, ticker=ticker, position_id=None, block=block, plan=plan, issues=issues)
    if logger:
        logger.warning("[MICRO_SPLIT][%s] %s add_plan source=BUY status=%s scenarios=%s dropped=%s", market, ticker,
                       (plan or {}).get("status", "INVALID"), [s["id"] for s in (plan or {}).get("scenarios", [])],
                       [i["reason"] for i in issues])
    return plan


def _emit_planned(*, market, ticker, position_id, block, plan, issues):
    try:
        from observability.events import emit_event
        scenarios = (plan or {}).get("scenarios", [])
        emit_event("micro_split.add_planned", service=f"prism-{str(market).lower()}-micro-split",
                   market=str(market).upper(), ticker=ticker, position_id=position_id,
                   attributes={"slot_allocation": float(block["allocation"]), "plan_hash": (plan or {}).get("plan_hash"),
                               "plan_status": (plan or {}).get("status", "INVALID"),
                               "plan_source": (plan or {}).get("source"), "valid_for": (plan or {}).get("valid_for"),
                               "scenario_ids": [s["id"] for s in scenarios],
                               "lens": sorted({x for s in scenarios for x in s["lens"]}),
                               "dropped": [i["reason"] for i in issues]})
    except Exception as error:  # noqa: BLE001 - observability never affects trading
        import logging
        logging.getLogger(__name__).warning("[MICRO_SPLIT][%s] add_planned event skipped: %s", market, error)


def entry_message_line(scenario, market):
    """Buy-message line for a micro-split entry (allocation and the add scenarios), or ''."""
    from prism_core import add_plan

    block = record(scenario)
    if block is None:
        return ""
    us = str(market).upper() == "US"
    pct = round(float(block["allocation"]) * 100)
    tilt = block.get("conviction_tilt") or {}
    base = round(float(tilt["base"]) * 100) if tilt.get("base") else None
    plan = block.get("add_plan") or {}
    labels = [add_plan.scenario_label(s, market, "en" if us else "ko") for s in plan.get("scenarios") or []]
    if not plan_adds_enabled(market):
        adds = "adds are currently paused" if us else "추가 매수는 현재 멈춰 있고"
    elif labels:
        adds = (f"add scenarios: {'; '.join(labels)} (adds only when a scenario is confirmed)" if us
                else f"증액 시나리오: {'; '.join(labels)} (조건이 확인될 때만 증액하며)")
    else:
        adds = ("no add scenario yet; the next holdings review sets one" if us
                else "증액 시나리오는 다음 보유 점검에서 세우며")
    if us:
        tilted = f" (top-setup tilt from {base}%)" if base is not None else ""
        return (f"Micro-split allocation: {pct}% of one slot{tilted} — {adds}; a stop exits the whole position. "
                "Whole shares are rounded down.\n")
    tilted = f", 상위 셋업 가중으로 기본 {base}%에서 상향" if base is not None else ""
    return (f"초분할 비중: {pct}% (1슬롯 기준{tilted}) — {adds}, 손절 시 전량 매도합니다. "
            "정수 수량 내림으로 실제 체결 비중은 조금 낮을 수 있습니다.\n")


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def review_prompt_block(scenario, *, market, language, now=None, stop_loss=None):
    """Holdings-review appendix for a micro-split holding while LIVE is on, else ''."""
    if not live_enabled(market):
        return ""
    try:
        loaded = json.loads(scenario or "{}") if isinstance(scenario, str) else scenario
        block = record(loaded)
        if block is None:
            return ""
        from prism_core import add_plan, add_plan_prompts
        return add_plan_prompts.review_block(block, market=market, language=language,
                                             valid_for=add_plan.review_valid_for(market, now or _utc_now()),
                                             stop_loss=stop_loss)
    except Exception:  # noqa: BLE001 - a broken record never breaks the sell review
        return ""


def add_plan_buy_block(agent, *, market, ticker, language):
    """BUY appendix asking for add_plan while micro-split LIVE is on, else ''."""
    if not live_enabled(market):
        return ""
    from prism_core import add_plan_prompts
    return add_plan_prompts.buy_block(market, language, expected_initial=_expected_initial(agent, market, ticker))


def _expected_initial(agent, market, ticker):
    """B3 first allocation estimated from the decision-time daily bars (None when unavailable)."""
    try:
        from datetime import datetime, timezone

        from observability.b3_ae_capture import atr14
        from prism_core.oneil_adaptive_policy import MARKETS, initial_sizing
        captured = (getattr(agent, "_decision_input_bars", {}) or {}).get(ticker)
        if not captured or captured.get("market") != str(market).upper():
            return None
        today = datetime.now(timezone.utc).astimezone(MARKETS[str(market).upper()][0]).date()
        atr, _ = atr14(captured["bars"], today)
        return float(initial_sizing(captured["bars"][-1]["close"], round(atr, 6))[1])
    except Exception:  # noqa: BLE001 - the estimate is optional prompt context
        return None


def apply_review(agent, *, market, row_id, ticker, decision, now=None, logger=None):
    """Store the review's next_session_add_plan on a micro-split holding (BEGIN IMMEDIATE row update).

    A sell decision cancels the stored plan (no add on a sell day). A missing key
    keeps the stored plan, which is valid for its own session only. Returns the status.
    """
    if not live_enabled(market) or row_id is None or not isinstance(decision, dict):
        return "SKIPPED"
    if getattr(agent, "_no_order_effects", None) is not None:
        return "SKIPPED"
    from prism_core import add_plan

    market, now = str(market).upper(), now or _utc_now()
    selling = bool(decision.get("should_sell"))
    raw = {"cancel": True, "reason": "SELL_DECISION"} if selling else decision.get("next_session_add_plan")
    if raw is None:
        return "NO_PLAN_KEY"
    agent.conn.commit()
    agent.conn.execute("BEGIN IMMEDIATE")
    try:
        row = agent.cursor.execute(_SCENARIO_SQL[market], (row_id,)).fetchone()
        scenario = json.loads((row[0] if row else None) or "{}")
        block = record(scenario)
        if block is None:
            agent.conn.rollback()
            return "NOT_MICRO_SPLIT"
        valid_for = add_plan.review_valid_for(market, now)
        if selling:  # a sell decision also cancels the plan of the session in progress
            valid_for = (block.get("add_plan") or {}).get("valid_for") or valid_for
        plan, issues = add_plan.validate_plan(
            raw, market=market, source="SELL_DECISION" if selling else "REVIEW", created_at=now,
            valid_for=valid_for, allocation=block["allocation"], last_step=block["legs"][-1]["allocation"])
        add_plan.store(block, plan, issues, raw=raw, source="REVIEW", created_at=now)
        agent.cursor.execute(_UPDATE_SQL[market], (json.dumps(scenario, ensure_ascii=False), row_id))
        agent.conn.commit()
    except Exception:
        agent.conn.rollback()
        raise
    _emit_planned(market=market, ticker=ticker, position_id=f"legacy:{market}:{row_id}", block=block, plan=plan,
                  issues=issues)
    status = (plan or {}).get("status", "INVALID")
    if logger:
        logger.warning("[MICRO_SPLIT][%s] %s row=%s add_plan source=REVIEW status=%s valid_for=%s dropped=%s", market,
                       ticker, row_id, status, valid_for, [i["reason"] for i in issues])
    return status


_ROW_SQL = {
    "KR": "SELECT id, ticker, company_name, account_key, scenario, buy_price, stop_loss FROM stock_holdings "
          "WHERE id=? AND ticker=? AND account_key=?",
    "US": "SELECT id, ticker, company_name, account_key, scenario, buy_price, stop_loss FROM us_stock_holdings "
          "WHERE id=? AND ticker=? AND account_key=?",
}
_UPDATE_SQL = {
    "KR": "UPDATE stock_holdings SET scenario=? WHERE id=?",
    "US": "UPDATE us_stock_holdings SET scenario=? WHERE id=?",
}
_SCENARIO_SQL = {
    "KR": "SELECT scenario FROM stock_holdings WHERE id=?",
    "US": "SELECT scenario FROM us_stock_holdings WHERE id=?",
}


def add_order_status(result, market):
    """Plain order status for the add message (reason codes stay in logs and the add_executed event)."""
    us = str(market).upper() == "US"
    if result.get("success"):
        return "Submitted (fill not yet confirmed)" if us else "주문 접수(체결은 별도 확인)"
    if result.get("reason_code") == "micro_split_add_below_one_share":
        return ("Not placed: below one share (strategy allocation recorded)" if us
                else "1주 미만이라 주문하지 않았습니다(전략 비중에는 반영)")
    return ("Not placed (strategy allocation recorded)" if us
            else "주문이 접수되지 않았습니다(전략 비중에는 반영)")


def add_message(*, market, company_name, ticker, before, after, price, average, order_status, scenario=None,
                stop_loss=None):
    """Telegram text for an executed in-slot add (KR Korean, US English like other US trade texts)."""
    from prism_core.add_plan import TYPE_LABELS

    old, new = round(before * 100), round(after * 100)
    us = str(market).upper() == "US"

    def money(value):
        return f"${value:,.2f}" if us else f"{value:,.0f}원"

    why = ""
    if scenario:
        name = TYPE_LABELS.get(scenario.get("scenario_type"), ("", ""))[1 if us else 0]
        rationale = (scenario.get("rationale") or "")[:80]
        if name:
            why = (f"Why: {name} condition confirmed" if us else f"근거: {name} 조건 확인")
            why += (f" — {rationale}" if rationale else "") + "\n"
        if scenario.get("rail") == "ACCELERATION":
            accel = scenario.get("acceleration") or {}
            gain, pace = accel.get("gain_pct"), accel.get("volume_pace")
            facts = ((f" (+{gain:.1f}% vs initial entry, volume {pace:.1f}x usual)" if us else
                      f" (최초 매수가 대비 +{gain:.1f}%, 거래량 평소의 {pace:.1f}배)") if gain is not None and pace else "")
            why += (f"Acceleration: second add today{facts}\n" if us else
                    f"가속 구간: 오늘 두 번째 추가 매수{facts}\n")
    stop = ""
    if stop_loss:
        stop = (f"Stop Loss: {money(float(stop_loss))} (a stop exits the whole position)\n" if us else
                f"손절가: {money(float(stop_loss))} (손절 시 전량 매도)\n")
    if us:
        return (f"📈 Position Add: {company_name}({ticker})\n"
                f"Allocation: {old}% → {new}% of one slot\nAdd Price: {money(price)}\n"
                f"Average Entry: {money(average)}\n{stop}{why}Order: {order_status}\n")
    return (f"📈 추가 매수(비중 확대): {company_name}({ticker})\n"
            f"비중: {old}% → {new}% (1슬롯 기준)\n추가 매수가: {money(price)}\n"
            f"평균 매수가: {money(average)}\n{stop}{why}주문: {order_status}\n")


def add_signal_fields(*, market, campaign, meta, before, after, price, stop_loss, result, signal_id):
    """Payload of the ADD trading signal (Redis/GCP). ``delta_fraction`` is of one slot, the same basis as a
    BUY's ``position_fraction``; ``signal_id`` is unique per add (the order intent's decision id)."""
    return {"market": str(market).upper(), "signal_id": signal_id, "position_id": campaign["position_id"],
            "allocation_before": round(float(before), 4), "allocation_after": round(float(after), 4),
            "delta_fraction": round(float(after) - float(before), 4), "limit_price": price,
            "stop_loss": float(stop_loss) if stop_loss else None, "scenario_type": meta.get("scenario_type"),
            "acceleration": meta.get("rail") == "ACCELERATION", "trade_success": bool(result.get("success")),
            "trade_message": str(result.get("message") or "")[:200]}


async def publish_add_signal(*, ticker, company_name, price, fields):
    """Publish the ADD on Redis Streams and GCP Pub/Sub; best effort, never raises.

    Each publisher no-ops when unconfigured and refuses when PRISM_DISABLE_SIGNAL_PUBLISH is set
    (messaging/publish_guard.py)."""
    import importlib
    import logging

    for name in ("messaging.redis_signal_publisher", "messaging.gcp_pubsub_signal_publisher"):
        try:
            module = importlib.import_module(name)
            await module.publish_add_signal(ticker=ticker, company_name=company_name, price=price, fields=fields)
        except Exception as error:  # noqa: BLE001 - a signal failure never affects the recorded add
            logging.getLogger(__name__).warning("[MICRO_SPLIT] add signal via %s failed (non-critical): %s",
                                                name, error)


def primary_account_key(agent):
    """Account key of the primary (first configured) account, or None."""
    accounts = getattr(agent, "account_configs", None) or []
    return accounts[0].get("account_key") if accounts and isinstance(accounts[0], dict) else None


async def execute_add(agent, *, market, campaign, decision, now, chat_id=None):
    """Apply a qualified add-plan add LIVE: holding row (strategy ledger) -> KIS add order -> message/event.

    Only scenario-based decisions (``decision["add_plan"]``) are executed, and only
    while the row still carries the same plan; the key (plan session + scenario id)
    is the leg's ``bar_end``, so a scenario adds at most once per session (plus one
    ``:acceleration`` continuation in an ACCELERATION session, recorded as ``rail``).
    The holding row's scenario is updated first and independently of the broker
    fill, like the legacy entry. ``buy_price`` stays the initial entry (positions
    mirror, -7% stop basis and SHADOW parity); the cost-weighted entry is recorded
    at exit by ``exit_basis``. The order notional is the allocation delta of one slot.
    """
    from observability.events import emit_event
    from prism_core.execution_service import ExecutionService
    from prism_core.oneil_adaptive_policy import _hash
    from prism_core.order_budget import whole_share_quantity
    from prism_core.order_intents import OrderIntent

    market = str(market).upper()
    meta = decision.get("add_plan")
    if not isinstance(meta, dict) or not meta.get("plan_hash") or not meta.get("scenario_id"):
        return {"status": "NOT_A_PLAN_ADD"}  # the fixed ladder never orders LIVE
    row_id = int(str(campaign["position_id"]).rsplit(":", 1)[1])
    account = next(a for a in agent.account_configs if a.get("account_key") == campaign["account_key"])
    agent._set_active_account(account)
    agent.conn.commit()
    agent.conn.execute("BEGIN IMMEDIATE")
    try:
        row = agent.cursor.execute(_ROW_SQL[market], (row_id, campaign["symbol"], campaign["account_key"])).fetchone()
        if row is None:
            agent.conn.rollback()
            return {"status": "ROW_MISSING"}
        row = dict(row)
        scenario = json.loads(row["scenario"] or "{}")
        block = record(scenario)
        if block is None:
            agent.conn.rollback()
            return {"status": "NOT_MICRO_SPLIT"}
        from prism_core.add_plan import plan_for_session
        if (plan_for_session(block, meta.get("session")) or {}).get("plan_hash") != meta["plan_hash"]:
            agent.conn.rollback()
            return {"status": "PLAN_CHANGED"}
        before = float(block["allocation"])
        delta = _dec(decision["target_allocation"]) - _dec(block["allocation"])
        price = float(_dec(decision["price"]))
        extra = {k: meta.get(k) for k in ("scenario_id", "scenario_type", "lens", "plan_hash", "session",
                                          "trigger_price")}
        extra["source"] = "add_plan"
        if meta.get("rail"):  # second add of an ACCELERATION session (reason code on the leg)
            extra.update(rail=meta["rail"], acceleration=meta.get("acceleration"))
        updated, average = apply_add(scenario, delta=delta, price=price, at=now, bar_end=decision["bar_end"],
                                     extra=extra)
        agent.cursor.execute(_UPDATE_SQL[market], (json.dumps(updated, ensure_ascii=False), row_id))
        agent.conn.commit()
    except Exception:
        agent.conn.rollback()
        raise
    after = float(updated[SCENARIO_KEY]["allocation"])
    cash = scaled_cash(block["unit_amount"], delta, market)
    intent = OrderIntent.create(
        market=market, account_id=campaign["account_key"], symbol=campaign["symbol"], side="buy",
        order_style="smart", source="micro_split_add",
        source_decision_id=_hash(["micro-split-add", campaign["campaign_id"], decision["bar_end"]]),
        source_position_id=campaign["position_id"], cash_amount=cash, limit_price=price,
        reason="Micro-split add")
    if whole_share_quantity(cash, price) <= 0:
        result = {"success": False, "status": "blocked_budget", "reason_code": "micro_split_add_below_one_share",
                  "message": "Add budget below one share; strategy allocation recorded", "quantity": 0}
    else:
        service = (ExecutionService.domestic if market == "KR" else ExecutionService.us)
        async with service(account_name=account["name"], db_path=agent.db_path) as trading:
            kwargs = {"stock_code": campaign["symbol"]} if market == "KR" else {"ticker": campaign["symbol"]}
            result = await trading.execute_buy(buy_amount=cash, limit_price=price, intent=intent,
                                               strict_budget=True, **kwargs)
    # One channel message per add: a secondary account's add of the same position sends no second notice.
    announce = campaign["account_key"] == (primary_account_key(agent) or campaign["account_key"])
    if announce:
        agent.message_queue.append(add_message(
            market=market, company_name=row.get("company_name") or campaign["symbol"], ticker=campaign["symbol"],
            before=before, after=after, price=price, average=average, order_status=add_order_status(result, market),
            scenario=meta, stop_loss=row.get("stop_loss")))
        agent._msg_types.append("analysis")
        if chat_id:
            await agent.send_telegram_message(chat_id, await_broadcast=True)
        # Subscribers mirror the strategy ledger (sim = broadcast), so the ADD is published once the
        # leg is recorded, whatever the broker did (trade_success says what our own order did).
        try:
            fields = add_signal_fields(market=market, campaign=campaign, meta=meta, before=before, after=after,
                                       price=price, stop_loss=row.get("stop_loss"), result=result,
                                       signal_id=intent.source_decision_id)
        except (KeyError, TypeError, ValueError) as error:
            import logging
            logging.getLogger(__name__).warning("[MICRO_SPLIT][%s] add signal skipped: %s", market, error)
            fields = None
        if fields:
            await publish_add_signal(ticker=campaign["symbol"], price=price, fields=fields,
                                     company_name=row.get("company_name") or campaign["symbol"])
    emit_event("micro_split.add_executed", service=f"prism-{market.lower()}-micro-split", market=market,
               ticker=campaign["symbol"], position_id=campaign["position_id"],
               attributes={"campaign_id": campaign["campaign_id"], "slot_allocation": after,
                           "scenario_id": meta["scenario_id"], "scenario_type": meta.get("scenario_type"),
                           "lens": meta.get("lens"), "plan_hash": meta["plan_hash"],
                           "rail": meta.get("rail"), "acceleration": meta.get("acceleration"),
                           "allocation_before": before,
                           "allocation_after": after, "add_price": price, "average_entry": average,
                           "order_cash": cash, "intent_id": intent.id, "broker_success": bool(result.get("success")),
                           "broker_status": result.get("status") or result.get("intent_status"),
                           "broker_quantity": result.get("quantity"),
                           "broker_reason": result.get("reason_code") or result.get("message"),
                           "planned_target": decision.get("target_allocation"), "trigger_price": meta.get("trigger_price"),
                           "session": meta.get("session")})
    return {"status": "EXECUTED", "allocation": after, "cash": cash, "broker": result, "announced": announce}


def allocation_line(scenario, *, profit_rate=None, current_price=None, market="KR", indent="", language=None):
    """Allocation line for partial positions ('' for a full slot), e.g.
    '비중 80% (35%→80%) (1슬롯 기준) / 평균 매수가 10,100원 / 슬롯 기준 손익 +1.58%'.

    With adds, the return is measured from the cost-weighted entry when
    ``current_price`` is given (the row's buy_price stays the initial entry).
    ``market`` picks the currency; ``language`` ("ko"/"en", default by market) the labels.
    """
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario or "{}")
        except ValueError:
            scenario = {}
    fraction = slot_fraction(scenario)
    block = record(scenario)
    if fraction >= 1 and block is None:
        return ""
    usd = str(market).upper() == "US"
    us = (language or ("en" if usd else "ko")) == "en"
    label = display(scenario) or f"비중 {round(fraction * 100)}%"
    parts = [label.replace("비중", "Allocation") + " of one slot" if us else label + " (1슬롯 기준)"]
    if block is not None and len(block["legs"]) > 1:
        average = float(weighted_entry(block["legs"]))
        price = f"${average:,.2f}" if usd else f"{average:,.0f}원"
        parts.append(f"Average entry {price}" if us else f"평균 매수가 {price}")
        if current_price:
            profit_rate = (float(current_price) / average - 1) * 100
    if profit_rate is not None:
        weighted = profit_rate * fraction
        parts.append(f"Slot-weighted P&L {weighted:+.2f}%" if us else f"슬롯 기준 손익 {weighted:+.2f}%")
    return indent + " / ".join(parts) + "\n"


def used_slots(scenarios):
    """Sum of slot allocations over holdings (each legacy row counts as 1)."""
    return sum(slot_fraction(s) for s in scenarios)


def keep_fresh_record(cursor, market, row_id, scenario):
    """Before rewriting a holding's scenario from an older snapshot (e.g. the highest_price ratchet),
    carry over the row's current micro_split block so a worker add or plan written meanwhile is not lost,
    and the row's current runner-hold block (prism_core.runner_hold) for the same reason."""
    from prism_core.runner_hold import SCENARIO_KEY as RUNNER_KEY, record as runner_record

    if row_id is None or not isinstance(scenario, dict):
        return scenario
    try:
        row = cursor.execute(_SCENARIO_SQL[str(market).upper()], (row_id,)).fetchone()
        loaded = json.loads(row[0]) if row and row[0] else None
    except (ValueError, TypeError):
        loaded = None
    fresh = record(loaded) if SCENARIO_KEY in scenario else None
    if fresh is not None:
        scenario[SCENARIO_KEY] = fresh
    runner = runner_record(loaded) if isinstance(loaded, dict) else None
    if runner is not None:
        scenario[RUNNER_KEY] = runner
    return scenario


def exit_basis(cursor, market, holding_ids, scenario):
    """(cost-weighted entry, fresh scenario JSON) for a micro-split exit, else None.

    Called by sell_stock inside its BEGIN IMMEDIATE guard: the row is re-read so an
    add committed by the worker after the caller's snapshot is not lost.
    """
    if len(holding_ids) == 1:
        row = cursor.execute(_SCENARIO_SQL[str(market).upper()], (holding_ids[0],)).fetchone()
        if row is not None and row[0]:
            scenario = row[0]
    loaded = json.loads(scenario) if isinstance(scenario, str) else scenario
    block = record(loaded)
    if block is None:
        return None
    text = scenario if isinstance(scenario, str) else json.dumps(scenario, ensure_ascii=False)
    return float(weighted_entry(block["legs"])), text


def dashboard_fields(scenario, *, buy_price=None, current_price=None):
    """Dashboard JSON fields: slot allocation, label, cost-weighted entry, slot-weighted return.

    Legacy rows get allocation 1.0 and no label. ``buy_price`` is the row's
    initial entry (holdings) or the recorded cost-weighted entry (history).
    """
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario or "{}")
        except ValueError:
            scenario = {}
    fraction = slot_fraction(scenario)
    block = record(scenario)
    entry = float(weighted_entry(block["legs"])) if block is not None else buy_price
    fields = {"allocation": fraction, "allocation_label": display(scenario) or (
        f"비중 {round(fraction * 100)}%" if fraction < 1 else None), "average_entry": entry,
        "add_count": max(len(block["legs"]) - 1, 0) if block is not None else 0}
    if current_price and entry:
        fields["slot_profit_rate"] = (float(current_price) / float(entry) - 1) * 100 * fraction
    return fields


SCORE_POLICY_KEY = "_entry_score_policy"


def score_floor(market):
    """Required buy score for micro-split entries (KR/US); None when not applicable.

    2026-10-02 user decision: a micro-split entry starts at 30-80% of a slot, so it
    enters at score >= 5 in every regime instead of the full-slot regime floors
    (sideways/moderate_bear 8, strong_bear 9). MICRO_SPLIT_MIN_SCORE=off restores them.
    """
    if not live_enabled(market):
        return None
    raw = os.getenv("MICRO_SPLIT_MIN_SCORE", "5").strip().lower()
    if raw in {"", "off", "false", "no", "0"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 1 <= value <= 10 else None


def plan_available(agent, *, market, ticker, current_price, stop_loss):
    """True when a v3-ae plan (hence a fractional order) can be built for this entry."""
    try:
        from observability.b3_ae_capture import build_plan
        build_plan(agent, market=str(market).upper(), ticker=ticker, entry_price=current_price,
                   stop_loss=stop_loss, decision_ref="score-floor-check")
        return True
    except Exception:  # noqa: BLE001 - no plan means the legacy floor stays
        return False


def relaxed_min_score(agent, *, market, ticker, current_price, scenario, min_score, is_add, rebound_pilot,
                      logger=None):
    """(required_score, scenario) for a new entry; relaxes to the micro-split floor only when
    the entry will really be fractional. Adds, pilots and plan-less entries keep min_score."""
    floor = score_floor(market)
    try:
        current = float(min_score or 0)
    except (TypeError, ValueError):
        current = 0.0
    if floor is None or is_add or rebound_pilot or current <= floor:
        return min_score, scenario
    if not plan_available(agent, market=market, ticker=ticker, current_price=current_price,
                          stop_loss=(scenario or {}).get("stop_loss")):
        if logger:
            logger.info("[MICRO_SPLIT_SCORE][%s] %s no plan; legacy floor %s kept", market, ticker, current)
        return min_score, scenario
    updated = dict(scenario or {})
    updated[SCORE_POLICY_KEY] = {"mode": "micro_split_floor", "required_score": floor,
                                 "legacy_required_score": current}
    if logger:
        logger.info("[MICRO_SPLIT_SCORE][%s] %s min_score %s->%s (micro-split entry)", market, ticker, current, floor)
    return floor, updated


def gate_score_override(scenario):
    """Required score the final buy gate must use for a relaxed micro-split entry, or None."""
    policy = (scenario or {}).get(SCORE_POLICY_KEY) if isinstance(scenario, dict) else None
    if isinstance(policy, dict) and policy.get("mode") == "micro_split_floor":
        try:
            return float(policy["required_score"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def buy_prompt_block(market, language="ko"):
    """Per-report BUY guidance while micro-split LIVE is on; '' otherwise (byte-identical prompts)."""
    floor = score_floor(market)
    if floor is None:
        return ""
    value = int(floor) if float(floor).is_integer() else floor
    # Framing first, then the rule. The shared instruction was written for all-in/all-out
    # entries (constraint 4) and frames weak markets as risk control and strong markets as
    # opportunity cost; a micro-split pilot changes the error costs, so this block states
    # which shared lines it supersedes instead of leaving the model to reconcile them.
    if language == "ko":
        return (
            "\n\n### 초분할 진입 프레임 (이번 신규 진입에 우선 적용)\n"
            "이번 매수는 1슬롯의 30~80%만 먼저 사는 정찰병 진입입니다. 비중은 시스템이 변동성(ATR)으로 이미 줄여 두었고, "
            "맞으면 증액 시나리오(add_plan)로 키우며, 틀리면 최초 진입가 기준 손절로 작게 끝납니다(예: 비중 35% × 손절 6% = "
            "슬롯의 약 2%). 그래서 이 판단의 오류 비용은 비대칭입니다. 틀린 진입은 작게 잘리지만, 놓친 진입은 되돌릴 수 "
            "없습니다(이 트리거는 다시 오지 않습니다).\n"
            "- 불확실성은 미진입이 아니라 비중이 처리합니다. 아래 기준을 모두 통과했다면 '확신 부족'·'추가 확인 필요'·"
            "'돌파 확인 후'는 미진입 사유가 아닙니다. 확인되면 더 사는 일은 add_plan 시나리오(breakout·pullback_reclaim 등)의 "
            "몫이니 그 조건은 시나리오로 쓰십시오.\n"
            "- 점수는 산정 기준대로 매기십시오. 입력에 기준을 충족하는 근거가 있는데 '애매하다'는 이유로 한 단계 낮추지 "
            "마십시오. 근거가 없으면 만들어내지 마십시오.\n"
            "- 바뀌지 않는 것: 1단계 펀더멘털·1.5단계 추세·1.6단계 상습 손절 게이트, 미진입 단독·복합 사유(아래 사유 1 예외 "
            "제외), 손익비 floor, 최대 손절폭, 손절 규칙. 보유 종목이 7개 이상이면 '6점 이상만 진입' 규칙은 그대로 "
            "적용됩니다. 강세 국면의 목표 산정만 아래 규칙을 따릅니다. 이 프레임은 기준 안의 재량 판단에만 적용됩니다.\n"
            "- 시스템 제약 4(분할매매 불가·올인/올아웃)는 이번 매수에 적용되지 않습니다. 매수는 초분할(최초 비중 + 증액 "
            "시나리오), 매도는 기존대로 전량입니다. 제약 2·3은 진입/미진입 결정 문장에만 적용되며 add_plan의 조건부 "
            "시나리오를 막지 않습니다. 최초 비중은 결정론적으로 정해지므로 parabolic의 '사이징 축소 금지'와 충돌하지 않습니다.\n"
            f"\n### 초분할 진입 기준 (결정론적)\n"
            f"진입 최소 점수는 시장 국면과 관계없이 {value}점입니다(매트릭스의 min_score 열과 JSON 예시값 대신, 보유 종목 "
            f"7개 이상일 때의 6점 규칙은 예외로 유지). min_score "
            f"필드에 {value}점을 쓰십시오. 결정 규칙: effective_score ≥ {value} AND 1·1.5·1.6단계 게이트 통과 AND 미진입 "
            "단독·복합 사유 없음 AND 손익비 ≥ 현재 국면 floor AND |손절폭| ≤ 최대 손절폭 AND 모멘텀 신호·추가 확인 개수 "
            "충족 → 진입. min_score 외의 매트릭스 값, 점수 산정 기준, 스키마는 바뀌지 않습니다.\n"
            "- 미진입 단독 사유 1(지지선이 -10% 이하)은 이번 진입에 적용하지 않습니다. '손절가 설정' 규칙상 지지선이 멀면 "
            "매트릭스 최대 손절폭이 손절가가 되므로 사용 가능한 손절은 항상 있습니다.\n"
            "- 강세 국면(분산일 Kill Switch 반영 후 parabolic·strong_bull·moderate_bull)에서는 목표 도달이 매도가 아니라 "
            "trailing 전환이고, 초분할에서는 바로 위 저항 돌파가 증액 조건입니다. 그래서 가장 가까운 확정 주요 저항이 "
            "현재가 +3% 이내이면 그 저항은 목표가 아니라 증액 조건입니다. add_plan에 그 저항 돌파(breakout) 시나리오를 "
            "쓰고, target_price는 그 다음 확정 주요 저항까지 거리의 80%로 산정합니다. 다음 저항이 없으면 2a 조건을 확인하고, "
            "둘 다 없으면 기존대로 목표 근거 없음입니다. 이는 손익비를 맞추려는 선택이 아니라 이 규칙에서 나온 목표이므로 "
            "'먼 저항 금지' 계약의 예외이며, target_provenance.reason에 첫 저항 가격과 '증액 조건'을 쓰십시오. 횡보·약세 "
            "국면은 목표 도달 시 매도하므로 기존 규칙(가장 가까운 저항)을 유지합니다.\n")
    return (
        "\n\n### Micro-split entry frame (takes precedence for this new entry)\n"
        "This buy is a scout entry: only 30-80% of one slot is bought first. The system has already sized it down by "
        "volatility (ATR); if it works, the add scenarios (add_plan) build it up, and if it fails, the initial-entry "
        "stop ends it small (e.g. 35% allocation x 6% stop = about 2% of a slot). The error costs are therefore "
        "asymmetric: a wrong entry is cut small, a missed entry cannot be recovered (this trigger will not fire "
        "again).\n"
        "- Size, not abstention, absorbs uncertainty. When every criterion below passes, 'low conviction', 'needs "
        "more confirmation' or 'wait for the breakout' is not a no-entry reason; buying more on confirmation is the "
        "job of the add_plan scenarios (breakout, pullback_reclaim, ...), so write that condition as a scenario.\n"
        "- Score by the rubric. When the inputs contain evidence that meets a band, do not mark it down a step for "
        "being 'borderline'. Never invent evidence that is not there.\n"
        "- Unchanged: the Step 1 fundamental, Step 1.5 trend and Step 1.6 repeat stop-out gates, standalone and "
        "compound no-entry reasons (except reason 1 below), the R/R floor, the maximum stop width and the stop "
        "rules. With 7 or more holdings, the 'only buy_score >= 6' rule still applies. Only the bull-regime target "
        "follows the rule below. This frame only applies to discretionary judgment inside those criteria.\n"
        "- System constraint 4 (no split trading, all-in/all-out) does not apply to this buy: buying is a "
        "micro-split (initial allocation plus add scenarios) and selling stays a full exit. Constraints 2 and 3 "
        "apply to the enter/no-entry decision only and do not forbid conditional add_plan scenarios. The initial "
        "allocation is deterministic, so it does not conflict with the parabolic 'no size reduction' rule.\n"
        "\n### Micro-split entry threshold (deterministic)\n"
        f"The minimum entry score is {value} in every market regime (instead of the matrix min_score column and the "
        f"JSON example values; the 6-point rule for 7 or more holdings stays as an exception). Write {value} in "
        f"min_score. Decision rule: effective_score >= {value} AND Steps 1, "
        "1.5 and 1.6 pass AND no standalone or compound no-entry reason AND R/R >= the current regime floor AND "
        "|stop| <= the maximum stop AND the momentum-signal and extra-confirmation counts are met -> Enter. Every "
        "other matrix value, the scoring rubric and the schema are unchanged.\n"
        "- Standalone no-entry reason 1 (support at -10% or worse) does not apply to this entry: under the stop rules "
        "a distant support makes the matrix maximum stop the stop, so a usable stop always exists.\n"
        "- In bull regimes (parabolic, strong_bull, moderate_bull after the distribution-day kill switch) reaching the "
        "target switches to a trailing stop instead of selling, and in a micro-split a break above the next "
        "resistance is an add condition. So when the nearest confirmed major resistance is within +3% of the current "
        "price, that resistance is an add condition, not the target: write a breakout add_plan scenario over it and "
        "set target_price at 80% of the distance to the following confirmed major resistance. If there is none, "
        "check the 2a conditions; if neither applies, the target is unsupported as before. This target comes from "
        "this rule, not from fitting the R/R floor, so it is an exception to the no-farther-resistance contract; "
        "state the first resistance price and 'add condition' in target_provenance.reason. Sideways and bear regimes "
        "sell at the target, so they keep the nearest-resistance rule.\n")


def journal_position_line(scenario, profit_rate=None):
    """Journal/compression line on position size for partial positions; '' for a full slot.

    The retrospective must know a stop on 35% of a slot is not a full-slot loss and that
    adds changed the average entry (lessons and compressed intuitions inherit this)."""
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario or "{}")
        except ValueError:
            scenario = {}
    fraction = slot_fraction(scenario)
    block = record(scenario)
    if fraction >= 1 and block is None:
        return ""
    parts = [f"{fraction:.0%} of one slot"]
    if block is not None:
        legs = block.get("legs") or []
        first = float(legs[0]["allocation"]) if legs else fraction
        adds = max(len(legs) - 1, 0)
        tilt = block.get("conviction_tilt") or {}
        tilted = f" (top-setup tilt from {float(tilt['base']):.0%})" if tilt.get("base") else ""
        parts.append(f"initial {first:.0%}{tilted}, {adds} add(s)")
        if adds:
            parts.append(f"cost-weighted entry {float(weighted_entry(legs)):,.2f}")
    if profit_rate is not None:
        parts.append(f"slot-weighted return {profit_rate * fraction:+.2f}%")
    return "- Position Size (micro-split): " + "; ".join(parts) + "\n"


def journal_stock_data(stock_data, *, buy_price, scenario):
    """stock_data for the journal with the recorded (cost-weighted) entry and fresh scenario."""
    data = dict(stock_data or {})
    data["buy_price"] = buy_price
    data["scenario"] = scenario
    return data
