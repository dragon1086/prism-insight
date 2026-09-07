"""Frozen parent-order splitting diagnostics; no runtime or broker integration."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import math
from pathlib import Path
import subprocess  # nosec B404 - fixed absolute git, read-only literal args, no shell
import time

import pandas as pd

from analysis import confidence_allocation_study as allocation
from backtest import engine
from backtest.split_entry import SplitConfig

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'docs/BTC_SPLIT_ENTRY_CONTRACT_2026-09-07_ko.md'
MODES = ('single', 'timed', 'confirmed', 'probe')
ALLOCATIONS = ('uniform', 'graded')
ORDERS = ('fill_first', 'stop_first')
write_json = allocation.write_json
write_lines = allocation.write_lines


def registry():
    return [{'id': f'{phase}_{policy}_{mode}_{order}_c{cost}',
             'phase': phase, 'allocation': policy, 'mode': mode, 'ordering': order,
             'cost': cost, 'start': start, 'end': end}
            for phase, start, end, costs in (
                ('diagnostic', '2022-01-01', '2023-01-01', (1,)),
                ('evaluation', '2023-01-01', '2026-01-01', (1, 2)))
            for policy in ALLOCATIONS for mode in MODES for order in ORDERS for cost in costs]


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ExecutionLedger:
    """Validate explicit parent/child evidence; never infer a fill from a bar."""
    def __init__(self):
        self.events = []

    def __call__(self, event):
        # Stable copy also validates finite/JSON-native event data immediately.
        import json
        self.events.append(json.loads(allocation.canonical(event)))

    def summarize(self, state):
        parents, children, closed, terminals = {}, {}, {}, set()
        reasons, counts = Counter(), Counter()
        fees = 0.0
        for event in self.events:
            kind, pid = event['event'], event['parent_id']
            counts[kind] += 1
            if event.get('synthetic') is not True:
                raise ValueError('unlabelled_synthetic_event')
            if kind == 'parent_rejected':
                reasons[event['reason']] += 1
                continue
            if kind == 'parent_accepted':
                if pid in parents:
                    raise ValueError('duplicate_parent_id')
                parents[pid] = {'first': event, 'last': event, 'fill_events': []}
            if pid not in parents:
                raise ValueError('event_without_parent')
            if (not math.isfinite(event['parent_qty']) or event['parent_qty'] <= 0
                    or any(not math.isfinite(event[k]) or event[k] < -1e-10
                           for k in ('filled_qty', 'remaining_qty', 'cancelled_qty'))
                    or not math.isclose(event['parent_qty'], event['filled_qty'] + event['remaining_qty']
                                        + event['cancelled_qty'], abs_tol=1e-9, rel_tol=0)):
                raise ValueError('parent_quantity_not_conserved')
            first = parents[pid]['first']
            if event['parent_qty'] != first['parent_qty']:
                raise ValueError('parent_budget_changed')
            if (not math.isclose(event['reserved_notional'], event['remaining_qty'] * first['limit_price'],
                                 rel_tol=1e-10, abs_tol=1e-9)
                    or not math.isclose(event['reserved_stop_risk'], event['remaining_qty']
                                        * abs(first['limit_price'] - first['sl_price']), rel_tol=1e-10, abs_tol=1e-9)):
                raise ValueError('reservation_not_remaining_only')
            if kind == 'child_fill':
                if pid in terminals or event['child_id'] in children:
                    raise ValueError('child_refill_or_duplicate')
                if (not 0 < event['qty'] <= first['parent_qty']
                        or event['price'] != first['limit_price'] or event['sl_price'] != first['sl_price']
                        or event.get('protection_from_first_fill') is not True or event['fee'] < 0):
                    raise ValueError('invalid_fill_or_unprotected_quantity')
                if pd.Timestamp(event['bar_time']) < pd.Timestamp(first['bar_time']) + pd.Timedelta(minutes=30):
                    raise ValueError('fill_before_decision_available')
                children[event['child_id']] = event
                parents[pid]['fill_events'].append(event)
                fees += event['fee']
            if kind == 'parent_terminal':
                if pid in terminals or abs(event['remaining_qty']) > 1e-10:
                    raise ValueError('nonterminal_parent')
                terminals.add(pid)
                reasons[event['reason']] += 1
            if kind == 'trade_closed':
                if event['trade_id'] in closed:
                    raise ValueError('duplicate_trade_receipt')
                closed[event['trade_id']] = event
            parents[pid]['last'] = event
        if set(parents) != terminals:
            raise ValueError('unresolved_parent_at_end')
        if set(closed) != {t.trade_id for t in state.trade_logs}:
            raise ValueError('trade_receipt_coverage')
        if len({e['parent_id'] for e in closed.values()}) != len(closed):
            raise ValueError('children_counted_as_multiple_trades')
        filled_parents = {pid for pid, p in parents.items() if p['fill_events']}
        if filled_parents != {e['parent_id'] for e in closed.values()}:
            raise ValueError('filled_parent_without_closed_trade')
        for trade in state.trade_logs:
            receipt = closed[trade.trade_id]
            first = parents[receipt['parent_id']]['first']
            fills = parents[receipt['parent_id']]['fill_events']
            if (receipt['net_pnl'] != trade.net_pnl or receipt['entry_time'] != trade.entry_time
                    or receipt['side'] != trade.side or receipt['tranche_index'] != trade.tranche_index
                    or not math.isclose(math.fsum(e['qty'] for e in fills), trade.qty, rel_tol=0, abs_tol=1e-9)
                    or trade.entry_price != first['limit_price'] or trade.leverage != 10):
                raise ValueError('trade_parent_economics_mismatch')
        if state.pending_order is not None or state.positions:
            raise ValueError('unresolved_end_state')
        if not math.isclose(math.fsum(t.fee_paid for t in state.trade_logs), state.total_fees,
                            abs_tol=max(1e-6, len(closed) * .00000051), rel_tol=0):
            raise ValueError('fee_ledger_identity')
        requested = math.fsum(p['first']['parent_qty'] for p in parents.values())
        filled = math.fsum(e['qty'] for e in children.values())
        ratios, delays = [], []
        for p in parents.values():
            q = math.fsum(e['qty'] for e in p['fill_events'])
            if not math.isclose(q, p['last']['filled_qty'], abs_tol=1e-9, rel_tol=0):
                raise ValueError('cumulative_fill_identity')
            ratios.append(q / p['first']['parent_qty'])
            if p['fill_events']:
                delay = (pd.Timestamp(p['fill_events'][0]['bar_time']) - pd.Timestamp(p['first']['bar_time']))
                delays.append(delay.total_seconds() / 60 - 30)
        return {'admitted_parents': len(parents), 'rejected_parents': counts['parent_rejected'],
                'filled_parents': len(filled_parents), 'zero_fill_parents': len(parents) - len(filled_parents),
                'multi_child_parents': sum(len(p['fill_events']) > 1 for p in parents.values()),
                'child_fills': len(children), 'requested_qty': requested, 'filled_qty': filled,
                'cancelled_qty': math.fsum(p['last']['cancelled_qty'] for p in parents.values()),
                'weighted_fill_fraction': filled / requested if requested else None,
                'mean_parent_fill_fraction': math.fsum(ratios) / len(ratios) if ratios else None,
                'full_fill_parents': sum(math.isclose(r, 1., abs_tol=1e-9, rel_tol=0) for r in ratios),
                'mean_first_fill_slot_delay_minutes': math.fsum(delays) / len(delays) if delays else None,
                'entry_fees_from_children': fees, 'event_counts': dict(counts),
                'terminal_or_rejection_reasons': dict(reasons),
                'unknown_trade_receipts': 0, 'quantity_and_fee_invariants': 'PASS'}


def off_reference(connection, reference, output, cache):
    """Exact old-model controls, not a competing new execution arm."""
    results = {}
    for policy in ALLOCATIONS:
        observer, audit = allocation.CloseObserver(), allocation.AllocationAudit(policy, '2023-01-01')
        with allocation.research_context(1, cache):
            state = engine.run_backtest(connection, pd.Timestamp('2023-01-01', tz='UTC'),
                                        pd.Timestamp('2026-01-01', tz='UTC'), entry_hook=audit,
                                        observer_hook=observer, execution_config=None)
        summary, daily = allocation.summarize(state, observer.rows, '2023-01-01', '2026-01-01')
        summary['allocation'] = allocation.grade_summary(audit.decisions, state.trade_logs, observer)
        directory = output / f'off_reference_{policy}'
        directory.mkdir()
        for name, rows in {'trades.jsonl': [asdict(t) for t in state.trade_logs],
                           'decisions.jsonl': audit.decisions, 'close_nav.jsonl': observer.rows,
                           'daily_nav.jsonl': daily}.items():
            write_lines(directory / name, rows)
        write_json(directory / 'summary.json', summary)
        hashes = {}
        for name in ('trades.jsonl', 'decisions.jsonl', 'close_nav.jsonl', 'daily_nav.jsonl', 'summary.json'):
            actual = file_hash(directory / name)
            if actual != file_hash(reference / f'evaluation_c1_{policy}' / name):
                raise ValueError(f'feature_off_baseline_changed:{policy}:{name}')
            hashes[name] = actual
        results[policy] = hashes
    write_json(output / 'off_reference_parity.json', {'status': 'PASS', 'hashes': results})


def result_comparison(results):
    def get(policy, mode, order, cost=1):
        return results[f'evaluation_{policy}_{mode}_{order}_c{cost}']
    timestamps = [r['timestamp_ms'] for r in get('graded', 'single', 'fill_first')['daily']]
    differences = {}
    for policy in ALLOCATIONS:
        for order in ORDERS:
            base = get(policy, 'single', order)
            for left, right in (('timed', 'single'), ('confirmed', 'single'),
                                ('probe', 'single'), ('confirmed', 'probe')):
                a, b = get(policy, left, order), get(policy, right, order)
                if any([r['timestamp_ms'] for r in x['daily']] != timestamps for x in (a, b, base)):
                    raise ValueError('unpaired_daily_grid')
                differences[f'{policy}:{order}:{left}-{right}'] = [x['return'] - y['return']
                    for x, y in zip(a['daily'], b['daily'])]
    if len(differences) != 16:
        raise ValueError('incomplete_statistical_family')
    stats = allocation.block_bounds(differences, timestamps)
    checks, tradeoffs = {}, {}
    for order in ORDERS:
        for cost in (1, 2):
            c = get('graded', 'confirmed', order, cost)['summary']
            s = get('graded', 'single', order, cost)['summary']
            label = f'{order}:c{cost}'
            checks[label + ':return_higher'] = c['net_return'] > s['net_return']
            checks[label + ':mdd_not_worse'] = c['bar_close_mtm_mdd'] <= s['bar_close_mtm_mdd']
            tradeoffs[label] = {'net_return_difference': c['net_return'] - s['net_return'],
                               'mdd_difference': c['bar_close_mtm_mdd'] - s['bar_close_mtm_mdd'],
                               'lower_return_lower_mdd': c['net_return'] < s['net_return']
                               and c['bar_close_mtm_mdd'] < s['bar_close_mtm_mdd']}
        c = get('graded', 'confirmed', order)['summary']
        s = get('graded', 'single', order)['summary']
        p = get('graded', 'probe', order)['summary']
        checks[order + ':beats_probe'] = c['net_return'] > p['net_return']
        checks[order + ':multiple_child_fills'] = c['execution']['multi_child_parents'] > 0
        checks[order + ':two_years_better'] = sum(c['yearly_returns'][y] > s['yearly_returns'][y]
                                                for y in ('2023', '2024', '2025')) >= 2
        checks[order + ':adjusted_lower_positive'] = stats['adjusted_lower_bounds'][f'graded:{order}:confirmed-single'] > 0
    identifiable = all(checks[order + ':multiple_child_fills'] for order in ORDERS)
    verdict = ('UNIDENTIFIABLE' if not identifiable else
               'SUPPORTED_WITHIN_MODEL' if all(checks.values()) else 'NOT_SUPPORTED')
    return {'exploratory_verdict': verdict, 'checks': checks, 'tradeoffs': tradeoffs,
            'statistics': stats, 'profitability_status': 'INSUFFICIENT', 'auto_activate': False}


def run_study(db, reference, output):
    output, reference = Path(output), Path(reference)
    if output.exists():
        raise ValueError('refuse_output_overwrite')
    if subprocess.check_output(  # nosec B603 - literal read-only args, no user input or shell
            ['/usr/bin/git', 'status', '--porcelain'], cwd=ROOT, text=True, timeout=10).strip():
        raise ValueError('freeze_clean_source_before_history')
    source = subprocess.check_output(  # nosec B603 - fixed absolute executable and literal args
        ['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=10).strip()
    planned = registry()
    if len(planned) != 48 or len({r['id'] for r in planned}) != 48:
        raise ValueError('bad_registry')
    output.mkdir(parents=True)
    identity = {'source_commit': source, 'contract_sha256': file_hash(CONTRACT),
                'registry_hash': allocation.digest(planned), 'registry': planned,
                'reference_kind': 'prior_fixed_allocation_feature_OFF_exact_financial_artifacts'}
    write_json(output / 'planned_registry.json', identity)
    connection, started = None, time.monotonic()
    try:
        connection, manifest = allocation.prepare_input(db, output)
        import json
        previous_manifest = json.loads((reference / 'data_manifest.json').read_text())
        if manifest['hash'] != previous_manifest['hash']:
            raise ValueError('baseline_input_mismatch')
        identity['data_hash'] = manifest['hash']
        cache = {}
        off_reference(connection, reference, output, cache)
        results = {}
        for spec in planned:
            trial_start = time.monotonic()
            observer, audit = allocation.CloseObserver(), allocation.AllocationAudit(spec['allocation'], spec['start'])
            ledger = ExecutionLedger()
            with allocation.research_context(spec['cost'], cache):
                state = engine.run_backtest(connection, pd.Timestamp(spec['start'], tz='UTC'),
                    pd.Timestamp(spec['end'], tz='UTC'), entry_hook=audit, observer_hook=observer,
                    execution_config=SplitConfig(spec['mode'], spec['ordering']), execution_event_hook=ledger)
            summary, daily = allocation.summarize(state, observer.rows, spec['start'], spec['end'])
            summary['execution'] = ledger.summarize(state)
            summary['allocation_grade_requests'] = dict(Counter(d['grade'] for d in audit.decisions))
            target = output / spec['id']
            target.mkdir()
            files = {'trades.jsonl': [asdict(t) for t in state.trade_logs], 'decisions.jsonl': audit.decisions,
                     'execution_events.jsonl': ledger.events, 'close_nav.jsonl': observer.rows, 'daily_nav.jsonl': daily}
            hashes = {}
            for name, rows in files.items():
                write_lines(target / name, rows)
                hashes[name] = file_hash(target / name)
            write_json(target / 'summary.json', summary)
            hashes['summary.json'] = file_hash(target / 'summary.json')
            results[spec['id']] = {**spec, 'summary': summary, 'daily': daily, 'hashes': hashes}
            write_json(output / 'run_state.json', {'completed': len(results), 'total': len(planned),
                       'last_trial': spec['id'], 'elapsed_seconds': time.monotonic() - started})
            print(allocation.canonical({'completed': len(results), 'trial': spec['id'],
                                        'seconds': round(time.monotonic() - trial_start, 3)}), flush=True)
        comparison = result_comparison(results)
        write_json(output / 'comparison.json', comparison)
        write_json(output / 'registry.json', {**identity, 'results': {
            key: {k: v for k, v in result.items() if k != 'daily'} for key, result in results.items()}})
        write_json(output / 'completed.json', {'status': 'COMPLETE', 'source_commit': source,
                   'trial_count': len(results), 'data_hash': manifest['hash'],
                   'elapsed_seconds': time.monotonic() - started,
                   'profitability_status': 'INSUFFICIENT', 'auto_activate': False})
        return comparison
    except Exception as exc:
        write_json(output / 'failure.json', {'status': 'INCOMPLETE', 'type': type(exc).__name__,
                   'reason': str(exc), 'elapsed_seconds': time.monotonic() - started})
        raise
    finally:
        if connection is not None:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(allocation.canonical(run_study(args.db, args.reference, args.out)))


if __name__ == '__main__':
    main()
