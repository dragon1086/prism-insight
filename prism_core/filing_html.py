"""Bounded filing HTML evidence, with DOM locators into the hashed representation.

No issuer/date/fact verification and no numeric conversion. A provider-cleaned
HTML hash is not an original-response hash. Callers retain that distinction.
"""
import hashlib
import json
import math
import re
from collections import Counter
from time import monotonic

from lxml import etree

from prism_core.filing_html_policy import MAX_HTML_BYTES
from prism_core.filing_html_tables import parse_html_table

_MAJOR = re.compile(r'^(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)[.)]\s*\S.{0,110}$')
_NUMBER = re.compile(r'^\d{1,3}(?:-\d{1,3})*[.)]\s*\S.{0,100}$')
_FINANCIAL_TITLE = re.compile(r'^(?:\d{1,3}[.)]\s*)?(?:연결\s*)?재무제표(?:\s*주석)?$')
_ACCOUNTING_NOTE = re.compile(
    r'^\d{1,3}[.)]\s*(?:(?P<prefix_scope>연결|별도)\s*)?재무제표\s*(?:의\s*)?작성(?:\s*기준|\s*의\s*기초)'
    r'(?:\s*및\s*중요한\s*회계정책)?(?:\s*\((?:연결|별도)\))?$')
_SUB = re.compile(r'^(?:\(\d{1,3}\)|[가-힣][.)])\s*\S.{0,100}$')
_CONTEXT = re.compile(r'단\s*위\s*[:：]|제\s*\d+\s*기|(?:당|전)(?:반기|분기|기)(?:말)?|재무상태표|손익계산서|현금흐름표')
_FOOT = re.compile(r'^(?:※|주\s*\d*\s*[):：.]|\(주\s*\d*\)|\(\*\d*\)|\*\d*\))')
_SKIP = {'head', 'script', 'style', 'noscript', 'iframe', 'object', 'embed', 'nav'}
_BLOCK = {'p', 'div', 'section', 'article', 'table', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'title'}
_MAX_LOCATOR_BYTES = 16 * 1024 * 1024
_MAX_PARSE_SECONDS = 10.0
_LAYOUT_LIMIT = 4096
_LAYOUT_PERIOD = re.compile(r'(?:당|전)(?:기|반기|분기)(?:말)?')
_LAYOUT_UNIT = re.compile(r'\(단위:(?:원|천원|백만원|억원|USD|천USD|백만USD|달러|천달러|백만달러)\)')


def _layout_role(node, table):
    """Recognize only lossless, non-data layout geometry, never by class alone."""
    if 'nb' not in node.get('class', '').split() or node.get('border', '').strip() not in {'', '0'}:
        return None
    groups = node.xpath('.//colgroup')
    if len(groups) > 1:
        return None
    if groups:
        group = groups[0]
        if (group.getparent() is not node or group.attrib or (group.text or '').strip()
                or len(group) != table['column_count']):
            return None
        for col in group:
            if (col.tag != 'col' or set(col.attrib) - {'width'} or len(col)
                    or (col.text or '').strip() or (col.tail or '').strip()
                    or ('width' in col.attrib and not re.fullmatch(r'[0-9]+', col.get('width')))):
                return None
    inline = {'span', 'font', 'b', 'strong', 'i', 'em', 'u', 'sup', 'sub', 'br'}
    for item in node.iter():
        if not isinstance(item.tag, str) or _hidden(item):
            return None
        if item.tag not in {'table', 'tbody', 'tr', 'td', 'colgroup', 'col'} | inline or 'rowspan' in item.attrib:
            return None
        allowed_parent = {'tbody': {'table'}, 'tr': {'table', 'tbody'}, 'td': {'tr'}, 'colgroup': {'table'}, 'col': {'colgroup'}}
        if item.tag in allowed_parent and item.getparent().tag not in allowed_parent[item.tag]:
            return None
        in_cell = item.tag == 'td' or any(p.tag == 'td' for p in item.iterancestors())
        if not in_cell and (item.text or '').strip():
            return None
        if item is not node and (item.tail or '').strip() and not any(p.tag == 'td' for p in item.iterancestors()):
            return None
        if item.tag in inline and not in_cell:
            return None
    cells = table['cells']
    shape = [(c['row'], c['col'], c['rowspan'], c['colspan']) for c in cells]
    if not cells or table['row_count'] != max(c['row'] + c['rowspan'] for c in cells):
        return None
    span_nodes = node.xpath('.//*[@colspan]')
    if span_nodes and not (len(span_nodes) == 1 and span_nodes[0].tag == 'td'
                           and span_nodes[0].get('colspan') == '2'
                           and shape == [(0, 0, 1, 2), (1, 0, 1, 1), (1, 1, 1, 1)]):
        return None
    if shape == [(0, 0, 1, 1)]:
        return 'spacer' if not cells[0]['text'] else 'footnote' if _FOOT.match(cells[0]['text']) else None
    if shape == [(0, 0, 1, 2), (1, 0, 1, 1), (1, 1, 1, 1)]:
        title = cells[0]['text']
        if not 1 <= len(title) <= 240 or not re.search(r'[가-힣A-Za-z]', title):
            return None
        role, pair = 'caption', cells[1:]
    elif shape == [(0, 0, 1, 1), (0, 1, 1, 1)]:
        role, pair = 'period_unit', cells
    else:
        return None
    period, unit = (c['text'] for c in pair)
    return role if (not period or _LAYOUT_PERIOD.fullmatch(period)) and _LAYOUT_UNIT.fullmatch(re.sub(r'\s+', '', unit)) else None


