"""Source-preserving adapters for explicitly opted-in filing report evidence.

Firecrawl HTML is a cleaned representation, not the original HTTP response.
Neither representation establishes publication, issuer identity or fact validity.
"""
import hashlib
import json
import os.path
import re
from time import monotonic

from prism_core.filing_selection import _classify, select_filing_evidence
from prism_core.filing_structure import parse_filing

VERSION = 'structured_v1'
MATERIAL_VERSION = 'material_v2'
_STATEMENT_TITLE = re.compile(
    r'^(?:연결|별도|개별)?'
    r'(?:재무상태표|(?:포괄)?손익계산서|현금흐름표|자본변동표)(?:\((?:연결|별도|개별)\))?$')
_ROUTES = {
    'customer_revenue': 'financial_quality_valuation', 'cashflow': 'financial_quality_valuation',
    'financial_quality': 'financial_quality_valuation', 'tax': 'financial_quality_valuation',
    'business_competition': 'business_segments', 'capital': 'ownership_governance',
    'related_party': 'ownership_governance', 'liquidity_collateral': 'catalysts_risks_counterevidence',
    'contingency': 'catalysts_risks_counterevidence', 'subsequent_events': 'catalysts_risks_counterevidence',
}


def _is_statement_title(title):
    label = ''.join(title.split())
    for separator in ('.', ')'):
        prefix, found, rest = label.partition(separator)
        if found and all(part.isdecimal() for part in prefix.split('-')):
            label = rest
            break
    return bool(_STATEMENT_TITLE.fullmatch(label))


def compact_html_provenance(provenance):
    """Lossless DOM-prefix factoring; financial text/context is never shortened."""
    if not isinstance(provenance, dict):
        raise TypeError('INVALID_COMPACT_DOM_PROVENANCE')
    if 'dom_paths' in provenance:
        if (any(key in provenance for key in ('source_path', 'source_paths', 'footnote_paths'))
                or (isinstance(provenance['dom_paths'], dict)
                    and 'context' in provenance['dom_paths'] and 'context_paths' in provenance)):
            raise ValueError('CONFLICTING_DOM_PROVENANCE')
        return dict(provenance)
    primary, parts = provenance.get('source_path'), provenance.get('source_paths')
    notes = provenance.get('footnote_paths', [])
    context = provenance.get('context_paths', [])
    if (not isinstance(primary, str) or not isinstance(parts, list) or not isinstance(notes, list)
            or not isinstance(context, list) or not parts
            or not all(isinstance(p, str) and p.startswith('/') for p in [primary, *parts, *notes, *context])):
        return dict(provenance)
    prefix = os.path.commonprefix([primary, *parts, *notes, *context]).rsplit('/', 1)[0] + '/'
    locators = {'base': prefix, 'source': primary[len(prefix):],
                'parts': [p[len(prefix):] for p in parts]}
    if 'footnote_paths' in provenance:
        locators['notes'] = [p[len(prefix):] for p in notes]
    if 'context_paths' in provenance:
        locators['context'] = [p[len(prefix):] for p in context]
    compact = {key: value for key, value in provenance.items()
               if key not in {'source_path', 'source_paths', 'footnote_paths', 'context_paths'}}
    compact['dom_paths'] = locators
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
    return compact if size(compact) < size(provenance) else dict(provenance)


