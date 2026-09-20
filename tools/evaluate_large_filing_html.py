"""Offline large-filing traversal, original DOM provenance and packet audit.

The independent full DOM is an evaluation oracle only, never a production
fallback. It checks source location/fidelity, not issuer claims or profitability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from lxml import etree
from lxml import html as lhtml

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_core.filing_html import _text, parse_filing_html
from prism_core.filing_html_tables import _visible_text, parse_html_table
from prism_core.filing_report_evidence import expand_html_provenance, filing_blocks
from prism_core.report_insight_prefetch import packet


def _normalized(value):
    return ' '.join(value.split())


def audit_source_paths(html, parsed):
    """Resolve every retained path against the unchanged full document model."""
    root = lhtml.document_fromstring(html, parser=lhtml.HTMLParser(no_network=True))
    tree = root.getroottree()
    node_order = {node: index for index, node in enumerate(root.iter())}
    errors, checked = [], set()

    def resolve(path):
        if not isinstance(path, str) or not path.startswith('/'):
            raise ValueError('INVALID_DOM_PATH')
        try:
            values = tree.xpath(path)
        except etree.XPathError:
            raise ValueError('INVALID_DOM_PATH') from None
        if len(values) != 1:
            raise ValueError('DOM_PATH_NOT_UNIQUE')
        checked.add(path)
        return values[0]

    def joined(paths, *, footnote_units=False):
        parts = []
        previous_cell = False
        for path in paths:
            node = resolve(path)
            cell = isinstance(node, etree._Element) and node.tag == 'td'
            if footnote_units and parts and (cell or previous_cell):
                parts.append(' ')
            parts.append(_text(node, normalized=False) if isinstance(node, etree._Element) else str(node))
            previous_cell = cell
        return _normalized(''.join(parts))

    for index, record in enumerate(parsed['records']):
        try:
            resolve(record['source_path'])
            if record['kind'] == 'prose':
                if joined(record['source_paths']) != _normalized(record['text']):
                    raise ValueError('PROSE_LOCATOR_TEXT_MISMATCH')
            else:
                node = resolve(record['source_path'])
                table = record['table']
                original = parse_html_table(node)
                if original['status'] != 'COMPLETE' or original['grid'] != table['grid']:
                    raise ValueError('TABLE_GEOMETRY_MISMATCH')
                if len(original['cells']) != len(table['cells']):
                    raise ValueError('TABLE_CELL_COUNT_MISMATCH')
                for expected, cell in zip(original['cells'], table['cells']):
                    value = resolve(cell['source_path'])
                    if _visible_text(value) != cell['text'] or any(expected[k] != cell[k]
                            for k in ('row', 'col', 'rowspan', 'colspan', 'text', 'tag')):
                        raise ValueError('CELL_LOCATOR_OR_VALUE_MISMATCH')
            if record.get('footnotes') and joined(record['footnote_paths'], footnote_units=True) != _normalized(record['footnotes']):
                raise ValueError('FOOTNOTE_LOCATOR_TEXT_MISMATCH')
            if 'context_paths' in record:
                paths = record['context_paths']
                if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
                    raise ValueError('INVALID_CONTEXT_PATHS')
                context = _normalized(record.get('context_before', ''))
                cursor, previous = 0, -1
                target = resolve(record['source_path'])
                for path in paths:
                    value = resolve(path)
                    if (not isinstance(value, etree._Element) or value.tag != 'td'
                            or not previous < node_order[value] < node_order[target]):
                        raise ValueError('CONTEXT_LOCATOR_ORDER_MISMATCH')
                    previous = node_order[value]
                    text = _normalized(_text(value))
                    if text:
                        position = context.find(text, cursor)
                        if position < 0:
                            raise ValueError('CONTEXT_LOCATOR_TEXT_MISMATCH')
                        cursor = position + len(text)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({'record_index': index, 'reason': str(exc)})
    return {'original_document_nodes': sum(1 for _ in root.iter()),
            'checked_unique_paths': len(checked), 'errors': errors,
            'scope': 'Original DOM position/geometry/text; not independent financial validation.'}


def audit_delivered_provenance(blocks, note, parsed, *, source_id=None):
    """Bind packet paths/encoding to admitted blocks and the original table hash."""
    errors = []
    keys = ('source_path', 'source_paths', 'footnote_paths', 'context_paths', 'scope',
            'section_path', 'kind', 'parser_version', 'representation', 'representation_sha256', 'excerpt_encoding')
    for index, row in enumerate(note['sources']):
        reason = 'DELIVERED_LOCATOR_MISMATCH'
        try:
            if source_id is not None and row.get('source_id') != source_id:
                raise ValueError
            local = row.get('provenance', {})
            shared = note.get('source_provenance', {}).get(row.get('source_id'), {})
            if not isinstance(local, dict) or not isinstance(shared, dict):
                raise TypeError
            if any(local[key] != shared[key] for key in local.keys() & shared.keys()):
                raise ValueError
            provenance = expand_html_provenance({**shared, **local})
            matches = [block for block in blocks if block['excerpt'] == row['excerpt'] and all(
                (key in block['provenance']) == (key in provenance)
                and block['provenance'].get(key) == provenance.get(key) for key in keys)]
            if len(matches) != 1:
                raise ValueError
            originals = [r for r in parsed['records'] if r['kind'] == 'table'
                         and r['source_path'] == provenance.get('source_path')]
            if originals:
                reason = 'DELIVERED_TABLE_SOURCE_MISMATCH'
                if (len(originals) != 1 or provenance.get('kind') != 'table'
                        or provenance.get('representation_sha256') != parsed['source_sha256']):
                    raise ValueError
                if 'excerpt_encoding' not in provenance and row['excerpt'] != originals[0]['text']:
                    raise ValueError
            if 'excerpt_encoding' in provenance:
                reason = 'DELIVERED_TABLE_CODEC_INVALID'
                if (provenance['excerpt_encoding'] != 'html_cell_tuples_v1'
                        or provenance.get('kind') != 'table'
                        or provenance.get('parser_version') != 'material_v2'
                        or provenance.get('representation') not in {'DART_VIEWER_HTML', 'FIRECRAWL_CLEANED_HTML'}):
                    raise ValueError
                from prism_core.filing_html_codec import expand_html_table_excerpt

                if provenance.get('representation_sha256') != parsed['source_sha256']:
                    raise ValueError
                if len(originals) != 1 or expand_html_table_excerpt(row['excerpt']) != originals[0]['text']:
                    raise ValueError
                # Decoder validates the bounded schema first; legacy text omits
                # shape, so compare it separately to the original geometry.
                table = originals[0]['table']
                if json.loads(row['excerpt'])['shape'] != [table['row_count'], table['column_count']]:
                    raise ValueError
        except (ValueError, KeyError, TypeError):
            errors.append({'index': index, 'reason': reason})
    return errors


def evaluate_case(case, *, parser=parse_filing_html, adapter=filing_blocks):
    path = Path(case['file'])
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != case['sha256']:
        raise ValueError('INPUT_FILE_HASH_MISMATCH')
    data = json.loads(raw)
    html, markdown = data['html'], data['markdown']
    source_url = data['metadata']['sourceURL']
    start = time.perf_counter()
    parsed = parser(html)
    parse_seconds = time.perf_counter() - start
    if parsed['source_sha256'] != hashlib.sha256(html.encode()).hexdigest():
        raise ValueError('REPRESENTATION_HASH_MISMATCH')
    audit = audit_source_paths(html, parsed)
    start = time.perf_counter()
    blocks, gaps = adapter(data, source_url, material_notes=True)
    source = {'source_id': hashlib.sha256((source_url + markdown).encode()).hexdigest()[:16],
              'url': source_url, 'published': 'UNKNOWN',
              'publication_basis': 'UNKNOWN', 'blocks': blocks}
    delivery = packet('KR', case['symbol'], '2026-09-18', {'sources': [source], 'gaps': gaps,
                       'calls': 0, 'filing_parser': 'material_v2'})
    notes = {owner: json.loads(note) for owner, note in delivery['section_notes'].items()}
    # Prove that model-envelope compaction did not erase or change audit paths.
    delivered_provenance_errors = []
    for owner, note in notes.items():
        delivered_provenance_errors.extend({'owner': owner, **error} for error in
            audit_delivered_provenance(blocks, note, parsed, source_id=source['source_id']))
    return {'symbol': case['symbol'], 'name': case['name'], 'file_sha256': digest,
            'source_url': source_url, 'html_sha256': parsed['source_sha256'],
            'html_characters': len(html), 'html_utf8_bytes': len(html.encode()),
            'markdown_characters': len(markdown), 'markdown_utf8_bytes': len(markdown.encode()),
            'parser_version': parsed['parser_version'], 'parse_status': parsed['status'],
            'parse_errors': parsed['errors'], 'record_count': len(parsed['records']),
            'table_count': sum(r['kind'] == 'table' for r in parsed['records']),
            'section_paths': sorted({tuple(r['section_path']) for r in parsed['records']}),
            'parse_seconds': parse_seconds, 'adapter_and_packet_seconds': time.perf_counter() - start,
            'streaming': parsed.get('streaming'), 'provenance_audit': audit,
            'delivered_provenance_errors': delivered_provenance_errors,
            'candidate_records': len(blocks), 'gaps': gaps,
            'delivered_records': sum(len(note['sources']) for note in notes.values()),
            'section_utf8_bytes': delivery['receipt']['section_utf8_bytes'], 'sections': notes,
            'model_calls': 0, 'network_calls': 0, 'financial_facts_certified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('--out must be new')
    manifest = json.loads(args.manifest.read_text())
    root = Path(__file__).resolve().parents[1]
    result = {'schema_version': 1, 'scope': manifest['scope'],
              'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              'implementation_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
                  'prism_core/filing_html.py', 'prism_core/filing_html_tables.py',
                  'prism_core/filing_report_evidence.py', 'prism_core/material_filing_selection.py',
                  'prism_core/report_insight_prefetch.py', 'tools/evaluate_large_filing_html.py')},
              'limitations': ['Regression corpus, not unseen company holdout.',
                  'Full DOM only used offline as locator oracle; parser timing excludes that oracle.',
                  'Active-node counters are not measured process RSS or model token usage.',
                  'Current saved source retrieval does not prove latest filing or historical snapshot.'],
              'cases': [evaluate_case(case) for case in manifest['cases']]}
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'cases': [{k: row[k] for k in ('name', 'parse_status', 'record_count', 'delivered_records', 'streaming')}
                               for row in result['cases']], 'provenance_errors': sum(len(row['provenance_audit']['errors']) for row in result['cases'])}))


if __name__ == '__main__':
    main()