def _hidden(node):
    style = re.sub(r'\s+', '', node.get('style', '')).lower()
    return (node.tag in _SKIP or 'hidden' in node.attrib
            or node.get('aria-hidden', '').lower().strip() == 'true'
            or 'display:none' in style or 'visibility:hidden' in style or 'visibility:collapse' in style
            or node.get('role', '').lower() == 'navigation')


def _text(node, *, normalized=True):
    def parts(item):
        if item.tag in {'br', 'p', 'div', 'li'}:
            yield ' '
        if item.text:
            yield item.text
        for child in item:
            if isinstance(child.tag, str) and not _hidden(child):
                yield from parts(child)
            if child.tail:
                yield child.tail
        if item.tag in {'br', 'p', 'div', 'li'}:
            yield ' '
    raw = ''.join(parts(node))
    return ' '.join(raw.split()) if normalized else raw


def _events(node, path_resolver=None, locator_account=None):
    resolve_path = path_resolver or node.getroottree().getpath
    if not isinstance(node.tag, str) or _hidden(node):
        return
    if node.tag == 'table':
        path = resolve_path(node)
        yield node, _text(node), path, [path]
        return
    descendants = any(isinstance(n.tag, str) and n.tag in _BLOCK for n in node.iterdescendants())
    if not descendants:
        path = resolve_path(node)
        yield node, _text(node), path, [path]
        return
    path = resolve_path(node)
    text_index = 0
    pieces, locators = [], []
    if node.text is not None:
        text_index += 1
        pieces.append(node.text)
        locator = f'{path}/text()[{text_index}]'
        locators.append(locator_account(locator) if locator_account else locator)
    for child in node:
        structural = (child.tag in _BLOCK or any(
            isinstance(n.tag, str) and n.tag in _BLOCK for n in child.iterdescendants()))
        if structural:
            if pieces:
                yield node, ' '.join(''.join(pieces).split()), locators[0], locators
                pieces, locators = [], []
            yield from _events(child, resolve_path, locator_account)
        elif isinstance(child.tag, str) and not _hidden(child):
            pieces.append(_text(child, normalized=False))
            locators.append(resolve_path(child))
        if child.tail is not None:
            text_index += 1
            pieces.append(child.tail)
            locator = f'{path}/text()[{text_index}]'
            locators.append(locator_account(locator) if locator_account else locator)
    if pieces:
        yield node, ' '.join(''.join(pieces).split()), locators[0], locators


def _navigation(node, text):
    links = node.xpath('.//a[@href]')
    return bool(links and ''.join(_text(a) for a in links).replace(' ', '') == text.replace(' ', ''))


def _heading(node, text, notes):
    if node.tag == 'table' or len(text) > 120:
        return None
    if _MAJOR.fullmatch(text):
        return 2
    if re.search(r'(?:니다|이다|있다|한다)[.。]?$', text):
        return None
    if _NUMBER.fullmatch(text):
        # Unknown/decorated financial titles must end the previous scope. Only
        # known accounting-preparation notes retain their explicit parent.
        nested_note = notes and ('재무제표' not in text or _ACCOUNTING_NOTE.fullmatch(text))
        return 4 if re.match(r'^\d+-\d+', text) or nested_note else 3
    if _SUB.fullmatch(text):
        return 5
    if node.tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
        return int(node.tag[1])
    return None


class _ParseLimit(Exception):
    """Abort admission; never expose a partially qualified record group."""


