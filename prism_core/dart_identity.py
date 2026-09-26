"""Bounded official DART identity lookup; never infer a ticker from a name."""

import asyncio
import hashlib
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from lxml import html

_CATALOG_URL = 'https://dart.fss.or.kr/dsab001/searchCorp.ax'
_PROFILE_URL = 'https://dart.fss.or.kr/dsae001/selectPopup.ax'
_MAX_CALLS = 3
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 3 * _MAX_RESPONSE_BYTES
_TIMEOUT_SECONDS = 15


class _IdentityError(ValueError):
    """Static, safe failure codes only."""


def _codes_from(tree, exact_name=None):
    codes, names = set(), set()
    for node in tree.xpath('//tbody[@id="tbody"]//a[contains(@onclick,"openCorpInfoNew")]'):
        displayed = ' '.join(node.text_content().split())
        match = re.search(r'''openCorpInfoNew\(\s*['"]([0-9]{8})['"]''', node.get('onclick', ''))
        if match and (exact_name is None or displayed == ' '.join(exact_name.split())):
            codes.add(match[1])
            names.add(displayed)
    return codes, names


async def resolve_dart_identity(name, ticker, start_date, decision_at, *, client_factory=None, _metrics=None):
    """Resolve with a company-profile ticker check, or return sanitized uncertainty.

    Dates bound the official catalog search, not historical identity certification.
    Only fixed DART endpoints are requested; redirects and compressed responses are
    rejected. The wall timeout covers I/O, not preemption of synchronous parsing.
    """
    metrics = _metrics if type(_metrics) is dict else {}
    metrics.update(calls=0, response_bytes=0)
    result = {'status': 'IDENTITY_UNRESOLVED', 'corp_code': None, 'lookups': [],
              'ticker_verified_from_company_profile': False,
              'metrics': metrics,
              'observed_at': datetime.now(timezone.utc).isoformat()}
    try:
        valid = ((_metrics is None or type(_metrics) is dict)
                 and type(name) is str and 0 < len(name.strip()) <= 80
                 and type(ticker) is str and re.fullmatch(r'[0-9]{6}', ticker)
                 and type(start_date) is date and isinstance(decision_at, datetime)
                 and decision_at.utcoffset() is not None)
        cutoff = decision_at.astimezone(ZoneInfo('Asia/Seoul')) if valid else None
        valid = valid and start_date <= cutoff.date()
    except (OverflowError, ValueError, TypeError):
        valid = False
    if not valid:
        result['reason'] = 'IDENTITY_INPUT_INVALID'
        return result
    data = {'currentPage': '1', 'maxResults': '100', 'maxLinks': '10', 'sort': 'date',
            'series': 'desc', 'textCrpNm': name, 'textCrpCik': '', 'autoSearchCorp': 'Y',
            'pageGubun': 'corp', 'startDate': start_date.strftime('%Y%m%d'),
            'endDate': cutoff.strftime('%Y%m%d'),
            'publicType': ['A001', 'A002', 'A003']}

    async def resolve():
        factory = client_factory or httpx.AsyncClient
        async with factory(timeout=_TIMEOUT_SECONDS, trust_env=False, follow_redirects=False) as client:
            async def fetch(url, fields, lookup):
                if url not in {_CATALOG_URL, _PROFILE_URL}:
                    raise _IdentityError('IDENTITY_URL_INVALID')
                if result['metrics']['calls'] >= _MAX_CALLS:
                    raise _IdentityError('IDENTITY_CALL_LIMIT')
                result['metrics']['calls'] += 1
                client.cookies.clear()
                async with client.stream('POST', url, data=fields,
                                         headers={'Accept-Encoding': 'identity'}) as response:
                    if (response.status_code != 200 or str(response.url) != url
                            or response.headers.get('content-encoding', 'identity').strip().lower() != 'identity'):
                        raise _IdentityError('IDENTITY_RESPONSE_FAILURE')
                    raw = bytearray()
                    chunks = response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
                    async for chunk in chunks:
                        result['metrics']['response_bytes'] += len(chunk)
                        if (len(raw) + len(chunk) > _MAX_RESPONSE_BYTES
                                or result['metrics']['response_bytes'] > _MAX_TOTAL_BYTES):
                            raise _IdentityError('IDENTITY_RESPONSE_LIMIT')
                        raw.extend(chunk)
                result['lookups'].append({'kind': lookup, 'url': url,
                                         'response_sha256': hashlib.sha256(raw).hexdigest(),
                                         'response_bytes': len(raw)})
                return html.fromstring(bytes(raw).decode('utf-8'))

            codes, names = _codes_from(await fetch(_CATALOG_URL, data, 'name'), name)
            resolution = 'OFFICIAL_NAME_QUERY'
            if len(codes) != 1:
                name_codes = codes
                codes, names = _codes_from(await fetch(_CATALOG_URL, {**data, 'textCrpNm': ticker}, 'ticker'))
                resolution = 'OFFICIAL_TICKER_QUERY'
                if name_codes and not codes <= name_codes:
                    raise _IdentityError('NAME_TICKER_IDENTITY_CONFLICT')
            if len(codes) != 1:
                raise _IdentityError('IDENTITY_AMBIGUOUS_OR_MISSING')
            code = next(iter(codes))
            profile = await fetch(_PROFILE_URL, {'selectKey': code}, 'profile')
            profile_tickers, industries, legal_names = [], [], []
            for row in profile.xpath('//tr'):
                nodes = row.xpath('./th|./td')
                cells = [' '.join(node.text_content().split()) for node in nodes]
                label = re.sub(r'\s+', '', cells[0]).rstrip(':') if cells else ''
                if len(cells) >= 2 and label == '종목코드':
                    profile_tickers.extend(re.findall(r'(?<![0-9])[0-9]{6}(?![0-9])', ' '.join(cells[1:])))
                elif len(cells) == 2 and label == '업종명' and cells[1]:
                    industries.append(cells[1])
                elif len(cells) == 2 and label == '회사이름':
                    # The cell's own text; its 정보 더보기/rss buttons are separate elements.
                    legal = ' '.join(''.join(nodes[1].xpath('./text()')).split())
                    if legal:
                        legal_names.append(legal)
            if profile_tickers != [ticker]:
                raise _IdentityError('PROFILE_TICKER_MISMATCH')
            result.update(status='RESOLVED_WITH_OFFICIAL_PROFILE', corp_code=code, resolution=resolution,
                          official_display_names=sorted(names), ticker_verified_from_company_profile=True)
            # Official KSIC industry name from the same profile page; absent or repeated stays unknown.
            if len(industries) == 1:
                result['industry_name'] = industries[0]
            # Official legal name from the same page (e.g. …부동산투자회사 주식회사).
            if len(legal_names) == 1:
                result['legal_name'] = legal_names[0]

    try:
        await asyncio.wait_for(resolve(), timeout=_TIMEOUT_SECONDS)
    except _IdentityError as exc:
        result['reason'] = str(exc)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        result['reason'] = 'IDENTITY_TIMEOUT'
    except httpx.HTTPError:
        result['reason'] = 'IDENTITY_TRANSPORT_FAILURE'
    except Exception:  # noqa: BLE001 - provider/parser messages must not escape
        result['reason'] = 'IDENTITY_LOOKUP_FAILED'
    if 'reason' in result:
        result.update(status='IDENTITY_UNRESOLVED', corp_code=None,
                      ticker_verified_from_company_profile=False)
        result.pop('resolution', None)
        result.pop('official_display_names', None)
    return result
