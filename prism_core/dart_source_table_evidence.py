"""Bounded, lossless table geometry projection for opt-in report experiments.

The source codec has no HTML scope/headers attributes. Header links here are
positional candidates, NOT semantic period/unit/accounting-scope assertions.
"""

import copy

from prism_core.dart_source_tree_catalog import (
    VERSION,
    _pack_table_wire,
    _unpack_table_wire,
    decode_table,
    pack_units,
    unpack_units,
)

GRID_VERSION = 'source-tree-grid-v1'
ADDRESSED_VERSION = 'source-tree-addressed-grid-v1'
ADDRESS_GUIDE = """
source-tree-addressed-grid-v1 uses the grid codec above with ONE path change:
table-kind t rows start with [table_id,original_packed_path] instead of a path
string. table_id is the zero-based index among ALL unit rows in this source
catalog (including non-tables), not a count of tables. Copy the displayed integer
table_id and registry.source_id for cell references; do not reconstruct a path.
IDs are local to this supplied source catalog. Other kinds keep their original
path strings. All source text, geometry, paths and metadata remain unchanged.
"""
GRID_GUIDE = """
Catalog version source-tree-grid-v1 overrides ONLY merged-table anchor rows in
the supplied base codec. A six-field table payload with nonempty spans contains full
COLUMN-ALIGNED TSV rows, not anchor-only rows. Every row has column_count slots.
The single token ^ marks a position covered by another anchor's rowspan/colspan;
the single token ` marks an unoccupied grid position. Neither is source text or
a value. An empty token is a real source-authored empty anchor. A source string
beginning with ^ or ` escapes its first character by doubling it: ^^ decodes to
literal ^, ``` decodes to literal ``. Remove exactly one leading character from
such escaped tokens. Other text, spans, paths, header flags and source registry
are unchanged. Tables without spans retain the original codec. Row coordinates
and columns are zero-based; header flags identify source th cells only. Geometry
does not certify financial scope, units or period semantics. If header meaning
is ambiguous (including td-only labels), leave it unknown rather than guessing.
"""


def _aligned_rows(table):
    rows = []
    for r, grid_row in enumerate(table['grid']):
        tokens = []
        for c, index in enumerate(grid_row):
            if index is None:
                cell_text = '`'
            else:
                cell = table['cells'][index]
                if (cell['row'], cell['col']) != (r, c):
                    cell_text = '^'
                else:
                    cell_text = cell['text']
                    if cell_text.startswith(('^', '`')):
                        cell_text = cell_text[0] + cell_text
            tokens.append(cell_text)
        rows.append('\t'.join(tokens))
    return rows


def pack_readable_units(units):
    """Replace merged-table rows only; retain every source string and metadata."""
    packed = pack_units(units)
    packed[0] = GRID_VERSION
    for unit, row in zip(units, packed[3]):
        if unit['kind'] not in {'table', 'container'} or not unit['payload'][3]:
            continue
        wire = copy.deepcopy(row[2])
        if not isinstance(wire, list):  # Original canonical caption shorthand.
            wire = copy.deepcopy(unit['payload'])
            wire[4] = 'body'
        wire[2] = _aligned_rows(decode_table(unit['payload'], unit['path']))
        row[2] = wire
    return packed


def pack_addressed_units(units):
    """Add copyable table addresses without changing grid/source metadata."""
    packed = pack_readable_units(units)
    packed[0] = ADDRESSED_VERSION
    for index, row in enumerate(packed[3]):
        if row[1] == 't':
            row[0] = [index, row[0]]
    return packed


def _unpack_addressed_units(payload):
    if len(payload) != 4 or not isinstance(payload[3], list):
        raise ValueError('invalid addressed catalog')
    grid = copy.deepcopy(payload)
    grid[0] = GRID_VERSION
    for index, row in enumerate(grid[3]):
        if not isinstance(row, list) or len(row) not in {3, 4, 5}:
            raise ValueError('invalid addressed catalog row')
        if row[1] == 't':
            address = row[0]
            if (not isinstance(address, list) or len(address) != 2
                    or type(address[0]) is not int or address[0] != index
                    or not isinstance(address[1], str)):
                raise ValueError('invalid table address')
            row[0] = address[1]
        elif not isinstance(row[0], str):
            raise ValueError('non-table address must remain a path string')
    units = unpack_readable_units(grid)
    if pack_addressed_units(units) != payload:
        raise ValueError('noncanonical addressed catalog')
    return units


