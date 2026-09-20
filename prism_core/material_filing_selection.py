"""Complete, source-bound material-topic candidates; never a trading score.

Groups retain conditions in adjacent same-section prose. Topic breadth replaces
length-driven selection, without treating any topic as confirmed adverse news.
"""
import hashlib
import re
from itertools import zip_longest

from prism_core.filing_materiality import TOPICS, material_topics
from prism_core.filing_selection import _note_key
from prism_core.filing_structure import parse_filing
from prism_core.filing_table_projection import project_table

_POLICY = re.compile(r'중요한\s*회계정책|재무제표\s*작성기준|재무제표\s*작성의\s*기초|significant accounting policies', re.IGNORECASE)
_CONDITIONAL = re.compile(r'다만|그러나|조건|불확실|위반|면제|상환|반환|한도|확정|가정|민감도|허가|임상|취소|해지|승인|provided that|subject to|uncertain|covenant', re.IGNORECASE)
_LIMIT = 2 * 1024 * 1024
_FOOTNOTE_SENTENCE_TAIL = re.compile(r'[.。!?]\s*(?:(?:\([^()（）]*\)|（[^()（）]*）)\s*)+$')


def html_retrieval_class(record):
    """Boolean source-detail classes, never polarity, amounts or risk scores."""
    headings = ' '.join(record.get('section_path', ()))
    generic = re.search(r'일반사항|회계정책|회계추정|금융위험\s*관리|accounting polic|financial risk management',
                        headings, re.IGNORECASE)
    cells = record.get('table', {}).get('cells', []) if record.get('kind') == 'table' else []
    pieces = (cell['text'] for cell in cells if cell.get('tag') != 'th') if cells else (record.get('text', ''),)
    def detail(text, *, footnote=False):
        complete = (text.rstrip().endswith(('.', '。', '!', '?'))
                    or (footnote and _FOOTNOTE_SENTENCE_TAIL.search(text)))
        return bool(complete and (_CONDITIONAL.search(text)
                    or re.search(r'소송|법적|판결|결과|의무|해소|소멸|환입|종결|'
                                 r'\b(?:litigation|lawsuit|proceedings|outcome|obligation|'
                                 r'resolved|reversed|settled|breach)\b', text, re.IGNORECASE)))

    qualified = any(detail(text) for text in pieces)
    footnotes = record.get('footnotes', '')
    if isinstance(footnotes, str):
        # Recognize a complete sentence followed by flat parenthetical detail;
        # neither the sentence nor its qualifying suffix is edited or removed.
        qualified = qualified or detail(footnotes, footnote=True)
    return {'specific_note': not bool(generic or _POLICY.search(headings)),
            'qualified_claim': qualified,
            'context_complete': record.get('scope') in {'consolidated', 'standalone'}
                                and bool(record.get('context_before'))}


def order_material_html_blocks(blocks):
    """Offer complete distinct note families before repeats, then apply cap."""
    if not blocks or not all(b.get('provenance', {}).get('parser_version') == 'material_v2'
            and b['provenance'].get('representation') in {'DART_VIEWER_HTML', 'FIRECRAWL_CLEANED_HTML'}
            for b in blocks):
        return list(blocks)

    def key(block):
        detail = block.get('_retrieval', {})
        return tuple(-int(detail.get(field) is True) for field in (
            'specific_note', 'qualified_claim', 'context_complete'))

    families = {}
    for block in blocks:
        provenance = block['provenance']
        # Captions identify distinct subjects within one large numbered note.
        # Preserve explicit period labels too; never merge different contexts.
        family = (provenance.get('scope'), _note_key({'section_path': provenance.get('section_path', [])}),
                  provenance.get('context_before', ''))
        families.setdefault(family, []).append(block)
    groups = [sorted(rows, key=key) for rows in families.values()]
    groups.sort(key=lambda rows: key(rows[0]))
    return [row for layer in zip_longest(*groups) for row in layer if row is not None]


