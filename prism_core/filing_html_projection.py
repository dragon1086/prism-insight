"""Conservative partial-column views with original geometry and explicit coverage.

The single development retrieval theme below is polarity-neutral and fixed; it
is not adjusted to fit a packet. Shared row labels never seed column selection.
Unsupported header associations or cross-column language refuse the whole view.
"""
import json
import math
import re
import time

from .filing_html_codec import (
    _BYTES,
    _FIELDS,
    _TEXT_BYTES,
    _constant,
    _dump,
    _legacy,
    _object,
    _preflight,
    _validate,
)

_SCHEMA = 'html_column_view_v1'
_ERROR = 'INVALID_HTML_COLUMN_VIEW'
_THEME = re.compile(
    r'소송|분쟁|법적|법원|판결|중재|피고|원고|과징금|재무약정|약정\s*위반|면제|조기상환|'
    r'\b(?:lawsuits?|litigation|legal|courts?|judg(?:e)?ments?|arbitration|'
    r'defendants?|plaintiffs?|penalt(?:y|ies)|covenants?|waivers?|'
    r'disputes?|early\s+repayment|accelerat(?:ed|ion)\s+(?:repayment|debt))\b', re.IGNORECASE)
_CROSS = re.compile(
    r'(?:다른|타|해당|위|아래)\s*열|(?:제?\s*\d+|[A-Za-z])\s*열|'
    r'상기|하기|전술|후술|합산|공통|양사|공동|'
    r'\b(?:other|above|below|preceding|following|adjacent|respective)\s+'
    r'(?:columns?|amounts?|items?|cases?)\b|\bcolumns?\s+(?:[a-z]|\d+)\b|'
    r'\b(?:cross.reference|combined|aggregate|respectively|both)\b', re.IGNORECASE)
# Without explicit associations a marker may refer into a discarded column.
# Refuse even paired markers rather than guess scope or claim note closure.
_NOTE_REFERENCE = re.compile(
    r'주(?:석)?\s*\(?\s*\d|\b(?:footnotes?|notes?)\s*\(?\s*\d|'
    r'[*＊∗﹡※†‡¹²³⁰⁴-⁹①-⑳]', re.IGNORECASE)


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError


def _validate_view(value):
    if (type(value) is not dict or set(value) != {
            'schema', 'cell_fields', 'shape', 'selected_columns', 'row_label_columns', 'cells'}
            or value['schema'] != _SCHEMA or value['cell_fields'] != _FIELDS):
        raise ValueError(_ERROR)
    shape, cells = value['shape'], value['cells']
    _validate(shape, cells)
    selected, labels = value['selected_columns'], value['row_label_columns']
    if (type(labels) is not int or not 1 <= labels < shape[1]
            or type(selected) is not list or not labels < len(selected) < shape[1]
            or any(type(c) is not int or not 0 <= c < shape[1] for c in selected)
            or selected != sorted(set(selected)) or selected[:labels] != list(range(labels))):
        raise ValueError(_ERROR)
    mask = set(selected)
    coverage = 0
    for row, col, rowspan, colspan, _text in cells:
        if (any(c not in mask for c in range(col, col + colspan))
                or col < labels < col + colspan):
            raise ValueError(_ERROR)
        coverage += rowspan * colspan
    # _validate already proves disjointness and bounds. Equal area therefore
    # proves every selected coordinate, every row and every label is present.
    if coverage != shape[0] * len(selected):
        raise ValueError(_ERROR)


def expand_html_column_excerpt(text):
    """Strict bounded partial schema decoder; never interpreted as a full table."""
    try:
        _preflight(text)
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
        _validate_view(value)
        return _legacy(value['cells'])
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(_ERROR) from None


def encode_html_column_view(projected):
    """Encode only a declared partial view; preserve coordinates and cell text."""
    try:
        if projected.get('projected') is not True or projected.get('projection_kind') != _SCHEMA:
            raise ValueError(_ERROR)
        table = projected['table']
        if (type(table) is not dict or table.get('status') != 'COMPLETE'
                or type(table.get('cells')) is not list or not 1 <= len(table['cells']) <= 12000
                or type(projected.get('original_columns')) is not int
                or projected['original_columns'] != table.get('column_count')):
            raise ValueError(_ERROR)
        value = {'schema': _SCHEMA, 'cell_fields': _FIELDS,
                 'shape': [table['row_count'], table['column_count']],
                 'selected_columns': projected['selected_columns'],
                 'row_label_columns': projected['row_label_columns'],
                 'cells': [[c[f] for f in _FIELDS] for c in table['cells']]}
        _validate_view(value)
        if _legacy(value['cells']) != projected['text']:
            raise ValueError(_ERROR)
        encoded = _dump(value)
        if len(encoded.encode('utf-8')) > _BYTES:
            raise ValueError(_ERROR)
        return encoded
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(_ERROR) from None


