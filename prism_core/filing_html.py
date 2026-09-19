"""Bounded filing HTML evidence, with DOM locators into the hashed representation.

No issuer/date/fact verification and no numeric conversion. A provider-cleaned
HTML hash is not an original-response hash. Callers retain that distinction.
"""
import hashlib
import json
import re
from collections import Counter

from lxml import etree
from lxml import html as lhtml

from prism_core.filing_html_tables import parse_html_table

_MAJOR = re.compile(r'^(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)[.)]\s*\S.{0,110}$')
_NUMBER = re.compile(r'^\d{1,3}(?:-\d{1,3})*[.)]\s*\S.{0,100}$')
_SUB = re.compile(r'^(?:\(\d{1,3}\)|[가-힣][.)])\s*\S.{0,100}$')
_CONTEXT = re.compile(r'단\s*위\s*[:：]|제\s*\d+\s*기|(?:당|전)(?:반기|분기|기)(?:말)?|재무상태표|손익계산서|현금흐름표')
_FOOT = re.compile(r'^(?:※|주\s*\d*\s*[):：.]|\(주\s*\d*\)|\(\*\d*\)|\*\d*\))')
_SKIP = {'head', 'script', 'style', 'noscript', 'iframe', 'object', 'embed', 'nav'}
_BLOCK = {'p', 'div', 'section', 'article', 'table', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'title'}


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


def _events(node):
    if not isinstance(node.tag, str) or _hidden(node):
        return
    if node.tag == 'table':
        path = node.getroottree().getpath(node)
        yield node, _text(node), path, [path]
        return
    descendants = any(isinstance(n.tag, str) and n.tag in _BLOCK for n in node.iterdescendants())
    if not descendants:
        path = node.getroottree().getpath(node)
        yield node, _text(node), path, [path]
        return
    path = node.getroottree().getpath(node)
    text_index = 0
    pieces, locators = [], []
    if node.text is not None:
        text_index += 1
        pieces.append(node.text)
        locators.append(f'{path}/text()[{text_index}]')
    for child in node:
        structural = (child.tag in _BLOCK or any(
            isinstance(n.tag, str) and n.tag in _BLOCK for n in child.iterdescendants()))
        if structural:
            if pieces:
                yield node, ' '.join(''.join(pieces).split()), locators[0], locators
                pieces, locators = [], []
            yield from _events(child)
        elif isinstance(child.tag, str) and not _hidden(child):
            pieces.append(_text(child, normalized=False))
            locators.append(child.getroottree().getpath(child))
        if child.tail is not None:
            text_index += 1
            pieces.append(child.tail)
            locators.append(f'{path}/text()[{text_index}]')
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
        return 4 if re.match(r'^\d+-\d+', text) or notes and '재무제표' not in text else 3
    if _SUB.fullmatch(text):
        return 5
    if node.tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
        return int(node.tag[1])
    return None