def expand_html_provenance(provenance):
    """Restore the original locator metadata for audit/replay, never run XPath."""
    if not isinstance(provenance, dict):
        raise TypeError('INVALID_COMPACT_DOM_PROVENANCE')
    if 'dom_paths' not in provenance:
        return dict(provenance)
    if any(key in provenance for key in ('source_path', 'source_paths', 'footnote_paths')):
        raise ValueError('CONFLICTING_DOM_PROVENANCE')
    locators = provenance['dom_paths']
    if isinstance(locators, dict) and 'context_paths' in provenance and 'context' in locators:
        raise ValueError('CONFLICTING_DOM_PROVENANCE')
    if (not isinstance(locators, dict) or not isinstance(locators.get('base'), str)
            or not locators['base'].startswith('/') or not locators['base'].endswith('/')
            or not isinstance(locators.get('source'), str) or not isinstance(locators.get('parts'), list)
            or not isinstance(locators.get('notes', []), list)
            or not isinstance(locators.get('context', []), list)
            or not all(isinstance(p, str) for p in [locators['source'], *locators['parts'],
                                                   *locators.get('notes', []), *locators.get('context', [])])):
        raise ValueError('INVALID_COMPACT_DOM_PROVENANCE')
    try:
        relatives = [locators['source'], *locators['parts'], *locators.get('notes', []), *locators.get('context', [])]
        restored_bytes = len(locators['base'].encode()) * len(relatives) + sum(len(p.encode()) for p in relatives)
    except UnicodeEncodeError:
        raise ValueError('INVALID_COMPACT_DOM_PROVENANCE') from None
    if restored_bytes > 16 * 1024 * 1024:
        raise ValueError('COMPACT_DOM_PROVENANCE_LIMIT')
    restored = {key: value for key, value in provenance.items() if key != 'dom_paths'}
    restored.update(source_path=locators['base'] + locators['source'],
                    source_paths=[locators['base'] + p for p in locators['parts']])
    if 'notes' in locators:
        restored['footnote_paths'] = [locators['base'] + p for p in locators['notes']]
    if 'context' in locators:
        restored['context_paths'] = [locators['base'] + p for p in locators['context']]
    return restored


def _normalized(text):
    return re.sub(r'[\s|*#`\\]+', '', text)


def _hierarchy(path):
    """Keep Korean levels separately: absence is not an explicit conflict."""
    major = next((i for i, label in enumerate(path) if re.match(r'^[IVX]+[.)]\s*', label)), None)
    if major is None:
        return tuple(_normalized(label) for label in path), ()
    core = tuple(_normalized(label) for label in path[major:]
                 if not re.match(r'^[가-힣][.)]\s*', label))
    korean = tuple(_normalized(label) for label in path[major:]
                   if re.match(r'^[가-힣][.)]\s*', label))
    return core, korean


def _same_markdown_unit(record, units, markdown):
    """Corroborate one local unit, never a bag of cells from the whole filing.

    This checks text order and local scope, not equivalence of HTML/Markdown
    table geometry. Merge geometry is quoted only from the hashed HTML.
    """
    table = record.get('table')
    anchors = ([cell['text'] for cell in table['cells']] if table else [record['text']])
    matches = set()
    record_core, record_korean = _hierarchy(record['section_path'])
    for index, unit in enumerate(units):
        unit_core, unit_korean = _hierarchy(unit['section_path'])
        if (unit['kind'] != record['kind'] or unit['scope'] != record['scope']
                or unit['is_heading'] or unit_core != record_core
                or (unit_korean and record_korean and unit_korean != record_korean)):
            continue
        if not table:
            # A raw HTML paragraph may contain <br>-separated Markdown
            # paragraphs. Join at most eight, never across a heading/table or
            # even a different original (pre-canonicalization) section path.
            end = unit['end']
            for following in units[index:index + 8]:
                if (following['kind'] != 'prose' or following['is_heading']
                        or following['scope'] != unit['scope']
                        or following['section_path'] != unit['section_path']
                        or markdown[end:following['start']].strip()
                        or following['end'] - unit['start'] > 12000):
                    break
                end = following['end']
                if _normalized(markdown[unit['start']:end]) == _normalized(record['text']):
                    matches.add((unit['start'], end))
            continue
        text, position = _normalized(unit['text']), 0
        cells = [_normalized(cell) for line in unit['text'].splitlines() if line.lstrip().startswith('|')
                 for cell in line.strip().strip('|').split('|')
                 if not re.fullmatch(r'\s*:?-{3,}:?\s*', cell)] if table else None
        for anchor in anchors:
            needle = _normalized(anchor)
            if not needle:
                continue
            if cells is None:
                found = text.find(needle, position)
            else:
                found = next((i for i in range(position, len(cells)) if cells[i] == needle), -1)
            if found < 0:
                break
            position = found + (1 if cells is not None else len(needle))
        else:
            if all(_normalized(record.get(key, '')) in text for key in ('context_before', 'footnotes')):
                matches.add((unit['start'], unit['end']))
    return len(matches) == 1


