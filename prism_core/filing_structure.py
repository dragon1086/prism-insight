"""Lossless, conservative Markdown filing blocks; no financial inference or ranking.

Offsets are Python character offsets into the original string, not UTF-8 bytes.
Tables remain atomic even without delimiter rows. Heading text remains in blocks;
link-only navigation is skipped. Context fields quote source text, never infer dates.
"""

import hashlib
import re

_ROMAN = re.compile(r'^(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+\S')
_NUMBER = re.compile(r'^\d{1,3}(?:-\d{1,3})*\.\s+\S')
_SUBNOTE = re.compile(r'^\(\d{1,3}\)\s*\S')
_UNIT = re.compile(r'단\s*위\s*[:：]')
_PERIOD = re.compile(r'(?:제\s*\d+\s*기|\d{4}\s*년.*(?:현재|부터|까지)|(?<![가-힣])(?:당|전)(?:분기|반기|기)(?:말)?(?![가-힣]))')
_FOOT = re.compile(r'^(?:주\s*\d*\s*[)）:：.]|\(주\s*\d*\)|※|\(\*\d*\)|\*\d+\))')


def _label(line):
    return re.sub(r'^#{1,6}\s+', '', line.strip()).replace('\\.', '.').strip('* ')


def _heading(line, in_notes):
    """Ambiguous numbered prose remains prose, rather than becoming a fact label."""
    label = _label(line)
    if '[' in label and '](' in label:
        return None
    if _ROMAN.match(label):
        return 2, label
    markdown = re.match(r'^\s*(#{1,6})\s+', line)
    if markdown:
        return len(markdown[1]), label
    if len(label) > 90 or re.search(r'(?:니다|이다|있다|한다|같다)[.。]?$', label):
        return None
    if in_notes and _NUMBER.match(label):
        return 4, label
    if _SUBNOTE.match(label):
        return 5, label
    return None


def _table(line):
    return line.lstrip().startswith('|') and line.count('|') >= 2


def _context(lines):
    joined = ' '.join(line.strip() for line in lines)
    # Caption-shaped only: do not attach ordinary paragraphs mentioning a date.
    return len(joined) < 240 and bool(_UNIT.search(joined) or _PERIOD.search(joined))


def _caption_table(lines):
    cells = [cell.strip() for line in lines for cell in line.strip().strip('|').split('|')
             if cell.strip() and not re.fullmatch(r'[:\-\s]+', cell)]
    if not cells:
        return False
    if all(_context([cell]) for cell in cells):
        return True
    titles = [cell for cell in cells if not _context([cell])]
    return (len(titles) == 1 and len(titles[0]) <= 80
            and not re.search(r'\d|[%％]', titles[0])
            and any(_UNIT.search(cell) for cell in cells)
            and any(_PERIOD.search(cell) for cell in cells))


def parse_filing(text):
    """Return exact-span prose/table dicts with hierarchy, scope and local context.

    ``text`` and ``source_spans`` recover the same original contiguous block.
    ``section_path`` normalizes only heading markup. ``period_context`` and
    ``unit_context`` contain exact caption lines *within* that block; absence
    means unknown. No total-size limits or issuer-specific assumptions apply.
    """
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    blocks, path = [], {}
    scope, in_notes, i = 'unknown', False, 0

    def emit(start, end, kind, is_heading=False):
        raw = text[offsets[start]:offsets[end]]
        section_path = [path[k] for k in sorted(path)]
        identity = f'{offsets[start]}:{offsets[end]}:{section_path}:{scope}:{raw}'
        caption_lines = [line.rstrip('\r\n') for line in lines[start:end]
                         if _context([line])]
        blocks.append({
            'block_id': 'filing-' + hashlib.sha256(identity.encode()).hexdigest()[:20],
            'start': offsets[start], 'end': offsets[end],
            'source_spans': [[offsets[start], offsets[end]]], 'text': raw,
            'section_path': section_path, 'kind': kind, 'scope': scope,
            'is_heading': is_heading,
            'period_context': '\n'.join(s for s in caption_lines if _PERIOD.search(s)),
            'unit_context': '\n'.join(s for s in caption_lines if _UNIT.search(s)),
        })

    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        # A link-only table of contents is navigation, never evidence.
        if re.fullmatch(r'\s*(?:[-*+]\s*)?\[[^\]]+\]\([^\n]+\)\s*', lines[i]):
            i += 1
            continue
        heading = _heading(lines[i], in_notes)
        if heading:
            level, label = heading
            path = {k: v for k, v in path.items() if k < level}
            path[level] = label
            if level <= 2:
                scope, in_notes = 'unknown', False
            if level <= 3 and '재무제표' in label:
                scope = 'consolidated' if '연결' in label else 'standalone'
                in_notes = '주석' in label
            elif level <= 3:
                scope, in_notes = 'unknown', False
            emit(i, i + 1, 'prose', is_heading=True)
            i += 1
            continue

        start, probe = i, i
        # Pull immediately preceding unit/period captions into the atomic table.
        while (probe < len(lines) and not _table(lines[probe])
               and (not lines[probe].strip() or _context([lines[probe]]))):
            probe += 1
        if probe < len(lines) and _table(lines[probe]):
            i = probe
            while True:
                table_start = i
                while i < len(lines) and _table(lines[i]):
                    i += 1
                after = i
                while after < len(lines) and not lines[after].strip():
                    after += 1
                if (after < len(lines) and _table(lines[after])
                        and _caption_table(lines[table_start:i])):
                    next_end = after
                    while next_end < len(lines) and _table(lines[next_end]):
                        next_end += 1
                    old_periods = set(_PERIOD.findall(''.join(lines[table_start:i])))
                    next_periods = set(_PERIOD.findall(''.join(lines[after:next_end])))
                    if (_caption_table(lines[after:next_end]) and old_periods and next_periods
                            and old_periods != next_periods):
                        break
                    i = after
                    continue
                break
            # Footnotes can span multiple lines but never swallow another table.
            while i < len(lines):
                after = i
                while after < len(lines) and not lines[after].strip():
                    after += 1
                if after == len(lines):
                    i = after
                    break
                if not _FOOT.match(lines[after].strip()):
                    break
                i = after + 1
                while (i < len(lines) and lines[i].strip() and not _table(lines[i])
                       and not _heading(lines[i], in_notes)):
                    i += 1
            emit(start, i, 'table')
            continue
        i += 1
        while (i < len(lines) and lines[i].strip() and not _table(lines[i])
               and not _heading(lines[i], in_notes)):
            i += 1
        emit(start, i, 'prose')
    return blocks
