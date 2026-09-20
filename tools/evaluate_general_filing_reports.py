"""Explicit, bounded KR filing-to-report cohort evaluation without report models."""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'tools/fixtures/filing_generalization_round3_20260920.json'


def _inputs(manifest, group):
    try:
        if type(manifest) is not dict or type(manifest['version']) is not int or manifest['version'] != 1:
            raise ValueError
        cutoff = datetime.fromisoformat(manifest['decision_at'].replace('Z', '+00:00'))
        cases = manifest['KR'][group]
        if (cutoff.utcoffset() is None or manifest['scope'] not in {'consolidated', 'standalone'}
                or type(cases) is not list or not 1 <= len(cases) <= 12):
            raise ValueError
        cutoff.astimezone(ZoneInfo('Asia/Seoul'))
        for case in cases:
            if (type(case) is not dict or any(type(case.get(k)) is not str or not 0 < len(case[k].strip()) <= 80
                                            for k in ('ticker', 'name', 'sector'))
                    or not re.fullmatch(r'[0-9]{6}', case['ticker'])):
                raise ValueError
        if len({c['ticker'] for c in cases}) != len(cases):
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError('INVALID_MANIFEST') from None
    return cutoff, cases


def _codes(values):
    """Only code-shaped errors leave this evaluator, never provider messages."""
    return sorted({x if type(x) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}', x)
                   else 'UNCLASSIFIED_SOURCE_ERROR' for x in values})


def _filing(source):
    return {k: source.get('filing', {}).get(k, 'UNKNOWN') for k in (
        'receipt_id', 'role', 'kind', 'period_start', 'period_end', 'scope', 'section', 'entity_id')}


def _fragment_delivery(values):
    """Project only bounded metadata; raw context is never an evaluator result."""
    from prism_core.filing_html_policy import MAX_HTML_BYTES

    def key(value):
        return value if type(value) is str and re.fullmatch(r'[0-9]{1,14}:[0-9]{1,14}', value) else None

    def digest(value):
        return value if type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) else None

    def count(value, ceiling=2000):
        return value if type(value) is int and 0 <= value <= ceiling else None

    def keys(value):
        return [v for v in value[:2000] if key(v)] if type(value) is list else []

    def url(value):
        if type(value) is not str or len(value) > 1024:
            return None
        import httpx

        from tools.dart_fixture_transport import _request_key

        try:
            request = httpx.Request('GET', value)
            _request_key(request)
            return value if request.url.path == '/report/viewer.do' else None
        except (ValueError, UnicodeError, httpx.InvalidURL):
            return None

    result = {}
    if type(values) is not dict:
        return result
    for receipt, value in islice(values.items(), 20):
        if type(receipt) is not str or not re.fullmatch(r'[0-9]{14}', receipt) or type(value) is not dict:
            continue
        row = {'version': value.get('version') if value.get('version') == 'dart-note-fragments-v1' else None,
               'basis': value.get('basis') if value.get('basis') == 'title-label-presence-v1' else None,
               'parent_key': key(value.get('parent_key')), 'main_sha256': digest(value.get('main_sha256')),
               'full_notes_acquired': False,
               'discarded_due_to_final_selection': value.get('discarded_due_to_final_selection') is True}
        row.update({k: count(value.get(k)) for k in ('child_total', 'eligible_total', 'unselected_count')})
        row.update({k: keys(value.get(k)) for k in ('planned_keys', 'requested_keys', 'acquired_keys', 'budget_omitted_keys')})
        row['stop_reason'] = _codes([value['stop_reason']])[0] if value.get('stop_reason') else None
        failures = value.get('failures', [])
        row['failures'] = [{'child_key': key(f.get('child_key')), 'code': _codes([f.get('code')])[0]}
                           for f in failures[:20] if type(f) is dict] if type(failures) is list else []
        row['fragments'] = {}
        fragments = value.get('fragments', {})
        for child, item in islice(fragments.items(), 2) if type(fragments) is dict else []:
            if not key(child) or type(item) is not dict:
                continue
            gaps = item.get('gaps', [])
            row['fragments'][child] = {
                'context_verified': item.get('context_verified') if type(item.get('context_verified')) is bool else None,
                'candidate_count': count(item.get('candidate_count')),
                'gaps': _codes(gaps[:64]) if type(gaps) is list else ['UNCLASSIFIED_SOURCE_ERROR'],
                'sha256': digest(item.get('sha256')), 'utf8_bytes': count(item.get('utf8_bytes'), MAX_HTML_BYTES),
                'url': url(item.get('url'))}
        result[receipt] = row
    return result