def _legacy_peer_units(markdown):
    """Preserve proven old peer coverage only with an unambiguous source unit.

    Old topic blocks may prepend a heading. Never ship that reconstructed text:
    recover the complete original unit or explicitly omit the candidate.
    """
    from prism_core.filing_topic_blocks import topic_blocks

    units = parse_filing(markdown)
    records, gaps, seen = [], [], set()
    for candidate in topic_blocks(markdown):
        if candidate['topic'] != 'direct_peers_competitive_position' or '|' in candidate['excerpt']:
            continue
        excerpt = candidate['excerpt'].strip()
        # Exact full candidate first; only remove one prepended heading when
        # that complete candidate does not occur in the source at all.
        if excerpt not in markdown:
            _, separator, excerpt = excerpt.partition('\n')
            if not separator:
                gaps.append('FILING_LEGACY_PEER_SPAN_UNRESOLVED')
                continue
            excerpt = excerpt.strip()
        if not excerpt or markdown.count(excerpt) != 1:
            gaps.append('FILING_LEGACY_PEER_SPAN_AMBIGUOUS')
            continue
        start = markdown.find(excerpt)
        matches = [unit for unit in units if not unit['is_heading'] and unit['kind'] == 'prose'
                   and unit['start'] <= start and start + len(excerpt) <= unit['end']
                   and unit['text'].strip() == excerpt]
        if len(matches) != 1:
            gaps.append('FILING_LEGACY_PEER_SPAN_UNRESOLVED')
            continue
        unit = matches[0]
        if unit['block_id'] not in seen:
            records.append(unit)
            seen.add(unit['block_id'])
    return records, gaps


def filing_blocks(data, url, *, material_notes=False):
    """Return complete candidate records and explicit representation gaps.

    Callers must first admit the source through existing URL/issuer/date gates.
    HTML present but unusable never falls back to flattened Markdown tables.
    Final per-owner budget enforcement remains in the existing packet builder.
    """
    deadline = monotonic() + 10.0
    markdown = data.get('markdown') if isinstance(data, dict) else None
    if type(markdown) is not str:
        return [], ['FILING_MARKDOWN_INVALID']
    if len(markdown) > 2 * 1024 * 1024:
        return [], ['FILING_MARKDOWN_BYTE_LIMIT']
    try:
        markdown_bytes = markdown.encode('utf-8')
    except UnicodeEncodeError:
        return [], ['FILING_MARKDOWN_INVALID_ENCODING']
    if len(markdown_bytes) > 2 * 1024 * 1024:
        return [], ['FILING_MARKDOWN_BYTE_LIMIT']
    md_hash = hashlib.sha256(markdown_bytes).hexdigest()
    gaps = []
    if 'html' in data:
        metadata = data.get('metadata') or {}
        if (not isinstance(metadata, dict) or metadata.get('sourceURL') != url
                or metadata.get('statusCode') != 200):
            return [], ['FILING_HTML_PROVENANCE_UNVERIFIED']
        from prism_core.filing_html import parse_filing_html

        parsed = parse_filing_html(data['html'], _deadline=deadline)
        gaps.extend('FILING_HTML_' + error for error in parsed['errors'])
        if parsed['status'] not in {'COMPLETE', 'PARTIAL'}:
            return [], gaps or ['FILING_HTML_UNSUPPORTED']
        records = parsed['records']
        if material_notes is True:
            from prism_core.material_filing_selection import material_html_records

            records, grouping_gaps = material_html_records(records)
            gaps.extend(grouping_gaps)
        representation = 'FIRECRAWL_CLEANED_HTML'
        digest = parsed['source_sha256']
        markdown_units = parse_filing(markdown)
    elif material_notes is True:
        from prism_core.material_filing_selection import material_filing_records

        peers, peer_gaps = _legacy_peer_units(markdown)
        records, material_gaps = material_filing_records(markdown, peer_records=peers)
        gaps.extend([*peer_gaps, *material_gaps])
        representation, digest = 'FIRECRAWL_MARKDOWN', md_hash
    else:
        # Independently rank each owner's candidates at the unchanged 6 KB
        # ceiling. The final packet also charges provenance and source metadata.
        owner_topics = (
            ('business_competition', 'liquidity_collateral', 'contingency', 'subsequent_events'),
            ('business_competition', 'capital', 'related_party'),
            ('customer_revenue', 'cashflow', 'financial_quality', 'tax'),
        )
        records, peer_gaps = _legacy_peer_units(markdown)
        gaps.extend(peer_gaps)
        seen = {record['block_id'] for record in records}
        for topics in owner_topics:
            selected = select_filing_evidence(markdown, source_url=url, method='structured_projected',
                                              budget_bytes=6000, topic_filter=topics)
            for record in selected.get('records', []):
                if record['block_id'] not in seen:
                    records.append(record)
                    seen.add(record['block_id'])
        representation, digest = 'FIRECRAWL_MARKDOWN', md_hash
    return _record_blocks(records, material_notes=material_notes, representation=representation,
                          digest=digest, md_hash=md_hash, gaps=gaps,
                          parsed=parsed if 'html' in data else None,
                          markdown_units=markdown_units if 'html' in data else None,
                          markdown=markdown, deadline=deadline if 'html' in data else None)


