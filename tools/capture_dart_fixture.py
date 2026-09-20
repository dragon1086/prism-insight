"""Explicit private DART fixture capture and strictly network-free replay.

Raw public responses are quarantined outside Git; only summaries reach stdout.
Capture completeness is not source/financial/model-quality certification.
"""
import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core.dart_report_evidence import collect_latest
from prism_core.report_insight_prefetch import packet
from tools.evaluate_general_filing_reports import _summary

_CODE_FILES = ('prism_core/dart_identity.py', 'prism_core/dart_public_filings.py',
               'prism_core/dart_viewer_tree.py', 'prism_core/dart_section_html.py',
               'prism_core/dart_report_evidence.py', 'prism_core/filing_html.py',
               'prism_core/filing_html_codec.py', 'prism_core/filing_html_policy.py',
               'prism_core/filing_html_projection.py',
               'prism_core/report_source_budget.py',
               'prism_core/filing_html_tables.py', 'prism_core/filing_catalog.py',
               'prism_core/filing_selection.py', 'prism_core/filing_structure.py',
               'prism_core/material_filing_selection.py', 'prism_core/filing_materiality.py',
               'prism_core/filing_table_projection.py', 'prism_core/report_research_prefetch.py',
               'prism_core/filing_report_evidence.py', 'prism_core/report_insight_prefetch.py',
               'tools/evaluate_general_filing_reports.py',
               'tools/dart_fixture_transport.py', 'tools/capture_dart_fixture.py')


def checked_inputs(value):
    try:
        if type(value) is not dict or set(value) != {'version', 'ticker', 'name', 'decision_at', 'scope'}:
            raise ValueError
        cutoff = datetime.fromisoformat(value['decision_at'].replace('Z', '+00:00'))
        if (type(value['version']) is not int or value['version'] != 1
                or not isinstance(value['ticker'], str) or not re.fullmatch(r'[0-9]{6}', value['ticker'])
                or not isinstance(value['name'], str) or not 0 < len(value['name'].strip()) <= 80
                or cutoff.utcoffset() is None or cutoff > datetime.now(timezone.utc)
                or value['scope'] not in {'consolidated', 'standalone'}):
            raise ValueError
        cutoff.astimezone(ZoneInfo('Asia/Seoul'))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('INVALID_CAPTURE_INPUTS') from None
    return cutoff


def _hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in _CODE_FILES}


async def _run(inputs, factory, progress):
    cutoff = checked_inputs(inputs)
    await collect_latest(inputs['ticker'], inputs['name'], cutoff, inputs['scope'], progress,
                         client_factory=factory)
    evidence = packet('KR', inputs['ticker'], cutoff.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat(), progress)
    return _summary(progress, evidence)


async def _settle(task, recorder):
    """Join owned cleanup even if the caller cancels repeatedly."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            recorder.failed = True
        except Exception:  # noqa: BLE001 - the caller reports cancellation, never success
            recorder.failed = True
    if not task.cancelled() and task.exception() is not None:
        recorder.failed = True


async def capture(path, inputs, *, transport_factory=None):
    from tools.dart_fixture_transport import FixtureRecorder

    checked_inputs(inputs)
    started_at = datetime.now(timezone.utc).isoformat()
    recorder = await FixtureRecorder.create(path, transport_factory=transport_factory)
    progress = {'sources': [], 'gaps': [], 'calls': 0}
    code_before = _hashes()
    summary, completed, cancelled = {}, False, False
    failure = None
    try:
        summary = await asyncio.wait_for(_run(inputs, recorder.client_factory, progress), timeout=80)
        completed = True
    except asyncio.CancelledError:
        cancelled, failure = True, 'CAPTURE_CANCELLED'
    except asyncio.TimeoutError:
        failure = 'CAPTURE_TIMEOUT'
    except Exception:  # noqa: BLE001 - preserve failure without response/path/credential text
        failure = 'CAPTURE_FAILED'
    finally:
        if not summary:
            metrics = progress.get('dart_metrics', {})
            summary = {'failure': failure or 'CAPTURE_FAILED', 'metrics': {
                key: sum(m.get(key, 0) for m in metrics.values()) for key in ('calls', 'response_bytes')}}
        metadata = {'result': summary, 'code_sha256': code_before,
                    'code_unchanged': code_before == _hashes(), 'financial_quality_certified': False,
                    'capture_failure_codes': [code for code in ('CAPTURE_BODY_LIMIT', 'CAPTURE_TOTAL_LIMIT')
                        if any(code in entry.get('capture_failure_codes', []) for entry in recorder.responses)],
                    'capture_started_at': started_at,
                    'collection_finished_at': datetime.now(timezone.utc).isoformat(),
                    'source_observed_at': progress.get('filing_selection', {}).get('observed_at'),
                    'historical_version_verified': False}
        finalizer = asyncio.create_task(recorder.finish(inputs, metadata,
            success=completed and metadata['code_unchanged'] and not recorder.failed))
        try:
            manifest = await asyncio.shield(finalizer)
        except asyncio.CancelledError:
            recorder.failed = True
            finalizer.cancel()
            await _settle(finalizer, recorder)
            invalidator = asyncio.create_task(recorder.invalidate())
            await _settle(invalidator, recorder)
            raise
    if cancelled:
        raise asyncio.CancelledError
    return {'stage': 'S1_CAPTURE', 'capture_complete': manifest['complete'],
            'responses': len(manifest['responses']), 'summary': summary,
            'capture_failure_codes': metadata['capture_failure_codes'],
            'code_unchanged': metadata['code_unchanged'], 'financial_quality_certified': False}


async def replay(path):
    from tools.dart_fixture_transport import FixtureReplay

    source = await FixtureReplay.load(path)
    inputs = source.manifest['inputs']
    checked_inputs(inputs)
    progress = {'sources': [], 'gaps': [], 'calls': 0}
    result = await asyncio.wait_for(_run(inputs, source.client_factory, progress), timeout=80)
    source.assert_consumed()
    expected = source.manifest['summary']['result']
    same = result == expected
    return {'stage': 'S1_REPLAY', 'summary_equal': same, 'summary': result,
            'capture_code_sha256': source.manifest['summary']['code_sha256'],
            'replay_code_sha256': _hashes(), 'replay_observed_at': datetime.now(timezone.utc).isoformat(),
            'capture_observed_at': source.manifest['summary']['capture_started_at'],
            'network_calls': 0, 'financial_quality_certified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    live = modes.add_parser('capture')
    live.add_argument('--live', action='store_true')
    live.add_argument('--ticker', required=True)
    live.add_argument('--name', required=True)
    live.add_argument('--decision-at', required=True)
    live.add_argument('--scope', choices=('consolidated', 'standalone'), default='consolidated')
    live.add_argument('--fixture-dir', type=Path, required=True)
    offline = modes.add_parser('replay')
    offline.add_argument('--fixture-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == 'capture':
            if not args.live:
                raise ValueError('LIVE_ACK_REQUIRED')
            inputs = {'version': 1, 'ticker': args.ticker, 'name': args.name,
                      'decision_at': args.decision_at, 'scope': args.scope}
            result = asyncio.run(capture(args.fixture_dir, inputs))
            okay = result['capture_complete']
        else:
            result = asyncio.run(replay(args.fixture_dir))
            okay = result['summary_equal']
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if okay else 2
    except Exception as exc:  # noqa: BLE001 - never expose provider or filesystem error detail
        code = str(exc)
        safe = code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,80}', code) else 'FIXTURE_OPERATION_FAILED'
        print(json.dumps({'status': 'FAILED', 'reason': safe}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
