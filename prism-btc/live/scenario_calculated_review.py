"""Order-free lifecycle research: model prices, host arithmetic, model selection.

Not a runtime proposer. Results are research receipts, never executable orders.
Caller supplies an audited, no-retry generator and a durable research recorder.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
import time

from core.llm_scenario import EconomicProposalRejection, ScenarioValidationError, risk_snapshot, validate_scenario
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


DRAFT_PROMPT = """
Order-free candidate research. The supplied API response schema replaces
the normal single-proposal output shape and overrides preceding instructions to
personally compute costs/reward/risk. All trading safety rules remain unchanged.
Return wait_proposal (a normal WAIT wire proposal), zero to two independent normal
wire candidates (OPEN when flat, ADJUST/EXIT when active), and no_candidate_reason.
wait_proposal is the WAIT fallback, not a global WAIT-only instruction: follow
the separate candidates schema and its cap. baseline_kind describes the stored
baseline's origin; the current first-observation flag is recovery.first_observation.
Read current_decision_state for literal current host flags and wire action choices.
Historical baseline_kind never makes first_observation=false into a first observation.
Unknown flags remain unknown: do not invent permission or a first-observation ban.
Choose strategic direction, entry,
SL, TP, quantities and fractions yourself from supplied market evidence; the host
does not choose these. Candidates are mutually exclusive alternatives, never a
combined portfolio. Do not calculate reward/risk, costs or projected PnL yourself:
the host will calculate your exact proposals for subsequent comparison. Do not
shrink a structural SL to make size fit. A candidate may fail host risk validation;
it will remain visible but cannot be selected. If no grounded candidate exists,
return an empty candidates list and a qualitative
reason identifying missing structure or evidence, not invented arithmetic.
Economic objections alone must submit a candidate for host calculation, not use
an empty list to bypass calculation. First recovery observation stays WAIT only,
with empty candidates. Candidate submission is not an order or permission
to trade. Unknown real fees never authorize invented net-reward arithmetic.
For a new flat OPEN, consider TP, optional structural-weakening partial stops and
remaining lots together. Noise alone need not justify partial SL; partial_stops=[]
is valid when no structural reduction is grounded. Confirmed failure still means
the existing full EXIT/protection rules, never partial-SL risk-budget credit.
Cut invalidated exposure, not normal thesis-preserving fluctuations. Compare early
TP concentration with participation in a sustained move: grounded later targets or
SL-protected lots without fixed TP are allowed, never mandatory. Do not label
rounding dust an intentional runner or invent a distant target to improve payoff.
Partial SL saves exposure on continued weakness but sacrifices lots on recovery;
design that tradeoff before entry, without a fixed stop width or exit percentage.
For an active scenario keep its side and original risk budget. ADJUST entries are
incremental only; exit targets are the complete replacement protection. Existing
confirmed positions already reflect prior partial exits; never restore sold lots.
When weakening evidence or a nearer TP changes a held trade, compare retaining the
current protection with a structurally grounded conditional partial SL or full EXIT.
A nearer TP alone does not repair the whole-trade payoff when the remaining stop
loss stays large. Propose a coherent replacement plan for host calculation; judge
exposure saved on further weakness against participation lost if price recovers.
No reduction is mandatory without structural evidence; normal fluctuations and
fees alone do not require a tighter stop or immediate exit. Confirmed failure
still takes full EXIT, never a partial reduction to keep a failed premise alive.
Pending quantities are unfilled reservations, not confirmed holdings; cancellation
requests do not release them. An active scenario with only pending entries is not
flat. Stop restoration still requires the original host stop_policy. EXIT is full
closure intent, not an assertion that orders canceled or positions filled.
"""

FLAT_PURPOSE_PROMPT = """
Fresh flat v2 entry design: determine the supported trade purpose BEFORE choosing
its goal, TP allocation and any alternative. Distinguish directional continuation
from a bounded move toward a particular reference using actual price progress;
a TREND/BREAKOUT label or MA alignment alone does not establish continuation.
For supported continuation, design participation if the move persists together
with structural invalidation: consider grounded later targets or an intentional
SL-protected unpriced remainder. Neither a runner nor a fixed fraction is mandatory.
Explain why the chosen exit allocation fits the purpose, including a fully priced
exit when that is supported. Do not stretch targets or tighten SL to improve a ratio.
A higher-frame MA is evidence to assess, not an automatic ceiling for every fresh
thesis. Choose BOUNDED_BEFORE only for a genuinely bounded purpose; use supported
REFERENCE_EXTENSION or NO_MA_BOUND only when their existing evidence rules fit.
MA10-to-MA35 gap traversal remains bounded: never relabel it continuation to evade
its cap. Bounded opportunities need their own justification; never invent a high
win probability. Smaller size or a nearer first TP alone does not improve location
or establish an edge. Preserve an executable current entry when supported; never
replace all such opportunities with mandatory retests or future closes.
Use at most the existing two candidate slots. A second plan needs independently
supported entry location or exit participation, not cosmetic prices/confidence.
Each fresh candidate must bind its own supported purpose and consistent goal;
never stretch a bounded plan merely to supply an alternative. Existing registered
goals are immutable. One candidate or no candidate remains valid. When only one
is grounded, explain the considered alternative and missing support in that
candidate's existing rationale; do not add fields or fabricate another plan.
Host arithmetic supports the unchanged SELECT comparison; no new permission,
minimum payoff ratio, risk allowance or assumed future profit is created.
"""


SELECT_PROMPT = """
Order-free calculated candidate review. The supplied API response schema
replaces the normal single-proposal output shape and overrides preceding requests
for model arithmetic, never safety constraints. Choose exactly one selection_token
from the schema enum. Each host token binds a candidate ID (or WAIT) and its full
receipt hash; never construct a token or copy a separate hash field. Never rewrite
prices, quantities, expiry or confidence; revised plans require a new research
run, not an extra call here. Interpret trend, support/resistance, fill plausibility
and supplied host calculations. Do not invent net profit or recompute supplied
arithmetic. Risk reserves are NOT observed per-side fees or guaranteed losses.
Read calculation_field_units for projection.targets fields: gross_pnl is the
before-cost USDT amount for that target's quantity, not whole-scenario profit;
gross_reward_per_btc is USDT/BTC price distance, never total USDT reward. Keep
quantity in BTC and reward/risk ratios dimensionless; do not recompute these.
The same target-field units apply to projection.confirmed_remaining.targets and
projection.hypothetical_all_fills.targets; keep their assumptions separate, never
add these alternative projections together.
Flat-entry gross target projections assume all proposed entries fill: a partial target is
not whole-scenario profit, and unknown future fees/funding remain unknown.
When supplied, joint_exit_paths compares bounded hypothetical exit sequences for
new flat OPEN and confirmed remaining lots of a proposed ADJUST. Compare whole-path payoff and remaining lots, not sums of
independent target profits. Flat paths assume full entry fills; active paths use
only confirmed remaining lots. Both assume the displayed exit fills, not
probabilities or expected net profit; partial SL is optional, never risk
credit or a substitute for full EXIT on confirmed failure. No fixed payoff gate.
Each path closes any remaining quantity at hard SL as a stress test, NOT the
expected ending of a trend or the value of an open runner. Do not prefer liquidating upside simply because
an assumed later hard-stop exit makes retained lots look costly. Separately assess
the plan's early-TP concentration and grounded participation if the move continues;
an unpriced protected remainder has no invented profit. Rounding dust is not intent.
joint_exit_paths version2 paths are rows [sequence_ids,remaining_before_hard_stop,
gross_pnl,fee_only]. TP_STOP/SL_STOP mean applied TP/partial-SL legs;
TP1_STOP/SL1_STOP mean only the first TP/partial-SL leg; STOP means hard SL alone.
TP1_SL/SL1_TP mean first TP then partial SL / first partial SL then TP; both kinds
actually apply. All paths close any remainder at hard SL. Compare only displayed
paths: omitted/zero/subminimum/capped-out legs are NOT part of their names.
The corresponding projection.quantity/targets (flat) or
projection.confirmed_remaining.quantity/targets (ADJUST) supply quantities/prices for these
sequences; no independent leg profits are added to path gross. Targets retain their
initial revision quota (not a fraction of each new remainder), sorted by price
crossing order; zero/subminimum legs are omitted and quantities capped at remaining.
This omission/clip is a modelling assumption, not proof of actual reduce-only fills.
Partial SL's Mark trigger is only a hypothetical exit price, not its market fill.
fee_only=null means UNKNOWN; otherwise [assumed_entry_fee,assumed_exit_fee,
estimated_net_before_unmodelled_costs], in USDT, uses historical taker-max estimates
with total original entry quantity charged once for version2 flat OPEN. Gross is also USDT. Funding,
slippage, fill timing and revision races remain UNKNOWN/unmodelled, never zero.
Flat OPEN whole_plan_payoff uses that all-TP-then-residual-hard-SL fee-only net_reward
and full_stop_loss from full hard SL WITHOUT partial SL; both are USDT, ratio is
dimensionless. UNKNOWN means missing costs/path or nonpositive loss denominator.
This is a hypothetical payoff, not probability, expectancy or a guarantee; future
funding/slippage are excluded, NOT zero. It changes no eligibility or risk budget.
ADJUST confirmed_remaining joint paths version3 are supplied separately as
contract_context.holding_exit_paths rows [candidate_id,receipt_sha256,paths].
This optional view uses the same row columns and
sequence rules but gross covers remaining lots ONLY. fee_only entry fee is zero:
its net includes recorded net exactly once plus remaining gross minus assumed
future exit fees. recorded_net already includes prior realized PnL and paid costs;
never add it or subtract paid fees again. Missing recorded net or fee evidence
makes path fee_only UNKNOWN, not zero. These paths are a proposed NEW revision,
not existing live order quotas: never replay current_plan fractions against the
remaining lots or resurrect filled targets. For WAIT compare existing
runner_economics current-Mark/full-stop diagnostics; it has no invented TP path.
Do not add confirmed paths to hypothetical_all_fills or call pending lots held.
An absent optional holding view means unavailable, not no loss or no permission;
use the original target/full-stop and runner_economics diagnostics when absent.
For holdings distinguish confirmed_remaining projections from hypothetical_all_fills
which additionally assume all pending and new entries fill. Never call hypothetical
quantity held or add the two projections. Replacement target fractions start from
confirmed remaining quantity, not the original pre-partial-exit position. Mark-based
EXIT gross is an estimate, not fill or settlement evidence. Missing accounting stays
unknown; a projection cannot replenish risk or grant stop-restoration permission.
Unknown actual future fees alone are not a new automatic WAIT gate; evaluate the
supplied reserve-based comparisons without inventing exact net profit or an edge.
Pure wire/economic validation is NOT recovery authorization or execution evidence.
Candidate eligible=true is selectable when its token is in the response enum;
eligible=false is excluded. allocation_receipt.executable=false and order-free
receipt flags mean the calculation cannot submit orders, NOT candidate rejection.
Do not use those authority flags as a reason to WAIT on an eligible candidate.
Current first_observation/recovery_eligible are in current_decision_state;
baseline_kind is historical origin, not a current waiting restriction. Unknown
state is not permission; fresh host recovery and execution checks still decide.
No trade is mandatory. Explain the decisive evidence and invalidation briefly in
Korean. When flat-OPEN or confirmed ADJUST joint paths are supplied, organize rationale as three short
items: '계획:' name the candidate (also when choosing WAIT) and the decisive evidence;
'경로:' compare its displayed paths using supplied host results. Only if partial-SL
legs actually apply, compare exposure saved on weakness versus participation lost
on recovery; otherwise compare the applicable full-stop/TP paths, never invent a partial SL;
'연장:' assess early-TP concentration, grounded later targets/protected lots and why
that balance fits this thesis. No partial SL, full TP or no fixed TP can each be
valid; explain the applicable tradeoff, never force a runner or a partial stop.
Use path IDs and existing host figures, not new arithmetic, probabilities or a
claim of guaranteed profit. These items expose the response, not hidden reasoning;
missing items alone never authorize a new WAIT rule or delay confirmed-failure EXIT.
Return exactly selection_token and rationale, no other fields.
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
    # The versioned column/sequence meanings live once in SELECT_PROMPT, not
    # repeated per candidate/path in the size-limited user payload.
    result = dict(version=2, paths=[[path[0], *path[2:]] for path in paths])
    if confirmed:
        result.update(version=3, recorded_net=recorded_net,
            gross_scope='CONFIRMED_REMAINING_ONLY',
            fee_net_scope='WHOLE_SCENARIO_RECORDED_NET_ONCE')
    return result


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
            new_entry=new_entry, recorded_net=context.get("recorded_scenario_net") if whole and confirmed else None)
    for kind in ("take_profits", "partial_stops"):
        allocated = Decimal(0)
        for row in plan[kind]:
            # New revision: prior reductions are already absent from supplied lots.
            qty = (max(Decimal(0), min(quantity - allocated,
                   quantity * _decimal(row["fraction"]))) / step).to_integral_value(
                       rounding=ROUND_DOWN) * step
            allocated += qty
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
    bind_receipt(receipt, context, snapshot=explanation_snapshot)
    receipt["receipt_sha256"] = receipt_hash(receipt)
    return receipt


