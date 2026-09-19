"""Pure, bounded coverage manifest. Input presence is never validated insight.

No I/O, tools, ranking, or source-body duplication. This describes which supplied
inputs need interpretation; it must not authorize skipping existing research.
"""
import hashlib
import json
import math
import re
from datetime import date

_CATEGORIES = {
    "business_segments": ("company_overview", ("company_profile", "company_research_profile", "segment_revenue"), ("segment_scope", "revenue_mix")),
    "financial_quality_valuation": ("company_status", ("stock_info", "financial_statements"), ("period", "accounting_scope", "valuation_basis")),
    "ownership_governance": ("company_overview", ("holder_info",), ("ownership_date", "control", "governance")),
    "earnings_estimates_guidance": ("company_status", ("analysis_estimates", "recommendations"), ("estimate_period", "consensus_coverage", "issuer_guidance")),
    "direct_peers_competitive_position": ("news_analysis", (), ("direct_peer_relationship", "business_scope", "comparable_metric")),
    "industry_price_leadership": ("price_volume_analysis", ("stock_ohlcv",), ("named_universe", "same_period_returns", "coverage")),
    "market_rotation_participation": ("market_index_analysis", ("market_indices", "kospi_index", "kosdaq_index"), ("breadth", "rotation_window", "universe")),
    "flows_positioning": ("institutional_holdings_analysis", ("trading_volume", "flow_evidence"), ("investor_class", "flow_window", "net_vs_gross")),
    "catalysts_risks_counterevidence": ("news_analysis", ("social_sentiment",), ("publication_time", "event_status", "counterevidence")),
}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _present(value):
    return isinstance(value, (str, dict, list)) and bool(value.strip() if isinstance(value, str) else value)


def _reference(key, value):
    try:
        digest = hashlib.sha256(_encode(value).encode()).hexdigest()[:16]
    except (TypeError, ValueError, RecursionError):
        return None
    return {"input": key, "sha256_16": digest}


def _target_price_observation(market, symbol, reference_date, macro_context):
    """Only one observed KR classification group, never global/business leaders."""
    packet = macro_context.get('snapshot_price_leaders')
    sector_map = macro_context.get('sector_map')
    if market != 'KR' or not isinstance(packet, dict) or not isinstance(sector_map, dict):
        return None
    if (packet.get('contract') != 'snapshot_price_leaders_v1'
            or packet.get('metric') != 'snapshot_vs_previous_close_return_pct'
            or packet.get('classification_kind') != 'kis_sector'
            or packet.get('status') not in {'OK', 'PARTIAL'}
            or packet.get('is_business_leadership') is not False
            or packet.get('is_market_cap_rank') is not False
            or packet.get('intraday') is not True):
        return None
    try:
        current = date.fromisoformat(packet['observed_date'])
        prior = date.fromisoformat(packet['prior_date'])
        compact_date = str(reference_date).replace('-', '')
        cutoff = date.fromisoformat(f'{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:]}')
    except (TypeError, ValueError, KeyError):
        return None
    if not prior < current <= cutoff:
        return None
    group_name = sector_map.get(symbol)
    groups = packet.get('groups')
    if not isinstance(group_name, str) or not isinstance(groups, dict):
        return None
    group_name = group_name.strip()
    group = groups.get(group_name)
    if not isinstance(group, dict) or not isinstance(group.get('leaders'), list):
        return None
    if not 0 < len(group['leaders']) <= 3:
        return None
    for row in group['leaders']:
        if (not isinstance(row, dict) or not isinstance(row.get('ticker'), str)
                or not re.fullmatch(r'\d{6}', row['ticker'])
                or type(row.get('return_pct')) not in (int, float)
                or not math.isfinite(row['return_pct'])
                or type(row.get('rank')) is not int or row['rank'] < 1
                or type(row.get('tie_count')) is not int or row['tie_count'] < 1):
            return None
    result = {key: packet[key] for key in ('metric', 'classification_kind', 'source', 'observed_date', 'prior_date', 'universe_sha256',
              'intraday', 'is_business_leadership', 'is_market_cap_rank', 'coverage', 'universe_scope') if key in packet}
    result.update(classification=group_name, group=group,
                  interpretation='Observed KIS classification-group intraday price strength, NOT whole-industry or business leadership.')
    return result if len(_encode(result).encode()) <= 1300 else None


