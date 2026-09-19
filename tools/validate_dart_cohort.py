"""Preregistered multi-company DART diagnostic, never a trading entry point."""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
_URL = 'https://dart.fss.or.kr/dsab001/searchCorp.ax'


def _inputs(manifest, group):
    if not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest['version'] != 1:
        raise ValueError('INVALID_MANIFEST')
    try:
        cutoff = datetime.fromisoformat(manifest['decision_at'].replace('Z', '+00:00'))
        start = date.fromisoformat(manifest['start_date'])
        cases = manifest['groups'][group]
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError('INVALID_MANIFEST') from exc
    if (cutoff.utcoffset() is None or type(cases) is not list or not 1 <= len(cases) <= 10
            or manifest.get('scope') not in ('consolidated', 'standalone')
            or start > cutoff.astimezone(ZoneInfo('Asia/Seoul')).date()):
        raise ValueError('INVALID_MANIFEST')
    for case in cases:
        if not isinstance(case, dict) or any(type(case.get(k)) is not str or not 0 < len(case[k].strip()) <= 80
                                             for k in ('name', 'ticker', 'sector')):
            raise ValueError('INVALID_MANIFEST')
        if not re.fullmatch(r'[0-9]{6}', case['ticker']):
            raise ValueError('INVALID_MANIFEST')
    if len({c['name'] for c in cases}) != len(cases) or len({c['ticker'] for c in cases}) != len(cases):
        raise ValueError('DUPLICATE_CASE')
    return cutoff, start, cases


async def _identity(name, ticker, start, cutoff, client_factory):
    data = {'currentPage': '1', 'maxResults': '100', 'maxLinks': '10', 'sort': 'date',
            'series': 'desc', 'textCrpNm': name, 'textCrpCik': '', 'autoSearchCorp': 'Y',
            'pageGubun': 'corp', 'startDate': start.strftime('%Y%m%d'),
            'endDate': cutoff.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),
            'publicType': ['A001', 'A002', 'A003']}
    lookups = []

    def codes_from(tree, exact_name=None):
        codes, names = set(), set()
        for node in tree.xpath('//tbody[@id="tbody"]//a[contains(@onclick,"openCorpInfoNew")]'):
            displayed = ' '.join(node.text_content().split())
            match = re.search(r'''openCorpInfoNew\(\s*['"]([0-9]{8})['"]''', node.get('onclick', ''))
            if match and (exact_name is None or displayed == ' '.join(exact_name.split())):
                codes.add(match[1])
                names.add(displayed)
        return codes, names

    result = {'status': 'IDENTITY_UNRESOLVED', 'corp_code': None, 'lookups': lookups,
              'ticker_verified_from_company_profile': False}
    async with client_factory(timeout=15, trust_env=False, follow_redirects=False) as client:
        async def fetch(url, fields, lookup):
            client.cookies.clear()
            async with client.stream('POST', url, data=fields, headers={'Accept-Encoding': 'identity'}) as response:
                if (response.status_code != 200
                        or response.headers.get('content-encoding', 'identity').strip().lower() != 'identity'):
                    raise ValueError('IDENTITY_RESPONSE_FAILURE')
                raw = bytearray()
                chunks = response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
                async for chunk in chunks:
                    if len(raw) + len(chunk) > 1024 * 1024:
                        raise ValueError('IDENTITY_RESPONSE_LIMIT')
                    raw.extend(chunk)
            lookups.append({'kind': lookup, 'url': url, 'response_sha256': hashlib.sha256(raw).hexdigest(),
                            'response_bytes': len(raw)})
            return html.fromstring(bytes(raw).decode('utf-8'))

        codes, names = codes_from(await fetch(_URL, data, 'name'), name)
        resolution = 'OFFICIAL_NAME_QUERY'
        if len(codes) != 1:
            name_codes = codes
            codes, names = codes_from(await fetch(_URL, {**data, 'textCrpNm': ticker}, 'ticker'))
            resolution = 'OFFICIAL_TICKER_QUERY'
            if name_codes and not codes <= name_codes:
                result['reason'] = 'NAME_TICKER_IDENTITY_CONFLICT'
                return result
        if len(codes) != 1:
            result['reason'] = 'IDENTITY_AMBIGUOUS_OR_MISSING'
            return result
        code = next(iter(codes))
        profile = await fetch('https://dart.fss.or.kr/dsae001/selectPopup.ax', {'selectKey': code}, 'profile')
        profile_tickers = []
        for row in profile.xpath('//tr'):
            cells = [' '.join(node.text_content().split()) for node in row.xpath('./th|./td')]
            if len(cells) >= 2 and re.sub(r'\s+', '', cells[0]).rstrip(':') == '종목코드':
                profile_tickers.extend(re.findall(r'(?<![0-9])[0-9]{6}(?![0-9])', ' '.join(cells[1:])))
        if profile_tickers != [ticker]:
            result['reason'] = 'PROFILE_TICKER_MISMATCH'
            return result
        result.update(status='RESOLVED_WITH_OFFICIAL_PROFILE', corp_code=code, resolution=resolution,
                      official_display_names=sorted(names), ticker_verified_from_company_profile=True)
        return result


