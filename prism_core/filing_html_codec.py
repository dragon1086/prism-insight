"""Bounded, explicit full-cell HTML serialization; no financial interpretation."""
import json

_SCHEMA = 'html_cell_tuples_v1'
_GRID_SCHEMA = 'html_cell_grid_v1'
_FIELDS = ['row', 'col', 'rowspan', 'colspan', 'text']
_BYTES = 8 * 1024 * 1024
_TEXT_BYTES = 2 * 1024 * 1024
_ERROR = 'INVALID_HTML_TABLE_CODEC'


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _fail():
    raise ValueError(_ERROR)


def _preflight(text):
    """Bound allocation before JSON parsing, including scalar-heavy documents."""
    if not isinstance(text, str) or len(text) > _BYTES or len(text.encode('utf-8')) > _BYTES:
        _fail()
    quoted, escaped = False, False
    depth = containers = separators = unicode_left = 0
    for char in text:
        if quoted:
            if unicode_left:
                if char not in '0123456789abcdefABCDEF':
                    _fail()
                unicode_left -= 1
            elif escaped:
                if char == 'u':
                    unicode_left = 4
                elif char not in '"\\/bfnrt':
                    _fail()
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            elif ord(char) < 32:
                _fail()
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            containers += 1
            if depth > 3 or containers > 12010:
                _fail()
        elif char in ']}':
            depth -= 1
            if depth < 0:
                _fail()
        elif char in ',:':
            separators += 1
            if separators > 75000:
                _fail()
    if quoted or escaped or unicode_left or depth:
        _fail()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _constant(_value):
    _fail()


def _string_bytes(text):
    """JSON string bytes without first allocating escape-expanded output."""
    return 2 + len(text.encode('utf-8')) + sum(
        1 if char in '"\\\b\f\n\r\t' else 5 if ord(char) < 32 else 0 for char in text)


def _validate_shape(shape):
    if (type(shape) is not list or len(shape) != 2
            or any(type(n) is not int or not 1 <= n <= 300 for n in shape)
            or min(shape) > 80 or shape[0] * shape[1] > 12000):
        _fail()


def _validate(shape, cells):
    _validate_shape(shape)
    if type(cells) is not list or not 1 <= len(cells) <= 12000:
        _fail()
    occupied, previous = set(), (-1, -1)
    text_bytes = 0
    # Fixed object keys/delimiters plus each JSON integer/string, before making
    # the complete expanded string. Empty grid slots are not invented.
    expanded_bytes = len('{"cells":[]}') + len(cells) - 1
    fixed_cell_bytes = len('{"row":,"col":,"rowspan":,"colspan":,"text":}')
    for cell in cells:
        if (type(cell) is not list or len(cell) != 5 or any(type(n) is not int for n in cell[:4])
                or not isinstance(cell[4], str)):
            _fail()
        row, col, rowspan, colspan, text = cell
        if (row < 0 or col < 0 or rowspan < 1 or colspan < 1
                or row + rowspan > shape[0] or col + colspan > shape[1]
                or (row, col) <= previous):
            _fail()
        previous = row, col
        for r in range(row, row + rowspan):
            for c in range(col, col + colspan):
                slot = r * shape[1] + c
                if slot in occupied:
                    _fail()
                occupied.add(slot)
        if len(text) > _TEXT_BYTES:
            _fail()
        text_bytes += len(text.encode('utf-8'))
        if text_bytes > _TEXT_BYTES:
            _fail()
        expanded_bytes += fixed_cell_bytes + sum(len(str(n)) for n in cell[:4]) + _string_bytes(text)
        if expanded_bytes > _BYTES:
            _fail()


def _legacy(cells):
    text = _dump({'cells': [dict(zip(_FIELDS, cell)) for cell in cells]})
    if len(text.encode('utf-8')) > _BYTES:
        _fail()
    return text


