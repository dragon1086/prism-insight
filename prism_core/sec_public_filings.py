"""Bounded official SEC metadata, not verified filing content or financial facts.

API contract: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
Fair access: https://www.sec.gov/about/developer-resources
No credentials, retry, redirect, alternate-host fallback, or inferred fiscal dates.
"""
import asyncio
import hashlib
import json
import re
import threading
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

_TICKERS = 'https://www.sec.gov/files/company_tickers.json'
_SUBMISSIONS = 'https://data.sec.gov/submissions/'
_PERIODIC = {'10-Q', '10-K', '20-F', '40-F'}
_ANNUAL = {'10-K', '20-F', '40-F'}
_EVENTS = {'8-K', '6-K'}
_COLUMNS = ('accessionNumber', 'form', 'reportDate', 'filingDate', 'acceptanceDateTime', 'primaryDocument')
_REQUEST_INTERVAL = 0.2  # At most five starts/second across this process.
_rate_lock = threading.Lock()
_next_request_at = 0.0


class _SECError(ValueError):
    """Static public-safe failure code."""


async def _rate_wait():
    global _next_request_at
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_request_at) if _REQUEST_INTERVAL else now
        _next_request_at = slot + _REQUEST_INTERVAL
    await asyncio.sleep(max(0, slot - now))


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError
    return date.fromisoformat(value)


def _rows(table, cik, decision_at, start_date, out):
    if (not isinstance(table, dict) or any(not isinstance(table.get(k), list) for k in _COLUMNS)
            or len({len(table[k]) for k in _COLUMNS}) != 1
            or len(table['form']) > 50_000):
        raise _SECError('SEC_SUBMISSIONS_STRUCTURE_INVALID')
    result = []
    for values in zip(*(table[k] for k in _COLUMNS)):
        item = dict(zip(_COLUMNS, values))
        form = item['form']
        if not isinstance(form, str):
            raise _SECError('SEC_FILING_METADATA_INVALID')
        base_form = form.removesuffix('/A')
        if base_form not in _PERIODIC | _EVENTS:
            continue
        try:
            accepted_raw = item['acceptanceDateTime']
            if not isinstance(accepted_raw, str):
                raise TypeError
            accepted = datetime.fromisoformat(accepted_raw.replace('Z', '+00:00'))
            if accepted.utcoffset() is None:
                raise ValueError
            filed = _date(item['filingDate'])
            if accepted > decision_at:
                out['excluded_future'] += 1
                continue
            if filed < start_date:
                continue
            report = _date(item['reportDate']) if item['reportDate'] else None
            accession, document = item['accessionNumber'], item['primaryDocument']
            if (not isinstance(accession, str) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession)
                    or not isinstance(document, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', document)
                    or '..' in document or (base_form in _PERIODIC and report is None)
                    or (report is not None and (report > filed
                        or base_form in _PERIODIC and report > accepted.astimezone(ZoneInfo('America/New_York')).date()))):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise _SECError('SEC_FILING_METADATA_INVALID') from None
        result.append({'accession': accession, 'form': form, 'is_amendment': form.endswith('/A'),
            'cik': cik, 'acceptance_at': accepted.astimezone(timezone.utc).isoformat(),
            'filing_date': filed.isoformat(), 'report_date': report.isoformat() if report else None,
            'period_start': None, 'scope': None,
            'source_url': f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/{document}',
            'content_verified': False})
    return result


def _select(rows, out):
    amendments = [r for r in rows if r['is_amendment']]
    out['amendments'] = amendments
    out['events'] = sorted((r for r in rows if r['form'].removesuffix('/A') in _EVENTS),
                           key=lambda r: (r['acceptance_at'], r['accession']), reverse=True)
    periodic = [r for r in rows if r['form'] in _PERIODIC]
    periodic_amendments = [r for r in amendments if r['form'].removesuffix('/A') in _PERIODIC]
    if periodic_amendments:
        out['gaps'].append('AMENDMENT_SCOPE_UNVERIFIED')
    unresolved = [r for r in periodic_amendments if not any(
        b['form'] == r['form'].removesuffix('/A') and b['report_date'] == r['report_date'] for b in periodic)]
    if unresolved:
        out['gaps'].append('AMENDMENT_BASE_UNRESOLVED')

    def choose(candidates):
        if not candidates:
            return None
        newest = max(r['report_date'] for r in candidates)
        chosen = [r for r in candidates if r['report_date'] == newest]
        if len(chosen) != 1:
            out['gaps'].append('PERIODIC_EDITION_AMBIGUOUS')
            out['selection']['blocked_by'].extend(r['accession'] for r in chosen)
            return None
        row = chosen[0]
        blockers = [r for r in periodic_amendments if (
            r['report_date'] == row['report_date'] and r['form'].removesuffix('/A') == row['form'])
            or (r in unresolved and r['report_date'] >= row['report_date'])]
        if blockers:
            out['selection']['blocked_by'].extend(r['accession'] for r in blockers)
            return None
        return row

    out['primary'] = choose(periodic)
    annual = choose([r for r in periodic if r['form'] in _ANNUAL])
    out['annual_supplement'] = annual if annual != out['primary'] else None
    if not out['primary']:
        out['gaps'].append('SEC_PRIMARY_UNAVAILABLE')
    out['selection'].update(primary_id=out['primary']['accession'] if out['primary'] else None,
        annual_supplement_id=out['annual_supplement']['accession'] if out['annual_supplement'] else None)
    out['selection']['blocked_by'] = sorted(set(out['selection']['blocked_by']))


