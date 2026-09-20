"""Opt-in SEC catalog -> exact primary document -> contextual revenue facts.

This supplements, not replaces, narrative filings/news. No trading signals,
consolidation inference or company-specific rules are introduced.
"""
import asyncio
import hashlib
import json
import re
from datetime import date, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from prism_core.sec_inline_evidence import parse_inline_revenue

VERSION = 'sec-selected-inline-v2'
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


def _document_url(row, cik):
    try:
        url = row['source_url']
        parts = urlsplit(url)
        accession = row['accession']
        if (parts.scheme != 'https' or parts.netloc != 'www.sec.gov' or parts.query or parts.fragment
                or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession)
                or not re.fullmatch(r'\d{1,10}', str(cik))):
            return None
        prefix = f'/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/'
        if not parts.path.startswith(prefix) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.html?', parts.path[len(prefix):]):
            return None
        return url
    except (KeyError, TypeError, ValueError):
        return None


def _blocks(parsed, row):
    blocks, gaps = [], []
    if parsed.get('status') not in {'ok', 'partial'}:
        return [], ['SEC_INLINE_UNSUPPORTED']
    report_date = date.fromisoformat(row['report_date'])
    for fact in parsed['facts']:
        # Comparative periods remain identified, but no later context is allowed
        # to masquerade as a fact known for the selected reporting period.
        try:
            if date.fromisoformat(fact['period']['end']) > report_date:
                gaps.append('SEC_FACT_AFTER_REPORT_PERIOD')
                continue
        except (KeyError, TypeError, ValueError):
            gaps.append('SEC_FACT_PERIOD_UNRESOLVED')
            continue
        excerpt = json.dumps({k: fact[k] for k in ('concept', 'entity', 'period', 'dimensions', 'unit', 'value')},
                             ensure_ascii=False, separators=(',', ':'))
        provenance = {k: fact[k] for k in ('context_ref', 'raw_value', 'scale', 'sign', 'format', 'format_qname', 'decimals',
                                           'precision', 'source_path', 'source_xpath', 'source_line',
                                           'duplicate_sources') if k in fact}
        provenance.update(parser_version='sec_inline_v1', representation='SEC_INLINE_XBRL',
                          representation_sha256=parsed['source_sha256'])
        blocks.append({'topic': 'business_segments' if fact['dimensions'] else 'financial_quality_valuation',
                       'excerpt': excerpt, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED', 'provenance': provenance})
    gaps.extend('SEC_INLINE_' + g['reason'] for g in parsed.get('gaps', []) if isinstance(g, dict) and 'reason' in g)
    return blocks, list(dict.fromkeys(gaps))


async def collect_latest(symbol, decision_at, user_agent, progress, *, client_factory=None):
    from prism_core.sec_public_filings import _rate_wait, collect_sec_public_filings

    metrics = {'catalog': {}, 'documents': {'calls': 0, 'response_bytes': 0}}
    progress['sec_metrics'] = metrics
    progress['filing_selection'] = {'version': VERSION, 'decision_at': decision_at.isoformat(),
                                  'source_family': 'SEC', 'scope': 'reported_context'}
    start = decision_at.astimezone(ZoneInfo('America/New_York')).date() - timedelta(days=550)
    result = await collect_sec_public_filings(ticker=symbol, decision_at=decision_at, start_date=start,
        user_agent=user_agent, client_factory=client_factory, _metrics=metrics['catalog'])
    progress['filing_selection'].update({k: result[k] for k in (
        'identity', 'selection', 'coverage', 'events', 'amendments', 'gaps') if k in result})
    progress['gaps'].extend(result.get('gaps', []))
    if result.get('events'):
        progress['gaps'].append('SEC_EVENT_BODIES_NOT_ACQUIRED')
    if not result.get('identity', {}).get('verified') or not result.get('primary'):
        progress['gaps'].append('SEC_PRIMARY_UNAVAILABLE')
        return
    cik = result['identity']['cik']
    progress['filing_selection']['document_delivery'] = []

    async def acquire():
        factory = client_factory or httpx.AsyncClient
        async with factory(timeout=15, follow_redirects=False, trust_env=False) as client:
            for role, row in (('primary', result['primary']), ('annual_supplement', result.get('annual_supplement'))):
                if not row or role == 'annual_supplement' and row['accession'] == result['primary']['accession']:
                    continue
                url = _document_url(row, cik)
                delivery = {'role': role, 'accession': row['accession'], 'status': 'UNAVAILABLE'}
                progress['filing_selection']['document_delivery'].append(delivery)
                if not url:
                    progress['gaps'].append('SEC_DOCUMENT_LOCATOR_INVALID')
                    if role == 'primary':
                        break
                    continue
                await _rate_wait()
                metrics['documents']['calls'] += 1
                client.cookies.clear()
                async with client.stream('GET', url, headers={'User-Agent': user_agent, 'Accept-Encoding': 'identity'}) as response:
                    if response.status_code != 200:
                        progress['gaps'].append('SEC_DOCUMENT_HTTP_' + str(response.status_code))
                        break  # No alternate host/proxy or older-document substitution.
                    if (str(response.url) != url or response.headers.get('content-encoding', 'identity').lower() != 'identity'):
                        progress['gaps'].append('SEC_DOCUMENT_RESPONSE_INVALID')
                        break
                    raw = bytearray()
                    chunks = response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
                    async for chunk in chunks:
                        metrics['documents']['response_bytes'] += len(chunk)
                        if len(raw) + len(chunk) > MAX_DOCUMENT_BYTES:
                            progress['gaps'].append('SEC_DOCUMENT_BYTE_LIMIT')
                            return
                        raw.extend(chunk)
                try:
                    html = raw.decode('utf-8')
                except UnicodeDecodeError:
                    progress['gaps'].append('SEC_DOCUMENT_ENCODING_UNSUPPORTED')
                    return
                parsed = parse_inline_revenue(html, expected_cik=cik)
                digest = hashlib.sha256(raw).hexdigest()
                if parsed.get('source_sha256') != digest:
                    progress['gaps'].append('SEC_DOCUMENT_HASH_MISMATCH')
                    return
                blocks, gaps = _blocks(parsed, row)
                progress['gaps'].extend(gaps)
                delivery.update(status='PARTIAL' if blocks else 'NO_SUPPORTED_FACTS', sha256=digest,
                                utf8_bytes=len(raw), candidate_facts=len(blocks), gaps=gaps)
                if not blocks:
                    if role == 'primary':
                        return
                    continue
                filing = {'receipt_id': row['accession'], 'kind': row['form'], 'role': role,
                          'entity_id': 'SEC:' + str(cik), 'period_end': row['report_date'],
                          'period_start': None, 'scope': 'UNKNOWN', 'event_date': 'UNKNOWN',
                          'decision_at': decision_at.isoformat(), 'latest_confirmed': False}
                progress['sources'].append({'source_id': 'S-' + row['accession'], 'url': url,
                    'published': row['acceptance_at'], 'publication_basis': 'SEC_ACCEPTANCE_TIMESTAMP',
                    'filing': filing, 'blocks': blocks, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'})

    try:
        await asyncio.wait_for(acquire(), timeout=35)
    except asyncio.TimeoutError:
        progress['gaps'].append('SEC_DOCUMENT_TIMEOUT')
    except httpx.HTTPError:
        progress['gaps'].append('SEC_DOCUMENT_TRANSPORT_FAILURE')
    progress['gaps'].append('SEC_FACTS_ONLY_NOT_FULL_FILING')