async def run_cohort(manifest, group, *, collector=None, client_factory=httpx.AsyncClient):
    from tools.probe_dart_filings import _receipt

    cutoff, start, cases = _inputs(manifest, group)
    if collector is None:
        from prism_core.dart_public_filings import collect_dart_periodic_filings
        collector = collect_dart_periodic_filings
    started = time.monotonic()
    result = {'group': group, 'observed_at': datetime.now(timezone.utc).isoformat(), 'cases': [],
              'manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              'collector_sha256': hashlib.sha256((ROOT / 'prism_core/dart_public_filings.py').read_bytes()).hexdigest(),
              'query': {'decision_at': cutoff.isoformat(), 'start_date': start.isoformat(), 'scope': manifest['scope']},
              'limitations': ['FINITE_COHORT_NOT_ALL_SYMBOLS', 'LIVE_REQUERY_NOT_FROZEN_RESPONSE_AB',
                             'MAPPING_VERIFIED_WITHIN_DART_NOT_SECOND_PROVIDER', 'NOT_REPORT_OR_BUY_VALIDATION']}
    for case in cases:
        row = {**case, 'status': 'CASE_FAILED'}
        result['cases'].append(row)
        begin = time.monotonic()
        stage = 'IDENTITY'
        try:
            identity = await asyncio.wait_for(_identity(case['name'], case['ticker'], start, cutoff, client_factory), timeout=15)
            row['identity'] = identity
            if identity['corp_code'] is None:
                row['status'] = 'IDENTITY_UNRESOLVED'
            else:
                stage = 'COLLECTION'
                receipt = await collector(corp_code=identity['corp_code'], decision_at=cutoff,
                                          start_date=start, scope=manifest['scope'])
                row.update(status=receipt['status'], receipt=_receipt(receipt))
        except asyncio.TimeoutError:
            row.update(status='CASE_FAILED', reason=f'{stage}_TIMEOUT')
        except httpx.HTTPError:
            row.update(status='CASE_FAILED', reason=f'{stage}_TRANSPORT_FAILURE')
        except Exception:  # noqa: BLE001 - keep failed cases without leaking provider errors
            row.update(status='CASE_FAILED', reason='COHORT_CASE_FAILED')
        row['elapsed_seconds'] = round(time.monotonic() - begin, 6)
    result['summary'] = {'case_count': len(cases), 'selected_count': sum(
        bool(r.get('receipt', {}).get('selection', {}).get('primary_id')) for r in result['cases']),
        'complete_count': sum(r['status'] == 'COMPLETE_WITHIN_QUERY' for r in result['cases'])}
    result['elapsed_seconds'] = round(time.monotonic() - started, 6)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'tools/fixtures/dart_generalization_20260920.json')
    parser.add_argument('--group', choices=('control', 'development', 'holdout', 'holdout_round2'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({'status': 'FAILED', 'reason': 'LIVE_ACK_REQUIRED'}))
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(json.dumps({'status': 'FAILED', 'reason': 'OUTPUT_EXISTS'}))
        return 2
    try:
        result = asyncio.run(run_cohort(json.loads(args.manifest.read_text()), args.group))
        result['runner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result['code_version_basis'] = 'SOURCE_SHA256_COMMIT_RECORDED_EXTERNALLY'
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        print(json.dumps(result['summary']))
        return 0 if result['summary']['complete_count'] == result['summary']['case_count'] else 2
    except Exception:  # noqa: BLE001 - diagnostics retain a static outer failure
        print(json.dumps({'status': 'FAILED', 'reason': 'COHORT_FAILED'}))
        return 2


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
