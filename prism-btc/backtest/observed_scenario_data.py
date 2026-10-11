"""Exact retained public snapshots, released at their evidenced availability time.

Hashes establish artifact identity, not market truth. Execution remains simulated.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from engine.scenario_snapshot import TIMEFRAME_MS, candle_start

INTERVAL_MS = 300000
PRIMARY = ('15m', '30m', '1h')
FRAMES = {*PRIMARY, '4h', '12h', '1d', '1w'}
TOP = {'schema_version', 'kind', 'symbol', 'start_ms', 'end_ms', 'source', 'rows', 'rows_sha256'}
AVAILABLE = {'slot', 'status', 'available_at_ms', 'source_context_now', 'snapshot',
             'snapshot_hash', 'source_evidence', 'quote_evidence'}
EVIDENCE = {'audit_event_ref', 'audit_body_sha256', 'context_hash', 'manifest_ref',
            'source_git_revision', 'availability_basis', 'request_market_verified'}


class ObservedInputGap(ValueError):
    """Exact scheduled input is explicitly missing; never use an adjacent record."""


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def _hex(value, length=64):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{' + str(length) + '}', value) is not None


def _integer(value):
    return type(value) is int and value >= 0


def _public(value):
    """No account context, secrets, or private decisions inside public market data."""
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            _require(not any(s in lowered for s in ('account', 'credential', 'token', 'api_key',
                'apikey', 'password', 'authorization', 'secret', 'private_key'))
                and lowered not in {'position', 'positions', 'balance', 'balances', 'proposal',
                    'outcome', 'orders', 'scenario_id', 'input_id', 'main_uid', 'context', 'recovery',
                    'risk', 'current_plan', 'entries', 'take_profits', 'partial_stops', 'equity',
                    'fees_paid', 'funding_paid', 'realized_loss', 'net_pnl'}, 'private_snapshot_field')
            _public(item)
    elif isinstance(value, list):
        for item in value:
            _public(item)
    elif type(value) is float:
        _require(math.isfinite(value), 'nonfinite_market_value')


def _closed_times(value, cutoff):
    if isinstance(value, dict):
        if 'close_time_ms' in value:
            _require(_integer(value['close_time_ms']) and value['close_time_ms'] <= cutoff,
                     'future_confirmed_close')
        if 'observed_at_ms' in value:
            _require(_integer(value['observed_at_ms']) and value['observed_at_ms'] <= cutoff,
                     'future_embedded_observation')
        for item in value.values():
            _closed_times(item, cutoff)
    elif isinstance(value, list):
        for item in value:
            _closed_times(item, cutoff)


def _bar_time(bar, duration, cutoff, *, confirmed, flag_required=True):
    """Validate actual bar schemas, including those without close_time_ms."""
    _require(isinstance(bar, dict), 'bar_time_shape')
    opened = bar.get('open_time_ms')
    _require(_integer(opened) and candle_start(opened, duration) == opened, 'unaligned_bar_open')
    if flag_required or 'is_confirmed' in bar:
        _require(bar.get('is_confirmed') is confirmed, 'bar_confirmation_mismatch')
    if confirmed:
        _require(opened + duration <= cutoff, 'future_confirmed_bar')
        if 'close_time_ms' in bar:
            _require(_integer(bar['close_time_ms']) and bar['close_time_ms'] == opened + duration,
                     'confirmed_close_mismatch')
    else:
        observed = bar.get('observed_at_ms')
        _require(_integer(observed) and observed <= cutoff and candle_start(observed, duration) == opened,
                 'forming_candle_time_mismatch')


def _frame_times(frame, name, as_of):
    duration = TIMEFRAME_MS[name]
    forming = frame.get('forming')
    cutoff = as_of
    if forming is not None:
        _bar_time(forming, duration, as_of, confirmed=False)
        cutoff = min(as_of, forming['observed_at_ms'])
    if frame.get('confirmed') is not None:
        _bar_time(frame['confirmed'], duration, cutoff, confirmed=True)
    recent = frame.get('recent_confirmed_bars', [])
    _require(isinstance(recent, list), 'recent_confirmed_bars_shape')
    for bar in recent:
        _bar_time(bar, duration, cutoff, confirmed=True, flag_required=False)
    path = frame.get('ma_path', {})
    _require(isinstance(path, dict), 'ma_path_shape')
    points = path.get('confirmed_points', [])
    _require(isinstance(points, list), 'confirmed_points_shape')
    for point in points:
        _bar_time(point, duration, cutoff, confirmed=True)
    point = path.get('forming_point')
    if point is not None:
        _bar_time(point, duration, as_of, confirmed=False)
        _require(forming is not None and point['open_time_ms'] == forming['open_time_ms'] and
                 point['observed_at_ms'] == forming['observed_at_ms'], 'forming_point_time_mismatch')


class ObservedScenarioData:
    def __init__(self, packet_path, expected_sha256, allow_gaps=False):
        _require(type(allow_gaps) is bool, 'allow_gaps_boolean_required')
        path = Path(packet_path)
        _require(path.stat().st_size <= 64*1024*1024, 'packet_size_limit')
        raw = path.read_bytes()
        _require(_hex(expected_sha256) and hashlib.sha256(raw).hexdigest() == expected_sha256,
                 'packet_hash_mismatch')
        value = json.loads(raw, object_pairs_hook=_object)
        _require(isinstance(value, dict) and set(value) == TOP, 'packet_schema_fields')
        _require(type(value['schema_version']) is int and value['schema_version'] == 1
                 and value['kind'] == 'OBSERVED_MARKET_SNAPSHOTS' and value['symbol'] == 'BTCUSDT',
                 'packet_schema_identity')
        start, end = value['start_ms'], value['end_ms']
        _require(_integer(start) and _integer(end) and start < end and
                 start % INTERVAL_MS == end % INTERVAL_MS == 0 and end-start <= 288*INTERVAL_MS,
                 'packet_range')
        source = value['source']
        _require(isinstance(source, dict) and set(source) == {'read_only', 'metadata_hashes'}
                 and source['read_only'] is True, 'source_read_only_evidence')
        hashes = source['metadata_hashes']
        _require(isinstance(hashes, dict) and set(hashes) == {'source_identity', 'exporter_code', 'manifest_set'}
                 and all(_hex(h) for h in hashes.values()), 'source_metadata_hashes')
        rows = value['rows']
        _require(isinstance(rows, list) and len(rows) == (end-start)//INTERVAL_MS, 'slot_coverage')
        _require(_hex(value['rows_sha256']) and _digest(rows) == value['rows_sha256'], 'rows_hash_mismatch')
        self._rows, self._schedule = {}, []
        gaps = 0
        for index, row in enumerate(rows):
            slot = start//INTERVAL_MS + index
            _require(isinstance(row, dict) and type(row.get('slot')) is int and row['slot'] == slot,
                     'duplicate_missing_unordered_slot')
            if row.get('status') == 'MISSING':
                _require(set(row) == {'slot', 'status', 'reason'} and isinstance(row['reason'], str)
                         and re.fullmatch('[A-Za-z0-9_]{1,80}', row['reason']), 'gap_schema')
                gaps += 1
                if not allow_gaps:
                    raise ObservedInputGap('observed_input_gap')
                release = slot*INTERVAL_MS
            else:
                self._validate_row(row)
                release = row['available_at_ms']
            self._rows[slot] = row
            self._schedule.append({'slot': slot, 'available_at_ms': release, 'status': row['status']})
        self._start, self._end = start, end
        self._manifest = dict(schema_version=1, kind='OBSERVED_MARKET_SNAPSHOTS', symbol='BTCUSDT',
            packet_sha256=expected_sha256, rows_sha256=value['rows_sha256'],
            schedule_sha256=_digest(self._schedule), start_ms=start, end_ms=end, slots=len(rows),
            available_slots=len(rows)-gaps, missing_slots=gaps, allow_gaps=allow_gaps,
            input_coverage_eligible=not allow_gaps and gaps == 0, profitability_proven=False,
            execution_basis='HYBRID_OBSERVED_INPUT_ASSUMED_EXECUTION', market_truth_verified=False,
            source=deepcopy(source))

    @staticmethod
    def _validate_row(row):
        _require(set(row) == AVAILABLE and row['status'] == 'AVAILABLE', 'observed_row_fields')
        slot, release, ctx_now = row['slot'], row['available_at_ms'], row['source_context_now']
        _require(_integer(release) and slot*INTERVAL_MS <= release < (slot+1)*INTERVAL_MS,
                 'availability_outside_slot')
        _require(type(ctx_now) in (int, float) and math.isfinite(ctx_now)
                 and slot*INTERVAL_MS <= ctx_now*1000 <= release, 'availability_before_context')
        evidence = row['source_evidence']
        _require(isinstance(evidence, dict) and set(evidence) == EVIDENCE, 'source_evidence_fields')
        _require(all(_hex(evidence[k]) for k in ('audit_event_ref', 'audit_body_sha256', 'context_hash', 'manifest_ref'))
                 and (evidence['source_git_revision'] is None or _hex(evidence['source_git_revision'], 40))
                 and evidence['availability_basis'] == 'decision_input_audit_observed_at'
                 and (evidence['request_market_verified'] is True or evidence['request_market_verified'] is None),
                 'source_evidence_invalid')
        quote = row['quote_evidence']
        _require(isinstance(quote, dict) and set(quote) == {'mark_price', 'observed_at_ms', 'source'}
                 and quote['source'] == 'retained_context_mark'
                 and type(quote['mark_price']) in (int, float) and math.isfinite(quote['mark_price'])
                 and quote['mark_price'] > 0 and _integer(quote['observed_at_ms'])
                 and quote['observed_at_ms'] <= release, 'quote_evidence_invalid')
        snap = row['snapshot']
        _require(isinstance(snap, dict) and set(snap) == {'as_of_ms', 'issues', 'timeframes', 'valid'}
                 and snap['valid'] is True and isinstance(snap['issues'], list), 'snapshot_shape_or_invalid')
        _require(_hex(row['snapshot_hash']) and _digest(snap) == row['snapshot_hash'], 'snapshot_hash_mismatch')
        _require(_integer(snap['as_of_ms']) and snap['as_of_ms'] <= release, 'snapshot_from_future')
        _public(snap)
        frames = snap['timeframes']
        _require(isinstance(frames, dict) and set(frames) == FRAMES, 'timeframe_coverage')
        for name, frame in frames.items():
            _require(isinstance(frame, dict), 'frame_shape')
            forming = frame.get('forming')
            if forming is not None:
                _require(isinstance(forming, dict) and _integer(forming.get('observed_at_ms'))
                         and forming['observed_at_ms'] <= snap['as_of_ms'], 'forming_from_future')
            if name in PRIMARY:
                _require(isinstance(forming, dict) and 0 <= release-forming['observed_at_ms'] <= 120000,
                         'primary_input_stale')
            _frame_times(frame, name, snap['as_of_ms'])
        _closed_times(snap, snap['as_of_ms'])

    def schedule(self, start_ms=None, end_ms=None):
        start = self._start if start_ms is None else start_ms
        end = self._end if end_ms is None else end_ms
        _require(_integer(start) and _integer(end) and self._start <= start < end <= self._end
                 and start % INTERVAL_MS == end % INTERVAL_MS == 0, 'schedule_range')
        return deepcopy([row for row in self._schedule if start <= row['slot']*INTERVAL_MS < end])

    def snapshot(self, now_ms):
        _require(_integer(now_ms) and self._start <= now_ms < self._end, 'snapshot_time_outside_packet')
        row = self._rows[now_ms//INTERVAL_MS]
        if row['status'] == 'MISSING':
            raise ObservedInputGap('observed_input_gap')
        _require(now_ms >= row['available_at_ms'], 'observed_input_not_available')
        _require(all(now_ms-row['snapshot']['timeframes'][tf]['forming']['observed_at_ms'] <= 120000
                     for tf in PRIMARY), 'observed_input_stale_at_access')
        return deepcopy(row['snapshot'])

    def manifest(self):
        return deepcopy(self._manifest)
