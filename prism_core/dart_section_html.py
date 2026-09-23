"""Bounded DOM scans for large viewer sections, not a preemptive C timeout."""
import re
from time import monotonic

from lxml import etree

from prism_core.filing_html_policy import MAX_HTML_BYTES

MAX_NODES = 100_000
MAX_DEPTH = 100
TIMEOUT_SECONDS = 10.0


class SectionHTMLLimit(ValueError):
    """Code-only resource rejection."""


class SectionHTML:
    def __init__(self, body):
        self.deadline = monotonic() + TIMEOUT_SECONDS
        if not isinstance(body, str) or len(body) > MAX_HTML_BYTES:
            raise SectionHTMLLimit('SECTION_HTML_BYTES_LIMIT')
        raw = body.encode('utf-8')
        if len(raw) > MAX_HTML_BYTES:
            raise SectionHTMLLimit('SECTION_HTML_BYTES_LIMIT')
        parser = etree.HTMLPullParser(events=('start', 'end', 'comment'), no_network=True,
                                      encoding='utf-8')
        count = depth = 0

        def drain():
            nonlocal count, depth
            for event, _ in parser.read_events():
                self.check()
                if event in {'start', 'comment'}:
                    count += 1
                    if count > MAX_NODES:
                        raise SectionHTMLLimit('SECTION_HTML_NODE_LIMIT')
                if event == 'start':
                    depth += 1
                    if depth > MAX_DEPTH:
                        raise SectionHTMLLimit('SECTION_HTML_DEPTH_LIMIT')
                elif event == 'end':
                    depth -= 1

        try:
            for offset in range(0, len(raw), 8192):
                self.check()
                parser.feed(raw[offset:offset + 8192])
                drain()
            self.check()
            self.root = parser.close()
            drain()
        except (etree.ParserError, etree.XMLSyntaxError) as exc:
            raise SectionHTMLLimit('HTML_INVALID') from exc
        if any(entry.level_name == 'FATAL' or entry.type_name in {'ERR_RESOURCE_LIMIT', 'ERR_INTERNAL_ERROR'}
               for entry in parser.feed_error_log):
            raise SectionHTMLLimit('HTML_INVALID')
        self.check()
        if self.root is None:
            raise SectionHTMLLimit('HTML_INVALID')

    def check(self):
        if monotonic() >= self.deadline:
            raise SectionHTMLLimit('SECTION_HTML_TIMEOUT')

    def nodes(self, tags, root=None):
        for node in (self.root if root is None else root).iter():
            self.check()
            if node.tag in tags:
                yield node
                self.check()

    def text(self, node, *, compact=False, limit=None):
        """Match joined text_content whitespace semantics without whole preview."""
        parts, length, pending_space = [], 0, False
        for chunk in node.itertext():
            self.check()
            for token in re.finditer(r'\s+|\S+', chunk):
                self.check()
                word = token.group()
                if word.isspace():
                    pending_space = bool(length)
                    continue
                if pending_space and not compact:
                    parts.append(' ')
                    length += 1
                pending_space = False
                if limit is not None:
                    word = word[:max(0, limit - length)]
                parts.append(word)
                length += len(word)
                if limit is not None and length >= limit:
                    self.check()
                    return ''.join(parts)[:limit]
            self.check()
        self.check()
        return ''.join(parts)