class _ParseStructure(Exception):
    """The pull-parser event sequence cannot identify one source document."""


class _Reducer:
    """One semantic state across physical units; records are staged until EOF."""

    def __init__(self, out, resolve_path):
        self.out, self.resolve_path = out, resolve_path
        self.path, self.scope, self.notes = {}, 'unknown', False
        self.footnote_target, self.context_records = None, []
        self.layout_context = None
        self.blocked_footnotes = False
        self.major_counts = Counter()
        self.event_index = 0

    def accept(self, node, text, source_path, source_paths, *, check_navigation=True):
        if (not text and node.tag != 'table') or check_navigation and _navigation(node, text):
            return
        event_index = self.event_index
        self.event_index += 1
        if node.tag != 'table' and _MAJOR.fullmatch(text):
            self.major_counts[text] += 1
        level = None if _FOOT.match(text) else _heading(node, text, self.notes)
        if self.notes and level is not None:
            qualifier = re.search(r'\((연결|별도)\)$', text)
            accounting = _ACCOUNTING_NOTE.fullmatch(text)
            labels = [qualifier[1] if qualifier else None,
                      accounting['prefix_scope'] if accounting else None]
            if any(label and self.scope != ('consolidated' if label == '연결' else 'standalone') for label in labels):
                level = 3  # Contradictory explicit scope must not inherit its parent.
        if level is not None:
            self.footnote_target, self.context_records = None, []
            self.layout_context = None
            self.blocked_footnotes = False
            self.path = {k: v for k, v in self.path.items() if k < level}
            self.path[level] = text
            if level <= 3:
                financial_title = bool(_FINANCIAL_TITLE.fullmatch(text))
                self.scope = ('consolidated' if '연결' in text else 'standalone') if financial_title else 'unknown'
                self.notes = financial_title and '주석' in text
            if 'heading_events' in self.out:
                if len(self.out['heading_events']) >= 2000:
                    raise _ParseLimit('HTML_HEADING_LIMIT')
                self.out['heading_events'].append({
                    'text': text, 'source_path': source_path, 'source_paths': list(source_paths),
                    'level': level, 'section_path': [self.path[k] for k in sorted(self.path)],
                    'scope': self.scope,
                })
            return
        records = self.out['records']
        if len(records) >= 2000:
            raise _ParseLimit('HTML_RECORD_LIMIT')
        record = {'kind': 'prose', 'text': text,
                  'section_path': [self.path[k] for k in sorted(self.path)],
                  'scope': self.scope, 'source_path': source_path, 'source_paths': source_paths,
                  'event_index': event_index, 'context_before': '', 'footnotes': ''}
        if node.tag != 'table' and _FOOT.match(text) and self.blocked_footnotes:
            record['context_incomplete'] = True
            records.append(record)
            return
        if node.tag == 'table':
            table = parse_html_table(node, path_resolver=self.resolve_path)
            if table['status'] != 'COMPLETE':
                self.out['errors'].append('TABLE_' + table['status'])
                self.context_records = []
                self.footnote_target, self.layout_context = None, None
                return
            role = _layout_role(node, table)
            if role == 'spacer':
                self.event_index -= 1  # Strict empty layout is adjacency-transparent.
                return
            if not text:
                self.footnote_target, self.layout_context, self.context_records = None, None, []
                return
            # Small atomic tables can still amplify the retained document via
            # merged-cell grids. Bound aggregate output, not just active DOM.
            stats = self.out['streaming']
            grid_slots = sum(len(row) for row in table['grid'])
            origin_cells = len(table['cells'])
            if stats['accepted_grid_slots'] + grid_slots > 120000:
                raise _ParseLimit('HTML_DOCUMENT_GRID_LIMIT')
            if stats['accepted_origin_cells'] + origin_cells > 60000:
                raise _ParseLimit('HTML_DOCUMENT_CELL_LIMIT')
            stats['accepted_grid_slots'] += grid_slots
            stats['accepted_origin_cells'] += origin_cells
            preceding = []
            for previous in reversed(self.context_records[-3:]):
                if (previous['kind'] != 'prose' or previous['section_path'] != record['section_path']
                        or len(previous['text']) > 240 or not _CONTEXT.search(previous['text'])):
                    break
                preceding.insert(0, previous['text'])
            captions = [_text(c) for c in node.xpath('./caption') if not _hidden(c)]
            record['context_before'] = '\n'.join([*preceding, *captions])
            cells = [{k: c[k] for k in ('row', 'col', 'rowspan', 'colspan', 'text')}
                     for c in table['cells']]
            record.update(kind='table', table=table,
                          text=json.dumps({'cells': cells}, ensure_ascii=False, separators=(',', ':')))
            if role:
                record['layout_role'] = role
            if role != 'footnote':
                self.blocked_footnotes = False
            if role in {'caption', 'period_unit'}:
                self.layout_context = record
                self.footnote_target = None
            elif role == 'footnote':
                target = self.footnote_target
                foot = table['cells'][0]['text']
                self._append_bounded_footnote(target, foot, [table['cells'][0]['source_path']])
                self.layout_context, self.context_records = None, []
            else:
                pending = self.layout_context
                if pending is not None and pending['section_path'] == record['section_path'] and pending['scope'] == record['scope']:
                    labels = re.findall(r'연결|별도|개별', pending['table']['cells'][0]['text']) if pending['layout_role'] == 'caption' else []
                    expected = {'consolidated': '연결', 'standalone': '별도'}.get(record['scope'])
                    if any(label != expected for label in labels):
                        record['context_incomplete'] = True
                        self.out['errors'].append('LAYOUT_SCOPE_CONFLICT')
                    else:
                        values = [c['text'] for c in pending['table']['cells']]
                        record['context_before'] = '\n'.join(filter(None, [*preceding, *values, *captions]))
                        record['context_paths'] = [c['source_path'] for c in pending['table']['cells']]
                self.footnote_target, self.context_records, self.layout_context = record, [], None
        elif _FOOT.match(text) and self.footnote_target is not None:
            self._append_bounded_footnote(self.footnote_target, text, source_paths)
            return
        else:
            self.footnote_target = None
            self.layout_context = None
            self.blocked_footnotes = False
            self.context_records.append(record)
            self.context_records = self.context_records[-3:]
        records.append(record)

    def _append_bounded_footnote(self, target, text, paths):
        prior = target['footnotes'] if target is not None else ''
        if len(prior.encode('utf-8')) + bool(prior) + len(text.encode('utf-8')) > _LAYOUT_LIMIT:
            self.out['errors'].append('LAYOUT_CONTEXT_LIMIT')
            if target is not None:
                target['context_incomplete'] = True
            self.footnote_target = None
            self.blocked_footnotes = True
        elif target is not None:
            target['footnotes'] = (prior + '\n' if prior else '') + text
            target.setdefault('footnote_paths', []).extend(paths)


