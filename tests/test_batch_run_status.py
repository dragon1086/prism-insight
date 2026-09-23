import json

import pandas as pd

from prism_core.batch_run_status import (
    batch_status_message,
    fresh_result_metadata,
    result_fingerprint,
    snapshot_coverage,
)


def test_sparse_previous_is_partial_not_no_signal():
    current = pd.DataFrame(index=['AAA', 'BBB', 'CCC'])
    previous = pd.DataFrame(index=['AAA'])
    coverage = snapshot_coverage(current, previous, current.index, '20260923', '20260922')
    assert coverage['status'] == 'PARTIAL' and coverage['comparable_count'] == 1
    text = batch_status_message('US', 'afternoon', '20260923', 'no_candidates', {'snapshot_coverage': coverage})
    assert '정상적인 신호 없음으로 해석하지 마세요' in text and '전일 시세: 1' in text


def test_legacy_incident_counts_not_mislabeled_as_normal_empty():
    text = batch_status_message('US', 'afternoon', '20260923', 'no_candidates', {
        'market_participation': {'status': 'AVAILABLE', 'universe_count': 518, 'valid_count': 1}})
    assert '요청 종목: 518' in text and '비교 가능: 1' in text and '충분히 수집되지 않아' in text


def test_empty_complete_and_error_are_distinct():
    normal = batch_status_message('KR', 'morning', '20260923', 'no_candidates', {'trade_date': '20260923'})
    failed = batch_status_message('KR', 'morning', '20260923', 'no_candidates')
    assert '선정된 종목이 없습니다' in normal and '오류 또는 결과 미확인' in failed
    assert '모두 실패' in batch_status_message('US', 'afternoon', '20260923', 'pdf_failed')


def test_only_new_result_is_read(tmp_path):
    path = tmp_path / 'result.json'
    assert fresh_result_metadata(path, None) is None
    path.write_text(json.dumps({'metadata': {'trade_date': '20260923'}}))
    stamp = result_fingerprint(path)
    assert fresh_result_metadata(path, stamp) is None
    assert fresh_result_metadata(path, None) == {'trade_date': '20260923'}
    path.write_text('invalid')
    assert fresh_result_metadata(path, stamp) is None