def _model_payload(snapshot, context, response_contract, *, calculated_candidates=None):
    baseline = _base_model_payload(snapshot, context, response_contract, calculated_candidates=calculated_candidates)
    if not explanation_enabled(context):
        baseline = project_holding_price_order_input(baseline)[0]
    return project_explanation_input(baseline, context, snapshot=snapshot)


def _base_model_payload(snapshot, context, response_contract, *, calculated_candidates=None):
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
    if isinstance(context.get("current_plan"), dict):
        model_context["current_plan"] = {key: value for key, value in context["current_plan"].items()
                                         if key not in {"rationale", "host_entry_contract_id"}}
    for field in ("recent_waits", "recent_actions"):
        if isinstance(context.get(field), list):
            model_context[field] = [{key: value for key, value in row.items() if key not in {"rationale", "host_entry_contract_id"}}
                if isinstance(row, dict) else row for row in context[field]]
    payload = dict(market_snapshot=snapshot, contract_context=model_context,
                   response_contract=response_contract)
    if calculated_candidates is not None:
        payload["calculated_candidates"] = calculated_candidates
        # Model-only labels: do not change hash-bound numerical receipts or tapes.
        payload["calculation_field_units"] = {"projection.targets": {
            "quantity": "BTC", "gross_pnl": "USDT_FOR_THIS_TARGET_QUANTITY_BEFORE_COSTS",
            "gross_reward_per_btc": "USDT_PER_BTC_NOT_TOTAL_PNL",
            "gross_reward_to_reserved_risk_ratio": "DIMENSIONLESS_NOT_USDT"}}
    payload = analysis_model_view(payload)
    if context.get('scenario_id') is None:
        from live.scenario_entry_display import project_entry_display_input
        return project_entry_display_input(payload)[0]
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


