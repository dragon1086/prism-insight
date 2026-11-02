"""Explicit judgment-only timeframe view; raw evidence and historical codecs stay intact."""
from copy import deepcopy
import math
import re

ANALYSIS_FRAMES = ('30m', '1h', '4h', '12h', '1d', '1w')
PRIMARY_FRAMES = ('30m', '1h')
_RETIRED_TEXT = re.compile(r'(?<![a-z0-9])15[\s_-]*(?:m(?:in(?:ute)?s?)?|분(?:봉)?)(?![a-z0-9])', re.IGNORECASE)
_OPINIONS = {'reason', 'rationale', 'counterevidence', 'invalidation', 'summary', 'note'}


def _omit_retired_opinions(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'entry_contract', 'registered_entry_contract', 'original_contract', 'resolved_goal'}:
                continue
            if key in _OPINIONS and isinstance(child, str) and _RETIRED_TEXT.search(child):
                value[key] = 'OMITTED_RETIRED_TIMEFRAME_OPINION'
            else:
                _omit_retired_opinions(child)
    elif isinstance(value, list):
        for child in value:
            _omit_retired_opinions(child)


def allowed_evidence_path(path):
    if not isinstance(path, str):
        return False
    parts = path.split('.')
    return (not ('volume_acceleration' in parts or 'same_progress_profile' in parts)
            and (not path.startswith('timeframes.') or len(parts) > 1 and parts[1] in ANALYSIS_FRAMES))


def _schema_frames(value, *, field=None, recovery=False):
    if isinstance(value, dict):
        if field == 'frame' and isinstance(value.get('enum'), list):
            value['enum'] = [v for v in value['enum'] if v != '15m']
        if recovery and field == 'changed_evidence':
            items = value.get('items')
            if isinstance(items, dict) and isinstance(items.get('enum'), list):
                items['enum'] = [v for v in items['enum'] if allowed_evidence_path(v)]
        for key, child in value.items():
            if key == 'properties' and isinstance(child, dict):
                for name, schema in child.items():
                    _schema_frames(schema, field=name, recovery=recovery or name == 'recovery')
            else:
                _schema_frames(child, field=field, recovery=recovery or key == 'recovery')
    elif isinstance(value, list):
        for child in value:
            _schema_frames(child, field=field, recovery=recovery)


def analysis_model_view(payload):
    """Do not mistake this deliberate policy view for lossless raw-source storage."""
    result = deepcopy(payload)
    market = result.get('market_snapshot', {})
    if isinstance(market.get('issues'), list):
        market['issues'] = ['NON_ANALYSIS_COLLECTION_ISSUE_DETAIL_OMITTED'
            if isinstance(issue, str) and _RETIRED_TEXT.search(issue) else issue for issue in market['issues']]
    frames = market.get('timeframes')
    if isinstance(frames, dict):
        market['timeframes'] = {k:v for k,v in frames.items() if k in ANALYSIS_FRAMES}
        for frame in market['timeframes'].values():
            forming = frame.get('forming') if isinstance(frame, dict) else None
            if isinstance(forming, dict):
                forming.pop('volume_acceleration', None)
                projection = forming.get('volume_projection')
                if isinstance(projection, dict):
                    projection.pop('same_progress_profile', None)
    ctx = result.get('contract_context', {})
    if (ctx.get('scenario_id') is None and not ctx.get('positions')
            and market.get('valid') is True and isinstance(frames, dict)):
        for tf in PRIMARY_FRAMES:
            frame = market['timeframes'].get(tf)
            if not isinstance(frame, dict) or frame.get('status') != 'ok':
                continue
            confirmed, forming = frame.get('confirmed'), frame.get('forming')
            if (not isinstance(confirmed, dict) or not isinstance(forming, dict)
                    or forming.get('observation_kind') == 'synthetic_boundary'):
                continue
            ohlcv = forming.get('ohlcv')
            if not isinstance(ohlcv, dict) or confirmed.get('is_confirmed') is not True:
                continue
            close, ma, atr = ohlcv.get('close'), confirmed.get('ma10'), confirmed.get('atr14_risk_only')
            if not all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (close, ma, atr)):
                continue
            ratio = (close - ma) / atr
            if math.isfinite(ratio):
                frame['forming_last_vs_confirmed_ma10_atr'] = round(ratio, 3)
    if (ctx.get('scenario_id') is not None or ctx.get('positions')) and isinstance(frames, dict):
        primary = market['timeframes'].get('30m')
        if isinstance(primary, dict):
            primary.pop('volume_4d_30m', None)
    structure = ctx.get('ma_structure')
    if isinstance(structure, dict):
        structure['primary'] = {k:v for k,v in structure.get('primary', {}).items() if k in PRIMARY_FRAMES}
        if isinstance(structure.get('higher_frames'), dict):
            structure['higher_frames'] = {k:v for k,v in structure['higher_frames'].items() if k in ANALYSIS_FRAMES}
        if isinstance(structure.get('levels'), dict):
            structure['levels'] = {k:v for k,v in structure['levels'].items() if k.split('.')[0] in ANALYSIS_FRAMES}
            for key in ('long_upward_obstacles', 'short_downward_obstacles', 'equal_reference_levels'):
                if isinstance(structure.get(key), list):
                    structure[key] = [ref for ref in structure[key] if ref in structure['levels']]
        if 'status' in structure:
            retained = list(structure.get('primary', {}).values()) + list(structure.get('higher_frames', {}).values())
            structure['status'] = 'available' if any(row.get('status') == 'available' for row in retained) else 'unavailable'
        structure.get('decision_priority', {})['timing_only'] = []
    facts = ctx.get('current_primary_frame_facts')
    if isinstance(facts, dict):
        ctx['current_primary_frame_facts'] = {k:v for k,v in facts.items() if k in PRIMARY_FRAMES}
    display = ctx.get('entry_display')
    if isinstance(display, dict) and isinstance(display.get('confirmed_close_tail'), dict):
        display['confirmed_close_tail'] = {k:v for k,v in display['confirmed_close_tail'].items() if k in PRIMARY_FRAMES}
    recovery = ctx.get('recovery')
    if isinstance(recovery, dict):
        for key in ('baseline', 'current', 'changed_evidence'):
            if isinstance(recovery.get(key), dict):
                recovery[key] = {k:v for k,v in recovery[key].items() if allowed_evidence_path(k)}
        _omit_retired_opinions(recovery.get('historical_outcomes'))
    for key in ('position_flow', 'last_closed_trade', 'previous_thesis_assessment', 'review_memory',
                'current_plan', 'recent_actions', 'recent_waits'):
        _omit_retired_opinions(ctx.get(key))
    _schema_frames(result.get('response_contract'))
    ctx['analysis_frame_policy'] = dict(version=1, frames=list(ANALYSIS_FRAMES),
        scope='CURRENT_JUDGMENT_ONLY_NO_15M_OR_15M_DERIVED_TIMING',
        history='Original contracts/IDs/goal prices/failure rules are immutable history, not current lower-frame evidence.')
    return result
