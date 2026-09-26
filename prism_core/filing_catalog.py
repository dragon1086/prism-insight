"""Point-in-time selection over caller-verified periodic filing metadata.

Pure selection, not discovery, source validation or a guarantee that the supplied
catalog is complete. Corrections must represent whole-document editions. Fiscal
labels/event dates are not silently converted to reporting/publication dates.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from prism_core.report_research_prefetch import public_url as _public_url


def public_url(value):
    """Retain only exact DART receipt locators beyond the existing URL policy."""
    if isinstance(value, str) and re.fullmatch(
            r'https://dart\.fss\.or\.kr/dsaf001/main\.do\?rcpNo=\d{14}', value):
        return value
    return _public_url(value)

_KINDS = {"annual", "interim", "quarterly"}
_NON_PERIODIC = {"event", "transcript", "presentation", "press_release", "insider_transaction"}
_SCOPES = {"consolidated", "standalone"}


@dataclass(frozen=True)
class FilingCandidate:
    filing_id: str
    entity_id: str
    source_url: str
    kind: str
    period_start: date | None
    period_end: date | None
    scope: str | None
    published_at: datetime | date | None
    publication_verified: bool
    amendment_of: str | None
    body_status: str


def _publication(row, decision, zone):
    if row.publication_verified is not True:
        return None, "UNVERIFIED_PUBLICATION"
    value = row.published_at
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            return None, "PUBLICATION_TIME_UNKNOWN"
        try:
            lower = upper = value.astimezone(timezone.utc)
            lower.astimezone(zone)
        except (OverflowError, ValueError):
            return None, "INVALID_PUBLICATION"
        precision, exclusive = "timestamp", False
        future = lower > decision
    elif type(value) is date:
        if value == decision.astimezone(zone).date():
            return None, "PUBLICATION_TIME_UNKNOWN"
        try:
            lower = datetime.combine(value, time.min, zone).astimezone(timezone.utc)
            upper = datetime.combine(value + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        except (OverflowError, ValueError):
            return None, "INVALID_PUBLICATION"
        precision, exclusive = "date", True
        future = lower > decision
    else:
        return None, "PUBLICATION_TIME_UNKNOWN"
    if future:
        return None, "FUTURE_PUBLICATION"
    return {"lower": lower.isoformat(), "upper": upper.isoformat(),
            "upper_exclusive": exclusive, "precision": precision}, None


def _series_scope(row):
    return row.entity_id, row.kind, row.scope, row.period_start, row.period_end


def select_periodic_filings(candidates, *, entity_id, scope, decision_at, market_timezone,
                           listing_complete=False):
    """Select best-known base and annual detail supplement without silent fallback.

    A primary_id with INCOMPLETE is explicitly not certified latest. Missing the
    latest body, or an unresolved correction of the current series, gives no
    primary_id. The caller must not promote these receipts into trading signals.
    """
    if (not isinstance(entity_id, str) or not entity_id.strip()
            or not isinstance(scope, str) or scope not in _SCOPES
            or not isinstance(decision_at, datetime) or decision_at.utcoffset() is None
            or type(listing_complete) is not bool or type(candidates) not in (list, tuple)
            or len(candidates) > 1000):
        raise ValueError("Invalid filing selection policy")
    try:
        zone = ZoneInfo(market_timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("Invalid market timezone") from exc
    try:
        decision = decision_at.astimezone(timezone.utc)
        decision.astimezone(zone)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Invalid decision time") from exc
    out = {"status": "NO_ELIGIBLE", "primary_id": None, "latest_candidate_id": None,
           "annual_supplement_id": None, "latest_confirmed": False,
           "reasons": [], "exclusions": [], "publication_bounds": {},
           "blocked_by": [], "fact_validated": False}
    records, unresolved, eligible = {}, set(), set()

    def exclude(key, reason, uncertain=False):
        out["exclusions"].append({"filing_id": key, "reason": reason})
        if uncertain:
            unresolved.add(key)

    for row in candidates:
        if not isinstance(row, FilingCandidate) or not isinstance(row.filing_id, str) or not row.filing_id.strip():
            raise ValueError("Invalid filing candidate identity")
        # repr preserves datetime fold unlike same-zone datetime equality.
        if row.filing_id in records and repr(records[row.filing_id]) != repr(row):
            out.update(status="AMBIGUOUS", reasons=["CONFLICTING_DUPLICATE_ID"])
            return out
        records[row.filing_id] = row
    for key, row in sorted(records.items()):
        if (row.amendment_of is None and isinstance(row.entity_id, str)
                and row.entity_id and row.entity_id != entity_id):
            exclude(key, "ENTITY_KIND_OR_SCOPE_MISMATCH")
            continue
        # A verified future amendment is irrelevant at this cutoff, even when
        # its other metadata would conflict with an earlier edition.
        bounds, reason = _publication(row, decision, zone)
        if reason == "FUTURE_PUBLICATION":
            exclude(key, reason)
            continue
        if (not isinstance(row.entity_id, str) or not row.entity_id.strip()
                or not isinstance(row.kind, str)
                or (row.scope is not None and (not isinstance(row.scope, str) or row.scope not in _SCOPES))):
            exclude(key, "UNRESOLVED_METADATA", True)
            continue
        if row.amendment_of is None and (row.kind in _NON_PERIODIC or (
                row.scope is not None and row.scope != scope)):
            exclude(key, "ENTITY_KIND_OR_SCOPE_MISMATCH")
            continue
        if row.kind not in _KINDS:
            exclude(key, "UNRESOLVED_METADATA", True)
            continue
        if reason:
            exclude(key, reason, True)
            continue
        if (type(row.period_start) is not date or type(row.period_end) is not date
                or row.period_start > row.period_end or row.scope is None
                or not isinstance(row.body_status, str)
                or row.body_status not in {"available", "unread", "unavailable"}
                or not public_url(row.source_url)
                or (row.amendment_of is not None and
                    (not isinstance(row.amendment_of, str) or not row.amendment_of))):
            exclude(key, "UNRESOLVED_METADATA", True)
            continue
        if row.period_end > datetime.fromisoformat(bounds["lower"]).astimezone(zone).date():
            exclude(key, "PERIOD_AFTER_PUBLICATION", True)
            continue
        out["publication_bounds"][key] = bounds
        eligible.add(key)

    def chain(key):
        path = []
        while key in records and key not in path:
            path.append(key)
            parent = records[key].amendment_of
            if parent is None:
                return path
            if not isinstance(parent, str):
                return None
            key = parent
        return None

    chains = {key: chain(key) for key in records}
    original_eligible = eligible.copy()
    for key in sorted(original_eligible):
        path = chains[key]
        if not path or any(ancestor not in original_eligible for ancestor in path):
            exclude(key, "UNRESOLVED_AMENDMENT_CHAIN", True)
            eligible.discard(key)
            continue
        for child, parent in pairwise(path):
            cb, pb = out["publication_bounds"][child], out["publication_bounds"][parent]
            # Same-day editions share one date window; their order then rests on
            # the verified amendment edge, not on publication time.
            same_window = pb["lower"] == cb["lower"] and pb["upper"] == cb["upper"]
            if (_series_scope(records[child]) != _series_scope(records[parent])
                    or (not same_window
                        and datetime.fromisoformat(pb["upper"]) > datetime.fromisoformat(cb["lower"]))):
                exclude(key, "CONFLICTING_AMENDMENT_CHAIN", True)
                eligible.discard(key)
                break

    series = {}
    for key in sorted(eligible):
        series.setdefault(chains[key][-1], []).append(key)
    terminals, ambiguous = {}, set()
    for root, keys in series.items():
        parents = {records[key].amendment_of for key in keys}
        ends = [key for key in keys if key not in parents]
        if len(ends) != 1:
            ambiguous.add(root)
        else:
            terminals[root] = ends[0]
    if not series:
        out["reasons"] = ["UNRESOLVED_CATALOG"] if unresolved else ["NO_ELIGIBLE_PERIODIC_FILING"]
    else:
        latest_period = max(records[keys[0]].period_end for keys in series.values())
        roots = [root for root, keys in series.items() if records[keys[0]].period_end == latest_period]
        if len(roots) != 1 or roots[0] in ambiguous:
            out.update(status="AMBIGUOUS", reasons=["CONFLICTING_LATEST_EDITIONS"])
        else:
            root = roots[0]
            chosen = terminals[root]
            out["latest_candidate_id"] = chosen

            def blockers(series_root):
                return sorted(key for key in unresolved if chains.get(key) and series_root in chains[key])

            out["blocked_by"] = blockers(root)
            if out["blocked_by"]:
                out.update(status="UNRESOLVED_CURRENT_EDITION", reasons=["UNRESOLVED_CORRECTION"])
            elif records[chosen].body_status != "available":
                out.update(status="LATEST_BODY_UNAVAILABLE", reasons=["LATEST_BODY_NOT_AVAILABLE"])
            else:
                incomplete = not listing_complete or bool(unresolved) or bool(ambiguous)
                out.update(status="INCOMPLETE" if incomplete else "SELECTED", primary_id=chosen,
                           latest_confirmed=not incomplete)
                if not listing_complete:
                    out["reasons"].append("CATALOG_COVERAGE_UNCONFIRMED")
                if unresolved or ambiguous:
                    out["reasons"].append("UNRESOLVED_CATALOG")
                if records[chosen].kind != "annual":
                    annuals = [key for series_root, key in terminals.items()
                               if records[key].kind == "annual" and records[key].body_status == "available"
                               and records[key].period_end <= latest_period and not blockers(series_root)]
                    if annuals:
                        latest_annual = max(records[key].period_end for key in annuals)
                        matches = [key for key in annuals if records[key].period_end == latest_annual]
                        if len(matches) == 1:
                            out["annual_supplement_id"] = matches[0]
    out["exclusions"].sort(key=lambda item: (item["filing_id"], item["reason"]))
    return out
