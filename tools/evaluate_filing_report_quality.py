"""Offline source-fidelity evaluation of evidence actually delivered to report owners.

This measures input evidence, not generated prose quality or trading performance.
The frozen three-document set is a regression corpus, not an unseen holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.evaluate_filing_extraction import (
    load_documents,
    load_source,
    score_records,
    source_bound_records,
    source_units,
    source_url,
)


def delivered_records(packet, text, *, baseline):
    """Recover only delivered, exact source slices, never expand source context.

    Baseline scope recovery is an evaluator-only audit. It must not be presented
    as metadata received by the report agent. Ambiguous duplicate text is not
    assigned an arbitrary location. Invalid treatment provenance is rejected.
    """
    units = source_units(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    records, errors, missing = [], [], 0
    for owner, serialized in packet['section_notes'].items():
        for index, row in enumerate(json.loads(serialized)['sources']):
            block_id = f'{owner}:{index}'
            excerpt = row['excerpt']
            provenance = row.get('provenance', {})
            if not provenance:
                missing += 1
            try:
                if baseline:
                    candidates = [excerpt]
                    if '\n' in excerpt:
                        candidates.append(excerpt.split('\n', 1)[1])
                    recovered = next((candidate for candidate in candidates if candidate
                                      and text.count(candidate) == 1), None)
                    if recovered is None:
                        raise ValueError('BASELINE_SPAN_NOT_UNIQUE_OR_NOT_EXACT')
                    start = text.index(recovered)
                    end = start + len(recovered)
                    # Split by original units before assigning audit-only scope.
                    slices = [{ 'block_id': block_id, 'text': text[max(start, u['start']):min(end, u['end'])],
                                'source_spans': [[max(start, u['start']), min(end, u['end'])]],
                                'scope': u['scope'], 'delivered_scope': 'unknown'}
                              for u in units if max(start, u['start']) < min(end, u['end'])]
                    if not slices:
                        raise ValueError('BASELINE_NO_SOURCE_UNIT')
                    records.extend(source_bound_records(slices, text, units))
                else:
                    if (provenance.get('representation') != 'FIRECRAWL_MARKDOWN'
                            or provenance.get('markdown_sha256') != digest
                            or provenance.get('representation_sha256') != digest):
                        raise ValueError('SOURCE_REPRESENTATION_OR_HASH_MISMATCH')
                    record = {'block_id': block_id, 'text': excerpt,
                              'source_spans': provenance.get('source_spans', []),
                              'scope': provenance.get('scope', 'unknown'),
                              'delivered_scope': provenance.get('scope', 'unknown')}
                    records.extend(source_bound_records([record], text, units))
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({'block_id': block_id, 'reason': str(exc)})
    return records, errors, missing


def _metrics(facts, records):
    score = score_records(facts, records)
    groups = [group for fact in score['facts'] for group in fact['groups']]
    score['missing_literal_count_any_scope'] = sum(g['needle_count'] - g['literal_hits_any_scope'] for g in groups)
    score['missing_context_count_correct_scope'] = sum(len(g['missing_context_any_eligible_record']) for g in groups)
    return score


def evaluate_document(document):
    from prism_core import report_insight_prefetch as insight
    from prism_core.filing_report_evidence import filing_blocks

    text, url = load_source(document), source_url(document)
    result = {'document_id': document['document_id'], 'source_url': url,
              'source_file_sha256': hashlib.sha256(Path(document['source_file']).read_bytes()).hexdigest(),
              'markdown_sha256': hashlib.sha256(text.encode()).hexdigest(),
              'source_bytes': len(text.encode()), 'provider_calls': 0, 'model_calls': 0}
    for method in ('baseline', 'treatment'):
        start = time.perf_counter()
        blocks, gaps = (insight.topic_blocks(text), []) if method == 'baseline' else filing_blocks({'markdown': text}, url)
        source = {'source_id': document['document_id'], 'url': url, 'published': 'UNKNOWN',
                  'publication_basis': 'UNKNOWN', 'blocks': blocks}
        packet = insight.packet('KR', document.get('ticker', 'UNKNOWN'), '2026-09-18', {
            'sources': [source], 'gaps': gaps, 'calls': 0})
        elapsed = (time.perf_counter() - start) * 1000
        sections = {owner: json.loads(payload) for owner, payload in packet['section_notes'].items()}
        sizes = {owner: len(payload.encode()) for owner, payload in packet['section_notes'].items()}
        if any(size > insight.SECTION_BYTES for size in sizes.values()):
            raise ValueError('Report owner byte budget exceeded')
        records, errors, missing = delivered_records(packet, text, baseline=method == 'baseline')
        delivered = [{**record, 'scope': record['delivered_scope']} for record in records]
        result[method] = {
            'status': 'PROVENANCE_ERRORS' if errors else 'SOURCE_BOUND',
            'section_utf8_bytes': sizes, 'total_section_utf8_bytes': sum(sizes.values()),
            'delivered_record_count': sum(len(section['sources']) for section in sections.values()),
            'delivered_text_bytes': sum(len(row['excerpt'].encode()) for section in sections.values() for row in section['sources']),
            'source_evaluation_unit_count': len(records), 'processing_ms': elapsed,
            'records_without_delivered_provenance': missing, 'provenance_errors': errors,
            'source_scope_audit': _metrics(document['facts'], records),
            'delivered_scope_score': _metrics(document['facts'], delivered),
            'sections': sections,
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold', action='append', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error('--out must be a new file')
    documents = load_documents(args.gold)
    root = Path(__file__).resolve().parents[1]
    report = {'schema_version': 1, 'status': 'OFFLINE_REGRESSION_INPUT_EVIDENCE_ONLY',
              'gold_files': [{'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in args.gold],
              'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'implementation_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                  for name in ('prism_core/filing_report_evidence.py', 'prism_core/filing_selection.py',
                               'prism_core/filing_structure.py', 'prism_core/filing_table_projection.py',
                               'prism_core/report_insight_prefetch.py', 'tools/evaluate_filing_extraction.py')},
              'limits': ['Previously used regression corpus, not an unseen holdout.',
                         'Saved Markdown only; no new filing retrieval, HTML assessment or freshness proof.',
                         'Source fidelity is not independent factual validation.',
                         'Baseline source_scope_audit infers scope offline; downstream receives no such metadata.',
                         'No generated prose, PDF, investment decision or profit quality is measured.',
                         'Same 6000-byte owner ceilings; delivered sizes may differ.'],
              'documents': [evaluate_document(document) for document in documents]}
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': report['status'], 'documents': len(documents), 'out': str(args.out)}))


if __name__ == '__main__':
    main()
