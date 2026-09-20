"""Bounded HTML table geometry without financial-value interpretation.

Indices are zero-based; grid entries point to original cells, not fabricated
copies. Unsupported geometry returns no partial grid. This is a DOM parser,
not a browser: external CSS and rendered layout are not evaluated.
"""

import re

from lxml import etree

_HIDDEN_STYLE = re.compile(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\s*(?:!important\s*)?(?:;|$)', re.IGNORECASE)
_MAX_NODES = 48000
_MAX_TEXT = 2 * 1024 * 1024


def _visible_text(element):
    pieces = []
    stack = [element]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            pieces.append(item)
            continue
        if not isinstance(item.tag, str) or item.tag.lower() in {'script', 'style'}:
            continue
        if item.tag in {'br', 'p', 'div', 'li'}:
            pieces.append(' ')
            stack.append(' ')
        if item.text:
            pieces.append(item.text)
        for child in reversed(item):
            if child.tail:
                stack.append(child.tail)
            stack.append(child)
    return ' '.join(''.join(pieces).split())


def parse_html_table(table_element, *, max_rows=300, max_columns=300, max_cells=12000,
                     path_resolver=None):
    """Return source cells and their merged-cell grid, or an explicit failure.

    ``max_cells`` bounds the rectangular output grid as well as origin cells.
    At least one axis must be at most 80; source coordinates are never transposed.
    Limits may be lowered, not raised above the defaults. Text is whitespace
    normalized only: empty strings, dashes, units and period labels stay text.
    A streaming caller may inject original-document paths captured before
    pruning siblings; standalone calls retain the existing lxml path behavior.
    """
    for value, ceiling in ((max_rows, 300), (max_columns, 300), (max_cells, 12000)):
        if type(value) is not int or not 0 < value <= ceiling:
            raise ValueError('table limits must be positive integers within hard bounds')
    result = {'status': 'UNSUPPORTED', 'errors': [], 'cells': [], 'grid': [],
              'row_count': 0, 'column_count': 0, 'source_path': None}

    def fail(error, status='UNSUPPORTED'):
        result['status'] = status
        result['errors'] = [error]
        return result

    if not isinstance(table_element, etree._Element) or table_element.tag != 'table':
        return fail('NOT_A_TABLE')
    resolve_path = path_resolver or table_element.getroottree().getpath
    result['source_path'] = resolve_path(table_element)
    rows = []
    text_size = 0
    for index, node in enumerate(table_element.iter()):
        if index >= _MAX_NODES:
            return fail('NODE_LIMIT', 'LIMIT_EXCEEDED')
        text_size += len(node.text or '') + len(node.tail or '')
        if text_size > _MAX_TEXT:
            return fail('TEXT_LIMIT', 'LIMIT_EXCEEDED')
        if not isinstance(node.tag, str):
            continue
        if node.tag.lower() in {'noscript', 'iframe', 'object', 'embed'}:
            return fail('ACTIVE_OR_ALTERNATE_CONTENT')
        text_size += sum(len(key) + len(value) for key, value in node.attrib.items())
        if text_size > _MAX_TEXT:
            return fail('TEXT_LIMIT', 'LIMIT_EXCEEDED')
        for depth, parent in enumerate(node.iterancestors()):
            if parent is table_element:
                break
            if depth >= 64:
                return fail('DEPTH_LIMIT', 'LIMIT_EXCEEDED')
            if node.tag == 'tr' and parent.tag == 'tr':
                return fail('NESTED_ROW')
        if node is not table_element and node.tag == 'table':
            return fail('NESTED_TABLE')
        if ('hidden' in node.attrib or node.get('aria-hidden', '').lower().strip() == 'true'
                or _HIDDEN_STYLE.search(node.get('style', ''))):
            return fail('HIDDEN_CONTENT')
        if node.tag == 'tr':
            rows.append(node)
            if len(rows) > max_rows:
                return fail('ROW_LIMIT', 'LIMIT_EXCEEDED')
        if node.tag in {'td', 'th'} and node.getparent().tag != 'tr':
            return fail('CELL_OUTSIDE_ROW')
    if not rows:
        return fail('NO_ROWS')
    grid = [[] for _ in rows]
    cells = []
    width = 0
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row:
            if cell.tag not in {'td', 'th'}:
                continue
            while column < len(grid[row_index]) and grid[row_index][column] is not None:
                column += 1
            spans = []
            for attribute, ceiling in (('rowspan', max_rows), ('colspan', max_columns)):
                raw = cell.get(attribute, '1').strip()
                if not raw or not raw.isascii() or not raw.isdigit():
                    return fail('INVALID_SPAN')
                if len(raw) > 6:
                    return fail('SPAN_LIMIT', 'LIMIT_EXCEEDED')
                value = int(raw)
                if value < 1:
                    return fail('INVALID_SPAN')
                if value > ceiling:
                    return fail('SPAN_LIMIT', 'LIMIT_EXCEEDED')
                spans.append(value)
            rowspan, colspan = spans
            if row_index + rowspan > len(rows):
                return fail('ROWSPAN_BEYOND_ROWS')
            end_column = column + colspan
            width = max(width, end_column)
            if (width > max_columns or min(width, len(rows)) > 80
                    or width * len(rows) > max_cells or len(cells) >= max_cells):
                return fail('GRID_LIMIT', 'LIMIT_EXCEEDED')
            for target_row in range(row_index, row_index + rowspan):
                if len(grid[target_row]) < end_column:
                    grid[target_row].extend([None] * (end_column - len(grid[target_row])))
                if any(value is not None for value in grid[target_row][column:end_column]):
                    return fail('OVERLAPPING_SPAN')
                grid[target_row][column:end_column] = [len(cells)] * colspan
            cells.append({'row': row_index, 'col': column, 'rowspan': rowspan, 'colspan': colspan,
                          'text': _visible_text(cell), 'tag': cell.tag, 'source_path': resolve_path(cell)})
            column = end_column
    if not cells:
        return fail('NO_CELLS')
    for row in grid:
        row.extend([None] * (width - len(row)))
    result.update(status='COMPLETE', cells=cells, grid=grid, row_count=len(rows), column_count=width)
    return result
