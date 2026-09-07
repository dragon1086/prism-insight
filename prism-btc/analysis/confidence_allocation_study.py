"""Preregistered MAIN sizing diagnostics, NOT a production-parity profit proof.

No exchange imports/orders. Original signal/exit/funding semantics are retained.
Only the explicit entry hook varies allocation; the observer measures close MTM.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import time

import numpy as np
import pandas as pd

from analysis.replay_data import load_bars, load_funding
from backtest import engine
from core.confidence_allocation import AllocationRequest, allocate, grade_agreement
from core.portfolio_risk import ActualPosition

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'docs/BTC_CONFIDENCE_ALLOCATION_CONTRACT_2026-09-07_ko.md'
ARMS = ('legacy', 'fixed_low', 'uniform', 'fixed_high', 'graded', 'reversed')
INITIAL = 10000.0
DAY_MS = 86400000


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                   allow_nan=False, indent=2) + '\n')


def write_lines(path, rows):
    with Path(path).open('w') as stream:
        for row in rows:
            stream.write(canonical(row) + '\n')


def utc_ms(value):
    return int(pd.Timestamp(value, tz='UTC').timestamp() * 1000)


def marked_equity(state, mark):
    return state.equity + math.fsum(p.qty * (mark - p.entry_price)
                                   * (1 if p.side == 'long' else -1)
                                   for p in state.positions)


def prepare_input(source, directory):
    """One read-only source snapshot, strict existing loaders, filtered memory DB."""
    snapshot = directory / 'input_snapshot.sqlite'
    if snapshot.exists():
        raise ValueError('refuse_input_overwrite')
    with sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True) as src:
        src.execute('PRAGMA query_only=ON')
        with sqlite3.connect(snapshot) as dst:
            src.backup(dst)
    memory = sqlite3.connect(':memory:')
    memory.executescript('CREATE TABLE klines(timeframe TEXT,open_time INTEGER,open REAL,'
                         'high REAL,low REAL,close REAL,volume REAL,turnover REAL,confirmed INTEGER,'
                         'PRIMARY KEY(timeframe,open_time));'
                         'CREATE TABLE funding(funding_time INTEGER PRIMARY KEY,rate REAL);')
    manifest = {'bars': {}, 'scope': '2021 warmup; 2022 diagnosis; 2023-2025 known history'}
    start, end = utc_ms('2021-01-04'), utc_ms('2026-01-01')
    for tf in engine.ALL_TFS:
        tf_end = utc_ms('2025-12-29') if tf == '1w' else end
        loaded = load_bars(snapshot, tf, start, tf_end)
        manifest['bars'][tf] = loaded.manifest
        columns = ('timeframe', 'open_time', 'open', 'high', 'low', 'close',
                   'volume', 'turnover', 'confirmed')
        memory.executemany('INSERT INTO klines VALUES(?,?,?,?,?,?,?,?,?)',
                           [tuple(row[k] for k in columns) for row in loaded.rows])
    funding = load_funding(snapshot, start, end, 8 * 3600000)
    memory.executemany('INSERT INTO funding VALUES(?,?)',
                       [(r['funding_time'], r['rate']) for r in funding.rows])
    memory.commit()
    memory.execute('PRAGMA query_only=ON')
    manifest['funding'] = funding.manifest
    manifest['hash'] = digest(manifest)
    write_json(directory / 'data_manifest.json', manifest)
    return memory, manifest


@contextmanager
def research_context(cost, snapshot_cache=None):
    """Process-local research settings; always restore, never patch live modules."""
    names = ('MAKER_FEE', 'TAKER_FEE', 'SLIPPAGE_SL')
    old = {name: getattr(engine, name) for name in names}
    builder = engine._build_snapshot_at
    try:
        for name in names:
            setattr(engine, name, old[name] * cost)
        engine._END_NS_CACHE.clear()
        if snapshot_cache is not None:
            def cached(frames, timestamp):
                key = int(timestamp.value)
                if key not in snapshot_cache:
                    snapshot_cache[key] = builder(frames, timestamp)
                return snapshot_cache[key]
            engine._build_snapshot_at = cached
        yield
    finally:
        for name, value in old.items():
            setattr(engine, name, value)
        engine._build_snapshot_at = builder
        engine._END_NS_CACHE.clear()


class AllocationAudit:
    def __init__(self, arm, start):
        self.arm = arm
        self.start = pd.Timestamp(start, tz='UTC')
        self.decisions = []

    def __call__(self, intent, snapshot, state, timestamp):
        trends = {tf: getattr(snapshot.tf_states.get(tf), 'trend', None)
                  for tf in ('1h', '4h', '1d')}
        grade = grade_agreement(intent.side, trends)
        price = intent.limit_price
        nav = marked_equity(state, price)
        row = {'decision_id': f'{timestamp.isoformat()}:{intent.side}:{intent.tranche_index}',
               'bar_idx': int((timestamp - self.start) / pd.Timedelta(minutes=30)),
               'feature_cutoff': timestamp.isoformat(),
               'available_at': (timestamp + pd.Timedelta(minutes=30)).isoformat(),
               'side': intent.side, 'tranche_index': intent.tranche_index,
               'trends': trends, 'grade': grade, 'price': price,
               'stop': intent.sizing.sl_price, 'leverage': intent.sizing.leverage,
               'reference_nav': nav, 'legacy_qty': intent.sizing.qty}
        if state.pending_order is not None:
            raise ValueError('entry_hook_unexpected_pending')
        if self.arm == 'legacy':
            row.update(qty=intent.sizing.qty, reasons=[], target_margin_fraction=None,
                       actual_margin_fraction=intent.sizing.qty * price / 10 / nav)
            self.decisions.append(row)
            return intent  # Exact identity: retain legacy R denominator too.
        positions = tuple(ActualPosition('main', p.side, p.qty, p.entry_price,
                                         price, p.sl_price, True) for p in state.positions)
        result = allocate(AllocationRequest(
            lane='main', side=intent.side, equity=nav, price=price,
            stop=intent.sizing.sl_price, tranche_index=intent.tranche_index,
            grade=grade, policy=self.arm, positions=positions, pending=(),
            legacy_qty=intent.sizing.qty))
        row.update(asdict(result))
        self.decisions.append(row)
        if result.qty <= 0:
            return None
        return replace(intent, sizing=replace(intent.sizing, qty=result.qty),
                       initial_risk=result.qty * abs(price - intent.sizing.sl_price))


class CloseObserver:
    def __init__(self):
        self.rows = []
        self.pending = None
        self.known_positions = set()
        self.fill_links = {}

    def __call__(self, state, timestamp, mark):
        nav = marked_equity(state, mark)
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError('nonpositive_or_nonfinite_close_nav')
        ts = int(timestamp.timestamp() * 1000)
        if self.rows and ts != self.rows[-1]['timestamp_ms'] + 1800000:
            raise ValueError('noncontiguous_observer')
        gross = math.fsum(p.qty * mark for p in state.positions)
        heat = math.fsum(p.qty * max(0, (p.entry_price - p.sl_price)
                                    * (1 if p.side == 'long' else -1)) for p in state.positions)
        self.rows.append({'timestamp_ms': ts, 'nav': nav, 'cash': state.equity,
                          'gross_fraction': gross / nav, 'entry_heat_fraction': heat / nav,
                          'open_tranches': len(state.positions),
                          'pending': state.pending_order is not None})
        # Exact prior pending -> surviving position evidence only. A position
        # opened AND closed within this bar is deliberately not guessed here.
        for position in state.positions:
            key = (position.entry_time, position.side, position.tranche_index)
            if key not in self.known_positions:
                pending = self.pending
                if (pending and pending.side == position.side
                        and pending.tranche_index == position.tranche_index
                        and pending.limit_price == position.entry_price
                        and math.isclose(pending.qty, position.initial_qty, rel_tol=0, abs_tol=1e-10)):
                    self.fill_links[key] = pending.bar_idx
                self.known_positions.add(key)
        self.pending = state.pending_order


def summarize(state, rows, start, end):
    expected = (utc_ms(end) - utc_ms(start)) // 1800000
    if len(rows) != expected or rows[-1]['timestamp_ms'] != utc_ms(end):
        raise ValueError('incomplete_close_observation')
    # Pre-EOP close and final cash both count for MDD; daily final includes fees.
    nav = np.r_[INITIAL, [r['nav'] for r in rows], state.equity]
    if not np.isfinite(nav).all() or (nav <= 0).any():
        raise ValueError('invalid_nav')
    mdd = float(np.max(1 - nav / np.maximum.accumulate(nav)))
    daily = [{'timestamp_ms': r['timestamp_ms'], 'nav': r['nav']}
             for r in rows if r['timestamp_ms'] % DAY_MS == 0]
    if not daily:
        raise ValueError('no_complete_daily_observation')
    daily[-1]['nav'] = state.equity
    daily_nav = np.asarray([r['nav'] for r in daily])
    daily_returns = daily_nav / np.r_[INITIAL, daily_nav[:-1]] - 1
    yearly, monthly = {}, {}
    for fmt, target in (('%Y', yearly), ('%Y-%m', monthly)):
        previous = INITIAL
        for i, row in enumerate(daily):
            key = datetime.fromtimestamp((row['timestamp_ms'] - 1) / 1000, timezone.utc).strftime(fmt)
            next_key = (datetime.fromtimestamp((daily[i + 1]['timestamp_ms'] - 1) / 1000,
                                               timezone.utc).strftime(fmt) if i + 1 < len(daily) else None)
            if key != next_key:
                target[key] = row['nav'] / previous - 1
                previous = row['nav']
    pnls = [t.net_pnl for t in state.trade_logs]
    cash_error = state.equity - INITIAL - math.fsum(pnls)
    if abs(cash_error) > max(.0001, len(pnls) * .000051):
        raise ValueError('closed_trade_cash_identity_failed')
    wins = math.fsum(p for p in pnls if p > 0)
    losses = -math.fsum(p for p in pnls if p < 0)
    logs = np.log1p(daily_returns)
    summary = {'net_return': state.equity / INITIAL - 1,
               'cagr': (state.equity / INITIAL) ** (365.25 / len(daily)) - 1,
               'bar_close_mtm_mdd': mdd, 'final_cash': state.equity,
               'yearly_returns': yearly, 'monthly_returns': monthly,
               'positive_month_fraction': sum(v > 0 for v in monthly.values()) / len(monthly),
               'mean_gross_fraction': float(np.mean([r['gross_fraction'] for r in rows])),
               'max_close_gross_fraction': max(r['gross_fraction'] for r in rows),
               'max_close_entry_heat_fraction': max(r['entry_heat_fraction'] for r in rows),
               'total_fees': state.total_fees, 'total_funding': state.total_funding,
               'closed_tranches': len(pnls), 'profit_factor': wins / losses if losses else None,
               'top5_days_removed_return': float(np.expm1(logs.sum() - np.sort(logs)[-5:].sum())),
               'closed_trade_cash_error': cash_error, 'daily_count': len(daily),
               'bar_count': len(rows), 'profitability_status': 'INSUFFICIENT',
               'auto_activate': False}
    for row, value in zip(daily, daily_returns):
        row['return'] = float(value)
    return summary, daily


def grade_summary(decisions, trades, observer):
    counts = {}
    by_bar = {r['bar_idx']: r for r in decisions}
    if len(by_bar) != len(decisions):
        raise ValueError('duplicate_decision_bar')
    for grade in ('low', 'medium', 'high', 'unknown'):
        group = [r for r in decisions if r['grade'] == grade]
        counts[grade] = {'requests': len(group), 'accepted_requests': sum(r['qty'] > 0 for r in group),
                         'mean_new_margin_fraction': (float(np.mean([r['actual_margin_fraction'] for r in group]))
                                                      if group else None),
                         'reasons': dict(Counter(reason for r in group for reason in r['reasons']))}
    known_pnl, unknown_pnl = Counter(), 0.0
    linked = 0
    for trade in trades:
        key = (trade.entry_time, trade.side, trade.tranche_index)
        request = by_bar.get(observer.fill_links.get(key))
        if request is None:
            unknown_pnl += trade.net_pnl
        else:
            linked += 1
            known_pnl[request['grade']] += trade.net_pnl
    return {'decisions': counts, 'linked_closed_tranches': linked,
            'unlinked_closed_tranches': len(trades) - linked,
            'linked_net_pnl_by_grade': dict(known_pnl), 'unlinked_net_pnl': unknown_pnl,
            'attribution_rule': 'exact_prior_pending_to_surviving_position; same-bar close remains unlinked'}


def block_bounds(differences, timestamps, *, draws=2000, seed=20260907):
    """Within-year circular blocks; shared indices over all five comparisons."""
    if (not isinstance(draws, int) or isinstance(draws, bool) or draws < 1
            or any(isinstance(ts, (bool, np.bool_))
                   or not isinstance(ts, (int, float, np.integer, np.floating))
                   or not math.isfinite(ts) or int(ts) != ts or not 0 <= ts <= 253402300799999
                   for ts in timestamps)):
        raise ValueError('invalid_bootstrap_parameters_or_timestamps')
    columns = sorted(differences)
    matrix = np.asarray([differences[c] for c in columns], dtype=float).T
    times = np.asarray(timestamps, dtype=np.int64)
    if (matrix.shape != (len(times), len(columns)) or not len(times)
            or not np.isfinite(matrix).all() or np.any(np.diff(times) != DAY_MS)
            or np.any(times % DAY_MS != 0)):
        raise ValueError('invalid_paired_daily_family')
    years = [datetime.fromtimestamp((int(ts) - 1) / 1000, timezone.utc).year for ts in times]
    groups = [np.flatnonzero(np.asarray(years) == y) for y in sorted(set(years))]
    means, errors = matrix.mean(axis=0), []
    rng = np.random.default_rng(seed)
    for _ in range(draws):
        indices = []
        for group in groups:
            starts = rng.integers(0, len(group), size=(len(group) + 29) // 30)
            indices.extend(group[((starts[:, None] + np.arange(30)) % len(group)).ravel()[:len(group)]])
        errors.append(float(np.max(matrix[indices].mean(axis=0) - means)))
    q = float(np.quantile(errors, .95, method='linear'))
    return {'method': 'within_year_30day_centered_mean_max_error', 'draws': draws,
            'seed': seed, 'family_size': len(columns), 'q95': q,
            'mean_daily_differences': dict(zip(columns, means.tolist())),
            'adjusted_lower_bounds': dict(zip(columns, (means - q).tolist()))}


def comparison(results):
    base = results['evaluation_c1_uniform']
    diffs = {}
    for arm in ('fixed_low', 'fixed_high', 'graded', 'reversed'):
        diffs[f'{arm}-uniform'] = [a['return'] - b['return'] for a, b in
                                  zip(results[f'evaluation_c1_{arm}']['daily'], base['daily'])]
    diffs['graded-reversed'] = [a['return'] - b['return'] for a, b in
                              zip(results['evaluation_c1_graded']['daily'], results['evaluation_c1_reversed']['daily'])]
    timestamps = [r['timestamp_ms'] for r in base['daily']]
    for result in results.values():
        if result['period'] == 'evaluation' and [r['timestamp_ms'] for r in result['daily']] != timestamps:
            raise ValueError('unpaired_daily_timestamps')
    stats = block_bounds(diffs, timestamps)
    checks = {}
    for cost in (1, 2):
        candidate = results[f'evaluation_c{cost}_graded']['summary']
        uniform = results[f'evaluation_c{cost}_uniform']['summary']
        checks[f'cost{cost}_higher_return'] = candidate['net_return'] > uniform['net_return']
        checks[f'cost{cost}_mdd_not_worse'] = candidate['bar_close_mtm_mdd'] <= uniform['bar_close_mtm_mdd']
    cand = results['evaluation_c1_graded']['summary']
    checks['beats_reversed_return'] = cand['net_return'] > results['evaluation_c1_reversed']['summary']['net_return']
    checks['two_of_three_years_better'] = sum(cand['yearly_returns'][y] > base['summary']['yearly_returns'][y]
                                             for y in ('2023', '2024', '2025')) >= 2
    checks['positive_adjusted_lower_bound'] = stats['adjusted_lower_bounds']['graded-uniform'] > 0
    grade_counts = cand['allocation']['decisions']
    checks['at_least_two_allocated_grades'] = sum(v['accepted_requests'] > 0
                                                 for g, v in grade_counts.items() if g != 'unknown') >= 2
    candidate_nav = [r['nav'] for r in results['evaluation_c1_graded']['daily']]
    equivalents = [arm for arm in ('fixed_low', 'uniform', 'fixed_high')
                   if candidate_nav == [r['nav'] for r in results[f'evaluation_c1_{arm}']['daily']]]
    checks['not_constant_allocation_equivalent'] = not equivalents
    identifiable = checks['at_least_two_allocated_grades'] and checks['not_constant_allocation_equivalent']
    verdict = ('UNIDENTIFIABLE_GRADE_EFFECT' if not identifiable else
               'SUPPORTED_WITHIN_LEGACY_MODEL' if all(checks.values()) else 'NOT_SUPPORTED')
    return {'exploratory_verdict': verdict, 'constant_equivalent_arms': equivalents,
            'checks': checks, 'statistics': stats, 'profitability_status': 'INSUFFICIENT', 'auto_activate': False}


def run_study(db, output):
    started = time.monotonic()
    output = Path(output)
    if output.exists():
        raise ValueError('refuse_output_overwrite')
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
        raise ValueError('freeze_clean_source_before_history')
    output.mkdir(parents=True)
    registry = [{'id': f'{period}_c{cost}_{arm}', 'period': period, 'cost': cost,
                 'arm': arm, 'start': start, 'end': end}
                for period, start, end, costs in (
                    ('diagnostic', '2022-01-01', '2023-01-01', (1,)),
                    ('evaluation', '2023-01-01', '2026-01-01', (1, 2)))
                for cost in costs for arm in ARMS]
    identity = {'source_commit': source_commit, 'contract_sha256': hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
                'registry': registry, 'registry_hash': digest(registry),
                'python': __import__('sys').version.split()[0], 'pandas': pd.__version__, 'numpy': np.__version__}
    write_json(output / 'planned_registry.json', identity)
    connection = None
    try:
        connection, manifest = prepare_input(db, output)
        identity['data_hash'] = manifest['hash']
        # Historical no-hook parity, not a performance-selection trial.
        with research_context(1):
            plain = engine.run_backtest(connection, pd.Timestamp('2022-01-01', tz='UTC'),
                                        pd.Timestamp('2023-01-01', tz='UTC'))
        parity_hash = digest(asdict(plain))
        write_json(output / 'baseline_parity.json', {'no_hook_financial_hash': parity_hash, 'status': 'await_identity'})
        cache, results = {}, {}
        for spec in registry:
            trial_start = time.monotonic()
            audit, observer = AllocationAudit(spec['arm'], spec['start']), CloseObserver()
            with research_context(spec['cost'], cache):
                state = engine.run_backtest(connection, pd.Timestamp(spec['start'], tz='UTC'),
                                            pd.Timestamp(spec['end'], tz='UTC'),
                                            entry_hook=audit, observer_hook=observer)
            if spec['id'] == 'diagnostic_c1_legacy':
                identity_hash = digest(asdict(state))
                if identity_hash != parity_hash:
                    raise ValueError('historical_identity_hook_parity_failed')
                write_json(output / 'baseline_parity.json', {'no_hook_financial_hash': parity_hash,
                           'identity_financial_hash': identity_hash, 'status': 'PASS'})
            summary, daily = summarize(state, observer.rows, spec['start'], spec['end'])
            summary['allocation'] = grade_summary(audit.decisions, state.trade_logs, observer)
            target = output / spec['id']
            target.mkdir()
            artifacts = {'trades.jsonl': [asdict(t) for t in state.trade_logs],
                         'decisions.jsonl': audit.decisions, 'close_nav.jsonl': observer.rows,
                         'daily_nav.jsonl': daily}
            hashes = {}
            for name, values in artifacts.items():
                write_lines(target / name, values)
                hashes[name] = hashlib.sha256((target / name).read_bytes()).hexdigest()
            write_json(target / 'summary.json', summary)
            hashes['summary.json'] = hashlib.sha256((target / 'summary.json').read_bytes()).hexdigest()
            results[spec['id']] = {**spec, 'summary': summary, 'daily': daily, 'hashes': hashes}
            write_json(output / 'run_state.json', {'completed': len(results), 'total': len(registry),
                       'last_trial': spec['id'], 'elapsed_seconds': time.monotonic() - started})
            print(canonical({'completed': len(results), 'trial': spec['id'],
                             'seconds': round(time.monotonic() - trial_start, 3)}), flush=True)
        verdict = comparison(results)
        write_json(output / 'comparison.json', verdict)
        write_json(output / 'registry.json', {**identity, 'results': {
            name: {k: v for k, v in result.items() if k != 'daily'} for name, result in results.items()}})
        write_json(output / 'completed.json', {'status': 'COMPLETE', 'trial_count': len(results),
                   'elapsed_seconds': time.monotonic() - started, 'source_commit': source_commit,
                   'data_hash': manifest['hash'], 'profitability_status': 'INSUFFICIENT', 'auto_activate': False})
        return verdict
    except Exception as exc:
        write_json(output / 'failure.json', {'status': 'INCOMPLETE', 'type': type(exc).__name__, 'reason': str(exc),
                   'elapsed_seconds': time.monotonic() - started})
        raise
    finally:
        if connection is not None:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(canonical(run_study(args.db, args.out)))


if __name__ == '__main__':
    main()
