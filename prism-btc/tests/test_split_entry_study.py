"""Synthetic ledger and CLI integration, never historical strategy evidence."""
from contextlib import nullcontext
from copy import deepcopy
import json
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from analysis import split_entry_study as study
from backtest import engine
from backtest.split_entry import SplitConfig, SplitExecution
from engine.sizing import SizingResult


TIME = pd.Timestamp("2024-01-01", tz="UTC")


def lifecycle():
    """Minimum representable target: two positive children, one zero slot."""
    ledger = study.ExecutionLedger()
    controller = SplitExecution(SplitConfig("timed"), ledger)
    state = engine.BacktestState(10000.)
    state.pending_order = engine.PendingOrder("long", 100., 0, SizingResult(
        10., .003, 95., 105., 110., 115., 90., 0), .015, 0)
    controller.accept(state, TIME)
    for slot in range(3):
        stamp = TIME+pd.Timedelta(minutes=30*(slot+1))
        controller.before_bar(state, stamp, slot+1,
            {"open": 100., "high": 102., "low": 99., "close": 101.},
            SimpleNamespace(), 101., lambda *args: SimpleNamespace(exit_action="hold"))
    pos = state.positions.pop()
    close_time = TIME+pd.Timedelta(hours=2)
    engine._close_position(pos, 100., str(close_time), "end_of_period", state)
    controller.finish(state, close_time, "end_of_period")
    controller.reconcile(state, close_time)
    return ledger, state


def test_registry_is_frozen_48_unique_trials():
    trials = study.registry()
    assert len(trials) == len({t['id'] for t in trials}) == 48
    assert sum(t['phase'] == 'diagnostic' for t in trials) == 16
    assert sum(t['phase'] == 'evaluation' for t in trials) == 32
    assert {t['mode'] for t in trials} == {'single', 'timed', 'confirmed', 'probe'}
    assert {t['cost'] for t in trials if t['phase'] == 'diagnostic'} == {1}
    assert all(t['end'] == '2026-01-01' for t in trials if t['phase'] == 'evaluation')


def test_two_children_are_one_trade_and_fees_reconcile():
    ledger, state = lifecycle()
    summary = ledger.summarize(state)
    assert summary['admitted_parents'] == summary['filled_parents'] == 1
    assert summary['multi_child_parents'] == 1 and summary['child_fills'] == 2
    assert len(state.trade_logs) == 1
    assert summary['filled_qty'] == pytest.approx(.003)
    assert summary['entry_fees_from_children'] == pytest.approx(.003*100*engine.MAKER_FEE)
    assert summary['quantity_and_fee_invariants'] == 'PASS'
    assert summary['unknown_trade_receipts'] == 0
    assert summary['weighted_fill_fraction'] == pytest.approx(1.)
    assert summary['mean_first_fill_slot_delay_minutes'] == 0
    assert not state.positions and state.pending_order is None


def test_ledger_copies_native_and_numpy_values():
    ledger = study.ExecutionLedger()
    value = {'event': 'parent_rejected', 'parent_id': None, 'bar_time': str(TIME),
             'synthetic': np.bool_(True), 'reason': 'tiny', 'count': np.int64(2),
             'fraction': np.float64(.5)}
    ledger(value)
    value['reason'] = 'changed'
    assert ledger.events[0]['reason'] == 'tiny'
    assert type(ledger.events[0]['count']) is int
    assert type(ledger.events[0]['synthetic']) is bool
    assert ledger.summarize(engine.BacktestState(10000))['rejected_parents'] == 1


@pytest.mark.parametrize('bad', [np.float64('nan'), np.float32('inf')])
def test_ledger_rejects_nonfinite_json(bad):
    with pytest.raises(ValueError):
        study.ExecutionLedger()({'value': bad})


@pytest.mark.parametrize('mutation,error', [
    ('conservation', 'parent_quantity_not_conserved'),
    ('duplicate_fill', 'child_refill_or_duplicate'),
    ('after_terminal', 'child_refill_or_duplicate'),
    ('unknown_close', 'event_without_parent'),
    ('early_fill', 'fill_before_decision_available'),
    ('unknown_trade', 'trade_receipt_coverage'),
    ('missing_terminal', 'unresolved_parent_at_end'),
    ('changed_reservation', 'reservation_not_remaining_only'),
    ('fee_mismatch', 'fee_ledger_identity'),
])
def test_ledger_rejects_corrupted_evidence(mutation, error):
    ledger, state = lifecycle()
    fill_index = next(i for i, e in enumerate(ledger.events) if e['event'] == 'child_fill')
    if mutation == 'conservation':
        ledger.events[fill_index]['remaining_qty'] += .1
    elif mutation == 'duplicate_fill':
        ledger.events.insert(fill_index+1, deepcopy(ledger.events[fill_index]))
    elif mutation == 'after_terminal':
        extra = deepcopy(ledger.events[fill_index])
        extra['child_id'] = 'new_id_after_terminal'
        ledger.events.append(extra)
    elif mutation == 'unknown_close':
        ledger.events[-1]['parent_id'] = 'unknown_parent'
    elif mutation == 'early_fill':
        ledger.events[fill_index]['bar_time'] = str(TIME)
    elif mutation == 'unknown_trade':
        ledger.events[-1]['trade_id'] = 999
    elif mutation == 'missing_terminal':
        ledger.events = [e for e in ledger.events if e['event'] != 'parent_terminal']
    elif mutation == 'changed_reservation':
        ledger.events[0]['reserved_notional'] += 1
    else:
        state.total_fees += 1
    with pytest.raises(ValueError, match=error):
        ledger.summarize(state)


