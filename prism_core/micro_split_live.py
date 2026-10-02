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


def adds_enabled(market):
    """Fixed-ladder LIVE adds (+2%/+4%); paused by default since 2026-10-02.

    The user judged the intraday ladder too hasty for an O'Neil-style hold; adds
    wait for the scenario-based add plan. The fractional first entry stays LIVE.
    """
    flag = os.getenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
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


def entry_record(*, plan, unit_amount, market, entered_at):
    """The micro_split block stored on the new holding's scenario."""
    initial = plan["initial_nominal"]
    return {"contract": CONTRACT, "policy_version": plan["policy_version"], "plan_hash": plan["plan_hash"],
            "market": str(market).upper(), "unit_amount": str(_dec(unit_amount)), "allocation": str(_dec(initial)),
            "legs": [{"kind": "INITIAL", "allocation": str(_dec(initial)), "price": str(_dec(plan["entry_reference"])),
                      "at": entered_at}]}


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


def apply_add(scenario, *, delta, price, at, bar_end, intent_id=None):
    """Return (new_scenario, new_buy_price) after an in-slot add; never above one slot."""
    updated = deepcopy(scenario)
    block = record(updated)
    if block is None:
        raise ValueError("not a micro-split holding")
    target = _dec(block["allocation"]) + _dec(delta)
    if target > 1:
        raise ValueError("add would exceed one slot")
    if any(leg.get("bar_end") == bar_end for leg in block["legs"]):
        raise ValueError("bar already used for an add")
    block["legs"].append({"kind": "ADD", "allocation": str(_dec(delta)), "price": str(_dec(price)), "at": at,
                          "bar_end": bar_end, "intent_id": intent_id})
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


def prepare_entry(agent, *, market, ticker, current_price, scenario, decision_ref, account, logger=None):
    """Size a LIVE first entry: (plan, cash_amount, scenario_with_record) or (None, None, scenario).

    Called with the refreshed execution price, before the strategy row and the
    broker order. When LIVE is off or the plan cannot be built (no ATR/stop),
    the legacy full-slot entry is unchanged and the reason is logged.
    """
    if not live_enabled(market):
        return None, None, scenario
    try:
        from observability.b3_ae_capture import build_plan
        unit = (account or {}).get("buy_amount_krw" if str(market).upper() == "KR" else "buy_amount_usd")
        plan = build_plan(agent, market=str(market).upper(), ticker=ticker, entry_price=current_price,
                          stop_loss=scenario.get("stop_loss"), decision_ref=decision_ref)
        cash = scaled_cash(unit, plan["initial_nominal"], market)
        updated = dict(scenario)
        updated[SCENARIO_KEY] = entry_record(plan=plan, unit_amount=unit, market=market,
                                             entered_at=plan["created_at"])
        if logger:
            logger.warning("[MICRO_SPLIT][%s] %s initial=%s cash=%s", market, ticker, plan["initial_nominal"], cash)
        return plan, cash, updated
    except Exception as error:  # noqa: BLE001 - legacy full slot when B3 cannot size the entry
        if logger:
            logger.warning("[MICRO_SPLIT][%s] %s unavailable, legacy full slot: %s", market, ticker, error)
        return None, None, scenario


def entry_message_line(scenario, market):
    """Buy-message line for a micro-split entry, or ''."""
    block = record(scenario)
    if block is None:
        return ""
    pct = round(float(block["allocation"]) * 100)
    if not adds_enabled(market):
        if str(market).upper() == "US":
            return (f"Micro-split allocation: {pct}% of one slot — adds are paused until scenario-based add plans "
                    "go live; a stop exits the whole position. Whole shares are rounded down.\n")
        return (f"초분할 비중: {pct}% (1슬롯 기준) — 추가 매수는 증액 시나리오 도입 전까지 멈춰 있고, 손절 시 "
                "전량 매도합니다. 정수 수량 내림으로 실제 체결 비중은 조금 낮을 수 있습니다.\n")
    two_steps = float(block["allocation"]) < 0.8  # an initial 80% has only the +4% -> 100% step left
    if str(market).upper() == "US":
        ladder = "adds to 80%/100% only after +2%/+4%" if two_steps else "adds to 100% only after +4%"
        return (f"Micro-split allocation: {pct}% of one slot — {ladder} above entry is confirmed; "
                "a stop exits the whole position. Whole shares are rounded down.\n")
    ladder = "+2%·+4% 상승이 확인되면 80%·100%까지" if two_steps else "+4% 상승이 확인되면 100%까지"
    return (f"초분할 비중: {pct}% (1슬롯 기준) — 진입가 대비 {ladder} "
            "추가 매수하고, 손절 시 전량 매도합니다. 정수 수량 내림으로 실제 체결 비중은 조금 낮을 수 있습니다.\n")