ALLOCATION_SINGLE_PROMPT = """
Research allocation contract: choose direction, strategic prices, SL, TP,
confidence and allocation_fraction for each NEW entry. Do not emit entry quantity.
The host converts allocation fractions to BTC using supplied costs, the original
budget, confidence and existing consumed/reserved risk; total new-entry fractions
must not exceed one. Fractions allocate the remaining confidence-limited NEW risk
budget, not equity, margin, existing holdings or TP quantities. Never raise
confidence to fit size. Cancellations retain reservations until confirmed.
Positive authorized chase is reserved at the worst permitted price for the whole
new batch. Host size may therefore be smaller; partial fills do not replenish this allocation.
No automatic price, stop, confidence or allocation repair is permitted. Below-minimum
sizes or post-price-adjustment budget excess are rejected, not resized.
Existing position quantities are observations, not fields for you to rewrite.
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
        if context.get('entry_contract_version') == 2 and not isinstance(exc, EconomicProposalRejection):
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
    if (context.get("accounting_status") not in {"confirmed", "pending"}
            or (raw["action"] in {"OPEN", "ADJUST"} and context["accounting_status"] != "confirmed")
            or context.get("protection_ok") is not True or context.get("legacy_fenced") is not False
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
    # Replace only new-entry sizing instructions; holding/exit quantities and
    # all directional, lifecycle and safety instructions remain unchanged.
    base = base.replace("Leverage is FIXED 10, not confidence-dependent. Allocate less quantity to weaker\n"
        "evidence. Set the structural invalidation first, then size down for the host\n"
        "risk cap including costs; recovery reduces quantity, not automatically the",
        "Leverage is FIXED 10, not confidence-dependent. Allocate less new-entry risk to weaker\n"
        "evidence. Set structural invalidation first, then choose allocation_fraction;\n"
        "the host calculates size including costs; recovery reduces size, not automatically the")
    base = base.replace("quantity must be a multiple of quantity_step and satisfy minimum_quantity and\n"
        "minimum_notional. Round proposed quantity DOWN, never squeeze SL to fit more size.",
        "the host checks quantity_step, minimum_quantity and minimum_notional after sizing.\n"
        "Choose allocation_fraction for new entries; never squeeze SL to fit more size.")
    base = base.replace("Existing sizing cap: sum(new-entry risk) <= min(original_budget*confidence,\n"
        "budget remaining after consumed/reserved risk). Per-BTC risk is positive entry-to-SL\n"
        "distance + entry*estimated_cost_rate + hard_stop*slippage_bps/10000; round quantity\n"
        "DOWN to quantity_step. Never raise confidence or widen SL merely to fit quantity.",
        "The host computes the new-entry budget and quantity including costs and reserved risk.\n"
        "Choose allocation_fraction without performing BTC sizing arithmetic. Never raise confidence\n"
        "or widen SL merely to fit size.")
    return base


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

    base = scenario_llm.SYSTEM_PROMPT + scenario_llm.MA_STRUCTURE_PROMPT
    if context.get('pending_wait_policy_version') == 1:
        base += scenario_llm.PENDING_WAIT_PROMPT
    if context.get("entry_contract_version") in (1, 2):
        base += scenario_llm.entry_contract_prompt(context)
    if context.get("scenario_id") is None:
        base += scenario_llm.FLAT_ENTRY_PROMPT
    allocation = research_mode != "legacy"
    if allocation:
        base = allocation_system_prompt(base)
    fresh()
    economic_reject = research_mode in ECONOMIC_REJECT_MODES
    action_contract = research_mode in ACTION_ALLOCATION_MODES or economic_reject
    schema = allocation_response_schema(frozen_context) if allocation else response_schema(frozen_context)
    payload = _model_payload(frozen_snapshot, frozen_context, schema if allocation else contract)
    wait_schema = deepcopy(schema)
    wait_schema["properties"]["action"]["enum"] = ["WAIT"]
    for field in ("side", "hard_stop", "chase", "stop_revision"):
        if field in wait_schema["properties"]:
            wait_schema["properties"][field] = {"type": "null"}
    for field in ("entries", "take_profits", "partial_stops"):
        wait_schema["properties"][field]["maxItems"] = 0
    candidate_schema = deepcopy(schema)
    candidate_schema["properties"]["action"]["enum"] = [a for a in candidate_schema['properties']['action']['enum'] if a != 'WAIT']
    wait_only = schema["properties"]["action"]["enum"] == ["WAIT"]
    first_observation = (frozen_context.get("recovery") or {}).get("first_observation") is True
    draft_schema = _object(dict(wait_proposal=wait_schema,
        candidates=dict(type="array", maxItems=0 if wait_only or first_observation else 2,
                        items=candidate_schema), no_candidate_reason=rationale_schema() if explanation_enabled(context) else dict(type="string")))
    if action_contract:
        schema = action_allocation_response_schema(frozen_context)
        payload = _model_payload(frozen_snapshot, frozen_context, schema)
        draft_schema["properties"]["wait_proposal"] = _action_allocation_branches(frozen_context, {"WAIT"})
        # Empty arrays still need a valid item schema, even in WAIT-only state.
        candidates = _action_allocation_branches(frozen_context, {"OPEN", "ADJUST", "EXIT", "REDUCE"})
        draft_schema["properties"]["candidates"]["items"] = candidates if candidates["anyOf"] else _action_allocation_branches(frozen_context)

    def call(phase, appendix, data, output_schema):
        now = fresh()
        prompt = _encoded(data)
        if len(prompt.encode()) > 100_000:
            raise ValueError("research_input_size")
        system = scenario_llm.immediate_reduce_instructions(base + appendix, frozen_context)
        if explanation_enabled(frozen_context):
            system += EXPLANATION_PROMPT
        request = dict(system_prompt=system, user_prompt=prompt,
            model=scenario_llm.MODEL, reasoning_effort=scenario_llm.EFFORT,
            fast_tier=True, timeout=scenario_llm.TIMEOUT_SECONDS - (now - started),
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
        except Exception:
            record(deepcopy(dict(kind="MODEL_ERROR", phase=phase, code="model_call_failed",
                request_sha256=request_hash, prepared_request_sha256=request_hash, actual_request=request,
                actual_request_sha256=actual_request_hash, elapsed_seconds=clock() - actual_started)))
            raise ValueError("research_model_call_failed") from None
        record(deepcopy(dict(kind="MODEL_RAW", phase=phase, request_sha256=request_hash,
                             prepared_request_sha256=request_hash,
                             actual_request=request, actual_request_sha256=actual_request_hash,
                             raw=result.text, elapsed_seconds=clock() - actual_started)))
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
        appendix = ALLOCATION_SINGLE_PROMPT
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
    if (frozen_context.get("entry_contract_version") == 2
            and frozen_context.get("scenario_id") is None
            and not frozen_context.get("positions") and not frozen_context.get("pending_entries")
            and frozen_context.get("new_risk_blocked") is False
            and not wait_only and not first_observation):
        draft_prompt += FLAT_PURPOSE_PROMPT
    if context.get("entry_contract_version") in (1, 2):
        draft_prompt += "\nEntry-contract exception: FAILED wait_proposal is a nonexecutable template, not a fallback. Draft assessments must share contract_id/status, not reason wording; supply valid EXIT candidates only when FAILED.\n"
    if allocation:
        draft_prompt = draft_prompt.replace("SL, TP, quantities and fractions yourself", "SL, TP, confidence and entry allocation_fraction yourself") + ALLOCATION_SINGLE_PROMPT
        payload = _model_payload(frozen_snapshot, frozen_context, draft_schema)
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
    receipts = [calculator(wire, now_context, candidate_id=f"c{index}",
                                   original_input_now=frozen_context["now"],
                                   **({'explanation_snapshot': frozen_snapshot} if explanation_enabled(context) else {}))
                for index, wire in enumerate(draft["candidates"])]
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
        selection_payload = _model_payload(frozen_snapshot, frozen_context, selection_schema,
                                          calculated_candidates=bundle)
        selection_wire = call("selection", SELECT_PROMPT, selection_payload, selection_schema)
        chosen = choices[selection_wire["selection_token"]]
        final = dict(selection=chosen["id"], receipt_sha256=chosen["receipt_sha256"],
                     rationale=selection_wire.get("rationale"))
    else:
        final = dict(selection="WAIT", receipt_sha256=wait_receipt["receipt_sha256"],
                     rationale=draft.get("no_candidate_reason"))
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
