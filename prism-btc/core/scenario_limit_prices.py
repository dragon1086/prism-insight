"""One-time, host-owned pricing of newly validated proposals, not stored intents."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math

from core.llm_scenario import ScenarioValidationError, validate_scenario

POLICY_VERSION = "round-limit-v1"


def _decimal(value):
    if isinstance(value, bool):
        raise ValueError("invalid number")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("invalid number")
    return result


def observed_quotes(ticker, captured_at):
    """Optional quote evidence: missing quotes must not block protection."""
    try:
        bid, ask = _decimal(ticker.get("bid1Price")), _decimal(ticker.get("ask1Price"))
        if not 0 < bid < ask or not all(math.isfinite(float(v)) for v in (bid, ask)):
            return None
        return dict(bid=float(bid), ask=float(ask), captured_at=captured_at)
    except (AttributeError, ValueError, TypeError, InvalidOperation, OverflowError):
        return None


def _basis(payload, context):
    rows = context.get("positions", []) + context.get("pending_entries", []) + payload.get("entries", [])
    quantity = sum(_decimal(row["quantity"]) for row in rows)
    if quantity <= 0:
        return None
    return sum(_decimal(row["price"]) * _decimal(row["quantity"]) for row in rows) / quantity


def _edge_preserved(original, candidate, context):
    if not original.get("take_profits"):
        return True
    before, after = _basis(original, context), _basis(candidate, context)
    if before is None or after is None:
        return False
    rate = _decimal(context["estimated_cost_rate"]) + _decimal(context["slippage_bps"]) / 10000
    sign = 1 if original["side"] == "LONG" else -1
    for old, new in zip(original["take_profits"], candidate["take_profits"]):
        old_edge = sign * (_decimal(old["price"]) - before) - before * rate
        new_edge = sign * (_decimal(new["price"]) - after) - after * rate
        if old_edge > 0 and new_edge <= 0:
            return False
    return True


def validate_execution_prices(payload, context):
    """Validate raw first; each safe row adjustment retains the last valid plan.

    Audit metadata is added only after strict validation, never accepted from the
    model. Passing a saved validated intent here is deliberately not supported.
    """
    validated = validate_scenario(payload, context)
    policy = context.get("execution_price_policy")
    if (not isinstance(policy, dict) or policy.get("version") != POLICY_VERSION
            or validated["action"] not in {"OPEN", "ADJUST"}):
        return validated
    raw = deepcopy(payload)
    audit = dict(version=POLICY_VERSION, applied=False, rows=[], price_tick=None, quotes=None)
    prerequisites = None
    try:
        tick = _decimal(context.get("price_tick"))
        quotes = context["limit_price_quotes"]
        bid, ask = _decimal(quotes["bid"]), _decimal(quotes["ask"])
        age = _decimal(context["now"]) - _decimal(quotes["captured_at"])
        if tick <= 0 or not 0 < bid < ask or not 0 <= age <= 10:
            prerequisites = "invalid_or_stale_quote_or_tick"
        elif not all(math.isfinite(float(v)) for v in (tick, bid, ask)):
            prerequisites = "invalid_or_stale_quote_or_tick"
        else:
            audit.update(price_tick=float(tick), quotes=dict(bid=float(bid), ask=float(ask),
                         captured_at=float(_decimal(quotes["captured_at"]))))
    except (KeyError, ValueError, TypeError, InvalidOperation):
        prerequisites = "missing_quote_or_tick"
    try:
        live_tp = {_decimal(row["price"]) for row in context.get("target_status", [])
                   if row.get("kind") == "tp" and row.get("status") == "LIVE"}
    except (AttributeError, KeyError, ValueError, TypeError, InvalidOperation):
        live_tp = set()
        prerequisites = "invalid_target_context"
    for kind in ("entries", "take_profits"):
        for index, row in enumerate(raw.get(kind, [])):
            price = _decimal(row["price"])
            record = dict(kind=kind, id=row["id"], raw_price=float(price), final_price=float(price), applied=False)
            reason = prerequisites
            # Reject unsupported decimal magnitudes as optional pricing evidence;
            # leave original broker validation/protection behavior unchanged.
            if abs(price.adjusted()) > 15 or (not prerequisites and (
                    abs(tick.adjusted()) > 15 or price.adjusted() - tick.adjusted() > 24)):
                reason = "unsupported_price_precision"
                record["reason"] = reason
                audit["rows"].append(record)
                continue
            if price % 10:
                reason = "not_round"
            elif kind == "take_profits" and validated["action"] == "ADJUST" and price in live_tp:
                reason = "existing_live_tp"
            if not reason and price % tick:
                reason = "raw_price_off_tick"
            if not reason:
                buy = (kind == "entries") == (payload["side"] == "LONG")
                adjusted = ((price + (Decimal("7.3") if buy else -Decimal("7.3"))) / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick
                if adjusted <= 0 or not 5 <= abs(adjusted - price) <= 10:
                    reason = "offset_out_of_bounds"
                elif adjusted % 10 == 0:
                    reason = "still_round_after_tick_alignment"
                elif (buy and price < ask <= adjusted) or (not buy and adjusted <= bid < price):
                    reason = "would_cross_spread"
                elif (kind == "entries" and context.get("minimum_notional") is not None
                      and adjusted * _decimal(row["quantity"]) < _decimal(context["minimum_notional"])):
                    reason = "minimum_notional"
                else:
                    candidate = deepcopy(raw)
                    candidate[kind][index]["price"] = float(adjusted)
                    if not _edge_preserved(payload, candidate, context):
                        reason = "cost_edge_or_basis"
                    else:
                        try:
                            result = validate_scenario(candidate, context)
                        except ScenarioValidationError:
                            reason = "candidate_validation_failed"
                        else:
                            raw, validated = candidate, result
                            record.update(final_price=float(adjusted), applied=True)
                            audit["applied"] = True
            record["reason"] = reason or "applied"
            audit["rows"].append(record)
    # Revalidate the exact final wire payload, not the risk/audit-enriched result.
    validated = validate_scenario(raw, context)
    validated["execution_pricing"] = audit
    return validated
