"""Balance-sheet totals read from DART statements for the filing chapter chart.

Only 자산총계/부채총계/자본총계 are read: every issuer (banks and insurers
included) reports them under these exact names, with one column per dated
period (two in interim reports, three in the annual report).
Any ambiguity or failed check returns None instead of a guessed value.
"""
import json
import re

from prism_core.dart_source_table_evidence import unpack_readable_units
from prism_core.dart_source_tree_catalog import decode_table

_UNITS = {'원': 1, '천원': 1_000, '백만원': 1_000_000, '억원': 100_000_000}
_ROWS = {'자산총계': 'assets', '부채총계': 'liabilities', '자본총계': 'equity'}
_TOTALS = {'부채와자본총계', '자본과부채총계'}
_DATE = re.compile(r'(\d{4})\.(\d{2})\.(\d{2})현재')
_UNIT = re.compile(r'단위[:：](원|천원|백만원|억원)\)?$')


def _norm(text):
    return re.sub(r'\s+', '', text or '')


def _amount(text):
    value = _norm(text).replace(',', '')
    match = re.fullmatch(r'\((\d+)\)|(-?\d+)', value)
    if not match:
        return None
    return -int(match.group(1)) if match.group(1) else int(match.group(2))


def _statement_tables(catalog):
    """(title table, body table) of the single statement of financial position."""
    tables = [decode_table(u['payload'], u['path']) for u in unpack_readable_units(catalog) if u['kind'] == 'table']
    found = []
    for index, table in enumerate(tables[:-1]):
        texts = [c['text'] for c in table['cells']]
        if table['column_count'] == 1 and texts and '재무상태표' in _norm(texts[0]):
            found.append((table, tables[index + 1]))
    return found[0] if len(found) == 1 else (None, None)


def _read(group):
    """Dated columns of totals in KRW, newest first, or None."""
    title, body = _statement_tables(group['catalog'])
    if title is None:
        return None
    lines = [_norm(c['text']) for c in title['cells']]
    dated = [(line, _DATE.search(line)) for line in lines]
    dated = [(line, match) for line, match in dated if match]
    units = [m.group(1) for m in (_UNIT.search(line) for line in lines) if m]
    periods = body['column_count'] - 1
    if periods not in (2, 3) or len(dated) != periods or len(units) != 1:
        return None
    cells = {(c['row'], c['col']): c for c in body['cells']}
    headers = [cells.get((0, col)) for col in range(1, periods + 1)]
    # Column headers must name the same periods as the dated title lines, in order.
    for header, (line, _) in zip(headers, dated):
        if not header or header['tag'] != 'th' or not _norm(header['text']) or _norm(header['text']) not in line:
            return None
    rows = {}
    for cell in body['cells']:
        label = _norm(cell['text'])
        if cell['col'] == 0 and (label in _ROWS or label in _TOTALS):
            if label in rows:
                return None
            rows[label] = cell['row']
    if set(_ROWS) - set(rows):
        return None
    scale = _UNITS[units[0]]
    columns = []
    for col, (_, date) in zip(range(1, periods + 1), dated):
        values = {}
        for label, row in rows.items():
            cell = cells.get((row, col))
            amount = _amount(cell['text']) if cell and cell['colspan'] == 1 else None
            if amount is None:
                return None
            values[_ROWS.get(label, 'total')] = amount * scale
        # The statement must balance exactly in its own unit.
        if values['assets'] != values['liabilities'] + values['equity']:
            return None
        if 'total' in values and values.pop('total') != values['assets']:
            return None
        columns.append({'date': '-'.join(date.groups()), **values})
    dates = [c['date'] for c in columns]
    return columns if dates == sorted(dates, reverse=True) and len(set(dates)) == len(dates) else None


def balance_sheet_series(packet):
    """Chronological balance-sheet totals from the finance writer's statements.

    The latest filing supplies the current and prior year-end points. The
    annual supplement adds its earlier year-ends only when its overlapping
    year-end matches the latest filing exactly (no restatement mixing).
    """
    try:
        context = json.loads((packet.get('contexts') or {}).get('finance', ''))
    except (TypeError, ValueError):
        return None
    groups = {}
    for group in context.get('sources', []):
        filing = group['source']['filing']
        if filing.get('section') == 'financial_statements' and filing.get('role') in ('primary', 'annual_supplement'):
            if filing['role'] in groups:
                return None
            groups[filing['role']] = group
    if 'primary' not in groups:
        return None
    primary_filing = groups['primary']['source']['filing']
    latest = _read(groups['primary'])
    if not latest or len(latest) != 2:
        return None
    points = [latest[1], latest[0]]
    sources = [groups['primary']['source']]
    annual = groups.get('annual_supplement')
    if annual and annual['source']['filing'].get('scope') == primary_filing.get('scope'):
        older = _read(annual)
        if older and older[0] == points[0]:
            points = list(reversed(older[1:])) + points
            sources.append(annual['source'])
    return {'points': points, 'scope': primary_filing.get('scope'),
            'source_urls': [s.get('url') for s in sources]}
