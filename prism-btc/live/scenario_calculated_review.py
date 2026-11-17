"""Order-free lifecycle research: model prices, host arithmetic, model selection.

Results are calculation receipts; callers alone authorize and execute orders.
Caller supplies an audited, no-retry generator and a durable research recorder.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
import time

from core.llm_scenario import target_quantities, EntryConvergenceRejection, EconomicProposalRejection, ScenarioValidationError, risk_snapshot, validate_scenario
from core.scenario_limit_prices import preview_execution_prices
from engine.scenario_ma_context import build_ma_structure_context
from live import scenario_llm
from live.scenario_contract import _object, response_schema, validate_wire_proposal
from live.scenario_entry_contract import strip_metadata
from live.scenario_entry_goal import GoalConsistencyRejection, prepare_goal_source, validate_goal_pair
from live.scenario_position_flow import project_position_flow_input, project_holding_price_order_input
from live.scenario_analysis_frames import analysis_model_view
from live.scenario_runtime import snapshot_input_time
from live.scenario_explanation import (enabled as explanation_enabled, rationale_schema,
    model_matches, bind_receipt, compile_explanation, project_input as project_explanation_input,
    PROMPT as EXPLANATION_PROMPT)


DRAFT_PROMPT = """Order-free proposal stage. Follow the API schema: wait_proposal is the WAIT alternative; candidates are mutually exclusive, never a combined portfolio. Offer only supported legal alternatives and explain current evidence, contrary evidence, purpose and invalidation concisely. Missing qualitative evidence permits no candidates; an economic concern with an otherwise grounded plan should reach host calculation.
The host computes effective prices, quantities, costs and payoff. Do not calculate them. If prepared_entry_plans is supplied, use coherent entry/SL and listed TP prices from one layout; choose TP subset/fractions and any goal-compatible runner. Illustrations fix neither weights nor confidence. Otherwise choose grounded structural prices under the original goal. Use each TP ID once. No output is an order or authority. First recovery observation stays WAIT when the host requires it.
When entry_readiness is requested, SUPPORTED_NOW needs present evidence for this entry; WAIT_FOR_EVIDENCE means another observation is still needed.
"""

SELECT_PROMPT = """Order-free selection stage. Choose one offered schema selection_token (candidate ID/full receipt hash). Eligible only; WAIT must be selectable. Never rewrite prices, quantities, confidence, direction or original goal in rationale. FAILED requires the offered EXIT.
DRAFT owns the market thesis; check its effective plan against purpose, location, executable path and whole-trade outcomes. Do not start a second independent directional forecast. Reject for a specific material contradiction or inadequate opportunity, not an unhit future TP. Conditional payoff is not probability or target-reach evidence.
Read calculation_field_units and displayed assumptions. Never recompute arithmetic or combine mutually exclusive paths. Distinguish actual fees, assumptions and reserves. Unknown future fees alone do not force WAIT when the host supplies a cost basis. Funding/unknown execution remain unknown.
Use existing_exit_paths for unchanged WAIT; candidate projections describe changes. Include prior realized costs once, never restore filled quotas or call checkpoints orders. confirmed_remaining covers held lots; hypothetical_all_fills additionally assumes pending/new fills. Never add them. joint_exit_paths is stress testing, not the predicted ending or value of an open runner. execution_authorized=false does not reject an eligible schema token. Host guard restrictions still apply.
"""


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))




def receipt_hash(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _decimal(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("missing_calculation_input")
    return Decimal(str(value))


def _joint_exit_paths(plan, targets, quantity, basis, minimum, fees, *, confirmed=False, recorded_net=None):
    """Reuse initial revision quotas; cap each hypothetical exit at live remainder."""
    if confirmed:
        try:
            recorded_net = float(_decimal(recorded_net))
        except (TypeError, ValueError, OverflowError):
            recorded_net = None
    sign = Decimal(1 if plan['side'] == 'LONG' else -1)
    tps = sorted((t for t in targets if t['kind'] == 'take_profits' and t['quantity'] > 0),
                 key=lambda t: sign * _decimal(t['price']))
    stops = sorted((t for t in targets if t['kind'] == 'partial_stops' and t['quantity'] > 0),
                   key=lambda t: -sign * _decimal(t['price']))
    sequences = [('TP_STOP', tps), ('SL_STOP', stops),
                 ('TP1_SL', tps[:1] + stops), ('SL1_TP', stops[:1] + tps)]
    paths = []
    for sequence_id, ordered in sequences:
        remaining, gross, legs, fee_legs = quantity, Decimal(0), [], []
        applied_kinds = set()
        for target in ordered:
            qty = min(remaining, _decimal(target['quantity']))
            if qty <= 0 or qty < minimum:
                continue
            remaining -= qty
            gross += sign * (_decimal(target['price']) - basis) * qty
            legs.append([target['id'], float(qty)])
            applied_kinds.add(target['kind'])
            fee_legs.append(fees(target['price'], qty))
        # Name only legs that survive quantity caps/minimums, not the template.
        if not applied_kinds:
            sequence_id = 'STOP'
        elif applied_kinds == {'take_profits'}:
            sequence_id = 'TP1_STOP' if sequence_id == 'TP1_SL' else 'TP_STOP'
        elif applied_kinds == {'partial_stops'}:
            sequence_id = 'SL1_STOP' if sequence_id == 'SL1_TP' else 'SL_STOP'
        residual = remaining
        if remaining > 0:
            gross += sign * (_decimal(plan['hard_stop']) - basis) * remaining
            legs.append([None, float(remaining)])
            fee_legs.append(fees(plan['hard_stop'], remaining))
        existing = next((p for p in paths if p[1] == legs), None)
        if existing is not None:
            if sequence_id not in existing[0]:
                existing[0].append(sequence_id)
            continue
        fee_view = None
        if (fee_legs and all(f.get('status') == 'AVAILABLE' for f in fee_legs)
                and (not confirmed or recorded_net is not None)):
            fee_view = [float(sum((_decimal(f[field]) for f in fee_legs), Decimal(0)))
                for field in ('assumed_entry_fee', 'assumed_exit_fee', 'estimated_net_before_unmodelled_costs')]
            if confirmed:
                fee_view[2] = float(_decimal(recorded_net) + _decimal(fee_view[2]))
        paths.append([[sequence_id], legs, float(residual), float(gross), fee_view])
    # The versioned column/sequence meanings live once in model-only units, not
    # repeated per candidate/path in the size-limited user payload.
    result = dict(version=2, paths=[[path[0], *path[2:]] for path in paths])
    if confirmed:
        result.update(version=3, recorded_net=recorded_net,
            gross_scope='CONFIRMED_REMAINING_ONLY',
            fee_net_scope='WHOLE_SCENARIO_RECORDED_NET_ONCE')
    return result


def _kept_exit_paths(context):
    """Value reported live quotas, never reapply old fractions after partial exits."""
    from live.scenario_fee_projection import fee_only_projection
    try:
        if (not context.get('scenario_id') or context.get('accounting_status')!='confirmed'
                or context.get('protection_ok') is not True or context.get('management_quarantine')
                or context.get('pending_entries')!=[]):
            return None
        now,at=map(_decimal,(context['now'],context['account_captured_at']))
        if not 0<=now-at<=120:return None
        side=context['side'];sign=1 if side=='LONG' else -1
        if side not in ('LONG','SHORT'):return None
        lots=context['positions'];reported=context['target_status']
        if not isinstance(lots,list) or not 0<len(lots)<=20 or not isinstance(reported,list) or len(reported)>100:return None
        quantity=notional=Decimal(0)
        for lot in lots:
            price,qty=map(_decimal,(lot['price'],lot['quantity']))
            if min(price,qty)<=0:return None
            quantity+=qty;notional+=price*qty
        basis=notional/quantity
        mark,stop=map(_decimal,(context['mark_price'],context['previous_hard_stop']))
        if min(mark,stop)<=0 or sign*(mark-stop)<=0:return None
        targets=[];seen=set()
        for row in reported:
            if row.get('status')=='TERMINAL':continue
            if row.get('status')!='LIVE' or row.get('kind') not in ('tp','partial_sl'):return None
            key=(row['intent_id'],row['target_id'])
            if any(not isinstance(v,str) or not v for v in key) or key in seen:return None
            seen.add(key)
            price,qty=(Decimal(str(row[k])) for k in ('price','remaining_quantity'))
            if not all(v.is_finite() and v>0 for v in (price,qty)):return None
            favorable=sign*(price-mark)
            if (row['kind']=='tp' and favorable<=0) or (row['kind']=='partial_sl' and favorable>=0):return None
            targets.append(dict(kind='take_profits' if row['kind']=='tp' else 'partial_stops',
                id=':'.join(key),price=float(price),quantity=float(qty)))
        def fees(price,qty):
            return fee_only_projection(side=side,entry_price=float(basis),exit_price=float(price),
                quantity=float(qty),evidence=context.get('observed_execution_fee_evidence'),
                now=float(now),new_entry=False)
        plan=dict(side=side,hard_stop=float(stop))
        # Existing partially filled orders can have leaves below the new-order minimum.
        joint=_joint_exit_paths(plan,targets,quantity,basis,Decimal(0),fees,
            confirmed=True,recorded_net=context.get('recorded_scenario_net'))
        return dict(version=1,quantity=float(quantity),joint_exit_paths=joint,
            source='BROKER_REPORTED_LIVE_LEAVES_NOT_PLAN_FRACTIONS',
            account_captured_at=float(at),future_fills_confirmed=False)
    except (KeyError,TypeError,ValueError,ArithmeticError,OverflowError):
        return None


def _project_kept_exit_paths(projected,context):
    from live.scenario_model_input import restore_model_input,project_model_input
    try:
        logical=restore_model_input(projected);target=logical['contract_context']
        keys=('scenario_id','positions','pending_entries','accounting_status','protection_ok',
              'management_quarantine','now','account_captured_at','side','mark_price',
              'previous_hard_stop','target_status','recorded_scenario_net','observed_execution_fee_evidence')
        if 'existing_exit_paths'in target or any(target.get(k)!=context.get(k) for k in keys):return projected
        view=_kept_exit_paths(target)
        if view is None or len(_encoded(view).encode())>2000:return projected
        original=deepcopy(logical);target['existing_exit_paths']=view
        candidate,_=project_model_input(logical,allow_gap_rescue=True)
        if len(_encoded(candidate).encode())>100000:return projected
        restored=restore_model_input(candidate);restored['contract_context'].pop('existing_exit_paths',None)
        return candidate if restored==original else projected
    except (KeyError,TypeError,ValueError,ArithmeticError,OverflowError):
        return projected


def _gross_projection(plan, context, *, lots=None,
                      assumption="HYPOTHETICAL_ALL_ENTRIES_FILL_AT_LIMIT_NO_PRIOR_EXITS",
                      holding_paths=False):
    """Separate confirmed/hypothetical lots; never exchange allocation evidence."""
    entries = plan["entries"] if lots is None else lots
    quantity = sum((_decimal(row["quantity"]) for row in entries), Decimal(0))
    if quantity == 0:
        return dict(status="NO_POSITION_IN_THIS_ASSUMPTION", quantity=0,
                    assumption=assumption, targets=[], actual_net_pnl=None)
    notional = sum((_decimal(row["price"]) * _decimal(row["quantity"])
                    for row in entries), Decimal(0))
    basis = notional / quantity
    sign = Decimal(1 if plan["side"] == "LONG" else -1)
    equity = _decimal(context["initial_equity"])
    step = _decimal(context["quantity_step"])
    minimum = _decimal(context["minimum_quantity"])
    if min(step, minimum, equity) <= 0:
        raise ValueError("invalid_calculation_input")
    targets = []
    lot_risk = risk_snapshot(initial_equity=context["initial_equity"], side=plan["side"],
        hard_stop=plan["hard_stop"], positions=entries, pending_entries=[], entries=[],
        realized_loss=0, fees_paid=0, funding_paid=0,
        estimated_cost_rate=context["estimated_cost_rate"], slippage_bps=context["slippage_bps"])
    risk_per_btc = _decimal(lot_risk["remaining_risk"]) / quantity
    from live.scenario_fee_projection import fee_only_projection
    confirmed = assumption == "CONFIRMED_REMAINING_NEW_REVISION_NO_ADDITIONAL_FILLS"
    new_entry = context.get("scenario_id") is None
    def fees(price, qty, whole=False):
        if not (confirmed or new_entry):
            return dict(status="UNKNOWN", reason="mixed_confirmed_and_unfilled_lots")
        if whole and confirmed and context.get("recorded_scenario_net") is None:
            return dict(status="UNKNOWN", reason="recorded_scenario_net_missing")
        return fee_only_projection(side=plan["side"], entry_price=basis, exit_price=price,
            quantity=qty, evidence=context.get("observed_execution_fee_evidence"), now=context.get("now"),
            new_entry=new_entry, recorded_net=context.get("recorded_scenario_net") if whole and confirmed else None,
            entry_fee_reference=context.get('entry_fee_reference') if new_entry else None)
    for kind in ("take_profits", "partial_stops"):
        # New revision: prior reductions are already absent from supplied lots.
        for row, qty in zip(plan[kind], target_quantities(quantity, plan[kind], step,
                                                       full_exit=kind == "take_profits")):
            below_minimum = qty < minimum
            projected_qty = Decimal(0) if below_minimum else qty
            gross = sign * (_decimal(row["price"]) - basis) * projected_qty
            targets.append(dict(kind=kind, id=row["id"], price=row["price"],
                quantity=float(projected_qty), below_minimum=below_minimum,
                gross_pnl=float(gross), equity_percent=float(gross / equity * 100),
                fee_only_projection=fees(row["price"], projected_qty),
                gross_reward_per_btc=float(sign * (_decimal(row["price"]) - basis)),
                gross_reward_to_reserved_risk_ratio=float(sign * (_decimal(row["price"]) - basis) / risk_per_btc)
                    if risk_per_btc > 0 else None))
    stop_gross = sign * (_decimal(plan["hard_stop"]) * quantity - notional)
    result = dict(assumption=assumption,
                quantity=float(quantity), weighted_entry=float(basis),
                notional=float(notional), gross_at_full_stop=float(stop_gross),
                fee_only_projection_at_full_stop=fees(plan["hard_stop"], quantity, whole=True),
                gross_at_full_stop_equity_percent=float(stop_gross / equity * 100),
                targets=targets, target_paths="INDEPENDENT_NOT_ADDITIVE_WITH_FULL_STOP",
                cost_basis="QUANTITY_WEIGHTED_REMAINING_LOTS_PRO_RATA_ESTIMATE",
                future_target_fills_confirmed=False,
                weighted_risk_per_btc=float(risk_per_btc),
                ratio_semantics="GROSS_PRICE_REWARD_OVER_HOST_RESERVED_RISK_NOT_NET_EXPECTANCY",
                actual_future_fees=None, actual_future_funding=None,
                actual_net_pnl=None)
    if (new_entry and plan.get('action') == 'OPEN' and lots is None
            and not context.get('positions') and not context.get('pending_entries')):
        result['joint_exit_paths'] = _joint_exit_paths(plan, targets, quantity, basis, minimum, fees)
        payoff = dict(status='UNKNOWN', net_reward=None, full_stop_loss=None, ratio=None)
        try:
            path = next(p for p in result['joint_exit_paths']['paths'] if 'TP_STOP' in p[0])
            full_stop = result['fee_only_projection_at_full_stop']
            if full_stop.get('status') == 'AVAILABLE':
                reward = _decimal(path[3][2])
                loss = -_decimal(full_stop['estimated_net_before_unmodelled_costs'])
                if loss > 0:
                    values = [float(reward), float(loss), float(reward / loss)]
                    if all(math.isfinite(v) for v in values):
                        payoff.update(status='AVAILABLE', net_reward=values[0], full_stop_loss=values[1], ratio=values[2])
                        from live.scenario_fee_projection import DEMO_REFERENCE_BASIS
                        if full_stop.get('basis') == DEMO_REFERENCE_BASIS:
                            payoff['fee_basis'] = DEMO_REFERENCE_BASIS
        except (StopIteration, KeyError, IndexError, TypeError, ValueError, OverflowError):
            pass  # Optional arithmetic cannot create an execution/selection veto.
        result['whole_plan_payoff'] = payoff
    elif holding_paths and confirmed and plan.get('action') == 'ADJUST':
        result['joint_exit_paths'] = _joint_exit_paths(plan, targets, quantity, basis, minimum, fees,
            confirmed=True, recorded_net=context.get('recorded_scenario_net'))
    return result


def _holding_projection_values(plan, context):
    unavailable = dict(status="UNAVAILABLE", reason="unconfirmed_accounting_or_position",
                       actual_net_pnl=None)
    if context.get("accounting_status") != "confirmed":
        return unavailable
    side = context.get("side")
    if side not in ("LONG", "SHORT") or (context.get("current_plan") or {}).get("side", side) != side:
        return dict(unavailable, reason="missing_or_conflicting_position_side")
    lots = context["positions"]
    if any(_decimal(row["price"]) <= 0 or _decimal(row["quantity"]) <= 0 for row in lots):
        return unavailable
    if plan['action'] == 'REDUCE':
        quantity = sum((_decimal(row['quantity']) for row in lots), Decimal(0))
        requested = _decimal(plan['reduce_plan']['requested_quantity'])
        average = sum((_decimal(row['price']) * _decimal(row['quantity']) for row in lots), Decimal(0)) / quantity
        gross = (1 if side == 'LONG' else -1) * (_decimal(context['mark_price']) - average) * requested
        return dict(status='AVAILABLE', assumption='REDUCE_LEG_AT_MARK_NOT_FILL_OR_WHOLE_SCENARIO_NET',
                    reduce_plan=deepcopy(plan['reduce_plan']), estimated_leg_gross_at_mark=float(gross),
                    actual_future_fees=None, actual_future_slippage=None, actual_net_pnl=None,
                    remaining_targets='PROPORTIONALLY_SCALE_VERIFIED_UNFILLED_QUOTAS_TO_ACTUAL_REMAINDER_NO_REFILL',
                    native_full_stop='UNCHANGED')
    if plan["action"] == "EXIT":
        mark = _decimal(context["mark_price"])
        equity = _decimal(context["initial_equity"])
        if min(mark, equity) <= 0:
            return unavailable
        sign = 1 if side == "LONG" else -1
        gross = sum((sign * (mark - _decimal(row["price"])) * _decimal(row["quantity"])
                     for row in lots), Decimal(0))
        from live.scenario_fee_projection import fee_only_projection
        quantity=sum((_decimal(row["quantity"]) for row in lots), Decimal(0))
        fee_view=dict(status="UNKNOWN", reason="recorded_scenario_net_missing")
        if quantity > 0 and context.get("recorded_scenario_net") is not None:
            basis=sum((_decimal(row["price"])*_decimal(row["quantity"]) for row in lots),Decimal(0))/quantity
            fee_view=fee_only_projection(side=side,entry_price=basis,exit_price=mark,quantity=quantity,
                evidence=context.get("observed_execution_fee_evidence"),now=context.get("now"),
                new_entry=False,recorded_net=context["recorded_scenario_net"])
        return dict(status="AVAILABLE", assumption="CONFIRMED_REMAINING_AT_MARK_NOT_ACTUAL_EXIT_FILL",
            fee_only_projection=fee_view,
            confirmed_quantity=float(sum((_decimal(row["quantity"]) for row in lots), Decimal(0))),
            mark_price=float(mark), estimated_gross_at_mark=float(gross), actual_net_pnl=None,
            estimated_gross_at_mark_equity_percent=float(gross / equity * 100),
            actual_future_fees=None, actual_future_funding=None,
            pending_entries_still_reserved=deepcopy(context["pending_entries"]),
            pending_cancellation_confirmed=False, settlement_confirmed=False)
    confirmed = _gross_projection(plan, context, lots=lots,
        assumption="CONFIRMED_REMAINING_NEW_REVISION_NO_ADDITIONAL_FILLS")
    hypothetical = _gross_projection(plan, context,
        lots=lots + context["pending_entries"] + plan["entries"],
        assumption="HYPOTHETICAL_CONFIRMED_PLUS_ALL_PENDING_AND_NEW_ENTRIES_FILL_NO_EXITS")
    return dict(status="AVAILABLE", confirmed_remaining=confirmed,
        hypothetical_all_fills=hypothetical,
        pending_quantities_are="UNFILLED_REMAINDERS_NOT_ORIGINAL_ORDER_QUANTITIES",
        pending_entries_still_reserved=deepcopy(context["pending_entries"]),
        requested_cancellations_do_not_release_risk=True,
        allocation_semantics="NEW_REVISION_REMAINING_BASIS_PRIOR_EXIT_FILLS_NOT_RECREATED",
        actual_net_pnl=None)


def _holding_projection(plan, context):
    try:
        return _holding_projection_values(plan, context)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return dict(status="UNAVAILABLE", reason="incomplete_holding_projection_inputs", actual_net_pnl=None)


def _current_reserved_risk(context):
    try:
        result = risk_snapshot(initial_equity=context["initial_equity"], side=context["side"],
            hard_stop=context["previous_hard_stop"], positions=context["positions"],
            pending_entries=context["pending_entries"], entries=[],
            risk_fraction=context.get("scenario_risk_fraction", .02),
            **{key: context[key] for key in ("realized_loss", "fees_paid", "funding_paid",
                                            "estimated_cost_rate", "slippage_bps")})
        return dict(status="AVAILABLE", **result, reservation_release_confirmed=False)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return dict(status="UNAVAILABLE", reservation_release_confirmed=False)


def _check_lifecycle_context(context):
    if not isinstance(context.get("positions"), list) or not isinstance(context.get("pending_entries"), list):
        raise ValueError("research_missing_exposure_context")
    if context.get("scenario_id") is None and (context["positions"] or context["pending_entries"]):
        raise ValueError("research_flat_only")


def _entry_risk_components(plan, context):
    stop = _decimal(plan["hard_stop"])
    cost = _decimal(context["estimated_cost_rate"])
    slip = _decimal(context["slippage_bps"]) / 10000
    sign = Decimal(1 if plan["side"] == "LONG" else -1)
    rows = []
    for row in plan["entries"]:
        price, quantity = _decimal(row["price"]), _decimal(row["quantity"])
        unit = risk_snapshot(initial_equity=context["initial_equity"], side=plan["side"],
            hard_stop=plan["hard_stop"], positions=[], pending_entries=[],
            entries=[dict(price=row["price"], quantity=1)], realized_loss=0,
            fees_paid=0, funding_paid=0, estimated_cost_rate=context["estimated_cost_rate"],
            slippage_bps=context["slippage_bps"])["proposed_risk"]
        rows.append(dict(id=row["id"], quantity=row["quantity"], price=row["price"],
            stop_distance_per_btc=float(max(Decimal(0), sign * (price - stop))),
            cost_reserve_per_btc=float(price * cost), stop_slippage_reserve_per_btc=float(stop * slip),
            risk_per_btc=unit, quantity_risk=float(quantity * Decimal(str(unit)))))
    return rows


def _supplied_instrument_filters(plan, context):
    """Subset available in saved context; not exchange preflight/authorization."""
    tick, step, minimum, notional, mark = (_decimal(context[key]) for key in
        ("price_tick", "quantity_step", "minimum_quantity", "minimum_notional", "mark_price"))
    if min(tick, step, minimum, notional, mark) <= 0:
        raise ValueError("invalid_instrument_filters")
    stop = _decimal(plan["hard_stop"])
    if stop % tick:
        raise ValueError("stop_off_tick")
    if (plan["side"] == "LONG" and stop >= mark) or (plan["side"] == "SHORT" and stop <= mark):
        raise ValueError("stop_crossed_market")
    for kind in ("entries", "take_profits", "partial_stops"):
        for row in plan[kind]:
            price = _decimal(row["price"])
            if price % tick:
                raise ValueError("price_off_tick")
            if kind == "entries":
                quantity = _decimal(row["quantity"])
                if quantity % step:
                    raise ValueError("quantity_off_step")
                if quantity < minimum or quantity * price < notional:
                    raise EconomicProposalRejection("entry_below_minimum", dict(
                        quantity=float(quantity), price=float(price),
                        minimum_quantity=float(minimum), minimum_notional=float(notional)))


def calculate_candidate(wire, context, *, candidate_id, original_input_now=None, strict=False, explanation_snapshot=None):
    """Return rejected candidates too; never repair model prices or quantities."""
    _check_lifecycle_context(context)
    original_now=context["now"] if original_input_now is None else original_input_now
    raw = deepcopy(wire)
    receipt = dict(id=candidate_id, raw_proposal=raw, raw_sha256=receipt_hash(raw),
                   context_sha256=receipt_hash(context),
                   eligible=False, validation_scope="PURE_WIRE_ECONOMIC_AND_SUPPLIED_FILTERS_ONLY",
                   execution_authorized=False,
                   cost_semantics=dict(estimated_cost_rate=context.get("estimated_cost_rate"),
                       estimated_cost_rate_meaning="ONE_REMAINING_ENTRY_EXIT_FUNDING_RISK_RESERVE_NOT_PER_SIDE_FEE",
                       slippage_bps=context.get("slippage_bps"),
                       slippage_meaning="HARD_STOP_RISK_RESERVE_NOT_KNOWN_TP_EXECUTION_COST",
                       actual_future_fees=None, actual_future_funding=None))
    try:
        normalized = validate_wire_proposal(raw, context)
        if normalized["action"] not in ("OPEN", "ADJUST", "EXIT", "REDUCE"):
            raise ValueError("candidate_not_actionable")
        core = {key: value for key, value in strip_metadata(normalized).items()
                if key not in {"review", "recovery"}}
        if core["action"] in {"EXIT", "REDUCE"}:
            effective = preview_execution_prices(core,context,original_input_now=original_now)
            quotes=context.get("limit_price_quotes")
            receipt["pricing_basis"]=dict(kind="ORIGINAL_INPUT_HYPOTHETICAL",
                original_input_now=original_now,quote_captured_at=quotes.get("captured_at") if isinstance(quotes,dict) else None,
                validation_now=context["now"],execution_authorized=False)
            receipt.update(effective_plan=effective, effective_sha256=receipt_hash(effective),
                current_reserved_risk=_current_reserved_risk(context),
                projection=_holding_projection(effective, context), eligible=True)
            bind_receipt(receipt, context, snapshot=explanation_snapshot)
            receipt["receipt_sha256"] = receipt_hash(receipt)
            return receipt
        # Canceled orders retain their reservation until confirmed by the host.
        receipt["raw_risk"] = risk_snapshot(initial_equity=context["initial_equity"],
            side=core["side"], hard_stop=core["hard_stop"], positions=context["positions"],
            pending_entries=context["pending_entries"],
            entries=core["entries"], risk_fraction=context.get("scenario_risk_fraction", .02),
            **{key: context[key] for key in ("realized_loss", "fees_paid", "funding_paid",
                                            "estimated_cost_rate", "slippage_bps")})
        receipt["raw_per_entry_risk"] = _entry_risk_components(core, context)
        _supplied_instrument_filters(core, context)
        if context.get('entry_contract_version') == 2:
            # Goal-only rejection must not hide malformed/unauthorized core plans.
            validate_scenario(core, context)
        validate_goal_pair(normalized, None, context)
        effective = preview_execution_prices(core,context,original_input_now=original_now)
        goal = validate_goal_pair(normalized, effective, context)
        if goal is not None:
            receipt['resolved_goal'] = goal
        quotes=context.get("limit_price_quotes")
        receipt["pricing_basis"]=dict(kind="ORIGINAL_INPUT_HYPOTHETICAL",
            original_input_now=original_now,quote_captured_at=quotes.get("captured_at") if isinstance(quotes,dict) else None,
            validation_now=context["now"],execution_authorized=False)
        _supplied_instrument_filters(effective, context)
        receipt.update(effective_plan=effective, effective_sha256=receipt_hash(effective),
            effective_per_entry_risk=_entry_risk_components(effective, context),
            projection=_holding_projection(effective, context) if context.get("scenario_id") is not None
                else _gross_projection(effective, context), eligible=True)
    except EntryConvergenceRejection as exc:
        if strict:
            raise
        receipt.update(rejection=str(exc), rejection_type='PREAUTHORITY_ENTRY_POLICY',
                       entry_policy_evidence=deepcopy(context.get('entry_convergence')))
    except GoalConsistencyRejection as exc:
        if strict:
            raise
        receipt.update(rejection=str(exc), rejection_type='PREAUTHORITY_PLAN_CONSISTENCY',
                       goal_evidence=exc.evidence)
    except ScenarioValidationError as exc:
        if strict or (context.get('entry_contract_version') == 2 and not isinstance(exc, EconomicProposalRejection)):
            raise
        receipt["rejection"] = str(exc)
    except ValueError as exc:
        if strict or context.get('entry_contract_version') == 2:
            raise
        # Local wire/calculator validators emit fixed codes, not input values.
        receipt["rejection"] = str(exc)
    except (KeyError, TypeError, ArithmeticError):
        if strict or context.get('entry_contract_version') == 2:
            raise
        receipt["rejection"] = "wire_economic_or_calculation_invalid"
    if receipt.get('eligible') is True:
        from live.scenario_entry_economics import assess
        economics = assess(receipt['effective_plan'], context, receipt.get('projection', {}))
        if economics is not None:
            receipt['entry_economics'] = economics
            if economics['status'] != 'PASS':
                receipt.update(eligible=False, rejection_type='PREAUTHORITY_ECONOMIC',
                    rejection='entry_payoff_below_minimum' if economics['status'] == 'REJECTED'
                              else 'entry_payoff_unavailable')
    bind_receipt(receipt, context, snapshot=explanation_snapshot)
    receipt["receipt_sha256"] = receipt_hash(receipt)
    return receipt


_ENTRY_PROGRESS_INPUT_LIMIT = 100_000


def _initial_entry_progress_units(context):
    """Initial entry-to-Mark geometry is neither portfolio PnL nor current risk."""
    try:
        evidence = context.get('entry_thesis')
        positions = context.get('positions')
        if (not context.get('scenario_id') or context.get('accounting_status') != 'confirmed'
                or not isinstance(positions, list) or not positions
                or any(not isinstance(p, dict) or _decimal(p.get('quantity')) <= 0 for p in positions)
                or not isinstance(evidence, dict) or evidence.get('status') != 'VERIFIED'):
            return None
        side = evidence['side']
        if side not in ('LONG', 'SHORT') or side != context.get('side'):
            return None
        average, stop, mark = (_decimal(evidence[k]) for k in ('initial_fill_average', 'initial_hard_stop', 'current_mark'))
        now, observed, first, captured = (_decimal(v) for v in
            (context['now'], evidence['observed_at'], evidence['first_fill_at'], context['account_captured_at']))
        if (min(average, stop, mark, first) <= 0 or not first <= captured <= now
                or not first <= observed <= now or now - captured > 120 or now - observed > 120
                or abs(observed - captured) > Decimal('0.001') or mark != _decimal(context['mark_price'])):
            return None
        sign = Decimal(1) if side == 'LONG' else Decimal(-1)
        distance = sign * (average - stop)
        progress = sign * (mark - average)
        if distance <= 0 or abs(progress - _decimal(evidence['favorable_price_change_from_initial_average'])) > Decimal('0.000001'):
            return None
        value = float(progress / distance)
        if not math.isfinite(value):
            return None
        return dict(value=value, meaning='SIGNED_PRICE_PROGRESS_OVER_INITIAL_STOP_DISTANCE_NOT_PNL_OR_CURRENT_RISK',
                    positive='FAVORABLE', negative='ADVERSE',
                    scope='INITIAL_OPEN_ENDPOINT_ONLY_NOT_INTERVENING_PATH_OR_EXIT_RULE')
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
        return None


def _project_initial_entry_progress(projected, context):
    """Add only if the original model input survives unchanged within its cap."""
    value = _initial_entry_progress_units(context)
    if value is None:
        return projected
    try:
        from live.scenario_model_input import project_model_input, restore_model_input
        if len(_encoded(projected).encode()) > _ENTRY_PROGRESS_INPUT_LIMIT:
            return projected
        original = restore_model_input(projected)
        logical = deepcopy(original)
        target = logical['contract_context']
        if (target.get('scenario_id') != context.get('scenario_id')
                or target.get('entry_thesis') != context.get('entry_thesis')
                or target.get('mark_price') != context.get('mark_price')
                or target.get('positions') != context.get('positions')
                or target.get('accounting_status') != context.get('accounting_status')
                or target.get('account_captured_at') != context.get('account_captured_at')
                or 'initial_entry_progress_units' in target):
            return projected
        target['initial_entry_progress_units'] = value
        candidate, _ = project_model_input(logical, allow_gap_rescue=True)
        if len(_encoded(candidate).encode()) > _ENTRY_PROGRESS_INPUT_LIMIT:
            return projected
        restored = restore_model_input(candidate)
        restored['contract_context'].pop('initial_entry_progress_units', None)
        return candidate if restored == original else projected
    except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
        return projected


def _model_payload(snapshot, context, response_contract, *, calculated_candidates=None, prepared_plans=None):
    def project(source):
        baseline = _base_model_payload(source, context, response_contract,
            calculated_candidates=calculated_candidates, prepared_plans=prepared_plans)
        if not explanation_enabled(context):
            baseline = project_holding_price_order_input(baseline)[0]
        projected = project_explanation_input(baseline, context, snapshot=source)
        if calculated_candidates is None and context.get('scenario_id') is None:
            from live.scenario_entry_geometry import project as project_entry_geometry
            projected = project_entry_geometry(projected, source, context)
        projected = _project_initial_entry_progress(projected, context)
        from live.scenario_position_flow import project_holding_management_input
        return _project_kept_exit_paths(project_holding_management_input(projected, context), context)
    return project(snapshot)


def _receipt_model_view(receipt):
    """A model summary points to a full host receipt; it is not that receipt."""
    from live.scenario_model_input import _wrapper_proposal
    fields = {'id','eligible','template_only','execution_authorized','validation_scope',
              'effective_plan','projection','entry_economics','resolved_goal','explanation',
              'error','errors','rejection','rejection_type','rejection_codes'}
    view = {k:v for k,v in receipt.items() if k in fields}
    raw = (_wrapper_proposal(receipt) or receipt.get('raw_allocation_proposal')
           or receipt.get('raw_proposal') or {})
    if ('effective_plan'not in view and isinstance(receipt.get('raw_proposal'),dict)
            and (receipt.get('eligible')is True or receipt.get('id')=='WAIT')):
        view['effective_plan'] = receipt['raw_proposal']
    if 'receipt_sha256'in receipt:
        view['host_receipt_sha256'] = receipt['receipt_sha256']
    view['draft_intent'] = {k:raw[k] for k in
        ('action','side','rationale','entry_contract','thesis_assessment','review','recovery') if k in raw}
    return view


def _base_model_payload(snapshot, context, response_contract, *, calculated_candidates=None, prepared_plans=None):
    model_context = deepcopy(context)
    model_context.pop('seen_action_ids', None)
    model_context.pop('entry_goal_source', None)
    model_context.pop('entry_goal_decision_slot', None)
    model_context.pop('explanation_source', None)
    if explanation_enabled(context):
        model_context.pop('holding_price_order', None)
    recovery = context.get("recovery") or {}
    # Interpretation-only copy. Do not alter source context, receipt hashes or
    # authorization; missing/non-boolean metadata remains explicitly unknown.
    model_context["current_decision_state"] = dict(
        first_observation=recovery.get("first_observation") if type(recovery.get("first_observation")) is bool else None,
        recovery_eligible=recovery.get("eligible") if type(recovery.get("eligible")) is bool else None,
        wire_action_choices=response_schema(context)["properties"]["action"]["enum"],
        execution_authorization="NOT_GRANTED_BY_MODEL_INPUT")
    model_context["ma_structure"] = build_ma_structure_context(snapshot, context)
    if context.get("scenario_id") is None:
        model_context["current_primary_frame_facts"] = scenario_llm._current_primary_frame_facts(snapshot)
    payload = dict(market_snapshot=snapshot, contract_context=model_context,
                   response_contract=dict(authority='API response_schema; full contract retained by the host',
                       **{k:response_contract[k] for k in ('expires_at','reservation_expires_at') if k in response_contract}))
    if prepared_plans is not None:
        if calculated_candidates is None:
            payload['prepared_entry_plans'] = prepared_plans
        else:
            payload['prepared_entry_plan_sources'] = [{k:p[k] for k in
                ('id','side','entry_kind','target_basis','stop_ref','stop_phase')}
                for p in prepared_plans['layouts']]
    if calculated_candidates is not None:
        view = dict(calculated_candidates)
        rows = calculated_candidates.get('candidates')
        if isinstance(rows, list):
            view['candidates'] = [_receipt_model_view(candidate) for candidate in rows]
        if isinstance(calculated_candidates.get('wait'), dict):
            view['wait'] = _receipt_model_view(calculated_candidates['wait'])
        payload['calculated_candidates'] = view
        # Full receipts stay in host results/tapes; the model sees one effective plan.
        # Model-only labels: do not change hash-bound numerical receipts or tapes.
        payload["calculation_field_units"] = {"projection.targets": {
            "quantity": "BTC", "gross_pnl": "USDT_FOR_THIS_TARGET_QUANTITY_BEFORE_COSTS",
            "gross_reward_per_btc": "USDT_PER_BTC_NOT_TOTAL_PNL",
            "gross_reward_to_reserved_risk_ratio": "DIMENSIONLESS_NOT_USDT"}}
        payload['calculation_field_units']['joint_exit_paths'] = dict(
            columns=['sequence_ids','remaining_before_hard_stop_BTC','gross_pnl_USDT','fee_only'],
            sequences=dict(TP_STOP='TP legs then residual hard SL',SL_STOP='partial SL legs then residual hard SL',
                TP1_STOP='first TP then residual hard SL',SL1_STOP='first partial SL then residual hard SL',
                TP1_SL='first TP then partial SL then residual hard SL',
                SL1_TP='first partial SL then TP then residual hard SL',STOP='hard SL only'),
            assumptions='Price-ordered initial revision quotas clipped to remaining lots; omitted legs never apply. Hypothetical fills, not probabilities. Funding/slippage/timing/races unmodelled, not zero.',
            fee_only=['assumed_entry_fee_USDT','assumed_exit_fee_USDT','estimated_net_before_unmodelled_costs_USDT'],
            version2='All entries fill; original entry fee charged once.',
            version3='Confirmed remaining gross only; zero new entry fee; net includes recorded_net once.',
            unknown='null fee_only is UNKNOWN')
        payload['calculation_field_units']['whole_plan_payoff'] = dict(
            net_reward='USDT: all TP legs then residual hard SL; not expected runner profit',
            full_stop_loss='USDT: full hard SL without partial SL risk credit',
            ratio='dimensionless conditional payoff, not expectancy/probability')
    payload = analysis_model_view(payload)
    if context.get('scenario_id') is None:
        from live.scenario_entry_display import project_entry_display_input
        return project_entry_display_input(payload, source_candidates=calculated_candidates)[0]
    baseline, _ = project_position_flow_input(payload)
    if not calculated_candidates or not context.get('positions') or context.get('accounting_status') != 'confirmed':
        return baseline
    # Select existing history before adding optional calculations, just as for
    # entry_display. Neither the hash-bound receipts nor source facts are edited.
    from live.scenario_model_input import restore_model_input, project_model_input
    from live.scenario_position_flow import _size, _MODEL_INPUT_LIMIT
    view = []
    candidates = calculated_candidates.get('candidates', [])
    if not isinstance(candidates, list) or len(candidates) > 2:
        return baseline
    for candidate in candidates:
        plan = candidate.get('effective_plan', {})
        if candidate.get('eligible') is not True or plan.get('action') != 'ADJUST':
            continue
        if receipt_hash({k: v for k, v in candidate.items() if k != 'receipt_sha256'}) != candidate.get('receipt_sha256'):
            continue
        try:
            if plan.get('side') != context.get('side'):
                continue
            validation_now = candidate['pricing_basis']['validation_now']
            _decimal(validation_now)
            calculation_context = dict(context, now=validation_now)
            if receipt_hash(calculation_context) != candidate.get('context_sha256'):
                continue
            projection = _gross_projection(plan, calculation_context, lots=context['positions'], holding_paths=True,
                assumption='CONFIRMED_REMAINING_NEW_REVISION_NO_ADDITIONAL_FILLS')
            if 'joint_exit_paths' in projection:
                view.append([candidate['id'], candidate['receipt_sha256'], projection['joint_exit_paths']])
        except (KeyError, TypeError, ValueError, OverflowError, ArithmeticError):
            continue  # Optional diagnostics must not suppress an existing safe decision.
    if not view:
        return baseline
    logical = restore_model_input(baseline)
    logical['contract_context']['holding_exit_paths'] = view
    augmented, _ = project_model_input(logical, allow_gap_rescue=True)
    return augmented if _size(augmented) <= _MODEL_INPUT_LIMIT else baseline


ALLOCATION_SINGLE_PROMPT = """Allocation mode: new entries use allocation_fraction, not BTC quantity. Fractions allocate remaining confidence-limited NEW risk, not equity, margin or holdings; total new-entry fractions must not exceed one. The host owns sizing, costs and lot rounding and may impose a smaller aggregate NEW-entry batch risk ceiling; do not inflate confidence/fractions to pass or compensate. OPEN target fractions use the initial batch; new ADJUST targets use the actual remainder. Never rewrite observed positions. REDUCE uses its separate fields. Failed minimums/budgets are rejected, not repaired.
"""


def calculate_allocation_candidate(raw, context, *, candidate_id, original_input_now=None, explanation_snapshot=None):
    """Keep model allocation and host quantity wire distinct and hash-bound."""
    from live.scenario_risk_allocation import allocate_entry_risk, validate_allocation_prices
    original = deepcopy(raw)
    try:
        if not model_matches(original, allocation_response_schema(context), context):
            raise ValueError("research_allocation_contract")
        wire, allocation = allocate_entry_risk(original, context)
        receipt = calculate_candidate(wire, context, candidate_id=candidate_id,
                                      original_input_now=original_input_now, explanation_snapshot=explanation_snapshot)
        receipt.update(raw_allocation_proposal=original, raw_allocation_sha256=receipt_hash(original),
                       allocation_receipt=allocation, allocation_sha256=receipt_hash(allocation))
        if receipt["eligible"]:
            validate_allocation_prices(receipt["effective_plan"], allocation, context)
    except (ScenarioValidationError, ValueError) as exc:
        if context.get('entry_contract_version') == 2 and not isinstance(exc, (EconomicProposalRejection, EntryConvergenceRejection)):
            raise
        if "receipt" not in locals():
            receipt = dict(id=candidate_id, raw_allocation_proposal=original,
                raw_allocation_sha256=receipt_hash(original), execution_authorized=False)
        receipt.update(eligible=False, rejection=str(exc))
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = receipt_hash(receipt)
    return receipt


def allocation_response_schema(context):
    """Research-only entry schema; the legacy wire contract is untouched."""
    schema = deepcopy(response_schema(context))
    row = schema["properties"]["entries"]["items"]
    row["properties"].pop("quantity")
    row["properties"]["allocation_fraction"] = {"type": "number"}
    row["required"] = ["allocation_fraction" if key == "quantity" else key for key in row["required"]]
    if (context.get("recovery") or {}).get("first_observation") is True:
        schema["properties"]["action"]["enum"] = ["WAIT"]
        for field in ("entries", "take_profits", "partial_stops"):
            schema["properties"][field]["maxItems"] = 0
    return schema


ACTION_ALLOCATION_MODES = {"action-allocation-single-v1", "action-allocation-calculated-v1"}
ECONOMIC_REJECT_MODES = {"economic-reject-single-v1", "economic-reject-calculated-v1"}


def calculate_economic_candidate(raw, context, *, candidate_id, original_input_now=None, explanation_snapshot=None):
    """Only producer-typed economic failures may become a non-authorizing result.

    Validate every raw field, identity and lifecycle before sizing can reject a
    small allocation. The preflight quantities preserve fractional risk weights;
    they are NOT executable quantities and never leave this validation scope.
    """
    from live.scenario_risk_allocation import (
        _filters, _number, _worst_unit_risk, allocate_entry_risk, validate_allocation_prices,
    )
    if not model_matches(raw, {"anyOf": _action_allocation_branches(context)["anyOf"]}, context):
        raise ValueError("research_allocation_contract")
    from live.scenario_management_quarantine import entry_aggregate_context
    limited_entry_management = entry_aggregate_context(context, raw['action'])
    if (context.get("accounting_status") not in {"confirmed", "pending"}
            or (raw["action"] in {"OPEN", "ADJUST"} and context["accounting_status"] != "confirmed")
            or context.get("protection_ok") is not True
            or (context.get("legacy_fenced") is not False and not limited_entry_management)
            or type(context.get("new_risk_blocked")) is not bool):
        raise ValueError("research_unsafe_economic_context")
    mark = _number(context.get("mark_price"), "mark_price", positive=True)
    if raw["action"] in {"OPEN", "ADJUST"}:
        stop = _number(raw["hard_stop"], "hard_stop", positive=True)
        if (raw["side"] == "LONG" and stop >= mark) or (raw["side"] == "SHORT" and stop <= mark):
            raise ValueError("stop_crossed_market")
    preflight = deepcopy(raw)
    fractions = []
    if preflight["entries"]:
        _filters(preflight, context)
    for row in preflight["entries"]:
        fraction = _number(row.pop("allocation_fraction"), "fraction", positive=True)
        fractions.append(fraction)
        row["quantity"] = float(fraction / _worst_unit_risk(raw, context, row["price"]))
    if sum(fractions) > 1:
        raise ScenarioValidationError("allocation_fraction_sum")
    normalized = validate_wire_proposal(preflight, context)
    core = {k: v for k, v in strip_metadata(normalized).items() if k not in {"review", "recovery"}}
    receipt = dict(id=candidate_id, raw_allocation_proposal=deepcopy(raw),
        raw_allocation_sha256=receipt_hash(raw), context_sha256=receipt_hash(context),
        prevalidation_basis="UNROUNDED_RELATIVE_RISK_WEIGHTS_NOT_EXECUTABLE",
        execution_authorized=False, eligible=False)
    try:
        wire, allocation = allocate_entry_risk(raw, context)
        receipt.update(raw_proposal=deepcopy(wire), raw_sha256=receipt_hash(wire),
                       allocation_receipt=allocation, allocation_sha256=receipt_hash(allocation))
        calculated = calculate_candidate(wire, context, candidate_id=candidate_id,
            original_input_now=original_input_now, strict=True, explanation_snapshot=explanation_snapshot)
        receipt.update(calculated)
        validate_allocation_prices(receipt["effective_plan"], allocation, context)
    except EntryConvergenceRejection as exc:
        unrestricted = dict(context)
        unrestricted.pop('entry_convergence', None)
        try:
            validate_scenario(core, unrestricted)
        except EconomicProposalRejection:
            pass
        receipt.update(eligible=False, rejection=str(exc), rejection_type='PREAUTHORITY_ENTRY_POLICY',
                       entry_policy_evidence=deepcopy(context.get('entry_convergence')))
    except GoalConsistencyRejection as exc:
        receipt.update(eligible=False, rejection=str(exc),
            rejection_type='PREAUTHORITY_PLAN_CONSISTENCY', goal_evidence=exc.evidence)
    except EconomicProposalRejection as exc:
        try:
            validate_scenario(core, context)
        except EconomicProposalRejection:
            # Only the typed risk verdict is immaterial for this non-executable
            # preflight. Every other field/lifecycle failure remains fatal.
            pass
        receipt.update(eligible=False, rejection=exc.code,
            rejection_type="PREAUTHORITY_ECONOMIC", economic_evidence=exc.evidence)
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = receipt_hash(receipt)
    return receipt


def _action_allocation_branches(context, actions=None):
    base = allocation_response_schema(context)
    allowed = base["properties"]["action"]["enum"]
    branches = []
    for action in allowed:
        if actions is not None and action not in actions:
            continue
        branch = deepcopy(base)
        props = branch["properties"]
        props["action"]["enum"] = [action]
        if context.get("entry_contract_version") in (1, 2):
            if action == "OPEN":
                from live.scenario_entry_contract import entry_contract_schema
                props["entry_contract"] = entry_contract_schema(context['entry_contract_version'])
            else:
                props["entry_contract"] = {"type": "null"}
        if action in {"WAIT", "EXIT", "REDUCE"}:
            for field in ("side", "hard_stop", "chase"):
                props[field] = {"type": "null"}
            for field in ("entries", "take_profits", "partial_stops"):
                props[field]["maxItems"] = 0
            if action == 'REDUCE':
                props['cancel_entry_ids']['maxItems'] = 0
        else:
            sides = [context["side"]] if action == "ADJUST" else ["LONG", "SHORT"]
            props["side"] = dict(type="string", enum=sides)
            props["hard_stop"] = dict(type="number")
            props["chase"] = deepcopy(props["chase"]["anyOf"][0])
            if action == "OPEN":
                props["entries"]["minItems"] = 1
        if 'pending_renewal' in props and action != 'WAIT':
            props['pending_renewal'] = {'type': 'null'}
        if 'reduce_fraction' in props:
            props['reduce_fraction'] = {'type': 'number' if action == 'REDUCE' else 'null'}
        branches.append(branch)
    return {"anyOf": branches}


def action_allocation_response_schema(context):
    """Explicit research v1 wrapper; legacy proposal schema stays unchanged."""
    return _object(dict(proposal=_action_allocation_branches(context)))


def _bind_model_wrapper(receipt, wrapper, extracted):
    receipt.update(raw_model_wrapper=deepcopy(wrapper), raw_model_wrapper_sha256=receipt_hash(wrapper),
                   raw_allocation_proposal=deepcopy(extracted), raw_allocation_sha256=receipt_hash(extracted),
                   action_contract_version=1)
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = receipt_hash(receipt)


def allocation_system_prompt(base):
    return base + ALLOCATION_SINGLE_PROMPT


def research_review(snapshot, context, response_contract, *, generate, record,
                    clock=time.time, research_mode="legacy"):
    """At most two no-retry calls, original 75-second budget; never an order API.

    record(event_dict) must durably preserve evidence or raise. Historical callers
    supply a clock mapped to that observation, including actual elapsed latency.
    """
    from live.scenario_recovery import _wire
    if research_mode not in {"legacy", "allocation-single", "allocation-calculated"} | ACTION_ALLOCATION_MODES | ECONOMIC_REJECT_MODES:
        raise ValueError("research_review_mode")
    if _wire.get() is not None:
        raise ValueError("production_wire_capture_forbidden")
    if not callable(generate) or not callable(record):
        raise ValueError("explicit_research_dependencies_required")
    if context.get('entry_contract_version') == 2 and 'single' in research_mode:
        raise ValueError('entry_goal_requires_two_phase_review')
    _check_lifecycle_context(context)
    started = clock()
    original = receipt_hash(dict(snapshot=snapshot, context=context,
                                 response_contract=response_contract))
    frozen_snapshot, frozen_context, contract = deepcopy((snapshot, context, response_contract))
    prepare_goal_source(frozen_snapshot, frozen_context)

    def fresh():
        now = clock()
        if not 0 <= now - started < scenario_llm.TIMEOUT_SECONDS:
            raise ValueError("research_deadline")
        if frozen_snapshot.get("valid") is not True:
            raise ValueError("snapshot_unavailable")
        max_age = frozen_context.get("max_input_age_seconds", 120)
        if type(max_age) not in (int, float) or not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("research_invalid_input_age")
        for stamp in (snapshot_input_time(frozen_snapshot),
                      frozen_context.get("input_captured_at"),
                      frozen_context.get("account_captured_at")):
            if type(stamp) not in (float, int) or not math.isfinite(stamp) or not 0 <= now - stamp <= min(120, max_age):
                raise ValueError("research_stale_input_or_account")
        if receipt_hash(dict(snapshot=snapshot, context=context, response_contract=response_contract)) != original:
            raise ValueError("research_input_mutated")
        return now

    base = scenario_llm.system_instructions(context)
    allocation = research_mode != "legacy"
    if allocation:
        base = allocation_system_prompt(base)
    fresh()
    economic_reject = research_mode in ECONOMIC_REJECT_MODES
    action_contract = research_mode in ACTION_ALLOCATION_MODES or economic_reject
    schema = allocation_response_schema(frozen_context) if allocation else response_schema(frozen_context)
    payload = _model_payload(frozen_snapshot, frozen_context, contract)
    wait_schema = deepcopy(schema)
    wait_schema["properties"]["action"]["enum"] = ["WAIT"]
    for field in ("side", "hard_stop", "chase", "stop_revision"):
        if field in wait_schema["properties"]:
            wait_schema["properties"][field] = {"type": "null"}
    for field in ("entries", "take_profits", "partial_stops"):
        wait_schema["properties"][field]["maxItems"] = 0
    candidate_schema = deepcopy(schema)
    candidate_schema["properties"]["action"]["enum"] = [a for a in candidate_schema['properties']['action']['enum'] if a != 'WAIT']
    convergence_wait = (frozen_context.get('scenario_id') is None
        and frozen_context.get('positions') == [] and frozen_context.get('pending_entries') == []
        and (frozen_context.get('entry_convergence') or {}).get('status') == 'BLOCKED')
    wait_only = schema["properties"]["action"]["enum"] == ["WAIT"] or convergence_wait
    first_observation = (frozen_context.get("recovery") or {}).get("first_observation") is True
    draft_schema = _object(dict(wait_proposal=wait_schema,
        candidates=dict(type="array", maxItems=0 if wait_only or first_observation else 2,
                        items=candidate_schema), no_candidate_reason=rationale_schema() if explanation_enabled(context) else dict(type="string")))
    if action_contract:
        schema = action_allocation_response_schema(frozen_context)
        payload = _model_payload(frozen_snapshot, frozen_context, contract)
        draft_schema["properties"]["wait_proposal"] = _action_allocation_branches(frozen_context, {"WAIT"})
        # Empty arrays still need a valid item schema, even in WAIT-only state.
        candidates = _action_allocation_branches(frozen_context, {"OPEN", "ADJUST", "EXIT", "REDUCE"})
        draft_schema["properties"]["candidates"]["items"] = candidates if candidates["anyOf"] else _action_allocation_branches(frozen_context)

    def call(phase, appendix, data, output_schema):
        now = fresh()
        prompt = _encoded(data)
        if len(prompt.encode()) > 100_000:
            raise ValueError("research_input_size")
        system = base + appendix
        if explanation_enabled(frozen_context):
            system += EXPLANATION_PROMPT
        request = dict(system_prompt=system, user_prompt=prompt,
            model=scenario_llm.MODEL, reasoning_effort=scenario_llm.EFFORT,
            fast_tier=False, timeout=scenario_llm.TIMEOUT_SECONDS - (now - started),
            mcp_profile=None, response_schema=output_schema, retry_transport=False)
        request_hash = receipt_hash(request)
        record(deepcopy(dict(kind="MODEL_REQUEST", phase=phase, request=request,
                             request_sha256=request_hash, started_at=now)))
        actual_started = fresh()
        request["timeout"] = scenario_llm.TIMEOUT_SECONDS - (actual_started - started)
        actual_request_hash = receipt_hash(request)
        # No retry, fallback model or logical repair on any failure.
        try:
            result = generate(**deepcopy(request))
        except Exception as exc:
            from live.scenario_oauth import safe_failure_reason
            message = str(exc)
            detail = safe_failure_reason(message[len('scenario_oauth_failed:'):]) if message.startswith('scenario_oauth_failed:') else None
            error_event = dict(kind="MODEL_ERROR", phase=phase, code="model_call_failed",
                request_sha256=request_hash, prepared_request_sha256=request_hash, actual_request=request,
                actual_request_sha256=actual_request_hash, elapsed_seconds=clock() - actual_started)
            if detail is not None:
                error_event['failure_detail'] = detail
            record(deepcopy(error_event))
            raise ValueError("research_model_call_failed" + (':' + detail if detail else '')) from None
        from live.scenario_oauth import usage_counts
        usage = usage_counts(getattr(result,'usage',None))
        record(deepcopy(dict(kind="MODEL_RAW", phase=phase, request_sha256=request_hash,
                             prepared_request_sha256=request_hash,
                             actual_request=request, actual_request_sha256=actual_request_hash,
                             raw=result.text, elapsed_seconds=clock() - actual_started,
                             **({'usage':usage} if usage is not None else {}))))
        fresh()
        parsed = scenario_llm.parse_proposal(result.text)
        if not model_matches(parsed, output_schema, frozen_context):
            raise ValueError("research_response_contract")
        return parsed

    def rejected_result(bundle):
        fresh()
        digest = receipt_hash(bundle)
        result = dict(mode=research_mode, disposition="REJECTED_NO_ACTION",
            rejection_policy="preauthority-economic-reject-v1", executable=False,
            orders_submitted=0, calls=1, candidates=bundle, bundle_sha256=digest,
            input_sha256=original, elapsed_seconds=clock()-started,
            recovery_authorization="NOT_EVALUATED", fresh_execution_validation="NOT_PERFORMED")
        record(deepcopy(dict(kind="RESEARCH_REJECTION", body=result, sha256=receipt_hash(result))))
        record(deepcopy(dict(kind="RESEARCH_RESULT", pending_post_record_time_check=True, body=result)))
        fresh()
        return deepcopy(result)

    if research_mode in {"allocation-single", "action-allocation-single-v1", "economic-reject-single-v1"}:
        appendix = ""
        if action_contract:
            appendix += "\nReturn exactly the root object {proposal: <action-specific allocation proposal>}. Follow its action branch; inactive fields must be null or empty as specified.\n"
        model_raw = call("single", appendix, payload, schema)
        raw = model_raw["proposal"] if action_contract else model_raw
        if raw.get('pending_renewal'):
            raise ValueError('pending_renewal_requires_calculated_selection')
        now_context = dict(frozen_context, now=fresh())
        if raw["action"] == "WAIT":
            normalized = validate_wire_proposal(raw, now_context)
            effective_wait = validate_scenario({k: v for k, v in strip_metadata(normalized).items() if k not in {"review", "recovery"}}, now_context)
            selected = dict(id="WAIT", raw_proposal=raw, input_sha256=original)
            if effective_wait.get('pending_renewal'):
                selected['effective_plan'] = effective_wait
            selected["receipt_sha256"] = receipt_hash(selected)
        else:
            calculator = calculate_economic_candidate if economic_reject else calculate_allocation_candidate
            selected = calculator(raw, now_context, candidate_id="c0",
                                                     original_input_now=frozen_context["now"])
            if action_contract:
                _bind_model_wrapper(selected, model_raw, raw)
            if not selected["eligible"]:
                if economic_reject:
                    selected["input_sha256"] = original
                    selected.pop("receipt_sha256", None)
                    selected["receipt_sha256"] = receipt_hash(selected)
                record(deepcopy(dict(kind="CALCULATED_CANDIDATE_REJECTED", body=selected)))
                if economic_reject:
                    return rejected_result(dict(wait=None, candidates=[selected], input_sha256=original))
                raise ValueError("research_allocation_rejected:" + selected["rejection"])
            selected["input_sha256"] = original
            del selected["receipt_sha256"]
            selected["receipt_sha256"] = receipt_hash(selected)
        if action_contract:
            _bind_model_wrapper(selected, model_raw, raw)
        bundle = dict(wait=selected if raw["action"] == "WAIT" else None,
                      candidates=[] if raw["action"] == "WAIT" else [selected], input_sha256=original)
        bundle_sha = receipt_hash(bundle)
        record(deepcopy(dict(kind="CALCULATED_CANDIDATES", body=bundle, sha256=bundle_sha)))
        final = dict(selection=selected["id"], receipt_sha256=selected["receipt_sha256"], rationale=raw["rationale"])
        if not isinstance(final["rationale"], str) or not 1 <= len(final["rationale"].strip()) <= 2000:
            raise ValueError("research_selection_rationale")
        if raw["expires_at"] <= fresh():
            raise ValueError("research_selected_expired")
        result = dict(mode="ORDER_FREE_ALLOCATION_SINGLE", executable=False, orders_submitted=0,
            calls=1, selection=final, selected=selected, candidates=bundle, bundle_sha256=bundle_sha,
            elapsed_seconds=clock()-started, recovery_authorization="NOT_EVALUATED",
            fresh_execution_validation="NOT_PERFORMED")
        if action_contract:
            result.update(mode="ORDER_FREE_ACTION_ALLOCATION_SINGLE_V1", action_contract_version=1)
        record(deepcopy(dict(kind="RESEARCH_RESULT", pending_post_record_time_check=True, body=result)))
        if raw["expires_at"] <= fresh():
            raise ValueError("research_selected_expired")
        return deepcopy(result)
    draft_prompt = DRAFT_PROMPT
    prepared = None
    if (action_contract and type(context.get('prepared_entry_plans_version')) is int and context['prepared_entry_plans_version']==1
            and frozen_context.get('scenario_id') is None and draft_schema['properties']['candidates']['maxItems']>0):
        from live.scenario_entry_plans import prepare, constrain
        menu=prepare(frozen_snapshot,frozen_context,build_ma_structure_context(frozen_snapshot,frozen_context))
        if menu['status']=='AVAILABLE':
            offered=deepcopy(draft_schema)
            offered['properties']['candidates']['items']=constrain(offered['properties']['candidates']['items'],menu)
            proposed_payload=_model_payload(frozen_snapshot,frozen_context,contract,prepared_plans=menu)
            if len(_encoded(proposed_payload).encode())<=100_000:
                draft_schema,payload,prepared=offered,proposed_payload,menu
    draft = call("candidate", draft_prompt, payload, draft_schema)
    category = "FIRST_OBSERVATION" if first_observation else "CANDIDATES_PROVIDED" if draft["candidates"] else "NO_CANDIDATES_SUPPLIED"
    if not explanation_enabled(context) and (len(draft["no_candidate_reason"]) > 2000 or (not draft["candidates"] and not draft["no_candidate_reason"].strip())):
        raise ValueError("research_missing_candidate_reason")
    now_context = dict(frozen_context, now=fresh())
    wait_wire = draft["wait_proposal"]
    failed_template = False
    if context.get("entry_contract_version") in (1, 2):
        assessment = wait_wire.get("thesis_assessment")
        assessments = [assessment] + [candidate.get("thesis_assessment") for candidate in draft["candidates"]]
        # Compare state only; retain original explanations in hash-bound receipts.
        states = [{key: value for key, value in item.items() if key != "reason"}
                  if isinstance(item, dict) else item for item in assessments]
        if any(state != states[0] for state in states[1:]):
            raise ValueError("research_entry_assessment_conflict")
        failed_template = isinstance(assessment, dict) and assessment.get("status") == "FAILED"
        if failed_template and (not draft["candidates"] or any(c["action"] != "EXIT" for c in draft["candidates"])):
            raise ValueError("research_failed_thesis_requires_exit")
    wait_normalized = validate_wire_proposal(wait_wire, now_context,
                                             _allow_failed_wait_template=failed_template)
    effective_wait = validate_scenario({k: v for k, v in strip_metadata(wait_normalized).items() if k not in {"review", "recovery"}}, now_context)
    wait_receipt = dict(id="WAIT", raw_proposal=wait_wire, input_sha256=original)
    if explanation_enabled(context):
        wait_receipt['effective_plan'] = effective_wait
        bind_receipt(wait_receipt, context, snapshot=frozen_snapshot)
    if effective_wait.get('pending_renewal'):
        wait_receipt['effective_plan'] = effective_wait
    if failed_template:
        wait_receipt.update(eligible=False, executable=False, template_only=True,
                            rejection="failed_thesis_requires_exit")
    wait_receipt["receipt_sha256"] = receipt_hash(wait_receipt)
    if action_contract:
        _bind_model_wrapper(wait_receipt, draft, wait_wire)
    calculator = calculate_economic_candidate if economic_reject else calculate_allocation_candidate if allocation else calculate_candidate
    receipts, readiness_reviews = [], []
    for index, model_wire in enumerate(draft["candidates"]):
        wire = deepcopy(model_wire)
        if prepared is not None and wire['action'] == 'OPEN':
            readiness = wire.pop('entry_readiness')
            if not 1 <= len(readiness['reason'].strip()) <= 600:
                raise ValueError('research_entry_readiness_reason')
            allowed = readiness['status'] == 'SUPPORTED_NOW'
            readiness_reviews.append(dict(candidate_id=f'c{index}', assessment=readiness,
                selectable=allowed, source='MODEL_QUALITATIVE_NOT_CALIBRATED'))
            if not allowed:
                continue
        receipts.append(calculator(wire, now_context, candidate_id=f"c{index}",
            original_input_now=frozen_context["now"],
            **({'explanation_snapshot': frozen_snapshot} if explanation_enabled(context) else {})))
    if effective_wait.get('pending_renewal'):
        # Preserve the model's exact WAIT proposal as an explicit selectable
        # renewal, plus a separately labelled host-derived inert fallback.
        renewal_receipt = deepcopy(wait_receipt)
        renewal_receipt.update(id='WAIT_RENEWAL', eligible=True, execution_authorized=False)
        renewal_receipt.pop('receipt_sha256', None)
        renewal_receipt['receipt_sha256'] = receipt_hash(renewal_receipt)
        fallback = deepcopy(wait_wire)
        fallback.update(pending_renewal=None, rationale='Host fallback: no waiting-lease extension selected.')
        wait_receipt = dict(id='WAIT', raw_proposal=fallback, input_sha256=original,
            proposal_source='HOST_NO_RENEWAL_FALLBACK', derived_from_model_proposal_sha256=receipt_hash(wait_wire))
        wait_receipt['receipt_sha256'] = receipt_hash(wait_receipt)
        receipts.append(renewal_receipt)
    if failed_template and not any(r["eligible"] for r in receipts):
        raise ValueError("research_failed_thesis_no_valid_exit")
    for receipt in receipts:
        bind_receipt(receipt, now_context, snapshot=frozen_snapshot)
        if action_contract:
            _bind_model_wrapper(receipt, draft, receipt["raw_allocation_proposal"])
        receipt["input_sha256"] = original
        del receipt["receipt_sha256"]
        receipt["receipt_sha256"] = receipt_hash(receipt)
    bundle = dict(wait=wait_receipt, candidates=receipts, input_sha256=original,
                  no_candidate_category=category, category_source="HOST_DERIVED_FROM_STATE_AND_ARRAY",
                  no_candidate_reason=draft.get("no_candidate_reason"),
                  qualitative_reason_truth="NOT_MECHANICALLY_VERIFIED")
    if readiness_reviews:
        bundle['entry_readiness_reviews'] = readiness_reviews
        if not receipts:
            bundle['no_candidate_category'] = 'ENTRY_EVIDENCE_PENDING'
    bundle_sha = receipt_hash(bundle)
    record(deepcopy(dict(kind="CALCULATED_CANDIDATES", body=bundle, sha256=bundle_sha)))
    if economic_reject and receipts and all(
            not r["eligible"] and r.get('rejection_type') == 'PREAUTHORITY_ECONOMIC' for r in receipts):
        return rejected_result(bundle)
    if receipts:
        choices = {f"{r['id']}:{r['receipt_sha256']}": r
                   for r in [wait_receipt] + receipts
                   if (r["id"] == "WAIT" and not failed_template) or (r["id"] != "WAIT" and r["eligible"])}
        selection_schema = _object(dict(selection_token=dict(type="string", enum=list(choices)),
                                        rationale=rationale_schema() if explanation_enabled(context) else dict(type="string")))
        # Re-project with the phase-specific contract so restoration hashes remain
        # valid. Market/account facts are unchanged; only obsolete output rules go.
        selection_payload = _model_payload(frozen_snapshot, frozen_context, contract,
                                          calculated_candidates=bundle, prepared_plans=prepared)
        selection_wire = call("selection", SELECT_PROMPT, selection_payload, selection_schema)
        chosen = choices[selection_wire["selection_token"]]
        final = dict(selection=chosen["id"], receipt_sha256=chosen["receipt_sha256"],
                     rationale=selection_wire.get("rationale"))
    else:
        final = dict(selection="WAIT", receipt_sha256=wait_receipt["receipt_sha256"],
                     rationale=wait_wire['rationale'] if readiness_reviews else draft.get("no_candidate_reason"))
    fresh()
    if receipt_hash(bundle) != bundle_sha:
        raise ValueError("research_candidate_mutated")
    selected = next((r for r in [wait_receipt] + receipts if r["id"] == final["selection"]), None)
    if selected is None or final["receipt_sha256"] != selected["receipt_sha256"]:
        raise ValueError("research_selection_hash")
    if explanation_enabled(context):
        final['explanation'] = compile_explanation(final['rationale'], context,
            selected.get('effective_plan') or selected['raw_proposal'], scope=selected['id'], snapshot=frozen_snapshot)
        final['rationale'] = final['explanation']['text']
    if not isinstance(final["rationale"], str) or not 1 <= len(final["rationale"].strip()) <= 2000:
        raise ValueError("research_selection_rationale")
    if selected["raw_proposal"]["expires_at"] <= fresh():
        raise ValueError("research_selected_expired")
    result = dict(mode="ORDER_FREE_CALCULATED_REVIEW", executable=False, orders_submitted=0,
                  calls=2 if receipts else 1, selection=final, selected=selected,
                  candidates=bundle, bundle_sha256=bundle_sha,
                  elapsed_seconds=clock() - started,
                  recovery_authorization="NOT_EVALUATED", fresh_execution_validation="NOT_PERFORMED")
    if allocation:
        result["mode"] = "ORDER_FREE_ALLOCATION_CALCULATED"
    if action_contract:
        result.update(mode="ORDER_FREE_ACTION_ALLOCATION_CALCULATED_V1", action_contract_version=1)
    record(deepcopy(dict(kind="RESEARCH_RESULT", pending_post_record_time_check=True, body=result)))
    if selected["raw_proposal"]["expires_at"] <= fresh():
        raise ValueError("research_selected_expired")
    return deepcopy(result)