def _summary(progress, evidence):
    from prism_core.report_insight_prefetch import expand_filing_record

    selection_context = progress.get('filing_selection', {})
    identity = selection_context.get('identity', {})
    selection = selection_context.get('selection', {})
    candidates = []
    for source in progress['sources']:
        blocks = source.get('blocks', [])
        candidates.append({**_filing(source), 'source_id': source['source_id'], 'blocks': len(blocks),
            'topics': dict(Counter(b['topic'] for b in blocks)),
            'representation_sha256': sorted({b.get('provenance', {}).get('representation_sha256')
                for b in blocks if re.fullmatch(r'[a-f0-9]{64}', str(b.get('provenance', {}).get('representation_sha256', '')))}),
            'locator_present_count': sum(bool(b.get('provenance', {}).get('source_path')) for b in blocks),
            'fact_validation': 'NOT_PERFORMED'})
    final = {}
    for section, raw in evidence['section_notes'].items():
        payload = json.loads(raw)
        rows = [expand_filing_record(row, payload) for row in payload['sources']]
        final[section] = {'utf8_bytes': len(raw.encode()), 'records': len(rows),
            'roles': dict(Counter(r.get('filing', {}).get('role', 'UNKNOWN') for r in rows)),
            'topics': dict(Counter(r['topic'] for r in rows)), 'omitted_blocks': payload['omitted_blocks'],
            'topic_gaps': payload['topic_gaps'],
            'delivered_filings': list({json.dumps(_filing(r), sort_keys=True): _filing(r) for r in rows}.values())}
    metrics = progress.get('dart_metrics', {})
    bodies = selection_context.get('selected_section_delivery', {})
    result = {
        'identity': {'status': identity.get('status', 'NOT_ATTEMPTED'), 'corp_code': identity.get('corp_code'),
                     'ticker_verified': identity.get('ticker_verified_from_company_profile') is True,
                     'reasons': _codes([identity['reason']]) if identity.get('reason') else []},
        'selection': {k: selection.get(k) for k in ('status', 'primary_id', 'annual_supplement_id', 'latest_confirmed')},
        'selection_reasons': _codes(selection.get('reasons', [])),
        'body_delivery': {key: {'status': value.get('status'), 'missing_sections': value.get('missing_sections', []),
                              'errors': _codes(value.get('errors', []))} for key, value in bodies.items()},
        'body_provenance': {key: {name: {field: section.get(field) for field in ('sha256', 'utf8_bytes', 'url')}
                                  for name, section in sections.items()}
                            for key, sections in selection_context.get('selected_section_provenance', {}).items()},
        'collector_errors': _codes(selection_context.get('errors', [])),
        'candidate_sources': candidates, 'final_delivery': final,
        'metrics': {'calls': sum(m.get('calls', 0) for m in metrics.values()),
                    'response_bytes': sum(m.get('response_bytes', 0) for m in metrics.values())},
        'gaps': _codes(progress['gaps']),
    }
    if selection_context.get('selected_note_fragments'):
        result['fragment_delivery'] = _fragment_delivery(selection_context['selected_note_fragments'])
    return result