def _grid_cells(value):
    if set(value) != {'schema', 'shape', 'rows', 'spans'}:
        _fail()
    shape, rows, spans = value['shape'], value['rows'], value['spans']
    # Validate dimensions before allocating the reconstructed cell list/map.
    _validate_shape(shape)
    if (type(rows) is not list or len(rows) != shape[0]
            or any(type(row) is not list or len(row) != shape[1] for row in rows)
            or type(spans) is not list or len(spans) > shape[0] * shape[1]):
        _fail()
    by_anchor, previous = {}, (-1, -1)
    for span in spans:
        if type(span) is not list or len(span) != 4 or any(type(n) is not int for n in span):
            _fail()
        r, c, rs, cs = span
        if (r < 0 or c < 0 or rs < 1 or cs < 1 or r + rs > shape[0] or c + cs > shape[1]
                or (rs, cs) == (1, 1) or (r, c) <= previous or type(rows[r][c]) is not str):
            _fail()
        previous = r, c
        by_anchor[(r, c)] = (rs, cs)
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text is None:
                continue
            if type(text) is not str:
                _fail()
            rs, cs = by_anchor.get((r, c), (1, 1))
            cells.append([r, c, rs, cs, text])
    return cells


def expand_html_table_excerpt(text):
    """Decode only the explicit schema; all failures contain a static code."""
    try:
        _preflight(text)
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
        if type(value) is not dict:
            _fail()
        if value.get('schema') == _GRID_SCHEMA:
            cells = _grid_cells(value)
        elif (set(value) == {'schema', 'cell_fields', 'shape', 'cells'}
                and value['schema'] == _SCHEMA and value['cell_fields'] == _FIELDS):
            cells = value['cells']
        else:
            _fail()
        _validate(value['shape'], cells)
        return _legacy(cells)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(_ERROR) from None


def compact_html_table_excerpt(record):
    """Return a smaller JSON-string representation, or the unchanged excerpt."""
    original = record.get('text', '')
    try:
        table = record.get('table')
        if (record.get('kind') != 'table' or 'layout_role' in record or 'context_incomplete' in record
                or type(table) is not dict or table.get('status') != 'COMPLETE'
                or not isinstance(original, str) or len(original) > _BYTES
                or len(original.encode('utf-8')) > _BYTES
                or type(table.get('cells')) is not list or not 1 <= len(table['cells']) <= 12000):
            return original
        cells = [[cell[field] for field in _FIELDS] for cell in table['cells']]
        shape = [table.get('row_count'), table.get('column_count')]
        _validate(shape, cells)
        if _legacy(cells) != original:
            return original
        value = {'schema': _SCHEMA, 'cell_fields': _FIELDS, 'shape': shape, 'cells': []}
        encoded_bytes = len(_dump(value).encode('utf-8')) + len(cells) - 1 + sum(
            6 + sum(len(str(n)) for n in cell[:4]) + _string_bytes(cell[4]) for cell in cells)
        if encoded_bytes > _BYTES:
            return original
        value['cells'] = cells
        encoded = _dump(value)
        if len(encoded.encode('utf-8')) <= _BYTES and len(_dump(encoded).encode('utf-8')) < len(_dump(original).encode('utf-8')):
            return encoded
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        pass
    return original


def compact_html_grid_excerpt(record):
    """Encode all anchors; null means absent/covered, never an empty text cell."""
    original = record.get('text', '')
    try:
        table = record.get('table')
        if (record.get('kind') != 'table' or 'layout_role' in record or 'context_incomplete' in record
                or type(table) is not dict or table.get('status') != 'COMPLETE'
                or not isinstance(original, str) or len(original) > _BYTES
                or len(original.encode('utf-8')) > _BYTES
                or type(table.get('cells')) is not list or not 1 <= len(table['cells']) <= 12000):
            return original
        cells = [[cell[field] for field in _FIELDS] for cell in table['cells']]
        shape = [table.get('row_count'), table.get('column_count')]
        _validate(shape, cells)
        if _legacy(cells) != original:
            return original
        rows = [[None] * shape[1] for _ in range(shape[0])]
        spans = []
        for r, c, rs, cs, text in cells:
            rows[r][c] = text
            if (rs, cs) != (1, 1):
                spans.append([r, c, rs, cs])
        value = {'schema': _GRID_SCHEMA, 'shape': shape, 'rows': [], 'spans': []}
        encoded_bytes = len(_dump(value).encode('utf-8')) + len(rows) - 1 + sum(
            2 + len(row) - 1 + sum(4 if text is None else _string_bytes(text) for text in row)
            for row in rows)
        if spans:
            encoded_bytes += len(spans) - 1 + sum(len(_dump(span)) for span in spans)
        if encoded_bytes > _BYTES:
            return original
        value.update(rows=rows, spans=spans)
        encoded = _dump(value)
        if len(encoded.encode('utf-8')) <= _BYTES and len(_dump(encoded).encode('utf-8')) < len(_dump(original).encode('utf-8')):
            return encoded
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        pass
    return original
