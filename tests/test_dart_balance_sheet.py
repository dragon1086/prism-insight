"""Balance-sheet chart data is read only from exact, self-checking DART totals."""
import json

import pytest

from prism_core.dart_balance_sheet import balance_sheet_series
from prism_core.dart_source_table_evidence import pack_readable_units
from prism_core.dart_source_tree_catalog import build_catalog


def _statement(periods, rows, unit='백만원', title='연결 재무상태표'):
    head = ''.join(f'<tr><td>{label} {date} 현재</td></tr>' for label, date in periods)
    header = '<tr><th></th>' + ''.join(f'<th>{label}</th>' for label, _ in periods) + '</tr>'
    body = ''.join('<tr><td>' + name + '</td>' + ''.join(f'<td>{v}</td>' for v in values) + '</tr>'
                   for name, values in rows)
    return (f'<p>2-1. {title}</p><table><tr><td>{title}</td></tr>{head}<tr><td>(단위 : {unit})</td></tr></table>'
            f'<table><thead>{header}</thead><tbody>{body}</tbody></table>')


def _group(role, html):
    return {'source': {'source_id': role, 'url': 'https://dart.fss.or.kr/report/viewer.do?rcpNo=1',
                       'filing': {'role': role, 'section': 'financial_statements', 'scope': 'consolidated'}},
            'catalog': pack_readable_units(build_catalog(html)['units'])}


INTERIM = [('제 32 기 반기말', '2026.06.30'), ('제 31 기말', '2025.12.31')]
ANNUAL = [('제 31 기', '2025.12.31'), ('제 30 기', '2024.12.31'), ('제 29 기', '2023.12.31')]
INTERIM_ROWS = [('유동자산', ['50', '40']), ('자산총계', ['300', '250']), ('부채총계', ['(100)', '90']),
                ('자본총계', ['400', '160']), ('부채와자본총계', ['300', '250'])]
ANNUAL_ROWS = [('자산총계', ['250', '220', '200']), ('부채총계', ['90', '80', '70']),
               ('자본총계', ['160', '140', '130'])]


def _packet(*groups):
    return {'contexts': {'finance': json.dumps({'sources': list(groups)}, ensure_ascii=False)}}


def test_interim_and_matching_annual_year_ends_form_one_chronological_series():
    series = balance_sheet_series(_packet(_group('primary', _statement(INTERIM, INTERIM_ROWS)),
                                          _group('annual_supplement', _statement(ANNUAL, ANNUAL_ROWS))))
    assert [p['date'] for p in series['points']] == ['2023-12-31', '2024-12-31', '2025-12-31', '2026-06-30']
    assert series['points'][-1] == {'date': '2026-06-30', 'assets': 300_000_000,
                                    'liabilities': -100_000_000, 'equity': 400_000_000}
    assert series['scope'] == 'consolidated' and len(series['source_urls']) == 2


def test_restated_overlap_keeps_only_the_latest_filing():
    restated = [('자산총계', ['251', '220', '200']), ('부채총계', ['91', '80', '70']),
                ('자본총계', ['160', '140', '130'])]
    series = balance_sheet_series(_packet(_group('primary', _statement(INTERIM, INTERIM_ROWS)),
                                          _group('annual_supplement', _statement(ANNUAL, restated))))
    assert [p['date'] for p in series['points']] == ['2025-12-31', '2026-06-30']


@pytest.mark.parametrize('rows,kwargs', [
    ([('자산총계', ['301', '250']), ('부채총계', ['(100)', '90']), ('자본총계', ['400', '160'])], {}),
    (INTERIM_ROWS + [('자산총계', ['300', '250'])], {}),
    ([('자산총계', ['300', '250']), ('부채총계', ['-', '90']), ('자본총계', ['400', '160'])], {}),
    ([('자산총계', ['300', '250']), ('자본총계', ['400', '160'])], {}),
    (INTERIM_ROWS, {'unit': 'USD'}),
    ([('자산총계', ['300', '250']), ('부채총계', ['(100)', '90']), ('자본총계', ['400', '160']),
      ('부채와자본총계', ['299', '250'])], {}),
])
def test_any_unverifiable_total_omits_the_chart(rows, kwargs):
    assert balance_sheet_series(_packet(_group('primary', _statement(INTERIM, rows, **kwargs)))) is None


def test_header_must_name_the_dated_period():
    wrong = [('제 33 기 반기말', '2026.06.30'), ('제 31 기말', '2025.12.31')]
    html = _statement(INTERIM, INTERIM_ROWS).replace('<th>제 32 기 반기말</th>', '<th>제 33 기 반기말</th>')
    assert balance_sheet_series(_packet(_group('primary', html))) is None
    assert balance_sheet_series(_packet(_group('primary', _statement(wrong, INTERIM_ROWS)))) is not None


def test_missing_or_ambiguous_statements_return_none():
    assert balance_sheet_series({'contexts': {}}) is None
    twice = _statement(INTERIM, INTERIM_ROWS) + _statement(INTERIM, INTERIM_ROWS, title='별도 재무상태표')
    assert balance_sheet_series(_packet(_group('primary', twice))) is None


def test_chart_renders_and_skips_ratio_for_non_positive_equity():
    from cores.stock_chart import create_dart_balance_sheet_chart
    series = balance_sheet_series(_packet(_group('primary', _statement(INTERIM, INTERIM_ROWS)),
                                          _group('annual_supplement', _statement(ANNUAL, ANNUAL_ROWS))))
    fig = create_dart_balance_sheet_chart('035720', '카카오', series=series)
    assert len(fig.axes) == 2
    series['points'][0]['equity'] = 0
    assert len(create_dart_balance_sheet_chart('035720', '카카오', series=series).axes) == 1
    assert create_dart_balance_sheet_chart('035720', series={'points': series['points'][:1]}) is None


def test_chart_closes_finance_subsection_inside_the_chapter():
    from cores.analysis import _with_balance_chart
    from cores.dart_deep_analysis import CHAPTER_END, CHAPTER_START
    chapter = f'{CHAPTER_START}\n\n## 5. DART\n\n### 5-1. 재무\n본문\n\n### 5-2. 사업\n본문\n\n{CHAPTER_END}'
    out = _with_balance_chart(chapter, '<img src="x"/>', 'ko')
    assert out.index('### 5-1.') < out.index('#### 재무구조 추이') < out.index('<img') < out.index('### 5-2.')
    assert out.count(CHAPTER_START) == 1 and out.rstrip().endswith(CHAPTER_END)
    finance_only = f'{CHAPTER_START}\n\n### 5-1. 재무\n본문\n\n{CHAPTER_END}'
    appended = _with_balance_chart(finance_only, '<img/>', 'ko')
    assert appended.index('본문') < appended.index('<img/>') < appended.index(CHAPTER_END)
    assert _with_balance_chart(chapter, None, 'ko') == chapter


def test_financial_chart_uses_equity_to_assets_instead_of_debt_ratio():
    from cores.stock_chart import create_dart_balance_sheet_chart
    series = balance_sheet_series(_packet(_group('primary', _statement(INTERIM, INTERIM_ROWS)),
                                          _group('annual_supplement', _statement(ANNUAL, ANNUAL_ROWS))))
    fig = create_dart_balance_sheet_chart('105560', 'KB금융', series=series, ratio='equity_to_assets')
    labels = [line.get_label() for ax in fig.axes for line in ax.get_lines()]
    assert 'Equity-to-assets (%)' in labels and 'Debt-to-equity (%)' not in labels
