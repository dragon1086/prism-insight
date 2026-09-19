"""Bounded recovery of unverified locators, never a substitute for source facts."""
import json
import re

from prism_core.report_research_prefetch import public_url

_REFERENCE = re.compile(r"\[(\d{1,6})\] (https://\S+)")


def discovery_fallback(texts, structured, *, size, digest):
    """Read shallow provider shapes only; omit every provider prose/title/date."""
    if size > 131072 or len(texts) > 4:
        return None
    if any(not isinstance(text, str) or len(text.encode("utf-8")) > 65536 for text in texts):
        return None
    candidates = []
    representations = []
    seen_texts = set()
    truncated = False
    lines_left = 1000
    provider_error = False

    def add(reference, url, representation):
        nonlocal truncated
        if len(candidates) >= 50:
            truncated = True
            return False
        candidates.append((reference, url))
        representation.append((reference, url if isinstance(url, str) else None))
        return True

    def read_text(text, representation, *, allow_object=True):
        nonlocal truncated, lines_left
        if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
            truncated = True
            return
        if text in seen_texts:
            return
        seen_texts.add(text)
        # Limit before parsing. A cut reference block cannot be called terminal.
        lines = text.split("\n", lines_left)
        if len(lines) > lines_left:
            lines_left = 0
            truncated = True
            return
        lines_left -= len(lines)
        if text.lstrip().startswith("{"):
            if not allow_object:
                return
            try:
                value = json.loads(text)
            except (ValueError, RecursionError):
                return
            read_object(value, representation)
            return
        terminal = []
        for line in reversed(lines):
            if not line.strip():
                continue
            match = _REFERENCE.fullmatch(line.strip())
            if match is None:
                break
            terminal.append(match.groups())
        for reference, url in reversed(terminal):
            if not add(reference, url, representation):
                break

    def read_object(value, representation):
        nonlocal provider_error
        if not isinstance(value, dict):
            return
        if value.get("isError") or value.get("success") is False or value.get("error"):
            provider_error = True
            return
        for key in ("search_results", "citations"):
            values = value.get(key)
            if not isinstance(values, list):
                continue
            for index, item in enumerate(values):
                url = item.get("url") if key == "search_results" and isinstance(item, dict) else item
                if not add(str(index + 1), url, representation):
                    break
        read_text(value.get("response", ""), representation, allow_object=False)

    for text in texts:
        representation = []
        read_text(text, representation)
        if representation:
            representations.append(representation)
    representation = []
    read_object(structured, representation)
    if representation:
        representations.append(representation)
    sources, seen_urls = [], set()
    filtered = 0
    for reference, raw_url in candidates:
        url = public_url(raw_url)
        if url is None:
            filtered += 1
        elif url not in seen_urls:
            seen_urls.add(url)
            if len(sources) < 10:
                sources.append({"reference": reference, "url": url})
            else:
                truncated = True
    if not sources or provider_error:
        return None
    return {"status": "UNKNOWN", "reason": "result_limit", "retrieval": "returned",
            "serialized_utf8_bytes": size, "sha256": digest,
            "delivery": "discovery_only", "answer_withheld": True, "sources": sources,
            "source_content_verified": False, "issuer_match": "UNKNOWN", "publication_time": "UNKNOWN",
            "filtered_count": filtered, "candidates_seen": len(candidates), "truncated": truncated,
            "representation_ambiguity": any(value != representations[0] for value in representations[1:]),
            "action": "Verify issuer, original content and publication time. Withheld answer is not no news."}