def parse_filing_html(html):
    """Return source-text records; COMPLETE means traversal only, not coverage.

    Repeated unmarked major headings are ambiguous (TOC or multiple documents),
    so no records are admitted. Marked navigation is excluded first. DOM paths
    resolve against lxml's parsed *unchanged* input, not a pruned/reordered tree.
    """
    out = {'status': 'UNSUPPORTED', 'errors': [], 'records': [], 'source_sha256': None,
           'parser_version': 'filing_html_v1', 'fact_validated': False,
           'document_complete': False}
    if not isinstance(html, str) or not html.strip():
        out['errors'].append('HTML_EMPTY_OR_INVALID')
        return out
    if len(html) > 2 * 1024 * 1024:
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_BYTE_LIMIT'])
        return out
    try:
        encoded = html.encode('utf-8')
    except UnicodeEncodeError:
        out['errors'].append('HTML_ENCODING_INVALID')
        return out
    if len(encoded) > 2 * 1024 * 1024:
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_BYTE_LIMIT'])
        return out
    out['source_sha256'] = hashlib.sha256(encoded).hexdigest()
    if '\ufffd' in html or re.search(r'<!ENTITY\b', html, re.IGNORECASE):
        out['errors'].append('HTML_ENCODING_OR_ENTITY_UNSUPPORTED')
        return out
    try:
        parser = lhtml.HTMLParser(no_network=True)
        root = lhtml.fromstring(html, parser=parser)
    except (ValueError, etree.ParserError):
        out['errors'].append('HTML_PARSE_FAILED')
        return out
    if any(error.level_name == 'FATAL' or 'depth' in error.message.lower() for error in parser.error_log):
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_PARSE_LIMIT'])
        return out
    if any(error.level_name == 'ERROR' for error in parser.error_log):
        out['errors'].append('HTML_RECOVERED_WITH_ERRORS')
    nodes = list(root.iter())
    if len(nodes) > 30000:
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_NODE_LIMIT'])
        return out
    # lxml truncates very deep trees by default; do not call that a full parse.
    if any(len(list(n.iterancestors())) > 100 for n in nodes):
        out.update(status='LIMIT_EXCEEDED', errors=['HTML_DEPTH_LIMIT'])
        return out
    events = [(n, t, p, ps) for n, t, p, ps in _events(root) if t and not _navigation(n, t)]
    counts = Counter(t for n, t, _, _ in events if n.tag != 'table' and _MAJOR.fullmatch(t))
    if any(n > 1 for n in counts.values()):
        out['errors'].append('AMBIGUOUS_MAJOR_HEADINGS')
        return out
    path, scope, notes = {}, 'unknown', False
    footnote_target, context_records = None, []
    records = out['records']
    for node, text, source_path, source_paths in events:
        level = None if _FOOT.match(text) else _heading(node, text, notes)
        if level is not None:
            footnote_target, context_records = None, []
            path = {k: v for k, v in path.items() if k < level}
            path[level] = text
            if level <= 3:
                scope = ('consolidated' if '연결' in text else 'standalone') if '재무제표' in text else 'unknown'
                notes = '재무제표' in text and '주석' in text
            continue
        if len(records) >= 2000:
            out['errors'].append('HTML_RECORD_LIMIT')
            break
        record = {'kind': 'prose', 'text': text, 'section_path': [path[k] for k in sorted(path)],
                  'scope': scope, 'source_path': source_path, 'source_paths': source_paths,
                  'context_before': '', 'footnotes': ''}
        if node.tag == 'table':
            footnote_target = None
            table = parse_html_table(node)
            if table['status'] != 'COMPLETE':
                out['errors'].append('TABLE_' + table['status'])
                context_records = []
                continue
            # Caption/period/unit lines remain literal; no guessed multiplier.
            preceding = []
            for previous in reversed(context_records[-3:]):
                if (previous['kind'] != 'prose' or previous['section_path'] != record['section_path']
                        or len(previous['text']) > 240 or not _CONTEXT.search(previous['text'])):
                    break
                preceding.insert(0, previous['text'])
            captions = [_text(c) for c in node.xpath('./caption') if not _hidden(c)]
            record['context_before'] = '\n'.join([*preceding, *captions])
            cells = [{k: c[k] for k in ('row', 'col', 'rowspan', 'colspan', 'text')}
                     for c in table['cells']]
            record.update(kind='table', table=table, text=json.dumps({'cells': cells}, ensure_ascii=False, separators=(',', ':')))
            footnote_target, context_records = record, []
        elif _FOOT.match(text) and footnote_target is not None:
            footnote_target['footnotes'] += ('\n' if footnote_target['footnotes'] else '') + text
            footnote_target.setdefault('footnote_paths', []).extend(record['source_paths'])
            continue
        else:
            footnote_target = None
            context_records.append(record)
            context_records = context_records[-3:]
        records.append(record)
    out['errors'] = list(dict.fromkeys(out['errors']))
    out['status'] = 'PARTIAL' if out['errors'] else 'COMPLETE'
    if not records and not out['errors']:
        out.update(status='UNSUPPORTED', errors=['NO_BODY_RECORDS'])
    return out
