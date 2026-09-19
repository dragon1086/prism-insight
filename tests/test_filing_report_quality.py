import hashlib
import json

import pytest

from tools.evaluate_filing_report_quality import delivered_records, evaluate_document

TEXT = '## III. 재무에 관한 사항\n\n### 3. 연결재무제표 주석\n\n매출액은 100억원이며 주요 제품을 판매합니다.\n'


def packet(record):
    return {'section_notes': {'company_overview': json.dumps({'sources': [record]})}}


def test_baseline_scope_is_only_inferred_not_delivered():
    excerpt = TEXT.split('\n\n')[-1].strip()
    records, errors, missing = delivered_records(packet({'excerpt': excerpt}), TEXT, baseline=True)
    assert not errors and missing == 1
    assert records and records[0]['scope'] == 'consolidated'
    assert records[0]['delivered_scope'] == 'unknown'


@pytest.mark.parametrize('spans', [[[0, 9999]], [[True, 3]], [[20, 30], [0, 5]]])
def test_invalid_provenance_is_not_scored(spans):
    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    record = {'excerpt': '100억원', 'provenance': {'source_spans': spans, 'scope': 'business',
              'representation': 'FIRECRAWL_MARKDOWN', 'representation_sha256': digest,
              'markdown_sha256': digest}}
    records, errors, _ = delivered_records(packet(record), TEXT, baseline=False)
    assert not records and errors


def test_ambiguous_baseline_occurrence_not_guessed():
    records, errors, _ = delivered_records(packet({'excerpt': 'same'}), 'same\n\nsame', baseline=True)
    assert not records and errors


def test_actual_packet_budget_and_source_hash(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'response': {'markdown': TEXT, 'metadata': {
        'sourceURL': 'https://kind.krx.co.kr/example.html'}}}))
    document = {'document_id': 'fixture', 'source_file': str(source), 'facts': [{
        'id': 'one', 'scope': 'consolidated', 'evidence_groups': [{'needles': ['100억원']}]}],
        'markdown_sha256': hashlib.sha256(TEXT.encode()).hexdigest()}
    result = evaluate_document(document)
    assert result['baseline']['source_scope_audit']['full_items'] == 1
    assert result['baseline']['delivered_scope_score']['full_items'] == 0
    assert result['treatment']['delivered_scope_score']['full_items'] == 1
    assert result['treatment']['provenance_errors'] == []
    assert all(size <= 6000 for size in result['treatment']['section_utf8_bytes'].values())
    document['markdown_sha256'] = 'bad'
    with pytest.raises(ValueError, match='hash mismatch'):
        evaluate_document(document)


def test_tampered_text_and_hash_are_rejected():
    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    start = TEXT.index('매출액은')
    provenance = {'representation': 'FIRECRAWL_MARKDOWN', 'representation_sha256': digest,
                  'markdown_sha256': digest, 'source_spans': [[start, len(TEXT)]],
                  'scope': 'consolidated'}
    records, errors, _ = delivered_records(packet({'excerpt': '매출액은 200억원입니다.',
                                                  'provenance': provenance}), TEXT, baseline=False)
    assert not records and errors
    provenance['markdown_sha256'] = 'wrong'
    records, errors, _ = delivered_records(packet({'excerpt': TEXT[start:],
                                                  'provenance': provenance}), TEXT, baseline=False)
    assert not records and errors


def test_only_delivered_records_count_after_owner_budget(tmp_path, monkeypatch):
    from prism_core import filing_report_evidence, report_insight_prefetch

    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'response': {'markdown': TEXT}}))
    blocks = [{'topic': 'financial_quality_valuation', 'excerpt': 'x' * 10000,
               'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'}]
    monkeypatch.setattr(filing_report_evidence, 'filing_blocks', lambda *args: (blocks, []))
    monkeypatch.setattr(report_insight_prefetch, 'topic_blocks', lambda *args: blocks)
    result = evaluate_document({'document_id': 'fixture', 'source_file': str(source), 'facts': []})
    for method in ('baseline', 'treatment'):
        assert result[method]['delivered_record_count'] == 0
        assert result[method]['source_evaluation_unit_count'] == 0
        assert all(size <= 6000 for size in result[method]['section_utf8_bytes'].values())


def test_material_policy_is_explicit_and_uses_same_source_bound_metric(tmp_path):
    text = '## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n9. 매출채권\n매출채권 손실충당금은 100억원입니다.\n'
    source = tmp_path / 'material.json'
    source.write_text(json.dumps({'response': {'markdown': text}}))
    document = {'document_id': 'material', 'source_file': str(source), 'facts': [{
        'id': 'one', 'scope': 'consolidated', 'evidence_groups': [{'needles': ['100억원']}]}]}
    out = evaluate_document(document, treatment='material_v2')['treatment']
    assert out['source_scope_audit']['full_items'] == 1
    assert out['provenance_errors'] == []
    assert all(size <= 6000 for size in out['section_utf8_bytes'].values())
    with pytest.raises(ValueError, match='Unsupported treatment'):
        evaluate_document(document, treatment='anything')
