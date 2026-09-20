"""Opt-in, model-free multi-topic retrieval using the existing pinned MCP tools.

Large provider bodies are processed only here; models receive bounded complete
paragraphs/tables with source IDs, not raw documents or provider HTML.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from prism_core.report_source_budget import SOURCE_NOTE_BYTES, validate_source_budget

PROFILE = 'insight_prefetch_v5'
FILING_PARSER_REVISION = 'bounded-html-v12-statement-routing'
MAX_CALLS = 8
SECTION_BYTES = SOURCE_NOTE_BYTES
TOPICS = {
    'direct_peers_competitive_position': ('competitor', 'competes with', 'compete with', 'competition', '경쟁업체', '경쟁사', '경쟁현황', '시장점유율'),
    'business_segments': ('business segment', 'principal products', '주요 제품', '주요제품', '사업부문', '사업의 개요'),
    'earnings_estimates_guidance': ('guidance', 'outlook', 'backlog', '수주', '가이던스', '매출 전망'),
    'catalysts_risks_counterevidence': ('risk factor', 'regulatory', 'customer concentration', '위험', '리스크', '규제', '고객 집중'),
    'financial_quality_valuation': ('operating margin', 'cash flow', '영업이익률', '현금흐름', '매출액'),
    'ownership_governance': ('major shareholders', '주요 주주', '최대주주', '지배구조'),
}
OWNERS = {
    'news_analysis': ('direct_peers_competitive_position', 'earnings_estimates_guidance', 'catalysts_risks_counterevidence'),
    'company_overview': ('business_segments', 'ownership_governance'),
    'company_status': ('financial_quality_valuation',),
}


def enabled(config=None):
    override = os.getenv('PRISM_REPORT_INSIGHT_PREFETCH', '').lower()
    if override in {'0', 'false', 'off'}:
        return False
    if override in {'1', 'true'}:
        return True
    if config is None:
        from prism_core.report_research_prefetch import load_config

        config = load_config()
    return isinstance(config, dict) and config.get('research_profile') == PROFILE


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _size(value):
    return len(_dump(value).encode('utf-8'))


def _source_identity(value):
    """Canonical metadata identity; bool/int and missing/null remain distinct."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def expand_filing_record(record, payload):
    """Losslessly resolve source-local filing metadata for audit/consumers."""
    if 'filing_ref' not in record:
        return dict(record)
    key = record['filing_ref']
    filing = payload.get('source_filings', {}).get(key)
    if key != record.get('source_id') or not isinstance(filing, dict) or 'filing' in record:
        raise ValueError('INVALID_FILING_REFERENCE')
    return {k: v for k, v in {**record, 'filing': filing}.items() if k != 'filing_ref'}


def _mentions(text, word):
    if word == '수주':
        return bool(re.search(r'(?<![가-힣])(?:(?:신규|누적|총|주요)\s*)?수주', text))
    return word in text


def normalized_issuer(value):
    """Remove legal-form typography only; never erase Holdings/subsidiary words."""
    value = re.sub(r'주식회사|\(주\)|㈜', '', str(value)).casefold()
    return re.sub(r'[\s.,*]+', '', value)


def cover_issuer(text):
    if not isinstance(text, str):
        return None
    match = re.search(r'(?m)^\|\s*회\s*사\s*명\s*[:：]?\s*\|\s*([^|\n]+)\|', text[:20000])
    return match[1].strip() if match else None


def viewer_identity(text, symbol):
    """A KRX viewer's top-level stock code, not subsidiary mentions in a body."""
    match = re.search(r'(?m)^#\s+[^\n]+?\((\d{6})\)\s*$', text[:2000])
    return match[1] == symbol if match else None


def kind_viewer_candidate(url):
    """Navigation candidate only; identity requires returned ticker AND back-link.

    The static KRX directory contains receipt date/sequence. No facts or issuer
    match are inferred from this locator; an unsuccessful verification is UNKNOWN.
    """
    parts = urlsplit(url)
    match = re.fullmatch(r'/external/(\d{4})/(\d{2})/(\d{2})/(\d{6})/\d+/[\w-]+\.html?', parts.path)
    if parts.hostname != 'kind.krx.co.kr' or not match or parts.query:
        return None
    accession = ''.join(match.groups())
    return 'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=' + accession + '&docno=&viewerhost='


