"""Offline source-tree catalog, independent of the production scope reducer.

Paths refer to the parsed source DOM (not browser layout). Whitespace alone is
normalized. Containers preserve geometry separately from their ordered content;
they are never asserted to be ordinary flat tables. Context is literal source
heading ancestry, not an inferred financial scope or a statement of truth.
"""
import hashlib
import re

from lxml import etree

from prism_core.filing_html import _hidden, _text
from prism_core.filing_html_policy import MAX_HTML_BYTES
from prism_core.filing_html_tables import parse_html_table

VERSION = 'source-tree-v1'
CODEC_GUIDE = """
Readable source-tree-v1 base codec (the supplied grid guide overrides merged
table anchor rows only): Each sources[] entry contains source metadata,
core_paths and catalog. Each catalog is [version,path_prefix,contexts,rows].
Each row is [path_suffix,kind,payload,context_index,optional_heading_level].
Three-field rows inherit the preceding row's context within this catalog only.
Contexts are ordered literal heading paths, not inferred accounting scope.
Path tokens t/b/a/f/r/d/h/p/x mean table/tbody/thead/tfoot/tr/td/th/p/text();
suffix digits are XPath indices; a colon-prefixed token is literal.
Prepend path_prefix after expanding tokens. Kinds p/t/c/x mean
text/table/container/unsupported. Container geometry and its descendant units
must be read together, not flattened as a financial rectangle.
For t/c only, a string is a 1x1 tbody/tr/td cell. {body:[TSV rows]} is an
unmerged tbody of td anchors. {caption:[row1,row2]} means a 2x2 table with
first-row cell spanning two columns and two second-row cells. These defaults
preserve real empty strings; null row means no anchor, NOT an empty cell.
Other table payloads are
[row_count,column_count,TSV_anchor_rows,spans,rowpaths,headers].
Scan row-major into unoccupied grid slots; spans [anchor_index,rowspan,colspan]
override 1x1 defaults. All indices are zero-based. TSV values are full cell text.
rowpaths body/head/foot/rows mean /tbody/tr, /thead/tr, /tfoot/tr, /tr;
{head:N} means N thead rows followed by tbody rows.
['groups',[[stem,count],...]] expands group-local rows; explicit rowpath arrays
remain literal. Single rows omit [1], multiple siblings use one-based XPath
indices. headers is either a list of th anchor indices or integer N meaning
first N anchors are th; others td.
Each entry's source.filing contains entity_id, receipt_id, role, period_start,
period_end, scope and section together; these fields are NOT factored into a
separate registry. source.url and source.sha256 identify the source document;
source.published is its filing publication date. source.scope_context, when
present, records the verified external parent context for a note fragment,
without rewriting the source text. core_paths are this writer's assigned
source paths; other catalog rows supply necessary context. Do not confuse
source period with report date, or source fidelity with financial truth.
"""
_BLOCK = {'p', 'div', 'li', 'section', 'article', 'table', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
_PATH_TAGS = {'table': 't', 'tbody': 'b', 'thead': 'a', 'tfoot': 'f',
              'tr': 'r', 'td': 'd', 'th': 'h', 'p': 'p', 'text()': 'x'}


def _pack_path(path):
    def step(value):
        match = re.fullmatch(r'([a-z]+|text\(\))(?:\[(\d+)\])?', value)
        if match and match[1] in _PATH_TAGS:
            return _PATH_TAGS[match[1]] + (match[2] or '')
        return ':' + value
    return '/'.join(step(value) for value in path.split('/'))


def _unpack_path(path):
    tags = {v: k for k, v in _PATH_TAGS.items()}
    def step(value):
        if value.startswith(':'):
            return value[1:]
        match = re.fullmatch(r'([tbafrdhpx])(\d*)', value)
        if not match or match[1] not in tags:
            raise ValueError('invalid path token')
        return tags[match[1]] + (f'[{match[2]}]' if match[2] else '')
    return '/'.join(step(value) for value in path.split('/'))


def _pack_table_wire(payload):
    """Elide documented default geometry only; do not encode source text."""
    if (isinstance(payload, list) and len(payload) == 6 and payload[:2] == [1, 1]
            and isinstance(payload[2], list) and len(payload[2]) == 1
            and isinstance(payload[2][0], str) and '\t' not in payload[2][0]
            and payload[3:] == [[], '/tbody/tr', []]):
        return payload[2][0]
    if (isinstance(payload, list) and len(payload) == 6
            and isinstance(payload[2], list) and payload[2]
            and all(isinstance(row, str) for row in payload[2])):
        if (payload[3:] == [[], '/tbody/tr', []]
                and payload[0] == len(payload[2])
                and payload[1] == max(len(row.split('\t')) for row in payload[2])):
            return {'body': payload[2]}
        if (payload[:2] == [2, 2] and len(payload[2]) == 2
                and payload[3:] == [[[0, 1, 2]], '/tbody/tr', []]
                and '\t' not in payload[2][0] and payload[2][1].count('\t') == 1):
            return {'caption': payload[2]}
    if isinstance(payload, list) and len(payload) == 6:
        payload = list(payload)
        paths = payload[4]
        templates = {'/tbody/tr': 'body', '/thead/tr': 'head',
                     '/tfoot/tr': 'foot', '/tr': 'rows'}
        if isinstance(paths, str) and paths in templates:
            payload[4] = templates[paths]
        elif (isinstance(paths, list) and len(paths) == 2 and paths[0] == 'groups'
              and len(paths[1]) == 2 and paths[1][0][0] == '/thead/tr'
              and paths[1][1][0] == '/tbody/tr'
              and paths[1][0][1] + paths[1][1][1] == payload[0]):
            # Standard header rows followed by body rows; total row count is
            # already explicit. Singleton/indexed XPath syntax remains exact.
            payload[4] = {'head': paths[1][0][1]}
    return payload


def _unpack_table_wire(payload):
    if isinstance(payload, str):
        return [1, 1, [payload], [], '/tbody/tr', []]
    if isinstance(payload, dict):
        if len(payload) != 1 or next(iter(payload)) not in {'body', 'caption'}:
            raise ValueError('invalid default table descriptor')
        name, rows = next(iter(payload.items()))
        if (not isinstance(rows, list) or not 0 < len(rows) <= 300
                or any(not isinstance(row, str) for row in rows)):
            raise ValueError('invalid default table rows')
        if name == 'body':
            return [len(rows), max(len(row.split('\t')) for row in rows),
                    rows, [], '/tbody/tr', []]
        if len(rows) != 2 or '\t' in rows[0] or rows[1].count('\t') != 1:
            raise ValueError('invalid caption table rows')
        return [2, 2, rows, [[0, 1, 2]], '/tbody/tr', []]
    if isinstance(payload, list) and len(payload) == 6:
        payload = list(payload)
        paths = payload[4]
        templates = {'body': '/tbody/tr', 'head': '/thead/tr',
                     'foot': '/tfoot/tr', 'rows': '/tr'}
        if isinstance(paths, str) and paths in templates:
            payload[4] = templates[paths]
        elif isinstance(paths, dict):
            if (set(paths) != {'head'} or type(paths['head']) is not int
                    or type(payload[0]) is not int or not 0 < paths['head'] < payload[0]):
                raise ValueError('invalid standard row group descriptor')
            payload[4] = ['groups', [['/thead/tr', paths['head']],
                                    ['/tbody/tr', payload[0] - paths['head']]]]
    return payload


def pack_units(units):
    """Compact readable wire form, sharing path prefixes and heading contexts.

    Returns [version, prefix, contexts, rows]. Rows contain path suffix, kind,
    payload, context index and optionally heading level. A three-field row reuses
    the previous row's context; the first row always supplies its context index.
    Only repeated/default
    metadata is factored out; every source text stays readable in JSON.
    For table/container kinds only, a string payload denotes the explicit default
    shape: one tbody/tr/td cell, 1x1, no spans or headers. Unpack restores all six
    normal table fields; strings are never interpreted as source text for another
    shape. Empty string is a real empty cell, never a missing anchor.
    A body descriptor is an unmerged tbody of td anchors; caption is precisely
    two tbody rows, a first-row cell spanning two columns and two second-row cells.
    Other geometry uses the complete representation. No descriptor removes text.
    """
    paths = [u['path'] for u in units] + [p for u in units for p in u['context']]
    prefix = '/html/body/' if paths and all(p.startswith('/html/body/') for p in paths) else '/'
    contexts, seen, rows = [], {}, []
    previous_context = None
    kinds = {'text': 'p', 'table': 't', 'container': 'c', 'unsupported': 'x'}
    for unit in units:
        context = tuple(unit['context'])
        if context not in seen:
            seen[context] = len(contexts)
            contexts.append([_pack_path(p[len(prefix):]) for p in context])
        data = (_pack_table_wire(unit['payload']) if unit['kind'] in {'table', 'container'}
                else unit['payload'])
        row = [_pack_path(unit['path'][len(prefix):]), kinds[unit['kind']], data, seen[context]]
        if 'heading_level' in unit:
            row.append(unit['heading_level'])
        elif seen[context] == previous_context:
            row.pop()  # Omitted context means exactly the previous row's context.
        rows.append(row)
        previous_context = seen[context]
    return [VERSION, prefix, contexts, rows]


def unpack_units(payload):
    """Decode a canonical wire catalog chunk; table geometry stays compact."""
    if not isinstance(payload, list) or len(payload) != 4 or payload[0] != VERSION:
        raise ValueError('invalid catalog wire version')
    _, prefix, contexts, rows = payload
    if prefix not in {'/', '/html/body/'} or not isinstance(contexts, list) or not isinstance(rows, list):
        raise ValueError('invalid catalog wire structure')
    kinds = {'p': 'text', 't': 'table', 'c': 'container', 'x': 'unsupported'}
    if any(not isinstance(c, list) or any(not isinstance(p, str) for p in c) for c in contexts):
        raise ValueError('invalid contexts')
    units = []
    previous_context = None
    for row in rows:
        if (not isinstance(row, list) or len(row) not in {3, 4, 5}
                or not isinstance(row[0], str) or row[1] not in kinds):
            raise ValueError('invalid catalog row')
        context_index = row[3] if len(row) >= 4 else previous_context
        if type(context_index) is not int or not 0 <= context_index < len(contexts):
            raise ValueError('invalid catalog context')
        unit = {'path': prefix + _unpack_path(row[0]), 'kind': kinds[row[1]], 'payload': row[2],
                'context': [prefix + _unpack_path(p) for p in contexts[context_index]]}
        previous_context = context_index
        if unit['kind'] in {'table', 'container'}:
            unit['payload'] = _unpack_table_wire(unit['payload'])
        if len(row) == 5:
            if type(row[4]) is not int or not 1 <= row[4] <= 100:
                raise ValueError('invalid heading level')
            unit['heading_level'] = row[4]
        if unit['kind'] in {'table', 'container'}:
            decode_table(unit['payload'], unit['path'])
        elif unit['kind'] == 'text' and not isinstance(unit['payload'], str):
            raise ValueError('invalid text')
        units.append(unit)
    if pack_units(units) != payload:
        raise ValueError('noncanonical catalog wire form')
    return units


def heading_level(text, tag='p'):
    """Recognize short source-authored numbered titles without changing scope."""
    if not text or len(text) > 140:
        return None
    if re.fullmatch(r'h[1-6]', tag):
        return int(tag[1])
    if re.match(r'^[IVX]+[.)]\s*\S', text):
        return 1
    if re.match(r'^\d+\.\s+\S', text):
        return 2
    match = re.match(r'^\d+([.-]\d+)+\.?\s+\S', text)
    if match:
        return 3
    if re.match(r'^\(\d+\)\s*\S', text):
        return 4
    if re.match(r'^\d+\)\s*\S', text):
        return 5
    return None


def encode_table(table):
    """Readable codec: dimensions, anchor TSV rows, spans, row paths, th indices.

    None means no anchor in that row; an empty string means one empty anchor.
    Source text is whitespace normalized and therefore contains no TSV controls.
    Cell paths are reconstructed from source row paths and same-tag sibling order.
    Row-path runs are ['groups', [[stem, count], ...]], with source singleton
    versus indexed sibling syntax checked before encoding. An integer header
    descriptor means the first N anchor cells are th; other layouts keep indices.
    """
    if table['status'] != 'COMPLETE':
        raise ValueError('only complete table geometry can be encoded')
    rows = [[] for _ in range(table['row_count'])]
    paths = [None] * len(rows)
    spans, headers = [], []
    for index, cell in enumerate(table['cells']):
        text = cell['text']
        if any(c in text for c in '\t\r\n'):
            raise ValueError('table text must be normalized')
        rows[cell['row']].append(text)
        paths[cell['row']] = cell['source_path'].rsplit('/', 1)[0]
        if (cell['rowspan'], cell['colspan']) != (1, 1):
            spans.append([index, cell['rowspan'], cell['colspan']])
        if cell['tag'] == 'th':
            headers.append(index)
    prefix = table['source_path']
    relative = [path[len(prefix):] if path is not None else None for path in paths]
    for stem in ('/tr', '/tbody/tr', '/thead/tr', '/tfoot/tr'):
        expected = [stem + (f'[{i + 1}]' if len(rows) > 1 else '') if row else None
                    for i, row in enumerate(rows)]
        if relative == expected:
            relative = stem
            break
    if isinstance(relative, list) and all(isinstance(path, str) for path in relative):
        groups = []
        for path in relative:
            stem = re.sub(r'\[\d+\]$', '', path)
            if groups and groups[-1][0] == stem:
                groups[-1][1] += 1
            else:
                groups.append([stem, 1])
        expanded = [stem + (f'[{i + 1}]' if count > 1 else '')
                    for stem, count in groups for i in range(count)]
        if expanded == relative:
            relative = ['groups', groups]
    if headers and headers == list(range(len(headers))):
        headers = len(headers)
    return [len(rows), table['column_count'],
            ['\t'.join(row) if row else None for row in rows], spans,
            relative, headers]


def decode_table(payload, path):
    """Expand the codec to the existing COMPLETE cells/grid representation."""
    if not isinstance(payload, list) or len(payload) != 6:
        raise ValueError('invalid table codec')
    nr, nc, rows, spans, rowpaths, headers = payload
    if (isinstance(rowpaths, list) and len(rowpaths) == 2
            and rowpaths[0] == 'groups'):
        groups = rowpaths[1]
        if (not isinstance(groups, list) or not groups or len(groups) > 300
                or any(not isinstance(group, list) or len(group) != 2
                       or not isinstance(group[0], str)
                       or not re.fullmatch(r'/(?:thead(?:\[\d+\])?/|tbody(?:\[\d+\])?/|tfoot(?:\[\d+\])?/)?tr', group[0])
                       or type(group[1]) is not int or not 0 < group[1] <= 300 for group in groups)
                or sum(group[1] for group in groups) > 300):
            raise ValueError('invalid row groups')
        rowpaths = [stem + (f'[{i + 1}]' if count > 1 else '')
                    for stem, count in groups for i in range(count)]
    if type(headers) is int:
        if not 0 < headers <= 12000:
            raise ValueError('invalid header range')
        headers = list(range(headers))
    if isinstance(rowpaths, str) and rowpaths in {'/tr', '/tbody/tr', '/thead/tr', '/tfoot/tr'}:
        if type(nr) is not int or not isinstance(rows, list) or not 0 < nr <= 300 or len(rows) != nr:
            raise ValueError('invalid template dimensions')
        rowpaths = [rowpaths + (f'[{i + 1}]' if nr > 1 else '') if value is not None else None
                    for i, value in enumerate(rows)]
    if (type(nr) is not int or type(nc) is not int or not 0 < nr <= 300
            or not 0 < nc <= 300 or nr * nc > 12000 or min(nr, nc) > 80
            or not isinstance(rows, list) or len(rows) != nr
            or not isinstance(rowpaths, list) or len(rowpaths) != nr
            or not isinstance(spans, list) or not isinstance(headers, list)):
        raise ValueError('invalid table dimensions')
    spanmap = {}
    for item in spans:
        if (not isinstance(item, list) or len(item) != 3
                or any(type(n) is not int for n in item)
                or item[0] in spanmap or item[0] < 0 or min(item[1:]) < 1
                or max(item[1:]) > 300 or item[1:] == [1, 1]):
            raise ValueError('invalid spans')
        spanmap[item[0]] = item[1:]
    if any(type(i) is not int or i < 0 for i in headers) or len(set(headers)) != len(headers):
        raise ValueError('invalid header indices')
    grid = [[None] * nc for _ in range(nr)]
    cells = []
    for r, value in enumerate(rows):
        if value is None:
            if rowpaths[r] is not None:
                raise ValueError('empty row path')
            continue
        if (not isinstance(value, str) or '\r' in value or '\n' in value
                or not isinstance(rowpaths[r], str)
                or not re.fullmatch(r'/(?:thead(?:\[\d+\])?/|tbody(?:\[\d+\])?/|tfoot(?:\[\d+\])?/)?tr(?:\[\d+\])?', rowpaths[r])):
            raise ValueError('invalid row data')
        texts = value.split('\t')
        start = len(cells)
        tags = ['th' if start + i in headers else 'td' for i in range(len(texts))]
        seen = {'th': 0, 'td': 0}
        col = 0
        for text, tag in zip(texts, tags):
            while col < nc and grid[r][col] is not None:
                col += 1
            index = len(cells)
            rs, cs = spanmap.get(index, (1, 1))
            if r + rs > nr or col + cs > nc:
                raise ValueError('span outside grid')
            for rr in range(r, r + rs):
                for cc in range(col, col + cs):
                    if grid[rr][cc] is not None:
                        raise ValueError('overlapping spans')
                    grid[rr][cc] = index
            seen[tag] += 1
            suffix = f'[{seen[tag]}]' if tags.count(tag) > 1 else ''
            cells.append({'row': r, 'col': col, 'rowspan': rs, 'colspan': cs,
                          'text': text, 'tag': tag,
                          'source_path': path + rowpaths[r] + '/' + tag + suffix})
            col += cs
    if not cells or any(i >= len(cells) for i in [*spanmap, *headers]):
        raise ValueError('unbound cell index')
    return {'status': 'COMPLETE', 'errors': [], 'row_count': nr,
            'column_count': nc, 'source_path': path, 'cells': cells, 'grid': grid}


def _geometry(node):
    """Clone only a container's own rows/cells; retain original path bindings."""
    clone = etree.Element('table')
    paths = {clone: node.getroottree().getpath(node)}
    for item in node.iterdescendants():
        if next(item.iterancestors('table'), None) is not node:
            continue
        if item.tag in {'td', 'th'} and item.getparent().tag != 'tr':
            return {'status': 'UNSUPPORTED', 'errors': ['CELL_OUTSIDE_ROW']}
        if item.tag == 'tr':
            for parent in item.iterancestors():
                if parent is node:
                    break
                if parent.tag == 'tr':
                    return {'status': 'UNSUPPORTED', 'errors': ['NESTED_ROW']}
    for row in node.iterdescendants('tr'):
        if next(row.iterancestors('table'), None) is not node:
            continue
        target = etree.SubElement(clone, 'tr', dict(row.attrib))
        paths[target] = node.getroottree().getpath(row)
        for cell in row:
            if cell.tag in {'td', 'th'}:
                copied = etree.SubElement(target, cell.tag, dict(cell.attrib))
                paths[copied] = node.getroottree().getpath(cell)
    return parse_html_table(clone, path_resolver=paths.__getitem__)


def build_catalog(html):
    """Return ordered units with exact DOM paths, payloads and heading paths.

    ``text`` payloads are normalized strings. ``table`` and ``container``
    payloads use encode_table. Containers have empty cell texts and MUST be
    interpreted with all descendant units; omission is not subtree coverage.
    Unsupported tables retain explicit error units, never silent partial success.
    This function has no file, network, model, gold or owner-selection access.
    """
    if not isinstance(html, str) or len(html) > MAX_HTML_BYTES:
        raise ValueError('HTML must be bounded text')
    raw = html.encode('utf-8')
    if len(raw) > MAX_HTML_BYTES:
        raise ValueError('HTML byte limit')
    parser = etree.HTMLParser(encoding='utf-8', no_network=True, recover=True, huge_tree=False)
    root = etree.fromstring(raw, parser=parser)
    if root is None:
        raise ValueError('empty HTML')
    tree = root.getroottree()
    for index, node in enumerate(root.iter()):
        if index >= 200000 or len(list(node.iterancestors())) > 100:
            raise ValueError('HTML structural limit')
    units, headings = [], []

    def emit(path, kind, payload, tag='p'):
        level = heading_level(payload, tag) if kind == 'text' else None
        if level is not None:
            while headings and headings[-1][0] >= level:
                headings.pop()
        unit = {'path': path, 'kind': kind, 'payload': payload,
                'context': [p for _, p in headings]}
        if level is not None:
            unit['heading_level'] = level
        units.append(unit)
        if level is not None:
            headings.append((level, path))

    def ancillary(node):
        """Leaf-table visible content outside cells, never duplicate cell text."""
        if not isinstance(node.tag, str) or _hidden(node) or node.tag in {'td', 'th'}:
            return
        path = tree.getpath(node)
        if node.tag == 'caption' and not any(
                isinstance(n.tag, str) and n.tag in _BLOCK for n in node.iterdescendants()):
            text = _text(node)
            if text:
                emit(path, 'text', text, node.tag)
            return
        text_index = 0
        if node.text is not None:
            text_index += 1
            text = ' '.join(node.text.split())
            if text:
                emit(path + '/text()[1]', 'text', text)
        for child in node:
            ancillary(child)
            if child.tail is not None:
                text_index += 1
                text = ' '.join(child.tail.split())
                if text:
                    emit(path + f'/text()[{text_index}]', 'text', text)

    def walk(node):
        if not isinstance(node.tag, str) or _hidden(node):
            return
        path = tree.getpath(node)
        if node.tag == 'table':
            nested = bool(node.xpath('.//table'))
            cells = node.xpath('./tr/td|./tbody/tr/td')
            narrative = (len(cells) == 1 and 'nb' in node.get('class', '').split()
                         and bool(cells[0].xpath('.//p')))
            if nested or narrative:
                table = _geometry(node)
                if table['status'] != 'COMPLETE':
                    emit(path, 'unsupported', table['errors'])
                    return
                emit(path, 'container', encode_table(table))
            else:
                table = parse_html_table(node)
                if table['status'] == 'COMPLETE':
                    emit(path, 'table', encode_table(table))
                    ancillary(node)
                else:
                    emit(path, 'unsupported', table['errors'])
                return
        elif node.tag in _BLOCK and not any(
                isinstance(n.tag, str) and n.tag in _BLOCK for n in node.iterdescendants()):
            text = _text(node)
            if text:
                emit(path, 'text', text, node.tag)
            return
        text_index = 0
        if node.text is not None:
            text_index += 1
            text = ' '.join(node.text.split())
            if text:
                emit(path + '/text()[1]', 'text', text)
        for child in node:
            walk(child)
            if child.tail is not None:
                text_index += 1
                text = ' '.join(child.tail.split())
                if text:
                    emit(path + f'/text()[{text_index}]', 'text', text)

    walk(root)
    return {'version': VERSION, 'sha256': hashlib.sha256(raw).hexdigest(),
            'root_path': tree.getpath(root), 'units': units}