async def evaluate(manifest, group, *, collector=None, packet_builder=None):
    cutoff, cases = _inputs(manifest, group)
    if collector is None:
        from prism_core.dart_report_evidence import collect_latest
        collector = collect_latest
    if packet_builder is None:
        from prism_core.report_insight_prefetch import packet
        packet_builder = packet
    result = {'version': 2, 'market': 'KR', 'group': group, 'decision_at': cutoff.isoformat(),
              'observed_at': datetime.now(timezone.utc).isoformat(), 'scope': manifest['scope'],
              'manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              'source_hashes': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (
                  'prism_core/dart_identity.py', 'prism_core/dart_public_filings.py', 'prism_core/dart_report_evidence.py',
                  'prism_core/filing_html.py', 'prism_core/report_insight_prefetch.py')},
              'limitations': ['FINITE_COHORT_NOT_ALL_ISSUERS', 'LIVE_REQUERY_NOT_FROZEN_RESPONSE_AB',
                              'NO_MODEL_OR_INVESTMENT_QUALITY_VALIDATION', 'NO_CONDITION_OR_UNIT_SEMANTIC_CERTIFICATION'],
              'cases': []}
    consecutive_outages = 0
    for case in cases:  # Sequential by design; every requested issuer stays in the denominator.
        start = time.monotonic()
        progress = {'sources': [], 'gaps': [], 'calls': 0}
        row = {k: case[k] for k in ('ticker', 'name', 'sector')}
        row['status'] = 'PARTIAL'
        result['cases'].append(row)
        if consecutive_outages >= 2:
            row.update(status='NOT_RUN_PROVIDER_OUTAGE', failure='PROVIDER_OUTAGE_CIRCUIT_OPEN',
                       metrics={'calls': 0, 'response_bytes': 0}, elapsed_seconds=0)
            continue
        try:
            await asyncio.wait_for(collector(case['ticker'], case['name'], cutoff, manifest['scope'], progress), 80)
        except asyncio.TimeoutError:
            row.update(status='FAILED', failure='COLLECTION_TIMEOUT')
        except Exception:  # noqa: BLE001 - no raw provider exception output
            row.update(status='FAILED', failure='COLLECTION_FAILED')
        try:
            evidence = packet_builder('KR', case['ticker'], cutoff.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat(), progress)
            row.update(_summary(progress, evidence))
            codes = set(row['identity']['reasons']) | set(row['collector_errors'])
            unavailable = not row['identity']['ticker_verified'] or not row['selection']['primary_id']
            if unavailable and row['status'] != 'FAILED':
                row['status'] = 'UNAVAILABLE'
            outage = unavailable and bool(codes & {'IDENTITY_TRANSPORT_FAILURE', 'HTTP_TRANSPORT_FAILURE'})
            consecutive_outages = consecutive_outages + 1 if outage else 0
        except Exception:  # noqa: BLE001 - retain failed denominator and measured traffic
            row.update(status='FAILED', failure='PACKET_EVALUATION_FAILED', metrics={
                key: sum(m.get(key, 0) for m in progress.get('dart_metrics', {}).values())
                for key in ('calls', 'response_bytes')})
        row['elapsed_seconds'] = round(time.monotonic() - start, 6)
    rows = result['cases']
    result['summary'] = {'case_count': len(rows), 'failed_count': sum(r['status'] == 'FAILED' for r in rows),
        'unavailable_count': sum(r['status'] == 'UNAVAILABLE' for r in rows),
        'provider_outage_not_run_count': sum(r['status'] == 'NOT_RUN_PROVIDER_OUTAGE' for r in rows),
        'general_use_certified': False,
        'identity_verified_count': sum(r.get('identity', {}).get('ticker_verified', False) for r in rows),
        'primary_selected_count': sum(bool(r.get('selection', {}).get('primary_id')) for r in rows),
        'latest_confirmed_count': sum(r.get('selection', {}).get('latest_confirmed') is True for r in rows),
        'selected_bodies_available_count': sum(bool(r.get('body_delivery')) and all(
            v['status'] == 'AVAILABLE' for v in r['body_delivery'].values()) for r in rows),
        'candidate_present_count': sum(bool(r.get('candidate_sources')) for r in rows),
        'final_records_present_count': sum(any(v['records'] for v in r.get('final_delivery', {}).values()) for r in rows),
        'all_owner_sections_present_count': sum(len(r.get('final_delivery', {})) == 3 and all(
            v['records'] for v in r['final_delivery'].values()) for r in rows),
        'calls': sum(r['metrics']['calls'] for r in rows), 'response_bytes': sum(r['metrics']['response_bytes'] for r in rows)}
    result['source_hashes_unchanged_at_finish'] = all(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest for path, digest in result['source_hashes'].items())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--group', choices=('development', 'holdout'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({'status': 'FAILED', 'reason': 'LIVE_ACK_REQUIRED'}))
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(json.dumps({'status': 'FAILED', 'reason': 'OUTPUT_EXISTS'}))
        return 2
    try:
        result = asyncio.run(evaluate(json.loads(args.manifest.read_text()), args.group))
        result['evaluator_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        print(json.dumps(result['summary']))
        return 0 if (not result['summary']['failed_count'] and not result['summary']['unavailable_count']
                     and not result['summary']['provider_outage_not_run_count']) else 2
    except Exception:  # noqa: BLE001 - CLI must not expose provider/secrets text
        print(json.dumps({'status': 'FAILED', 'reason': 'EVALUATION_FAILED'}))
        return 2


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
