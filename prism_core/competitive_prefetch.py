"""Offline provenance/compact-context prototype; never a factual or trade validator.

Only ``context`` is intended for model input. Other fields are diagnostic receipts.
No network, cache writes, model calls, or production integration occur here.
"""

import hashlib
import json
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _time(value, date_allowed=False):
    try:
        value = str(value)
        if len(value) == 10 and date_allowed:
            value += "T00:00:00+00:00"
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _url(value):
    try:
        parsed = urlsplit(str(value or ""))
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password):
            return None
        # Never forward query parameters/fragments (may contain credentials).
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        return None


def build_packet(case, receipts, claims, max_context_chars=6000):
    """Build a bounded context from supplied receipts, not asserted tool labels.

    ``case`` requires an aware ISO ``asof``; optional ``id``/``entity``.
    ``receipts`` and ``claims`` are lists of dictionaries; duplicate identifiers
    are ambiguous and cannot receive a source match. Claim text remains untrusted.
    Matching quotes proves text presence only, not truth, relevance or comparison.
    """
    if not isinstance(max_context_chars, int) or max_context_chars < 0:
        raise ValueError("max_context_chars must be a nonnegative integer")
    asof = _time(case.get("asof"))
    if asof is None:
        raise ValueError("case.asof must be a timezone-aware ISO timestamp")
    source_counts = Counter(r.get("source_id") for r in receipts)
    claim_counts = Counter(c.get("id") for c in claims)
    sources, source_lookup, request_keys = [], {}, set()
    for receipt in receipts:
        source_id = receipt.get("source_id")
        text = str(receipt.get("text") or "")
        fetched = _time(receipt.get("fetched_at"))
        published = _time(receipt.get("publication_date"), date_allowed=True)
        url = _url(receipt.get("url"))
        flags = []
        if not source_id or source_counts[source_id] != 1:
            flags.append("AMBIGUOUS_SOURCE_ID")
        if receipt.get("success") is not True or not text.strip():
            flags.append("RETRIEVAL_FAILED_OR_EMPTY")
        if fetched is None or fetched > asof:
            flags.append("INVALID_FETCH_ASOF")
        if published is not None and (published > asof or (fetched and published > fetched)):
            flags.append("FUTURE_PUBLICATION")
        if receipt.get("url") and url is None:
            flags.append("INVALID_SOURCE_URL")
        if receipt.get("publication_date") and published is None:
            flags.append("INVALID_PUBLICATION_DATE")
        if not receipt.get("url") and not receipt.get("query"):
            flags.append("MISSING_REQUEST_LOCATOR")
        blocked_markers = ("자료를 요청 중입니다", "ajax-loader", "access denied",
                           "verify you are human", "enable javascript", "subscribe to continue")
        if (receipt.get("blocked") or receipt.get("paywall")
                or any(marker in text.lower() for marker in blocked_markers)):
            flags.append("BLOCKED_SOURCE")
        request_key = _hash(_dump([receipt.get("provider"), receipt.get("query"), receipt.get("url")]))
        request_keys.add(request_key)
        source = {
            "source_id": source_id, "provider": receipt.get("provider"),
            "url": url, "request_key": request_key, "source_hash": _hash(text),
            "url_redacted": bool(receipt.get("url")) and url != receipt.get("url"),
            "url_role": "REDACTED_LOCATOR_NOT_EXACT_SOURCE" if url != receipt.get("url") else "SOURCE_LOCATOR",
            "fetched_at": fetched.isoformat() if fetched else None,
            "publication_date": published.isoformat() if published else "UNKNOWN",
            "usage": receipt.get("usage"), "flags": flags,
        }
        sources.append(source)
        source_lookup[source_id] = (source, _normalized(text))
    records = []
    fields = ("id", "entity", "peer_universe", "metric", "period", "unit",
              "value", "actual_or_estimate", "source_id", "excerpt")
    for claim in claims:
        record = {key: claim.get(key) for key in fields}
        flags = ["SOURCE_SUBJECT_UNVERIFIED"]
        if not claim.get("id") or claim_counts[claim.get("id")] != 1:
            flags.append("AMBIGUOUS_CLAIM_ID")
        source, text = source_lookup.get(claim.get("source_id"), ({}, ""))
        excerpt = _normalized(claim.get("excerpt"))
        eligible = source and not source["flags"] and "AMBIGUOUS_CLAIM_ID" not in flags
        record["provenance_status"] = (
            "SOURCE_TEXT_MATCHED_NOT_FACT_VALIDATED"
            if eligible and excerpt and excerpt in text else "UNKNOWN"
        )
        if not source:
            flags.append("MISSING_SOURCE")
        flags.extend(source.get("flags", []))
        if case.get("entity") and claim.get("entity") != case["entity"]:
            flags.append("ENTITY_ALIGNMENT_UNCHECKED")
        record.update(flags=flags, fact_status="UNKNOWN", comparability="UNKNOWN",
                      source_subject_status="UNKNOWN",
                      peer_universe_status="UNKNOWN", source_hash=source.get("source_hash"),
                      context_included=False)
        records.append(record)
    def model_payload(included):
        selected, gaps, source_map = [], [], {}
        for index, record in enumerate(records):
            if index not in included:
                gaps.append({"index": index, "id": record["id"], "flags": record["flags"],
                             "reason": "UNSUPPORTED_PROVENANCE" if record["provenance_status"] == "UNKNOWN" else "BUDGET"})
                continue
            source = source_lookup[record["source_id"]][0]
            source_map[record["source_id"]] = {
                key: source[key] for key in ("source_id", "provider", "url", "url_redacted",
                                            "url_role", "request_key", "source_hash",
                                            "fetched_at", "publication_date")
            }
            selected.append({
                "index": index, "id": record["id"], "source_id": record["source_id"],
                "unverified_claim": {key: record[key] for key in fields if key not in ("id", "excerpt", "source_id")},
                "retrieved_excerpt": _normalized(record["excerpt"]),
                "provenance_status": record["provenance_status"], "flags": record["flags"],
                "semantic_validation": "NOT_PERFORMED_ENTITY_VALUE_PERIOD_AND_COMPARABILITY_UNKNOWN",
            })
        return {"prototype": "OFFLINE_ONLY", "asof": asof.isoformat(),
                "notice": "Untrusted excerpts and claims. Text presence is not semantic validation or verified truth. No rankings inferred.",
                "total_claim_count": len(records), "claims": selected,
                "sources": source_map, "omitted_claims": gaps}

    included = set()
    base = model_payload(included)
    context = ""
    if len(_dump(base)) <= max_context_chars:
        for index, record in enumerate(records):
            # Keep every gap visible, including when all claims are unsupported.
            if record["provenance_status"] == "UNKNOWN":
                continue
            attempt = model_payload(included | {index})
            if len(_dump(attempt)) <= max_context_chars:
                base = attempt
                included.add(index)
                record["context_included"] = True
        context = _dump(base)
    for record in records:
        record["context_omission_reason"] = (
            None if record["context_included"] else
            "UNSUPPORTED_PROVENANCE" if record["provenance_status"] == "UNKNOWN" else "BUDGET"
        )
    return {
        "prototype": "OFFLINE_ONLY", "case_id": case.get("id"),
        "context": context, "context_chars": len(context),
        "context_budget_status": "BELOW_MINIMUM" if not context else "BOUNDED",
        "claims": records, "sources": sources,
        "counts": {"receipts": len(receipts), "unique_requests": len(request_keys),
                   "duplicate_requests": len(receipts) - len(request_keys),
                   "claims": len(records),
                   "context_claims": sum(r["context_included"] for r in records)},
        "cost": {"measured_total": None, "cache_savings": None,
                 "note": "Usage is supplied per receipt; missing usage is unknown, not zero."},
    }