_ROW_SQL = {
    "KR": "SELECT id, ticker, company_name, account_key, scenario, buy_price FROM stock_holdings "
          "WHERE id=? AND ticker=? AND account_key=?",
    "US": "SELECT id, ticker, company_name, account_key, scenario, buy_price FROM us_stock_holdings "
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


def add_message(*, market, company_name, ticker, before, after, price, average, order_status):
    """Telegram text for an executed in-slot add (KR Korean, US English like other US trade texts)."""
    old, new = round(before * 100), round(after * 100)
    if str(market).upper() == "US":
        return (f"📈 Micro-split Add: {company_name}({ticker})\n"
                f"Allocation: {old}% → {new}% of one slot\nAdd Price: ${price:,.2f}\n"
                f"Average Entry: ${average:,.2f}\nOrder: {order_status}\n")
    return (f"📈 초분할 추가 매수: {company_name}({ticker})\n"
            f"비중: {old}% → {new}% (1슬롯 기준)\n추가 매수가: {price:,.0f}원\n"
            f"평균 매수가: {average:,.0f}원\n주문: {order_status}\n")


async def execute_add(agent, *, market, campaign, decision, now, chat_id=None):
    """Apply a qualified B3 add LIVE: holding row (strategy ledger) -> KIS add order -> message/event.

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
        before = float(block["allocation"])
        delta = _dec(decision["target_allocation"]) - _dec(block["allocation"])
        price = float(_dec(decision["price"]))
        updated, average = apply_add(scenario, delta=delta, price=price, at=now, bar_end=decision["bar_end"])
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
    status = ("주문 접수" if market == "KR" else "Submitted") if result.get("success") else (
        f"미체결/실패({result.get('reason_code') or result.get('message')})" if market == "KR"
        else f"Not filled ({result.get('reason_code') or result.get('message')})")
    agent.message_queue.append(add_message(market=market, company_name=row.get("company_name") or campaign["symbol"],
                                           ticker=campaign["symbol"], before=before, after=after, price=price,
                                           average=average, order_status=status))
    agent._msg_types.append("analysis")
    if chat_id:
        await agent.send_telegram_message(chat_id, await_broadcast=True)
    emit_event("micro_split.add_executed", service=f"prism-{market.lower()}-micro-split", market=market,
               ticker=campaign["symbol"], position_id=campaign["position_id"],
               attributes={"campaign_id": campaign["campaign_id"], "slot_allocation": after,
                           "allocation_before": before,
                           "allocation_after": after, "add_price": price, "average_entry": average,
                           "order_cash": cash, "intent_id": intent.id, "broker_success": bool(result.get("success")),
                           "broker_status": result.get("status") or result.get("intent_status")})
    return {"status": "EXECUTED", "allocation": after, "cash": cash, "broker": result}


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
    if language == "ko":
        return (
            "\n\n### 초분할 진입 기준 (결정론적)\n"
            f"이번 신규 진입은 초분할로 1슬롯의 30~80%만 먼저 매수합니다. 그래서 진입 최소 점수는 시장 국면과 관계없이 "
            f"{value}점입니다. min_score에는 {value}을 쓰고, buy_score가 {value}점 이상이며 미진입 단독 사유와 1단계 "
            "펀더멘털·추세 게이트에 걸리지 않으면 진입으로 판단하십시오. 점수 산정 기준·스키마·손절·손익비 규칙은 바뀌지 않습니다.\n")
    return (
        "\n\n### Micro-split entry threshold (deterministic)\n"
        f"This new entry is a micro-split: only 30-80% of one slot is bought first, so the minimum entry score is "
        f"{value} in every market regime. Write {value} in min_score and decide Enter when buy_score >= {value} and no "
        "standalone no-entry reason, Stage-1 fundamental gate or trend gate applies. Scoring rules, schema, stop and "
        "R/R rules are unchanged.\n")