def material_filing_records(text, *, peer_records=()):
    """Return bounded candidate breadth and omission reasons with exact spans.

    These are unvalidated source review topics. Publication/event date, industry
    applicability and numerical interpretation belong to other explicit stages.
    """
    if type(text) is not str:
        return [], ['MATERIAL_INPUT_INVALID']
    if len(text) > _LIMIT:
        return [], ['MATERIAL_INPUT_LIMIT']
    try:
        if len(text.encode()) > _LIMIT:
            return [], ['MATERIAL_INPUT_LIMIT']
    except UnicodeEncodeError:
        return [], ['MATERIAL_INPUT_INVALID']
    blocks = parse_filing(text)
    groups, active = [], None

    def append_group(record):
        raw = text[record['start']:record['end']]
        if raw != record['text']:
            record = {**record, 'text': raw, 'source_spans': [[record['start'], record['end']]],
                      'block_id': 'material-' + hashlib.sha256(f"{record['start']}:{raw}".encode()).hexdigest()[:20]}
        groups.append(record)

    for block in blocks:
        if block.get('is_heading'):
            if active is not None:
                append_group(active)
                active = None
            continue
        if (active is not None and block['kind'] == active['kind'] == 'prose'
                and block['section_path'] == active['section_path']
                and block['scope'] == active['scope']
                and not text[active['end']:block['start']].strip()):
            active = {**active, 'end': block['end']}
        else:
            if active is not None:
                append_group(active)
            active = dict(block)
    if active is not None:
        append_group(active)
    candidates, gaps, safe_peers, peer_groups = [], [], [], []
    for record in groups:
        tags = material_topics(record['text'], record['section_path'])
        peers = [peer for peer in peer_records if record['start'] <= peer['start']
                 and peer['end'] <= record['end'] and record['scope'] == peer['scope']
                 and record['section_path'] == peer['section_path']]
        if not tags:
            safe_peers.extend(peers)
        if not tags or _POLICY.search(' '.join(record['section_path'])):
            continue
        size = len(record['text'].encode())
        if record['kind'] == 'prose' and size > 3600:
            gaps.append('MATERIAL_PROSE_GROUP_OVERSIZE')
            continue
        if record['kind'] == 'table':
            record = project_table(record, text, max_bytes=2400) or record
            if len(record['text'].encode()) > 3600:
                gaps.append('MATERIAL_TABLE_OVERSIZE')
                continue
        # Use metadata only for retrieval order, not a model-visible risk score.
        # Explicit clauses and complete topic-specific notes precede bare tables.
        specificity = (
            not bool(re.search(r'일반사항|회계정책|회계추정|금융위험\s*관리|accounting polic|financial risk management',
                               ' '.join(record['section_path']), re.IGNORECASE)),
            record['scope'] == 'consolidated',
            any('주석' in p for p in record['section_path']),
            bool(re.search(r'\d[\d,.]*\s*(?:%|％|원|천원|백만원|억원|USD|KRW)|\|[^\n]*\d', record['text'])),
            bool(_CONDITIONAL.search(record['text'])),
            bool(re.search(r'\d', record['text'])),
        )
        candidates.append(({**record, 'material_topics': tags}, specificity))
        if peers:
            peer_groups.append({**record, 'material_topics': tags})
    queues = []
    for topic in TOPICS:
        rows = [(record, rank) for record, rank in candidates if topic in record['material_topics']]
        rows.sort(key=lambda pair: tuple(-int(v) for v in pair[1]))
        first, repeated, families = [], [], set()
        for pair in rows:
            family = _note_key(pair[0])
            (repeated if family in families else first).append(pair)
            families.add(family)
        rows = first + repeated
        queues.append([record for record, _ in rows[:12]])
        if len(rows) > 12:
            gaps.append('MATERIAL_CANDIDATE_LIMIT')
    output, seen = [], set()
    for record in [*safe_peers, *peer_groups]:
        if record['block_id'] not in seen:
            output.append(record)
            seen.add(record['block_id'])
    for group in zip_longest(*queues):
        for record in group:
            if record is None or record['block_id'] in seen:
                continue
            output.append(record)
            seen.add(record['block_id'])
    return output, list(dict.fromkeys(gaps))


def material_html_records(records):
    """Keep adjacent HTML qualifications together using parser event indices.

    Headers, tables (including rejected tables) and scope changes break groups.
    All DOM paths are retained. Oversized prose is omitted as a whole.
    """
    groups, active = [], []
    for record in records:
        if record.get('layout_role') or record.get('context_incomplete'):
            if active:
                groups.append(active)
                active = []
            continue
        previous = active[-1] if active else None
        adjacent = (previous is not None and previous['kind'] == record['kind'] == 'prose'
                    and type(previous.get('event_index')) is int and type(record.get('event_index')) is int
                    and record['event_index'] == previous['event_index'] + 1
                    and previous['scope'] == record['scope']
                    and previous['section_path'] == record['section_path'])
        if not adjacent and active:
            groups.append(active)
            active = []
        active.append(record)
    if active:
        groups.append(active)
    output, gaps = [], []
    for group in groups:
        first = group[0]
        if first['kind'] != 'prose':
            output.append(first)
            continue
        if any(type(record.get('event_index')) is not int for record in group):
            gaps.append('MATERIAL_HTML_ADJACENCY_UNKNOWN')
            continue
        text = '\n'.join(record['text'] for record in group)
        if len(text.encode()) > 3600:
            gaps.append('MATERIAL_PROSE_GROUP_OVERSIZE')
            continue
        paths = [path for record in group for path in record['source_paths']]
        output.append({**first, 'text': text, 'source_paths': paths})
    return output, list(dict.fromkeys(gaps))