def _record_blocks(records, *, material_notes, representation, digest, md_hash,
                   gaps, parsed=None, markdown_units=None, markdown=None, deadline=None):
    """Shared semantic routing; representation admission stays with the caller."""
    blocks = []
    for record in records:
        if deadline is not None and monotonic() >= deadline:
            return [], list(dict.fromkeys([*gaps, 'FILING_EVIDENCE_TIME_LIMIT']))
        if record.get('layout_role') or record.get('context_incomplete'):
            continue
        source_text = '\n'.join((record.get('context_before', ''), record['text'], record.get('footnotes', '')))
        statement = (material_notes is True and record['kind'] == 'table'
                     and representation in {'DART_VIEWER_HTML', 'FIRECRAWL_CLEANED_HTML'}
                     and record.get('section_path')
                     and _is_statement_title(record['section_path'][-1]))
        tags = ()
        if material_notes is True:
            from prism_core.filing_materiality import TOPICS, material_topics

            tags = material_topics(source_text, record['section_path'])
        topic, score = _classify({**record, 'text': source_text})
        if score <= 0 and not tags and not statement:
            continue
        if representation == 'FIRECRAWL_CLEANED_HTML' and not _same_markdown_unit(record, markdown_units, markdown):
            gaps.append('FILING_HTML_MARKDOWN_MISMATCH')
            continue
        text = record['text']
        # Preserve existing report owners, including explicit competitive prose.
        route = _ROUTES.get(topic)
        if topic == 'business_competition' and re.search('경쟁사|경쟁업체|시장점유율|competitor', text, re.IGNORECASE):
            route = 'direct_peers_competitive_position'
        if tags and route != 'direct_peers_competitive_position':
            # A tax/asset table mentioning options or insurance is not an
            # issuance/post-period event. Prefer its explicit local heading.
            heading_tags = next((found for heading in reversed(record['section_path'])
                                 if (found := material_topics('', (heading,)))), ())
            route_tags = heading_tags or (tags if record['kind'] == 'prose' or not route else ())
            if route_tags:
                primary = next(tag for tag in ('subsequent_events', 'audit_contingencies', 'liquidity_covenants',
                    'dilution_overhang', 'business_contracts', 'asset_rnd_quality', 'related_parties', 'earnings_quality') if tag in route_tags)
                route = {'news_analysis': 'catalysts_risks_counterevidence',
                         'company_status': 'financial_quality_valuation',
                         'company_overview': 'business_segments'}[TOPICS[primary]['owner_section']]
        # A canonical statement title owns the whole statement. Account labels
        # such as provisions or share capital do not turn it into event news.
        # Match only the nearest complete title, never a financial ancestor.
        if statement:
            route = 'financial_quality_valuation'
        if not route:
            continue
        provenance = {key: record[key] for key in (
            'section_path', 'scope', 'kind', 'source_path', 'source_paths', 'source_spans', 'context_before',
            'footnotes', 'footnote_paths', 'context_paths', 'projected', 'projection_kind', 'selected_data_rows',
            'original_data_rows', 'scope_context') if key in record}
        provenance.update(parser_version=MATERIAL_VERSION if material_notes is True else VERSION, representation=representation,
                          representation_sha256=digest, markdown_sha256=md_hash)
        if representation in {'FIRECRAWL_CLEANED_HTML', 'DART_VIEWER_HTML'}:
            provenance.update(html_parser_version=parsed['parser_version'],
                              locator_model='HTML_DOCUMENT' if parsed['parser_version'] == 'filing_html_v2' else 'LEGACY_HTML_FRAGMENT')
        if tags:
            provenance['material_topics'] = tags
        block = {'topic': route, 'excerpt': text, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
                 'provenance': provenance}
        if material_notes is True and representation in {'DART_VIEWER_HTML', 'FIRECRAWL_CLEANED_HTML'}:
            from prism_core.material_filing_selection import html_retrieval_class

            block['_retrieval'] = html_retrieval_class(record)
        if (material_notes is True and record['kind'] == 'table'
                and representation in {'DART_VIEWER_HTML', 'FIRECRAWL_CLEANED_HTML'}):
            from prism_core.filing_html_codec import compact_html_table_excerpt

            compact = compact_html_table_excerpt(record)
            size = lambda item: len(json.dumps(item, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            if compact != text:
                candidate = {**block, 'excerpt': compact,
                             'provenance': {**provenance, 'excerpt_encoding': 'html_cell_tuples_v1'}}
                if size(candidate) < size(block):
                    block = candidate
            if route == 'catalysts_risks_counterevidence' and len(block['excerpt'].encode('utf-8')) > 2400:
                from prism_core.filing_html_projection import (
                    encode_html_column_view,
                    project_html_columns,
                )

                projected, diagnostics = project_html_columns(record, deadline=deadline)
                if diagnostics['code'] == 'PROJECTION_DEADLINE':
                    return [], list(dict.fromkeys([*gaps, 'FILING_EVIDENCE_TIME_LIMIT']))
                block['_projection'] = diagnostics
                if projected is not None:
                    partial = {key: projected[key] for key in (
                        'projected', 'projection_kind', 'selected_columns', 'row_label_columns', 'original_columns')}
                    candidate = {**block, 'excerpt': encode_html_column_view(projected),
                        'provenance': {**block['provenance'], **partial, 'excerpt_encoding': 'html_column_view_v1'},
                        '_retrieval': html_retrieval_class(projected)}
                    # Compare only the model-visible block, not private audit counters.
                    def cost(value):
                        return len(json.dumps({k: v for k, v in value.items() if not k.startswith('_')},
                                              ensure_ascii=False, separators=(',', ':')).encode())
                    if cost(candidate) < cost(block):
                        block = candidate
            # Preserve the existing projection decision and retrieval order.
            # Only complete large tables can replace repeated tuple coordinates.
            if (not block['provenance'].get('projected')
                    and len(block['excerpt'].encode('utf-8')) > 2400):
                from prism_core.filing_html_codec import compact_html_grid_excerpt

                grid = compact_html_grid_excerpt(record)
                if grid != text:
                    candidate = {**block, 'excerpt': grid,
                        'provenance': {**block['provenance'], 'excerpt_encoding': 'html_cell_grid_v1'}}
                    if size(candidate) < size(block):
                        candidate['_packing_original'] = {
                            'excerpt': block['excerpt'],
                            'excerpt_encoding': block['provenance'].get('excerpt_encoding')}
                        block = candidate
        blocks.append(block)
    if material_notes is True and representation in {'DART_VIEWER_HTML', 'FIRECRAWL_CLEANED_HTML'}:
        from prism_core.material_filing_selection import order_material_html_blocks

        blocks = order_material_html_blocks(blocks)
    if deadline is not None and monotonic() >= deadline:
        return [], list(dict.fromkeys([*gaps, 'FILING_EVIDENCE_TIME_LIMIT']))
    return blocks, list(dict.fromkeys(gaps))