def project_html_columns(record, *, deadline=None):
    """Return one safe view or a static refusal plus bounded work diagnostics.

    Grammar: complete dense grid, contiguous leading all-th rows, a blank
    leading corner uniquely defining row-label width, and no span crossing the
    selected mask or label boundary. Context is retained, never used as a seed.
    There are two source-cell passes, no iterative closure or powerset search.
    """
    diagnostics = {'code': 'UNSUPPORTED_COLUMN_VIEW', 'source_cell_inspections': 0,
                   'column_visits': 0, 'dependency_incidence_visits': 0,
                   'selected_columns': [], 'omitted_columns': []}

    def refuse(code):
        diagnostics['code'] = code
        return None, diagnostics

    try:
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            return refuse('INVALID_PROJECTION_DEADLINE')
        _check_deadline(deadline)
        table = record.get('table')
        if (record.get('kind') != 'table' or record.get('projected')
                or 'layout_role' in record or 'context_incomplete' in record
                or type(table) is not dict or table.get('status') != 'COMPLETE'
                or type(table.get('cells')) is not list
                or not 1 <= len(table['cells']) <= 12000):
            return refuse('UNSUPPORTED_COLUMN_VIEW')
        # Parsed prose context is separate from table cells. Retaining its text
        # does not prove that the condition it references is also retained.
        context_bytes = 0
        for field in ('context_before', 'footnotes', 'caption', 'heading_path', 'section_path'):
            context = record.get(field, '')
            values = context if type(context) is list else [context]
            if len(values) > 12000:
                return refuse('INVALID_COLUMN_VIEW_CONTEXT')
            for text in values:
                _check_deadline(deadline)
                if not isinstance(text, str) or len(text) > _TEXT_BYTES:
                    return refuse('INVALID_COLUMN_VIEW_CONTEXT')
                context_bytes += len(text.encode('utf-8'))
                if context_bytes > _TEXT_BYTES:
                    return refuse('INVALID_COLUMN_VIEW_CONTEXT')
                if _NOTE_REFERENCE.search(text):
                    return refuse('UNRESOLVED_NOTE_REFERENCE')
                if _CROSS.search(text):
                    return refuse('AMBIGUOUS_CROSS_COLUMN_REFERENCE')
        shape = [table.get('row_count'), table.get('column_count')]
        tuples, tags = [], []
        for cell in table['cells']:
            _check_deadline(deadline)
            diagnostics['source_cell_inspections'] += 1
            tuples.append([cell[f] for f in _FIELDS])
            tags.append(cell.get('tag'))
        _validate(shape, tuples)
        _check_deadline(deadline)
        if _legacy(tuples) != record.get('text'):
            return refuse('NONCANONICAL_SOURCE')
        rows, columns = shape
        grid = [[None] * columns for _ in range(rows)]
        for index, (row, col, rowspan, colspan, text) in enumerate(tuples):
            _check_deadline(deadline)
            if _NOTE_REFERENCE.search(text):
                return refuse('UNRESOLVED_NOTE_REFERENCE')
            if _CROSS.search(text):
                return refuse('AMBIGUOUS_CROSS_COLUMN_REFERENCE')
            for r in range(row, row + rowspan):
                for c in range(col, col + colspan):
                    grid[r][c] = index
        if any(index is None for row in grid for index in row):
            return refuse('INCOMPLETE_GRID')
        header_rows = 0
        for row in grid:
            if not all(tags[i] == 'th' for i in row):
                break
            header_rows += 1
        if not 0 < header_rows < rows:
            return refuse('UNSUPPORTED_HEADER_GRAMMAR')
        labels = 0
        for index in grid[0]:
            if tuples[index][4].strip():
                break
            labels += 1
        if not 0 < labels < columns:
            return refuse('AMBIGUOUS_LABEL_BOUNDARY')
        for row in grid[:header_rows]:
            if any(tuples[row[c]][4].strip() for c in range(labels)):
                return refuse('AMBIGUOUS_LABEL_BOUNDARY')
        selected = set(range(labels))
        matches = [_THEME.search(text) is not None and not (row < header_rows and colspan > 1)
                   for row, _col, _rowspan, colspan, text in tuples]
        # One visit per data column. Grid incidences are bounded by 12,000;
        # labels and shared context/headings cannot seed an unrelated column.
        for c in range(labels, columns):
            _check_deadline(deadline)
            diagnostics['column_visits'] += 1
            if not any(tuples[grid[r][c]][4].strip() for r in range(header_rows)):
                return refuse('UNSUPPORTED_HEADER_GRAMMAR')
            for r in range(rows):
                diagnostics['dependency_incidence_visits'] += 1
                index = grid[r][c]
                if matches[index]:
                    selected.add(c)
        if len(selected) == labels:
            return refuse('NO_THEME_COLUMNS')
        if len(selected) == columns:
            return refuse('ALL_COLUMNS_REQUIRED')
        retained, retained_tuples = [], []
        for cell, values in zip(table['cells'], tuples):
            _check_deadline(deadline)
            diagnostics['source_cell_inspections'] += 1
            row, col, rowspan, colspan, _text = values
            if col < labels < col + colspan or row < header_rows < row + rowspan:
                return refuse('CROSS_BOUNDARY_SPAN')
            included = sum(c in selected for c in range(col, col + colspan))
            if included and included != colspan:
                return refuse('CROSS_COLUMN_SPAN')
            if included:
                retained.append(dict(cell))
                retained_tuples.append(values)
        projected_table = dict(table, cells=retained)
        # Do not retain the full source grid in a partial record.
        projected_table.pop('grid', None)
        projected = dict(record, table=projected_table, text=_legacy(retained_tuples),
                         projected=True, projection_kind=_SCHEMA,
                         selected_columns=sorted(selected), row_label_columns=labels,
                         original_columns=columns)
        _check_deadline(deadline)
        if len(encode_html_column_view(projected).encode('utf-8')) >= len(record['text'].encode('utf-8')):
            return refuse('NO_SMALLER_SAFE_VIEW')
        _check_deadline(deadline)
        diagnostics.update(code='PROJECTED', selected_columns=sorted(selected),
                           omitted_columns=[c for c in range(columns) if c not in selected])
        return projected, diagnostics
    except TimeoutError:
        return refuse('PROJECTION_DEADLINE')
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, OverflowError):
        return refuse('INVALID_COLUMN_VIEW_SOURCE')
