"""Exact-path citation delivery, not a test of model comprehension."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live import scenario_llm
from live.scenario_calculated_review import research_review
from live.scenario_model_input import project_model_input, restore_model_input
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context, wire


CITATION_RULE = """When citing a numeric path, use that exact path's supplied value, including its
before/now pair in recovery.changed_evidence, verbatim. Preserve its timeframe,
confirmed/forming phase and observation time; never substitute another frame's
close, current Mark or intended entry price. Frame closes in the same snapshot
can differ. A changed_evidence.before value is the recovery baseline value, not
the previous confirmed candle's close. If a cited value is unavailable, say so
rather than borrowing a nearby value or inventing a path/value pair.
"""


def fixture():
    # Only these close/baseline values are from slot 5975140. Other numbers are
    # explicit contrasting synthetic facts, not reconstructed market history.
    frames = {}
    for frame in ('1h', '30m', '12h', '1d', '1w'):
        frames[frame] = dict(forming=dict(ohlcv=dict(close=83123.1 if frame in ('1h', '30m') else 83133.6),
            ma10=82810.9, ma35=82142.8, observed_at_ms=1000000),
            confirmed=dict(ohlcv=dict(close=82900)))
    snap = dict(valid=True, as_of_ms=1000000, timeframes=frames)
    ctx = context()
    ctx.update(accounting_status='confirmed', protection_ok=True, legacy_fenced=False)
    current = {f'timeframes.{f}.forming.ohlcv.close': x['forming']['ohlcv']['close'] for f, x in frames.items()}
    baseline = {key: 82811.8 for key in current}
    ctx['recovery'] = dict(baseline=baseline, current=current,
        changed_evidence={key: dict(before=baseline[key], now=value) for key, value in current.items()})
    return snap, ctx


def test_cross_frame_prices_and_baseline_survive_lossless_projection():
    snap, ctx = fixture()
    source = dict(market_snapshot=snap, contract_context=ctx)
    restored = restore_model_input(project_model_input(source)[0])
    assert restored == source
    facts = scenario_llm._current_primary_frame_facts(snap)
    for frame in ('1h', '30m'):
        change = restored['contract_context']['recovery']['changed_evidence'][f'timeframes.{frame}.forming.ohlcv.close']
        assert change == dict(before=82811.8, now=83123.1)
        assert change['now'] == facts[frame]['close']
        assert change['before'] != snap['timeframes'][frame]['confirmed']['ohlcv']['close']
        assert change['now'] != ctx['mark_price']
    assert snap['timeframes']['12h']['forming']['ohlcv']['close'] == 83133.6


@pytest.mark.parametrize('mode', ['direct', 'legacy', 'allocation-single', 'economic-reject-calculated-v1'])
def test_shared_instruction_changes_no_serialized_facts_schema_or_calculation(monkeypatch, mode):
    assert CITATION_RULE in scenario_llm.SYSTEM_PROMPT
    snap, ctx = fixture()
    frozen = deepcopy((snap, ctx))
    def invoke():
        calls = []
        def generate(**request):
            calls.append(deepcopy(request))
            if mode in ('direct', 'allocation-single'):
                reply = wire(ctx)
            elif len(calls) == 1:
                candidate = wire(ctx, 'LONG')
                if mode == 'economic-reject-calculated-v1':
                    candidate['entries'][0].pop('quantity')
                    candidate['entries'][0]['allocation_fraction'] = 1
                reply = dict(wait_proposal=wire(ctx), candidates=[candidate], no_candidate_reason='')
            else:
                data = restore_model_input(json.loads(request['user_prompt']))['calculated_candidates']
                chosen = data['wait']
                reply = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale='Fixture WAIT')
            return SimpleNamespace(text=json.dumps(reply))
        if mode == 'direct':
            result = scenario_llm.propose(snap, ctx, response_contract(ctx), generate=generate, clock=lambda: 1000)
        else:
            result = research_review(snap, ctx, response_contract(ctx), generate=generate,
                record=lambda _: None, clock=lambda: 1000, research_mode=mode)
        return result, calls
    revised, calls = invoke()
    monkeypatch.setattr(scenario_llm, 'SYSTEM_PROMPT', scenario_llm.SYSTEM_PROMPT.replace(CITATION_RULE, ''))
    baseline, old_calls = invoke()
    assert revised == baseline
    assert len(calls) == (1 if mode in ('direct', 'allocation-single') else 2)
    for new, old in zip(calls, old_calls):
        assert CITATION_RULE in new['system_prompt']
        assert new['system_prompt'].replace(CITATION_RULE, '') == old['system_prompt']
        assert {k: v for k, v in new.items() if k != 'system_prompt'} == {k: v for k, v in old.items() if k != 'system_prompt'}
        assert len(new['user_prompt'].encode()) == len(old['user_prompt'].encode()) <= 100000
    assert (snap, ctx) == frozen