_CONTAINERS = {'html', 'body', 'div', 'section', 'article'}
_CONDITIONAL_CONTAINERS = {'main', 'form', 'aside', 'header', 'footer'}


class _Stream:
    """Release complete sibling units, keeping original start-event locators.

    Container text and inline children form one run until a structural boundary.
    A closed child remains pending until the next sibling starts or its parent
    ends: only then is its tail complete, regardless of feed boundaries.
    Tables and other non-container subtrees are atomic and capped at 30k nodes.
    ``peak_active_nodes`` counts accounted, retained nodes. libxml can also
    allocate lookahead from one bounded feed before its events are drained.
    """

    def __init__(self, out):
        self.stats = out['streaming']
        self.paths, self.stack = {}, []
        self.active = 0
        self.roots = 0
        self.reducer = _Reducer(out, self.paths.__getitem__)

    def _flush(self, frame):
        if frame['pieces']:
            text = ' '.join(''.join(frame['pieces']).split())
            navigation = (frame['links'] and ''.join(frame['links']).replace(' ', '')
                          == text.replace(' ', ''))
            if not navigation:
                self.reducer.accept(frame['node'], text, frame['locators'][0],
                                    frame['locators'][:], check_navigation=False)
            frame['pieces'], frame['locators'] = [], []
            frame['links'] = []

    def _promote(self, frame):
        # Standard wrappers absent from _BLOCK are inline until a descendant
        # block is observed. This preserves leaf-wrapper grouping while still
        # releasing a large <main><p>...</p>...</main> incrementally.
        if frame['conditional'] and not frame['structural']:
            parent = frame['parent']
            if parent is not None and parent['streamed']:
                self._promote(parent)
                self._flush(parent)
            frame['structural'] = True

    def _text(self, frame, value):
        if value is not None:
            frame['text_index'] += 1
            frame['pieces'].append(value)
            frame['locators'].append(self._locator(
                f"{frame['path']}/text()[{frame['text_index']}]"))

    def _locator(self, path):
        # Every constructed path is ASCII by the tag-name admission grammar.
        # Charge even paths later discarded: this bounds construction work and
        # deep-wrapper inline runs as well as retained table-cell locators.
        size = len(path)
        if self.stats['constructed_locator_bytes'] + size > _MAX_LOCATOR_BYTES:
            raise _ParseLimit('HTML_DOCUMENT_LOCATOR_LIMIT')
        self.stats['constructed_locator_bytes'] += size
        return path

    def _release(self, node):
        for child in node.iter():
            if child in self.paths:
                del self.paths[child]
                self.active -= 1
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
        node.clear()

    def _boundary(self, frame):
        if not frame['text_seen']:
            self._text(frame, frame['node'].text)
            frame['text_seen'] = True
        pending = frame['pending']
        if pending is None:
            return
        node, streamed = pending
        if not streamed:
            structural = (node.tag in _BLOCK or any(
                isinstance(n.tag, str) and n.tag in _BLOCK for n in node.iterdescendants()))
            if structural:
                self._promote(frame)
                self._flush(frame)
                for event in _events(node, self.paths.__getitem__, self._locator):
                    self.reducer.accept(*event)
            elif isinstance(node.tag, str) and not _hidden(node):
                frame['pieces'].append(_text(node, normalized=False))
                frame['locators'].append(self.paths[node])
                frame['links'].extend(_text(a) for a in node.xpath('descendant-or-self::a[@href]'))
            self.stats['units_processed'] += 1
        self._text(frame, node.tail)
        self._release(node)
        frame['pending'] = None

    def event(self, kind, node):
        if kind == 'comment':
            if self.stack and self.stack[-1]['streamed']:
                parent = self.stack[-1]
                self._boundary(parent)
                parent['pending'] = (node, False)
            self.paths[node] = None  # Count retained comments too, but never cite them.
            self.active += 1
            self.stats['total_nodes'] += 1
            self.stats['peak_active_nodes'] = max(self.stats['peak_active_nodes'], self.active)
            if self.active > 30000:
                raise _ParseLimit('HTML_ACTIVE_NODE_LIMIT')
            if not self.stack:
                self._release(node)
            return
        if kind == 'start':
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]*', node.tag):
                raise _ParseStructure('HTML_TAG_NAME_UNSUPPORTED')
            # HTML preserves names such as Word's o:p literally, without XML
            # namespace bindings. A name() test resolves those source nodes.
            segment = f"*[name()='{node.tag}']" if ':' in node.tag else node.tag
            parent = self.stack[-1] if self.stack else None
            if parent is not None:
                if parent['streamed']:
                    self._boundary(parent)
                parent['counts'][node.tag] += 1
                path = f"{parent['path']}/{segment}[{parent['counts'][node.tag]}]"
            else:
                self.roots += 1
                if self.roots != 1:
                    raise _ParseStructure('HTML_MULTIPLE_DOCUMENT_ROOTS')
                path = f'/{segment}[1]'
            conditional = node.tag in _CONDITIONAL_CONTAINERS
            streamed = (node.tag in _CONTAINERS | _CONDITIONAL_CONTAINERS and not _hidden(node)
                        and (parent is None or parent['streamed']))
            if parent is not None and parent['streamed'] and (streamed and not conditional or node.tag in _BLOCK):
                self._promote(parent)
                self._flush(parent)
            self.paths[node] = self._locator(path)
            self.active += 1
            self.stats['total_nodes'] += 1
            self.stats['peak_active_nodes'] = max(self.stats['peak_active_nodes'], self.active)
            if self.active > 30000:
                raise _ParseLimit('HTML_ACTIVE_NODE_LIMIT')
            if len(self.stack) > 100:
                raise _ParseLimit('HTML_DEPTH_LIMIT')
            self.stack.append({'node': node, 'path': path, 'counts': Counter(),
                               'parent': parent, 'conditional': conditional, 'structural': not conditional,
                               'streamed': streamed, 'pending': None, 'text_seen': False,
                               'text_index': 0, 'pieces': [], 'locators': [], 'links': []})
            return
        frame = self.stack.pop()
        if frame['streamed']:
            self._boundary(frame)
            if frame['conditional'] and not frame['structural']:
                parent = frame['parent']
                parent['pieces'].append(''.join(frame['pieces']))
                parent['locators'].append(frame['path'])
                parent['links'].extend(frame['links'])
            else:
                self._flush(frame)
            self.stats['units_processed'] += 1
        if self.stack and self.stack[-1]['streamed']:
            self.stack[-1]['pending'] = (node, frame['streamed'])


