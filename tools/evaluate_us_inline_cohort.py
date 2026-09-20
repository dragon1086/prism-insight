"""Frozen US development cohort, existing Yahoo feed/CDN only, never SEC access.

Compare fixed baseline parser and current inline parser against identical bytes.
This is provider-only evidence, not an official issuer/latest-filing certificate.
Each live case runs in a subprocess with a hard timeout; failed cases remain.
"""
import argparse
import ast
import hashlib
import json
import logging
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core.sec_inline_evidence import MAX_BYTES, parse_inline_revenue

BASELINE = '3a8bfac0'
MANIFEST = ROOT / 'tools/fixtures/filing_generalization_round3_20260920.json'
PARSER_PATH = ROOT / 'prism_core/sec_inline_evidence.py'
PARSER_SHA256 = hashlib.sha256(PARSER_PATH.read_bytes()).hexdigest()


def provider_identity(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r'/prod/sec-filings/(\d{10})/(\d{18})/([A-Za-z0-9][A-Za-z0-9_.-]{0,199})', parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc != 'cdn.yahoofinance.com'
            or parsed.query or parsed.fragment or not match or '..' in parsed.path):
        raise ValueError('PROVIDER_URL_INVALID')
    # Accession prefix can belong to an authorized filing agent, not the issuer.
    return {'cik': match[1], 'accession': match[2]}


def baseline_parser():
    source = subprocess.run(['git', 'show', f'{BASELINE}:prism-us/cores/data_prefetch.py'],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10).stdout
    names = {'_clean_xbrl_label', '_parse_10k_segment_revenue'}
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes} != names:
        raise ValueError('BASELINE_HELPERS_MISSING')
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<fixed-baseline-helpers>', 'exec'), namespace)  # noqa: S102 -- two named trusted local git functions, never provider code
    return namespace['_parse_10k_segment_revenue']


def compare_body(body, cik, *, old_parser):
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise ValueError('PROVIDER_BODY_LIMIT')
    result = {'source_sha256': hashlib.sha256(body).hexdigest(), 'source_bytes': len(body)}
    try:
        old = old_parser(body.decode('utf-8'))
        result['baseline'] = {'status': 'OUTPUT' if old else 'EMPTY', 'output_bytes': len(old.encode()),
            'assumed_millions_usd': 'in millions USD' in old,
            'fy_labels': sorted(set(re.findall(r'\bFY\d{4}\b', old))),
            'zero_cells': old.count('$0M'),
            'caveat': 'Zero cells may be missing-value fills; not proof of reported zero.'}
    except (ValueError, UnicodeError, KeyError, TypeError):
        result['baseline'] = {'status': 'FAILED', 'reason': 'BASELINE_PARSE_FAILURE'}
    parsed = parse_inline_revenue(body, expected_cik=cik)
    facts = parsed['facts']
    periods = sorted({(f['period']['start'], f['period']['end']) for f in facts})
    dimensions = sorted({(d['axis'], d['member']) for f in facts for d in f['dimensions']})
    result['new'] = {'status': parsed['status'], 'facts_count': len(facts),
        'units': sorted({f['unit'] for f in facts}),
        'periods': [{'start': start, 'end': end} for start, end in periods],
        'dimensions_count': len(dimensions),
        'dimensions': [{'axis': a, 'member': m} for a, m in dimensions[:100]],
        'dimensions_omitted': max(0, len(dimensions) - 100),
        'gap_reasons': sorted({g['reason'] for g in parsed['gaps']}), 'gaps_count': len(parsed['gaps']),
        'sample_facts': [{k: f[k] for k in ('concept', 'value', 'unit', 'period', 'dimensions',
            'raw_value', 'scale', 'sign', 'context_ref')} for f in facts[:5]],
        'exact_base_units_not_assumed_millions': True}
    return result


def _fetch_cdn(url):
    import httpx
    provider_identity(url)
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client, client.stream(
        'GET', url, headers={'User-Agent': 'PRISM-INSIGHT/1.0', 'Accept-Encoding': 'identity'},
    ) as response:
        if response.status_code != 200:
            raise ValueError(f'PROVIDER_HTTP_{response.status_code}')
        if str(response.url) != url or response.headers.get('content-encoding', 'identity') != 'identity':
            raise ValueError('PROVIDER_REDIRECT_OR_ENCODING')
        body = bytearray()
        for chunk in response.iter_raw():
            if len(body) + len(chunk) > MAX_BYTES:
                raise ValueError('PROVIDER_BODY_LIMIT')
            body.extend(chunk)
        return bytes(body)