@pytest.mark.parametrize('field', ['positions', 'pending_order'])
def test_ledger_refuses_unresolved_final_account(field):
    ledger, state = lifecycle()
    setattr(state, field, [object()] if field == 'positions' else object())
    with pytest.raises(ValueError, match='unresolved_end_state'):
        ledger.summarize(state)


def dummy_summary(multi=0):
    return {'net_return': np.float64(-.01), 'bar_close_mtm_mdd': np.float64(.02),
            'execution': {'multi_child_parents': np.int64(multi)},
            'yearly_returns': {y: np.float64(-.01) for y in ('2023', '2024', '2025')}}


def dummy_daily():
    start = study.allocation.utc_ms('2023-01-01')
    return [{'timestamp_ms': np.int64(start+i*study.allocation.DAY_MS),
             'return': np.float64(0.), 'nav': np.float64(10000.)} for i in range(60)]


def dummy_results(multi=0):
    return {spec['id']: {'summary': dummy_summary(multi), 'daily': dummy_daily()}
            for spec in study.registry() if spec['phase'] == 'evaluation'}


def test_comparison_shared_16_family_and_unidentifiable_additions():
    comparison = study.result_comparison(dummy_results())
    assert comparison['statistics']['family_size'] == 16
    assert len(comparison['statistics']['adjusted_lower_bounds']) == 16
    assert comparison['exploratory_verdict'] == 'UNIDENTIFIABLE'
    assert comparison['profitability_status'] == 'INSUFFICIENT'
    assert comparison['auto_activate'] is False
    assert study.allocation.canonical(comparison) == study.allocation.canonical(
        study.result_comparison(dummy_results()))


def test_comparison_with_identifiable_but_unprofitable_dummy_is_not_supported():
    comparison = study.result_comparison(dummy_results(multi=1))
    assert comparison['exploratory_verdict'] == 'NOT_SUPPORTED'
    for ordering in ('fill_first', 'stop_first'):
        for cost in (1, 2):
            assert not comparison['checks'][f'{ordering}:c{cost}:return_higher']
    assert comparison['auto_activate'] is False


def test_comparison_rejects_unpaired_dates():
    results = dummy_results()
    results['evaluation_uniform_timed_fill_first_c1']['daily'][0]['timestamp_ms'] += 1
    with pytest.raises(ValueError, match='unpaired_daily_grid'):
        study.result_comparison(results)


def test_full_cli_pipeline_serializes_48_synthetic_trials(monkeypatch, tmp_path, capsys):
    """Real registry, ledger, comparison and output; only market/source IO mocked."""
    reference, output = tmp_path/'reference', tmp_path/'output'
    reference.mkdir()
    study.write_json(reference/'data_manifest.json', {'hash': 'synthetic'})
    monkeypatch.setattr(study.subprocess, 'check_output',
                        lambda args, **kwargs: '' if 'status' in args else 'synthetic-source')
    monkeypatch.setattr(study.allocation, 'prepare_input',
                        lambda *args: (sqlite3.connect(':memory:'), {'hash': 'synthetic'}))
    monkeypatch.setattr(study, 'off_reference', lambda *args: None)
    monkeypatch.setattr(study.allocation, 'research_context', lambda *args: nullcontext())
    monkeypatch.setattr(study.allocation, 'summarize',
                        lambda *args: (dummy_summary(), dummy_daily()))
    def backtest(*args, execution_event_hook, **kwargs):
        ledger, state = lifecycle()
        for event in ledger.events:
            execution_event_hook(event)
        return state
    monkeypatch.setattr(study.engine, 'run_backtest', backtest)
    monkeypatch.setattr(sys, 'argv', ['split_entry_study', '--db', str(tmp_path/'not_used.db'),
                                    '--reference', str(reference), '--out', str(output)])
    study.main()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 49
    assert all(isinstance(json.loads(line), dict) for line in lines)
    completed = json.loads((output/'completed.json').read_text())
    assert completed['status'] == 'COMPLETE' and completed['trial_count'] == 48
    assert completed['auto_activate'] is False
    comparison = json.loads((output/'comparison.json').read_text())
    assert comparison['statistics']['family_size'] == 16
    assert comparison['exploratory_verdict'] == 'NOT_SUPPORTED'
    registry = json.loads((output/'registry.json').read_text())
    assert len(registry['results']) == 48
    for spec in study.registry():
        target = output/spec['id']
        summary = json.loads((target/'summary.json').read_text())
        assert summary['execution']['child_fills'] == 2
        assert summary['execution']['filled_parents'] == 1
        assert len((target/'trades.jsonl').read_text().splitlines()) == 1
    assert not (output/'failure.json').exists()