def document_links(data, parent):
    from prism_core.report_research_prefetch import _document_links, public_url

    text = data.get('markdown', '')
    links = _document_links(text, parent) if isinstance(text, str) else []
    # Fragments are client-side chapter anchors, not query/document IDs. Only
    # recognize the actual KRX static document shape; do not normalize any query.
    raw_links = data.get('links', [])
    for raw in raw_links[:200] if isinstance(raw_links, list) else []:
        if not isinstance(raw, str):
            continue
        try:
            parts = urlsplit(raw)
        except ValueError:
            continue
        if (parts.hostname == 'kind.krx.co.kr' and not parts.query
                and re.fullmatch(r'/external/\d{4}/\d{2}/\d{2}/\d+/\d+/[\w-]+\.html?', parts.path)
                and (not parts.fragment or re.fullmatch(r'toc_\d+', parts.fragment))):
            url = public_url(urlunsplit((parts.scheme, parts.netloc, parts.path, '', '')))
            if url and url not in links:
                links.append(url)
        elif parts.path.lower().endswith('.pdf'):
            url = public_url(raw)
            if url and url not in links:
                links.append(url)
    return links[:3]


def topic_blocks(text, max_block_bytes=2400):
    """Select full topic units with nearby headings; no claims or ranks inferred."""
    from prism_core.report_research_prefetch import _body_text

    if not isinstance(text, str):
        return []
    pieces = re.split(r'\n\s*\n', _body_text(text))
    units, heading = [], ''
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if re.fullmatch(r'#{1,6}\s+[^\n]+', piece) or (
                len(piece) < 90 and re.match(r'^(?:\([가-힣\d]+\)|[가-힣\d]+[.)]|[IVX]+[.])\s', piece)):
            if re.search(r'[A-Za-z가-힣]{2,}', piece):
                heading = piece
            continue
        # Treat table notes and related lists atomically with the prior unit.
        note = piece.lstrip(' *_\\').lower().startswith(('note', 'due to rounding', '주:', '주)', '참고', '반올림'))
        caption = units[-1].split('\n', 1)[-1] if units else ''
        unit_caption = units and len(caption) < 180 and re.search(r'(?i)(?:단위\s*[:：]|units?\s*[:：]|in (?:millions|billions))', caption)
        if units and (note and '|' in units[-1] or
                      (piece.startswith('|') and unit_caption) or
                      (re.match(r'^(?:[-*+]\s|•)', piece) and units[-1].splitlines()[0].endswith(':'))):
            units[-1] += '\n\n' + piece
        else:
            units.append((heading + '\n' if heading else '') + piece)
    output = []
    for topic, words in TOPICS.items():
        def body(unit):
            first, separator, rest = unit.partition('\n')
            heading_line = first.startswith('#') or re.match(r'^(?:\([가-힣\d]+\)|[가-힣\d]+[.)]|[IVX]+[.])\s', first)
            return rest if separator and heading_line else unit
        candidates = [unit for unit in units if any(_mentions(re.sub(r'https?://[^\s)]+', '',
                        unit if '|' in unit else body(unit)).lower(), word) for word in words)]
        # Explicit named relationship prose outranks general demand/risk mentions.
        candidates.sort(key=lambda unit: not any(word in unit.lower() for word in (
            '경쟁업체', '대표적인 경쟁', 'competes with', 'competitors include', '주요 고객')))
        seen = set()
        for unit in candidates:
            if unit in seen or len(unit.encode('utf-8')) > max_block_bytes:
                continue
            if unit.count('](') > 3 or ('|' not in unit and len(unit) < 25):
                continue
            prose = body(unit).strip()
            if re.search(r'!\[|(?i:자동.{0,8}요약|자동.{0,8}생성)', prose):
                continue
            if re.search(r'(?i)(신용등급.{0,8}정의|등급기호|rating definitions|definition of ratings)', prose):
                continue
            if re.match(r'(?i)^(?:for (?:more|additional) information|see (?:item|note)|refer to|Act,|driving\b|which\b)', prose):
                continue
            if topic == 'earnings_estimates_guidance' and re.search(r'(?i)(\b(?:accounting guidance|lease accounting|FASB|credit ratings?|ratings? (?:outlook|definitions?)|Fitch|Moody\S*)\b|회계기준|신용등급)', prose):
                continue
            if '|' not in unit:
                # PDF line/page fragments and IR titles are not standalone facts.
                if not prose.endswith(('.', '。', '!', '?', ')')):
                    continue
                if re.search(r'(?i)(forward-looking statements|actual results may differ|미래예측진술)', prose):
                    continue
            elif not re.search(r'\|\s*:?-{3,}', unit):
                continue  # never ship numeric rows without their table header
            seen.add(unit)
            output.append({'topic': topic, 'excerpt': unit, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'})
            if len(seen) >= 3:
                break
    return output


async def collect(market, symbol, day, company, transport, *, context, progress, filing_parser=False,
                  latest_filings=None):
    from prism_core.report_research_prefetch import (
        MEMORY_SOURCE,
        MEMORY_SUBJECTS,
        _candidates,
        _decode,
        _discovery_only,
        _source,
        public_url,
    )

    sources, gaps = progress['sources'], progress['gaps']
    filing_parser = ('structured_v1' if filing_parser is True else filing_parser)
    filing_parser = filing_parser if ((market == 'KR' and filing_parser in ('structured_v1', 'material_v2'))
                                     or (market == 'US' and filing_parser == 'sec_inline_v1')) else None
    if filing_parser:
        progress['filing_parser'] = filing_parser
    progress.setdefault('raw_response_utf8_bytes', 0)
    progress.setdefault('events', [])
    if latest_filings and market == 'KR' and filing_parser == 'material_v2':
        from prism_core.dart_report_evidence import collect_latest

        try:
            await collect_latest(symbol, company, latest_filings['decision_at'], latest_filings['scope'], progress)
        except Exception:  # noqa: BLE001 - optional provider failure cannot stop reports
            gaps.append('DART_COLLECTION_UNAVAILABLE')
    if latest_filings and market == 'US' and filing_parser == 'sec_inline_v1':
        from prism_core.sec_report_evidence import collect_latest

        try:
            await collect_latest(symbol, latest_filings['decision_at'], latest_filings.get('user_agent'), progress)
        except Exception:  # noqa: BLE001 - never expose provider/auth messages
            gaps.append('SEC_COLLECTION_UNAVAILABLE')
    alias = MEMORY_SUBJECTS.get((market, symbol))
    name = context.get('name') or company
    queries = [f'"{name}" {symbol} 사업보고서 주요제품 경쟁업체' if market == 'KR' else
               f'"{name}" {symbol} annual report competition business segments',
               f'"{name}" {symbol} 실적 전망 수주 위험 공시 {day[:4]}' if market == 'KR' else
               f'"{name}" {symbol} earnings guidance risks backlog {day[:4]}']
    queue, seen = ([(MEMORY_SOURCE, None)] if alias else []), set()
    body_links = set()
    linked_identity = {}
    pending = {}
    query_index = 0
    scrapes = 0
    maps = 0
    while progress['calls'] < MAX_CALLS or (queue and queue[0][0] in pending and queue[0][0] in linked_identity):
        # Reserve acquisition capacity for catalyst/risk discovery. Follow an
        # already located body before switching, rather than stranding a viewer.
        if query_index == 1 and scrapes >= 3 and (not queue or queue[0][0] not in body_links):
            queue.clear()
        if not queue:
            if query_index >= len(queries):
                break
            args = {'query': queries[query_index], 'max_results': 5, 'max_tokens_per_page': 256,
                    'country': 'KR' if market == 'KR' else 'US'}
            args['query'] += ' ' + day[:4]
            args['search_recency_filter'] = 'year'
            if query_index == 0:
                domains = ['kind.krx.co.kr', 'dart.fss.or.kr'] if market == 'KR' else ['sec.gov']
                host = urlsplit(context.get('website', '')).hostname
                if host:
                    domains.append(host)
                args['search_domain_filter'] = domains
            query_index += 1
            progress['calls'] += 1
            try:
                response = _decode(await transport('perplexity', 'perplexity_search', args))
                progress['raw_response_utf8_bytes'] += _size(response)
                queue.extend(_candidates(response)[:5])
            except Exception:  # noqa: BLE001 - optional discovery must fail open
                gaps.append('SEARCH_UNAVAILABLE')
            continue
        raw_url, published = queue.pop(0)
        url = public_url(raw_url)
        reuse = url in pending and url in linked_identity
        if not url or (url in seen and not reuse):
            continue
        if latest_filings and market == 'US' and urlsplit(url).hostname in {'sec.gov', 'www.sec.gov', 'data.sec.gov'}:
            # The dedicated official collector owns these documents, including
            # access errors. Never turn a 403 into a different-host scrape retry.
            gaps.append('SEC_DOCUMENT_REQUIRES_SELECTED_OFFICIAL_PATH')
            continue
        seen.add(url)
        if _discovery_only(url):
            gaps.append('AGGREGATED_SUMMARY_NOT_ORIGINAL')
            continue
        if scrapes >= 5 and not reuse:
            gaps.append('SCRAPE_BUDGET_EXHAUSTED')
            break
        viewer = urlsplit(url).hostname == 'kind.krx.co.kr' and '/common/disclsviewer.do' in url
        args = {'url': url, 'formats': ['markdown', 'links'], 'onlyMainContent': not viewer}
        structured_body = filing_parser and kind_viewer_candidate(url) is not None
        if structured_body:
            args['formats'].append('html')
        if viewer:
            args.update(waitFor=3000, maxAge=0)
        if not reuse:
            scrapes += 1
            progress['calls'] += 1
        try:
            if reuse:
                response = pending.pop(url)
            else:
                response = _decode(await transport('firecrawl', 'firecrawl_scrape', args))
                progress['raw_response_utf8_bytes'] += _size(response)
            data = response.get('data', response)
            if not isinstance(data, dict):
                gaps.append('INVALID_SOURCE_SHAPE')
                continue
            text = data.get('markdown', '')
            if not isinstance(text, str):
                gaps.append('INVALID_SOURCE_SHAPE')
                continue
            metadata = data.get('metadata') or {}
            if not isinstance(metadata, dict):
                gaps.append('INVALID_SOURCE_SHAPE')
                continue
            if viewer and metadata.get('statusCode') not in (None, 200):
                gaps.append('VIEWER_HTTP_FAILURE')
                continue
            identity = viewer_identity(text, symbol) if viewer else None
            if identity is False:
                gaps.append('ISSUER_STOCK_CODE_MISMATCH')
                continue
            links = document_links(data, url)
            body_links.update(links)
            if identity is True and metadata.get('statusCode') == 200:
                for link in links:
                    linked_identity[link] = {'parent_url': url, 'stock_code': symbol,
                                             'parent_sha256': hashlib.sha256(text.encode()).hexdigest(),
                                             'issuer': cover_issuer(text)}
            elif identity is True:
                gaps.append('VIEWER_HTTP_STATUS_UNKNOWN')
            queue[:0] = [(link, None) for link in links if link not in seen or (link in pending and link in linked_identity)]
            if viewer and links:
                progress['events'].append({'source': url, 'stage': 'VIEWER_RESOLVED_TO_BODY'})
                continue
            issuer = cover_issuer(text)
            if latest_filings and (issuer or urlsplit(url).hostname in {'dart.fss.or.kr', 'kind.krx.co.kr'}):
                # Keep positively identified current-event disclosures, not an
                # indiscriminate host ban that also removes contracts/financing.
                title = re.sub(r'\s+', '', str(metadata.get('title', '')) + text[:500])
                periodic = re.search(r'(사업|반기|분기)보고서|재무제표', title)
                event = re.search(r'단일판매|공급계약|유상증자|소송|주요사항보고서|자기주식|전환사채|신주인수권', title)
                if periodic or not event:
                    gaps.append('UNSELECTED_OR_UNCLASSIFIED_DISCLOSURE_NOT_USED')
                    continue
            parent = linked_identity.get(url)
            if issuer and parent and parent['issuer'] and normalized_issuer(issuer) != normalized_issuer(parent['issuer']):
                gaps.append('LINKED_ISSUER_MISMATCH')
                continue
            if issuer and (not parent or not parent.get('issuer')) and normalized_issuer(issuer) not in {
                    normalized_issuer(company), normalized_issuer(context.get('name', ''))}:
                gaps.append('ISSUER_NAME_UNRESOLVED_OR_MISMATCH')
                candidate = kind_viewer_candidate(url)
                if candidate and candidate not in seen:
                    pending[url] = response
                    queue.insert(0, (candidate, None))
                continue
            # A company IR listing is a locator, even when its headlines contain
            # financial/risk vocabulary. Follow document links, never quote it as a filing.
            listing = re.search(r'(?i)/ir-comp/', urlsplit(url).path) is not None
            source, gap = _source(response, url, published, day, company, symbol,
                                  aliases=tuple(v for v in (alias, context.get('name'), issuer if parent else None) if v))
            if listing:
                source, gap = None, 'DOCUMENT_INDEX_NOT_BODY'
            blocks = topic_blocks(data.get('markdown', '')) if source else []
            if source and structured_body:
                from prism_core.filing_report_evidence import filing_blocks

                blocks, parser_gaps = filing_blocks(data, url, material_notes=filing_parser == 'material_v2')
                gaps.extend(parser_gaps)
            if (not blocks and not links and maps < 1 and progress['calls'] < MAX_CALLS
                    and re.search(r'(?i)/(?:ir|ir-comp|investors?|annual-reports?)(?:/|$)', urlsplit(url).path)):
                maps += 1
                progress['calls'] += 1
                try:
                    mapped = _decode(await transport('firecrawl', 'firecrawl_map', {
                        'url': url, 'search': 'annual report investor presentation 사업보고서 IR', 'limit': 5}))
                    progress['raw_response_utf8_bytes'] += _size(mapped)
                    map_data = mapped.get('data', mapped)
                    found = map_data.get('links', []) if isinstance(map_data, dict) else []
                    queue[:0] = [(entry.get('url') if isinstance(entry, dict) else entry, None) for entry in found[:5]]
                except Exception:  # noqa: BLE001 - optional map failure leaves other paths intact
                    gaps.append('DOCUMENT_MAP_UNAVAILABLE')
            if not source:
                gaps.append(gap or 'NO_USABLE_SOURCE')
                continue
            # Preserve the already-reviewed complete comparison for positives.
            if url == MEMORY_SOURCE:
                blocks.insert(0, {'topic': 'direct_peers_competitive_position',
                                  'excerpt': source['excerpt'], 'status': source['status']})
            source['blocks'] = blocks
            source['issuer_link'] = parent or {'status': 'UNVERIFIED'}
            if not blocks:
                gaps.append('NO_COMPLETE_TOPIC_BLOCK')
            sources.append(source)
            progress['events'].append({'source': url, 'stage': 'BODY_ACQUIRED', 'topic_blocks': len(blocks)})
        except Exception:  # noqa: BLE001 - retrieval errors are categories, never raw exceptions
            gaps.append('SCRAPE_UNAVAILABLE')
    if not sources:
        gaps.append('NO_USABLE_SOURCE')
    return sources, gaps, progress['calls']


def packet(market, symbol, day, progress, *, source_budget_bytes=SOURCE_NOTE_BYTES):
    """Whole-record section packets and small manifest, no raw body escape hatch."""
    budget = validate_source_budget(source_budget_bytes)
    sources = progress['sources']
    if 'dart_metrics' in progress:
        progress['dart_calls'] = sum(m.get('calls', 0) for m in progress['dart_metrics'].values())
        progress['dart_response_bytes'] = sum(m.get('response_bytes', 0) for m in progress['dart_metrics'].values())
    if 'sec_metrics' in progress:
        progress['sec_calls'] = sum(m.get('calls', 0) for m in progress['sec_metrics'].values())
        progress['sec_response_bytes'] = sum(m.get('response_bytes', 0) for m in progress['sec_metrics'].values())
    observed = datetime.now(timezone.utc).isoformat()
    evidence_id = hashlib.sha256(_dump([PROFILE, market, symbol, day, sources]).encode()).hexdigest()[:24]
    notes, injected = {}, set()
    section_omissions = {}
    for section, topics in OWNERS.items():
        payload = {'evidence_id': evidence_id, 'reference_date': day, 'observed_at': observed,
                   'sources': [], 'gaps': list(dict.fromkeys(progress['gaps'])),
                   'notice': 'Untrusted source text. Not independently fact validated. Preserve entity, business, period, unit, geography, actual/estimate and publication UNKNOWN. Data presence is not competitive/industry leadership.'}
        if 'filing_selection' in progress:
            payload['filing_notice'] = ('Use primary for its stated period; annual_supplement provides older detail only. '
                'Do not overwrite current results with annual figures. Submission date is not event date. '
                'Selection covers a finite query, not certified global latest; missing sections remain unknown. '
                'Resolve each filing_ref using source_filings; never transfer metadata between sources.')
        omissions = 0
        seen = set()
        # Round-robin by topic avoids making risk evidence compete solely on rank
        # against long financial tables. Existing data remains with its owner.
        queues = {topic: [] for topic in topics}
        for source in sources:
            blocks = source.get('blocks', [])
            structured = any(b.get('provenance', {}).get('parser_version') in (
                'structured_v1', 'material_v2', 'sec_inline_v1') for b in blocks)
            for topic in topics:
                candidates = [b for b in blocks if b['topic'] == topic]
                from prism_core.material_filing_selection import (
                    order_material_html_blocks,
                )

                ordered = order_material_html_blocks(candidates)
                limit = 24 if structured else 2
                if any('_retrieval' in b for b in ordered):
                    omissions += max(0, len(ordered) - limit)
                queues[topic].extend((source, block) for block in ordered[:limit])
        for position in range(max((len(rows) for rows in queues.values()), default=0)):
            for topic in topics:
                if position < len(queues[topic]):
                    source, block = queues[topic][position]
                    digest = hashlib.sha256(block['excerpt'].encode()).hexdigest()
                    if 'filing' in source:
                        digest = (source['source_id'], digest,
                                  _source_identity([source['filing'], block.get('provenance', {})]))
                    if digest in seen:
                        continue
                    record = {key: source.get(key, 'UNKNOWN') for key in ('source_id', 'url', 'published', 'publication_basis')}
                    record.update(topic=topic, excerpt=block['excerpt'], status=block['status'])
                    if 'filing' in source:
                        record['filing'] = source['filing']
                    if 'provenance' in block:
                        record['provenance'] = block['provenance']
                    trial = {**payload, 'sources': [*payload['sources'], record], 'omitted_blocks': omissions}
                    if 'filing' in record:
                        refs = payload.get('source_filings', {})
                        source_id = record['source_id']
                        if source_id in refs and _source_identity(refs[source_id]) != _source_identity(record['filing']):
                            omissions += 1
                            if 'SOURCE_FILING_CONFLICT' not in payload['gaps']:
                                payload['gaps'].append('SOURCE_FILING_CONFLICT')
                            continue
                        trial['source_filings'] = {**refs, source_id: record.pop('filing')}
                        record['filing_ref'] = source_id
                    provenance = record.get('provenance', {})
                    shared_keys = ('parser_version', 'representation', 'representation_sha256')
                    if provenance.get('representation') not in {'DART_VIEWER_HTML', 'SEC_INLINE_XBRL'}:
                        shared_keys += ('markdown_sha256',)
                    if (provenance.get('parser_version') in {'material_v2', 'sec_inline_v1'}
                            and all(key in provenance for key in shared_keys)):
                        shared_keys += tuple(key for key in ('html_parser_version', 'locator_model') if key in provenance)
                        common = {key: provenance[key] for key in shared_keys}
                        references = payload.get('source_provenance', {})
                        source_id = record['source_id']
                        if source_id in references and references[source_id] != common:
                            omissions += 1
                            if 'SOURCE_PROVENANCE_CONFLICT' not in payload['gaps']:
                                payload['gaps'].append('SOURCE_PROVENANCE_CONFLICT')
                            continue
                        record['provenance'] = {key: value for key, value in provenance.items() if key not in shared_keys}
                        if provenance.get('representation') in {'FIRECRAWL_CLEANED_HTML', 'DART_VIEWER_HTML'}:
                            from prism_core.filing_report_evidence import (
                                compact_html_provenance,
                            )

                            record['provenance'] = compact_html_provenance(record['provenance'])
                        trial['source_provenance'] = {**references, source_id: common}
                    if _size(trial) <= budget - 80:
                        payload = trial
                        seen.add(digest)
                    else:
                        omissions += 1
        payload['omitted_blocks'] = omissions
        present_topics = {row['topic'] for row in payload['sources']}
        payload['topic_gaps'] = {topic: ('BUDGET_OR_DUPLICATE' if queues[topic] else 'NO_ELIGIBLE_SOURCE_BLOCK')
                                for topic in topics if topic not in present_topics}
        # Reserve metadata budget without ever slicing source text.
        while _size(payload) > budget and payload['sources']:
            removed = payload['sources'].pop()
            retained = {row['source_id'] for row in payload['sources']}
            for reference_table in ('source_filings', 'source_provenance'):
                if reference_table in payload:
                    payload[reference_table] = {k: v for k, v in payload[reference_table].items() if k in retained}
            payload['omitted_blocks'] += 1
            if not any(row['topic'] == removed['topic'] for row in payload['sources']):
                payload['topic_gaps'][removed['topic']] = 'BYTE_BUDGET'
        notes[section] = _dump(payload)
        injected.update(row['source_id'] for row in payload['sources'])
        section_omissions[section] = payload['omitted_blocks']
    metadata = [{k: v for k, v in source.items() if k not in {'excerpt', 'blocks'}} for source in sources]
    return {'evidence_id': evidence_id, 'news_usable': False, 'section_notes': notes,
            'receipt': {'version': 'report_research_v1', 'collector_version': PROFILE, 'market': market,
                        'symbol': symbol, 'reference_date': day, 'observed_at': observed,
                        'calls': progress['calls'], 'calls_this_run': progress['calls'], 'cache_hit': False,
                        'status': 'PARTIAL' if sources else 'UNAVAILABLE', 'usable_sources': len(sources),
                        'injected_sources': len(injected), 'gaps': list(dict.fromkeys(progress['gaps'])),
                        'context_gaps': section_omissions, 'sources': metadata,
                        'raw_response_utf8_bytes': progress.get('raw_response_utf8_bytes', 0),
                        'section_utf8_bytes': {k: len(v.encode('utf-8')) for k, v in notes.items()},
                        'competitive_complete': False, 'collection_complete': False, 'usage': 'UNKNOWN',
                        'events': progress.get('events', []), 'tradingview': 'RIGHTS_UNCONFIRMED',
                        **({key: progress[key] for key in ('filing_selection', 'dart_calls', 'dart_response_bytes',
                                                          'sec_calls', 'sec_response_bytes')
                            if key in progress}),
                        **({'dart_calls_this_run': progress['dart_calls'],
                            'total_calls_this_run': progress['calls'] + progress['dart_calls']}
                           if 'dart_calls' in progress else {}),
                        **({'sec_calls_this_run': progress['sec_calls'],
                            'total_calls_this_run': progress['calls'] + progress['sec_calls']}
                           if 'sec_calls' in progress else {}),
                        **({'filing_parser': progress['filing_parser'], 'filing_parser_revision': FILING_PARSER_REVISION}
                           if 'filing_parser' in progress else {})}}