def parse_filing_html(html, *, _feed_size=8192, _capture_headings=False, _deadline=None):
    """Return source-text records; COMPLETE means traversal only, not coverage.

    Repeated unmarked major headings are ambiguous (TOC or multiple documents),
    so no records are admitted. Marked navigation is excluded first. DOM paths
    resolve against lxml's document model of the *unchanged* input (as in
    ``document_fromstring``), not a fragment wrapper or pruned/reordered tree.
    """
    if type(_feed_size) is not int or not 0 < _feed_size <= 65536:
        raise ValueError('feed size must be an integer from 1 to 65536')
    deadline = monotonic() + _MAX_PARSE_SECONDS
    if _deadline is not None:
        if type(_deadline) not in (int, float) or not math.isfinite(_deadline):
            raise ValueError('deadline must be a finite monotonic time')
        deadline = min(deadline, _deadline)

    def check_time():
        # Cooperative, including reducer work; not preemption of libxml calls.
        if monotonic() >= deadline:
            raise _ParseLimit('HTML_TIME_LIMIT')

    out = {'status': 'UNSUPPORTED', 'errors': [], 'records': [], 'source_sha256': None,
           'parser_version': 'filing_html_v2', 'fact_validated': False,
           'document_complete': False, 'streaming': {
               'total_nodes': 0, 'peak_active_nodes': 0, 'units_processed': 0,
               'accepted_grid_slots': 0, 'accepted_origin_cells': 0,
               'constructed_locator_bytes': 0,
               'feed_size': _feed_size}}
    if _capture_headings is True:
        out['heading_events'] = []
    if not isinstance(html, str) or not html.strip():
        out['errors'].append('HTML_EMPTY_OR_INVALID')
        return out
    if len(html) > MAX_HTML_BYTES:
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_BYTE_LIMIT'])
        return out
    try:
        encoded = html.encode('utf-8')
    except UnicodeEncodeError:
        out['errors'].append('HTML_ENCODING_INVALID')
        return out
    if len(encoded) > MAX_HTML_BYTES:
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_BYTE_LIMIT'])
        return out
    out['source_sha256'] = hashlib.sha256(encoded).hexdigest()
    if '\ufffd' in html or re.search(r'<!ENTITY\b', html, re.IGNORECASE):
        out['errors'].append('HTML_ENCODING_OR_ENTITY_UNSUPPORTED')
        return out
    try:
        check_time()
        parser = etree.HTMLPullParser(events=('start', 'end', 'comment'),
                                      encoding='utf-8', no_network=True)
        stream = _Stream(out)
        for offset in range(0, len(encoded), _feed_size):
            check_time()
            parser.feed(encoded[offset:offset + _feed_size])
            check_time()
            for kind, node in parser.read_events():
                check_time()
                stream.event(kind, node)
        check_time()
        parser.close()
        check_time()
        for kind, node in parser.read_events():
            check_time()
            stream.event(kind, node)
        check_time()
    except _ParseLimit as error:
        out.update(status='LIMIT_EXCEEDED', errors=[str(error)], records=[])
        if 'heading_events' in out:
            out['heading_events'] = []
        return out
    except _ParseStructure as error:
        out.update(status='UNSUPPORTED', errors=[str(error)], records=[])
        if 'heading_events' in out:
            out['heading_events'] = []
        return out
    except (ValueError, etree.ParserError, etree.XMLSyntaxError):
        out['records'] = []
        if 'heading_events' in out:
            out['heading_events'] = []
        out['errors'].append('HTML_PARSE_FAILED')
        return out
    if any(error.level_name == 'FATAL' or 'depth' in error.message.lower() for error in parser.feed_error_log):
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_PARSE_LIMIT'], records=[])
        if 'heading_events' in out:
            out['heading_events'] = []
        return out
    if any(error.level_name == 'ERROR' for error in parser.feed_error_log):
        out['errors'].insert(0, 'HTML_RECOVERED_WITH_ERRORS')
    if any(n > 1 for n in stream.reducer.major_counts.values()):
        out['records'] = []
        if 'heading_events' in out:
            out['heading_events'] = []
        out['errors'].append('AMBIGUOUS_MAJOR_HEADINGS')
        return out
    out['errors'] = list(dict.fromkeys(out['errors']))
    out['status'] = 'PARTIAL' if out['errors'] else 'COMPLETE'
    if not out['records'] and not out['errors']:
        out.update(status='UNSUPPORTED', errors=['NO_BODY_RECORDS'])
    return out