def _sources(prefetched):
    """Only actual injected JSON excerpts count; receipts alone never count."""
    packet = prefetched.get("insight_prefetch", prefetched.get("report_research"))
    notes = packet.get("section_notes") if isinstance(packet, dict) else None
    result = {topic: [] for topic in _CATEGORIES}
    if not isinstance(notes, dict):
        return result
    for section in ("news_analysis", "company_overview", "company_status"):
        note = notes.get(section)
        if not isinstance(note, str) or len(note.encode("utf-8")) > 6000:
            continue
        try:
            body = json.loads(note)
        except (ValueError, RecursionError):
            continue
        sources = body.get("sources") if isinstance(body, dict) else None
        if not isinstance(sources, list):
            continue
        for source in sources[:8]:
            if not isinstance(source, dict):
                continue
            sid, excerpt, topic = source.get("source_id"), source.get("excerpt"), source.get("topic")
            if (isinstance(topic, str) and topic in result
                    and isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid)
                    and isinstance(excerpt, str) and excerpt.strip()):
                result[topic].append({"source_id": sid, "source_section": section,
                                      "sha256_16": hashlib.sha256(excerpt.encode()).hexdigest()[:16]})
    return result


def _bounded(manifest, max_bytes):
    if max_bytes < 160:
        raise ValueError("max_bytes must be at least 160")
    while len(_encode(manifest).encode()) > max_bytes and manifest["categories"]:
        manifest["categories"].pop(next(reversed(manifest["categories"])))
        manifest["omitted_categories"] += 1
    if manifest["omitted_categories"]:
        manifest["omission_reason"] = "BYTE_BUDGET_WHOLE_CATEGORY_OMISSION"
        while len(_encode(manifest).encode()) > max_bytes and manifest["categories"]:
            manifest["categories"].pop(next(reversed(manifest["categories"])))
            manifest["omitted_categories"] += 1
    if len(_encode(manifest).encode()) > max_bytes:
        return {"categories": {}, "omitted_categories": len(_CATEGORIES),
                "omission_reason": "BYTE_BUDGET_WHOLE_CATEGORY_OMISSION"}
    return manifest


def build_insight_manifest(market, symbol, reference_date, prefetched, macro_context, *, max_bytes=6000):
    """Track nine coverage dimensions without claiming source verification.

    Even a supplied ranking/verified flag is not accepted: this module has no
    provenance validator. Rankings need named constituents, period, metric,
    source and coverage validation upstream; absent that, insight stays UNKNOWN.
    """
    prefetched = prefetched if isinstance(prefetched, dict) else {}
    macro_context = macro_context if isinstance(macro_context, dict) else {}
    safe = lambda v: v if isinstance(v, str) and len(v) <= 32 else "UNKNOWN"
    manifest = {"version": "insight_manifest_v1", "market": safe(market), "symbol": safe(symbol),
                "reference_date": safe(reference_date), "categories": {}, "omitted_categories": 0}
    sources = _sources(prefetched)
    for name, (section, keys, dimensions) in _CATEGORIES.items():
        refs = []
        for key in keys:
            value = prefetched.get(key)
            if _present(value):
                ref = _reference(key, value)
                if ref:
                    refs.append(ref)
        if name == "market_rotation_participation":
            for key in ("market_intelligence", "market_participation", "leading_sectors"):
                if _present(macro_context.get(key)):
                    ref = _reference("macro." + key, macro_context[key])
                    if ref:
                        refs.append(ref)
        refs.extend(sources[name])
        if name == "flows_positioning" and market == "KR":
            section = "investor_trading_analysis"
        manifest["categories"][name] = {
            "owner_section": section, "collection_status": "INPUT_PRESENT" if refs else "UNKNOWN",
            "insight_status": "UNKNOWN", "input_refs": refs,
            "unknown_dimensions": list(dimensions),
            "unanswered": ["Verify source, date and scope before drawing a conclusion.",
                           "Resolve missing dimensions; input presence is not proof."],
        }
        if name == 'industry_price_leadership':
            observation = _target_price_observation(market, symbol, reference_date, macro_context)
            if observation:
                manifest['categories'][name].update(collection_status='INPUT_PRESENT',
                    insight_status='DESCRIPTIVE_OBSERVED_GROUP_ONLY', observation=observation,
                    unknown_dimensions=['business_leadership', 'narrow_industry_equivalence'],
                    unanswered=['Business leadership remains UNKNOWN; this is an observed price comparison only.'])
    return _bounded(manifest, max_bytes)


def section_manifest(manifest, section, max_bytes=1800):
    """Render only a section's compact pointers/questions; never duplicate bodies."""
    categories = manifest.get("categories", {}) if isinstance(manifest, dict) else {}
    categories = categories if isinstance(categories, dict) else {}
    result = {"categories": {key: value for key, value in categories.items()
                             if isinstance(value, dict) and value.get("owner_section") == section},
              "omitted_categories": 0}
    return _encode(_bounded(result, max_bytes))
