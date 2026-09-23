"""Deterministic batch diagnostics, never trading signals or order authority."""
import json
from pathlib import Path


def result_fingerprint(path):
    try:
        stat = Path(path).stat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def fresh_result_metadata(path, previous_fingerprint):
    """Do not diagnose a failed run using yesterday's or an unchanged result."""
    current = result_fingerprint(path)
    if current is None or current == previous_fingerprint or current[2] > 4_000_000:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        metadata = data.get('metadata') if isinstance(data, dict) else None
        return metadata if isinstance(metadata, dict) else None
    except (OSError, ValueError):
        return None


def snapshot_coverage(current, previous, requested, current_date, previous_date):
    """Coverage only: never a new percentage threshold or eligibility filter."""
    universe = set(map(str, requested))
    now = set(map(str, current.index)) & universe
    prior = set(map(str, previous.index)) & universe
    common = now & prior
    status = 'COMPLETE' if universe and len(common) == len(universe) else 'PARTIAL' if common else 'UNAVAILABLE'
    return {'contract': 'paired_snapshot_coverage_v1', 'status': status,
            'requested_count': len(universe), 'current_count': len(now),
            'previous_count': len(prior), 'comparable_count': len(common),
            'missing_count': len(universe - common), 'current_date': current_date,
            'previous_date': previous_date,
            'current_provider': current.attrs.get('snapshot_coverage', {}),
            'previous_provider': previous.attrs.get('snapshot_coverage', {})}


def batch_status_message(market, mode, trade_date, stage, metadata=None, language='ko',
                         selected_count=0, report_count=0, pdf_count=0):
    """Public text includes no raw exception, credentials, or false completion."""
    if market not in {'KR', 'US'} or mode not in {'morning', 'afternoon'}:
        raise ValueError('Invalid batch identity')
    if stage not in {'no_candidates', 'report_failed', 'pdf_failed', 'pipeline_failed'}:
        raise ValueError('Invalid batch stage')
    metadata = metadata if isinstance(metadata, dict) else {}
    coverage = metadata.get('snapshot_coverage', {})
    coverage = coverage if isinstance(coverage, dict) else {}
    partial = coverage.get('status') in {'PARTIAL', 'UNAVAILABLE'}
    # Legacy saved runs may expose only participation intersection counts.
    legacy = metadata.get('market_participation', {})
    if not coverage and isinstance(legacy, dict):
        total, valid = legacy.get('universe_count'), legacy.get('valid_count')
        if type(total) is int and type(valid) is int and 0 <= valid < total:
            partial = True
            coverage = {'requested_count': total, 'comparable_count': valid}
    errors = metadata.get('trigger_errors', [])
    ko = language == 'ko'
    title = ('한국' if market == 'KR' else '미국') + (' 오전' if mode == 'morning' else ' 오후') if ko else f'{market} {mode}'
    lines = [f'📋 {title} 배치 실행 안내' if ko else f'📋 {title} batch status', f'📅 {trade_date}']
    if stage == 'no_candidates':
        if partial:
            lines.append('⚠️ 비교 시세가 충분히 수집되지 않아 신규 분석 대상을 선정하지 못했습니다. 정상적인 신호 없음으로 해석하지 마세요.' if ko else
                         '⚠️ Incomplete comparison prices prevented candidate selection. This is not a confirmed no-signal result.')
        elif errors or not metadata:
            lines.append('⚠️ 선정 단계의 오류 또는 결과 미확인으로 신규 분석을 진행하지 못했습니다.' if ko else
                         '⚠️ Screening failed or its result could not be verified; no new analysis was started.')
        else:
            lines.append('이번 실행에서는 신규 분석 대상으로 선정된 종목이 없습니다.' if ko else
                         'No stocks were selected for new analysis in this run.')
        names = (('requested_count', '요청 종목', 'Requested'), ('current_count', '현재 시세', 'Current prices'),
                 ('previous_count', '전일 시세', 'Previous prices'), ('comparable_count', '비교 가능', 'Comparable'))
        counts = [f'{kr if ko else en}: {coverage[key]}' for key, kr, en in names
                  if type(coverage.get(key)) is int and coverage[key] >= 0]
        if counts:
            lines.append(' / '.join(counts))
    elif stage == 'report_failed':
        lines.append('⚠️ 선정 종목의 보고서 생성이 모두 실패해 후속 단계를 중단했습니다.' if ko else
                     '⚠️ All selected-stock reports failed; downstream stages were stopped.')
    elif stage == 'pdf_failed':
        lines.append('⚠️ PDF 생성이 모두 실패해 발송·보고서 기반 매매 검토를 진행하지 않았습니다.' if ko else
                     '⚠️ All PDFs failed; delivery and report-based trading review were not run.')
    else:
        lines.append('⚠️ 배치 처리 중 오류가 발생했습니다. 전체 완료로 처리하지 않았습니다.' if ko else
                     '⚠️ The batch encountered an error and was not marked fully complete.')
    if stage != 'no_candidates':
        lines.append(f'선정 {selected_count} / 보고서 {report_count} / PDF {pdf_count}' if ko else
                     f'Selected {selected_count} / Reports {report_count} / PDFs {pdf_count}')
    lines.append('이 안내는 매수·매도 신호가 아니며, 기존 독립 위험관리 일정은 변경하지 않습니다.' if ko else
                 'This notice is not a buy/sell signal and does not change independent risk-management schedules.')
    return '\n'.join(lines)