def unpack_readable_units(payload):
    """Recover the exact original catalog, rejecting inconsistent grid markers."""
    if isinstance(payload, list) and payload and payload[0] == VERSION:
        return unpack_units(payload)
    if isinstance(payload, list) and payload and payload[0] == ADDRESSED_VERSION:
        return _unpack_addressed_units(payload)
    if not isinstance(payload, list) or len(payload) != 4 or payload[0] != GRID_VERSION:
        raise ValueError('invalid readable catalog version')
    packed = copy.deepcopy(payload)
    packed[0] = VERSION
    if not isinstance(packed[3], list):
        raise TypeError('invalid readable catalog rows')
    for row in packed[3]:
        if not isinstance(row, list) or len(row) not in {3, 4, 5}:
            raise ValueError('invalid readable catalog row')
        wire = row[2]
        if (row[1] not in {'t', 'c'} or not isinstance(wire, list)
                or len(wire) != 6 or not wire[3]):
            continue
        if (type(wire[0]) is not int or type(wire[1]) is not int
                or not 0 < wire[0] <= 300 or not 0 < wire[1] <= 300
                or wire[0] * wire[1] > 12000 or min(wire[:2]) > 80
                or not isinstance(wire[2], list) or len(wire[2]) != wire[0]):
            raise ValueError('invalid aligned table dimensions')
        original_rows = []
        for value in wire[2]:
            if not isinstance(value, str) or len(value.split('\t')) != wire[1]:
                raise ValueError('invalid aligned table row')
            anchors = []
            for token in value.split('\t'):
                if token in {'^', '`'}:
                    continue
                if token.startswith(('^', '`')):
                    if len(token) < 2 or token[0] != token[1]:
                        raise ValueError('invalid aligned table escape')
                    token = token[1:]
                anchors.append(token)
            original_rows.append('\t'.join(anchors) if anchors else None)
        wire[2] = original_rows
        row[2] = _pack_table_wire(_unpack_table_wire(wire))
    units = unpack_units(packed)
    if pack_readable_units(units) != payload:
        raise ValueError('inconsistent aligned table geometry or noncanonical catalog')
    return units

def expand_table_evidence(unit):
    """Decode bounded source geometry; fail explicitly on invalid payloads.

Only an uninterrupted leading band of th/thead cells is a candidate header.
Body th, sparse grids and nested containers cannot establish that association.
No lexical/numeric heuristics or company-specific selectors are used.
"""
    if unit['kind'] not in {'table', 'container'}:
        raise ValueError('table expansion requires table or container')
    table = decode_table(unit['payload'], unit['path'])
    cells, grid = table['cells'], table['grid']
    header_rows = 0
    for row in grid:
        anchors = [cells[i] for i in set(row) if i is not None]
        if not anchors or not all(c['tag'] == 'th' or '/thead' in c['source_path']
                                  for c in anchors):
            break
        header_rows += 1
    reason = None
    if unit['kind'] == 'container':
        reason = 'CONTAINER_NOT_DATA_TABLE'
    elif any(i is None for row in grid for i in row):
        reason = 'SPARSE_GRID'
    elif not header_rows or header_rows == table['row_count']:
        reason = 'NO_EXPLICIT_HEADER_BAND'
    elif any(c['tag'] == 'th' or '/thead' in c['source_path']
             for c in cells if c['row'] >= header_rows):
        reason = 'HEADERS_OUTSIDE_LEADING_BAND'
    elif any(c['row'] + c['rowspan'] > header_rows
             for c in cells if c['row'] < header_rows):
        reason = 'HEADER_SPANS_INTO_BODY'
    expanded = []
    for cell in cells:
        headers = []
        if reason is None and cell['row'] >= header_rows:
            for row in grid[:header_rows]:
                for index in row[cell['col']:cell['col'] + cell['colspan']]:
                    if index not in headers:
                        headers.append(index)
        expanded.append({'path': cell['source_path'], 'row': cell['row'],
                         'column': cell['col'], 'rowspan': cell['rowspan'],
                         'colspan': cell['colspan'], 'tag': cell['tag'],
                         'text': cell['text'], 'column_header_candidates': headers})
    return {'path': unit['path'], 'status': 'AMBIGUOUS' if reason else 'STRUCTURAL_ONLY',
            'reason': reason, 'rows': table['row_count'], 'columns': table['column_count'],
            'cells': expanded, 'grid': grid}