async def collect_sec_public_filings(*, ticker, decision_at, start_date, user_agent,
                                    client_factory=None, max_calls=6,
                                    max_response_bytes=4 * 1024 * 1024,
                                    max_total_bytes=12 * 1024 * 1024,
                                    timeout_seconds=45, _metrics=None):
    """Discover exact official identity and select metadata within a finite window.

    User-Agent must identify the caller and a contact email. Caller owns its
    accuracy and cross-process rate coordination; no contact is invented here.
    Current ticker mapping is not a historical ticker-ownership certification.
    """
    metrics = _metrics if type(_metrics) is dict else {}
    metrics.update(calls=0, response_bytes=0)
    now = datetime.now(timezone.utc)
    out = {'status': 'FAILED', 'identity': {'ticker': ticker if isinstance(ticker, str) else None,
        'cik': None, 'verified': False}, 'primary': None, 'annual_supplement': None, 'events': [],
        'amendments': [], 'filings': [], 'gaps': [], 'metrics': metrics, 'provenance': [],
        'observed_at': now.isoformat(), 'excluded_future': 0,
        'selection': {'primary_id': None, 'annual_supplement_id': None, 'latest_confirmed': False, 'blocked_by': []},
        'coverage': {'complete_within_query': False, 'global_complete': False},
        'limitations': ['QUERY_WINDOW_ONLY', 'CURRENT_IDENTITY_NOT_HISTORICAL_CERTIFICATION',
                        'METADATA_NOT_CONTENT_VERIFICATION', 'PERIOD_START_AND_SCOPE_UNVERIFIED']}
    if (not isinstance(user_agent, str) or not 12 <= len(user_agent) <= 256
            or any(ord(c) < 32 or ord(c) > 126 for c in user_agent)
            or not re.search(r'\S.{2,}\s+[^\s@]+@[^\s@]+\.[A-Za-z]{2,}', user_agent)):
        out['gaps'].append('SEC_USER_AGENT_REQUIRED')
        return out
    try:
        valid = (isinstance(ticker, str) and bool(re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,14}', ticker))
            and isinstance(decision_at, datetime) and decision_at.utcoffset() is not None
            and decision_at <= now and type(start_date) is date
            and start_date <= decision_at.astimezone(ZoneInfo('America/New_York')).date()
            and (_metrics is None or type(_metrics) is dict)
            and type(max_calls) is int and 1 <= max_calls <= 12
            and type(max_response_bytes) is int and 1 <= max_response_bytes <= 4 * 1024 * 1024
            and type(max_total_bytes) is int and 1 <= max_total_bytes <= 12 * 1024 * 1024
            and type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 90)
    except (ValueError, TypeError, OverflowError):
        valid = False
    if not valid:
        out['gaps'].append('SEC_POLICY_INVALID')
        return out
    out['query'] = {'ticker': ticker, 'decision_at': decision_at.isoformat(), 'start_date': start_date.isoformat()}

    async def acquire():
        factory = client_factory or httpx.AsyncClient
        async with factory(timeout=15, follow_redirects=False, trust_env=False) as client:
            async def fetch(url):
                if not (url == _TICKERS or re.fullmatch(
                        r'https://data\.sec\.gov/submissions/CIK\d{10}(?:-submissions-\d{3})?\.json', url)):
                    raise _SECError('SEC_URL_INVALID')
                if metrics['calls'] >= max_calls:
                    raise _SECError('SEC_CALL_BUDGET_EXHAUSTED')
                if metrics['response_bytes'] >= max_total_bytes:
                    raise _SECError('SEC_RESPONSE_BYTES_EXCEEDED')
                await _rate_wait()
                client.cookies.clear()
                metrics['calls'] += 1
                async with client.stream('GET', url, headers={'User-Agent': user_agent, 'Accept-Encoding': 'identity'}) as response:
                    if response.status_code != 200:
                        raise _SECError(f'SEC_HTTP_{response.status_code}')
                    if str(response.url) != url or response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise _SECError('SEC_RESPONSE_ENCODING_OR_URL_INVALID')
                    body = bytearray()
                    chunks = response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
                    async for chunk in chunks:
                        metrics['response_bytes'] += len(chunk)
                        if len(body) + len(chunk) > max_response_bytes or metrics['response_bytes'] > max_total_bytes:
                            raise _SECError('SEC_RESPONSE_BYTES_EXCEEDED')
                        body.extend(chunk)
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError):
                        raise _SECError('SEC_JSON_INVALID') from None
                    out['provenance'].append({'url': url, 'sha256': hashlib.sha256(body).hexdigest(), 'response_bytes': len(body)})
                    return payload

            mapping = await fetch(_TICKERS)
            if not isinstance(mapping, dict) or len(mapping) > 50_000:
                raise _SECError('SEC_TICKER_MAPPING_INVALID')
            matches = [r for r in mapping.values() if isinstance(r, dict) and r.get('ticker') == ticker]
            if len(matches) != 1:
                raise _SECError('SEC_TICKER_AMBIGUOUS' if matches else 'SEC_TICKER_NOT_FOUND')
            number = matches[0].get('cik_str')
            if type(number) is not int or not 0 < number < 10**10:
                raise _SECError('SEC_TICKER_MAPPING_INVALID')
            cik = f'{number:010d}'
            submissions = await fetch(_SUBMISSIONS + 'CIK' + cik + '.json')
            returned_cik = submissions.get('cik') if isinstance(submissions, dict) else None
            cik_matches = (type(returned_cik) is int and returned_cik == number) or (
                isinstance(returned_cik, str) and bool(re.fullmatch(r'\d{1,10}', returned_cik))
                and int(returned_cik) == number)
            if (not isinstance(submissions, dict) or not cik_matches
                    or not isinstance(submissions.get('tickers'), list)
                    or ticker not in submissions['tickers']):
                raise _SECError('SEC_IDENTITY_MISMATCH')
            out['identity'].update(cik=cik, verified=True)
            filings = submissions.get('filings')
            if not isinstance(filings, dict) or not isinstance(filings.get('files'), list) or len(filings['files']) > 1000:
                raise _SECError('SEC_SUBMISSIONS_STRUCTURE_INVALID')
            rows = _rows(filings.get('recent'), cik, decision_at, start_date, out)
            names = set()
            for descriptor in filings['files']:
                try:
                    name = descriptor['name']
                    lower, upper = _date(descriptor['filingFrom']), _date(descriptor['filingTo'])
                    if (not isinstance(name, str) or not re.fullmatch(r'CIK' + cik + r'-submissions-\d{3}\.json', name)
                            or name in names or lower > upper):
                        raise ValueError
                    names.add(name)
                except (KeyError, TypeError, ValueError):
                    raise _SECError('SEC_HISTORY_INVALID') from None
                # Filing dates can be credited after acceptance; a descriptor's
                # lower filing date alone cannot prove future unavailability.
                if upper < start_date:
                    continue
                historical = await fetch(_SUBMISSIONS + name)
                count = descriptor.get('filingCount')
                if count is not None and (type(count) is not int or count < 0
                        or not isinstance(historical, dict)
                        or not isinstance(historical.get('filingDate'), list)
                        or len(historical['filingDate']) != count):
                    raise _SECError('SEC_HISTORY_COUNT_MISMATCH')
                rows.extend(_rows(historical, cik, decision_at, start_date, out))
            unique = {}
            for row in rows:
                if row['accession'] in unique and unique[row['accession']] != row:
                    raise _SECError('SEC_ACCESSION_CONFLICT')
                unique[row['accession']] = row
            out['filings'] = list(unique.values())
            out['coverage']['complete_within_query'] = True
            _select(out['filings'], out)
            out['status'] = 'PARTIAL' if out['gaps'] else 'COMPLETE_WITHIN_QUERY'

    try:
        await asyncio.wait_for(acquire(), timeout=timeout_seconds)
    except _SECError as exc:
        out['gaps'].append(str(exc))
    except asyncio.TimeoutError:
        out['gaps'].append('SEC_TOTAL_TIMEOUT')
    except httpx.HTTPError:
        out['gaps'].append('SEC_TRANSPORT_FAILURE')
    except Exception:  # noqa: BLE001 -- no provider exception text or payload leakage
        out['gaps'].append('SEC_ACQUISITION_FAILURE')
    out['gaps'] = sorted(set(out['gaps']))
    return out