def evaluate_case(case, filings, *, fetch, old_parser, decision_at=None):
    out = {**case, 'status': 'FAILED', 'identity_basis': 'PROVIDER_ONLY_NOT_OFFICIAL',
        'latest_certified': False, 'selection_basis': 'PROVIDER_FIRST_PERIODIC_NOT_LATEST_CERTIFIED'}
    filing = next((f for f in filings if isinstance(f, dict) and f.get('type') in {'10-K', '10-Q', '20-F', '40-F'}), None)
    if filing is None:
        return {**out, 'reason': 'PROVIDER_PERIODIC_MISSING'}
    try:
        form = filing['type']
        url = filing.get('exhibits', {}).get(form, '')
        identity = provider_identity(url)
        filed = filing.get('date')
        filed = filed.isoformat() if isinstance(filed, date) else filed
        if not isinstance(filed, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', filed):
            raise ValueError
        date.fromisoformat(filed)
    except (TypeError, ValueError, AttributeError):
        return {**out, 'reason': 'PROVIDER_METADATA_INVALID'}
    out.update(provider_filing_type=form, provider_filing_date=filed, source_url=url, **identity)
    if decision_at is not None:
        cutoff = decision_at.astimezone(ZoneInfo('America/New_York')).date()
        if date.fromisoformat(filed) >= cutoff:
            return {**out, 'reason': 'PROVIDER_FUTURE_FILING' if date.fromisoformat(filed) > cutoff
                    else 'PROVIDER_SAME_DAY_PUBLICATION_UNRESOLVED'}
    try:
        body = fetch(url)
        out.update(compare_body(body, identity['cik'], old_parser=old_parser))
        out['status'] = 'EVIDENCE' if out['new']['facts_count'] else 'NO_SUPPORTED_FACTS'
    except Exception as exc:  # noqa: BLE001 -- redact provider payloads and keep denominator
        code = str(exc)
        out['reason'] = code if re.fullmatch(r'PROVIDER_(?:BODY_LIMIT|HTTP_\d{3}|REDIRECT_OR_ENCODING)', code) else 'PROVIDER_BODY_FAILURE'
    return out


def _live_case(case, decision_at):
    import yfinance as yf
    logging.disable(logging.CRITICAL)
    try:
        filings = yf.Ticker(case['ticker']).sec_filings
        result = evaluate_case(case, filings or [], fetch=_fetch_cdn, old_parser=baseline_parser(), decision_at=decision_at)
    except Exception:  # noqa: BLE001 -- no upstream payload or credential leakage
        result = {**case, 'status': 'FAILED', 'reason': 'PROVIDER_FEED_FAILURE',
                  'identity_basis': 'PROVIDER_ONLY_NOT_OFFICIAL', 'latest_certified': False}
    result['feed_invocations'] = 1
    result['parser_sha256'] = PARSER_SHA256
    result['feed_http_request_count'] = None  # yfinance may issue internal requests.
    return result


def frozen_cohort(manifest, group):
    """Return the complete named cohort; never omit failed or unsupported cases."""
    if group not in {'development', 'holdout'}:
        raise ValueError('COHORT_GROUP_INVALID')
    cohort = manifest['US'][group]
    if (not isinstance(cohort, list) or not cohort
            or any(not isinstance(c, dict) or not re.fullmatch(r'[A-Z0-9.-]{1,15}', c.get('ticker', '')) for c in cohort)
            or len({c['ticker'] for c in cohort}) != len(cohort)):
        raise ValueError('COHORT_INVALID')
    return cohort


def compare_baseline(cases, baseline):
    earlier = {case['ticker']: case for case in baseline['cases']}
    for case in cases:
        previous = earlier.get(case['ticker'], {})
        old_hash, new_hash = previous.get('source_sha256'), case.get('source_sha256')
        case['baseline_source_sha256'] = old_hash
        case['baseline_body_unchanged'] = old_hash == new_hash if old_hash and new_hash else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-development', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--group', choices=('development', 'holdout'), default='development')
    parser.add_argument('--compare-baseline', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--worker-ticker')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    cohort = frozen_cohort(manifest, args.group)
    decision_at = datetime.fromisoformat(manifest['decision_at'])
    if decision_at.utcoffset() is None:
        parser.error('Frozen decision time must be aware')
    if args.run_development and args.group != 'development':
        parser.error('--run-development cannot select holdout')
    if not args.live and not args.run_development:
        parser.error('Explicit --live or --run-development required')
    if args.worker_ticker:
        case = next((c for c in cohort if c['ticker'] == args.worker_ticker), None)
        if case is None:
            parser.error('Only the named frozen cohort is allowed')
        print(json.dumps(_live_case(case, decision_at), ensure_ascii=False))
        return
    if args.out is None or args.out.exists():
        parser.error('A new --out path is required')
    parser_before = hashlib.sha256(PARSER_PATH.read_bytes()).hexdigest()
    cases = []
    for case in cohort:
        try:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--live', '--group', args.group,
                '--worker-ticker', case['ticker']],
                cwd=ROOT, capture_output=True, text=True, timeout=100, check=True)
            result = json.loads(process.stdout.strip().splitlines()[-1])
        except (subprocess.SubprocessError, ValueError, IndexError):
            result = {**case, 'status': 'FAILED', 'reason': 'BOUNDED_WORKER_FAILURE',
                      'identity_basis': 'PROVIDER_ONLY_NOT_OFFICIAL', 'latest_certified': False}
        cases.append(result)
    if args.compare_baseline:
        compare_baseline(cases, json.loads(args.compare_baseline.read_text()))
    parser_after = hashlib.sha256(PARSER_PATH.read_bytes()).hexdigest()
    out = {'observed_at': datetime.now(timezone.utc).isoformat(), 'baseline_commit': BASELINE,
        'parser_sha256_before': parser_before, 'parser_sha256_after': parser_after,
        'parser_code_stable': parser_before == parser_after and all(c.get('parser_sha256') == parser_before for c in cases),
        'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'manifest_sha256': hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), 'cohort': f'US.{args.group}',
        'decision_at': decision_at.isoformat(), 'denominator': len(cohort), 'holdout_run': args.group == 'holdout', 'sec_requests': 0,
        'limitations': ['Existing Yahoo provider only; SEC access denied separately.',
            'No official latest-filing proof, generated report quality, or investment-performance inference.',
            'One provider feed invocation per case; internal HTTP count is not observed.',
            'Bodies capped at parser MAX_BYTES; worker timeout 100 seconds per case.'],
        'cases': cases}
    with args.out.open('x') as stream:
        json.dump(out, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'cases': len(cases), 'evidence_cases': sum(c['status'] == 'EVIDENCE' for c in cases),
                      'output': str(args.out)}))


if __name__ == '__main__':
    main()
