"""Offline, conservative TradingView payload triage; not a fact validator.

Returned content is untrusted source material, never executable instructions.
No output authorizes trading or proves completeness, freshness or bar finality.
"""

import json
import math
from collections.abc import Mapping

VERSION = "tv_evidence_v1"
_META = ("id", "view_id", "symbol", "currency", "asof", "timestamp", "published",
         "published_at", "reported", "title", "source", "source_url", "provider",
         "url", "link", "permission", "type", "release_date", "release_next_date",
         "update_mode", "notice", "date", "referenceDate", "scale", "period")
_ROWS = {
    "get_documents": ("items", "documents"),
    "get_news": ("headlines", "items", "news"),
    "get_earnings_calendar": ("items", "earnings", "events"),
    "get_dividends_calendar": ("items", "dividends", "events", "data"),
    "get_economic_calendar": ("result", "items", "events"),
    "get_ohlcv": ("bars", "ohlcv", "data"),
    "get_financial_history": ("series", "data", "history"),
    "get_symbol_data_batch": ("items", "data", "symbols"),
    "run_screener": ("items", "data", "rows"),
}

def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _positive(value):
    return _finite(value) and value > 0


def _metadata(payload):
    return {key: payload[key] for key in _META if key in payload
            and (isinstance(payload[key], str) or _finite(payload[key]))}


def _row(payload):
    result = _metadata(payload)
    if isinstance(payload.get("provider"), Mapping):
        result["provider"] = {k: payload["provider"][k] for k in ("id", "name")
                              if isinstance(payload["provider"].get(k), str)}
    if isinstance(payload.get("paywall"), bool):
        result["paywall"] = payload["paywall"]
    for key in ("open", "high", "low", "close", "volume", "actual", "forecast", "previous",
                "t", "o", "h", "l", "c", "v"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[key] = value
    if isinstance(payload.get("views"), list):
        result["views"] = [_metadata(v) for v in payload["views"][:10] if isinstance(v, Mapping)]
    return result


def _document_row(payload):
    """Preserve discovery labels without deriving submission dates or periods."""
    result = _row(payload)
    identified = any(isinstance(result.get(key), str) and result[key].strip()
                     for key in ("id", "title", "source_url", "url", "link"))
    identified = identified or any(isinstance(view.get("id"), str) and view["id"].strip()
                                   for view in result.get("views", []))
    if not identified:
        return {}  # Empty metadata/labels alone cannot create a document.
    category = payload.get("category")
    if isinstance(category, Mapping):
        fields = {key: category[key] for key in ("id", "title")
                  if isinstance(category.get(key), str) and category[key].strip()}
        if fields:
            result["category"] = fields
    if type(payload.get("fiscal_year")) is int:
        result["fiscal_year"] = payload["fiscal_year"]
    if isinstance(payload.get("fiscal_period"), str):
        result["fiscal_period"] = payload["fiscal_period"]
    if isinstance(payload.get("status"), str):
        result["provider_status"] = payload["status"]
    symbols = payload.get("symbols")
    if isinstance(symbols, list):
        members = sorted({row["symbol"] for row in symbols[:100] if isinstance(row, Mapping)
                          and isinstance(row.get("symbol"), str) and row["symbol"].strip()})
        if members:
            result["symbols"] = members
        if len(symbols) > 100:
            result["symbol_membership_truncated"] = True
    return result


def _document_catalog_metadata(payload):
    fields = {"discovery_only": True, "official_listing_complete": False,
              "publication_basis": "UNVERIFIED_PROVIDER_METADATA"}
    if type(payload.get("total")) is int and payload["total"] >= 0:
        fields["provider_total"] = payload["total"]
    return fields


def _text(ast):
    """Extract only text nodes/paragraphs, with bounded traversal and output."""
    parts, remaining = [], 6000
    stack = [(ast, 0)]
    visited, truncated = 0, False
    while stack and remaining > 0 and visited < 1000:
        node, depth = stack.pop()
        visited += 1
        if depth > 16:
            truncated = True
            continue
        if isinstance(node, str):
            truncated = truncated or len(node) > remaining
            parts.append(node[:remaining])
            remaining -= len(parts[-1])
        elif isinstance(node, list):
            truncated = truncated or len(node) > 1000
            stack.extend((item, depth + 1) for item in reversed(node[:1000]))
        elif isinstance(node, Mapping):
            if node.get("type") in {"script", "style", "instruction", "system"}:
                continue
            for key in ("children", "content", "text", "p"):
                if key in node:
                    stack.append((node[key], depth + 1))
    text = "\n".join(parts)
    return text[:6000].strip(), truncated or bool(stack) or len(text) > 6000


def normalize_evidence(tool, payload, is_error=False, requested_symbols=None):
    """Classify an already-decoded response; preserve unknowns and exact IDs.

    ``usable_for_report`` means bounded research material only, not a verified
    fact. requested_symbols is an optional explicit collection denominator.
    """
    name = tool.removeprefix("mcp-tv-").replace("-", "_")
    out = {"version": VERSION, "tool": name, "status": "MISSING", "reasons": [],
           "usable_for_report": False, "asof": None, "source": None,
           "facts": {}, "research_only": True, "fact_validated": False,
           "bar_finality": "UNKNOWN"}

    def finish(status, reason=None, facts=None):
        out["status"] = status
        if reason:
            out["reasons"].append(reason)
        out["usable_for_report"] = status in {"AVAILABLE", "PARTIAL"}
        if facts is not None:
            out["facts"] = facts
        return out

    if is_error:
        return finish("PAYLOAD_FAILURE", "MCP_ERROR")
    if payload is None:
        return out
    if not isinstance(payload, Mapping):
        return finish("MALFORMED", "EXPECTED_OBJECT")
    if payload.get("success") is False or payload.get("error"):
        return finish("PAYLOAD_FAILURE", "PROVIDER_FAILURE")
    if name in {"get_forecasts", "get_news", "get_news_story", "get_earnings_calendar",
                "get_dividends_calendar", "run_screener"} and isinstance(payload.get("data"), Mapping):
        payload = payload["data"]
        if payload.get("success") is False or payload.get("error"):
            return finish("PAYLOAD_FAILURE", "PROVIDER_FAILURE")
    metadata = _metadata(payload)
    out.update(asof=metadata.get("asof", metadata.get("timestamp")),
               source=metadata.get("source", metadata.get("provider")))
    out["metadata"] = metadata
    if name == "get_forecasts":
        if payload.get("target_mismatch") is True:
            return finish("QUARANTINED", "TARGET_UNIT_MISMATCH")
        if payload.get("currency") not in {"USD", "KRW"}:
            return finish("QUARANTINED", "UNSUPPORTED_OR_MISSING_CURRENCY")
        estimates = payload.get("estimates", {})
        if not isinstance(estimates, Mapping):
            return finish("MALFORMED", "EXPECTED_ESTIMATES_OBJECT")
        price = payload.get("price")
        pe = estimates.get("pe_ratio", payload.get("pe"))
        eps = estimates.get("eps_ttm", payload.get("eps_ttm"))
        if all(_positive(v) for v in (price, pe, eps)):
            ratio = price / (pe * eps)
            out["consistency_ratio"] = ratio
            out["consistency_method"] = "UNVALIDATED_RESEARCH_HEURISTIC_0.8_TO_1.25_NOT_TRADING_GATE"
            if not 0.8 <= ratio <= 1.25:
                return finish("QUARANTINED", "PRICE_PE_EPS_INCONSISTENT_NOT_FX_PROOF")
        numbers = {k: payload[k] for k in ("price", "pe", "target_mean",
                   "target_high", "target_low") if _positive(payload.get(k))}
        if _finite(payload.get("eps_ttm")):
            numbers["eps_ttm"] = payload["eps_ttm"]
        numbers["estimates"] = {k: estimates[k] for k in ("eps_ttm", "pe_ratio", "eps_next_quarter",
            "eps_next_year", "revenue_next_quarter", "revenue_next_year") if _finite(estimates.get(k))
            and (k.startswith("eps") or (estimates[k] >= 0 if k.startswith("revenue") else estimates[k] > 0))}
        out["invalid_numeric_fields"] = [k for k, v in estimates.items()
            if isinstance(k, str) and k.startswith(("eps", "revenue")) and v is not None
            and (not _finite(v) or (k.startswith("revenue") and v < 0))]
        targets = payload.get("price_targets", {})
        if isinstance(targets, Mapping):
            numbers.update({"target_" + k: targets[k] for k in ("average", "high", "low")
                            if _positive(targets.get(k))})
        if not numbers["estimates"]:
            numbers.pop("estimates")
        if not numbers:
            return finish("MISSING", "NO_SUPPORTED_FORECAST_VALUES")
        return finish("AVAILABLE", "PROVIDER_VALUES_NOT_INDEPENDENTLY_VALIDATED", numbers)
    if name == "get_financial_history" and isinstance(payload.get("series"), Mapping):
        labels = payload.get("labels")
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            return finish("MALFORMED", "MISSING_PERIOD_LABELS")
        if not labels:
            return finish("EMPTY", "NO_PERIODS")
        series = {}
        for metric, rows in list(payload["series"].items())[:20]:
            if not isinstance(rows, list) or len(rows) != len(labels):
                return finish("MALFORMED", "PERIOD_SERIES_LENGTH_MISMATCH")
            series[metric] = [{k: v for k, v in row.items() if k in {"value", "yoy_pct"}
                and isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)}
                if isinstance(row, Mapping) else {} for row in rows[:20]]
        if not series or not any(any(row for row in rows) for rows in series.values()):
            return finish("MISSING", "NO_SUPPORTED_FINANCIAL_VALUES")
        return finish("PARTIAL", "CURRENCY_AND_ACCOUNTING_BASIS_NOT_VALIDATED",
                      {"labels": labels[:20], "series": series, **metadata})
    if name in {"get_document_view", "get_news_story"}:
        body = payload.get("astDescription", payload.get("body", payload.get("content")))
        if "ast_description" in payload:
            encoded = payload["ast_description"]
            if not isinstance(encoded, str) or len(encoded) > 200000:
                return finish("MALFORMED", "INVALID_OR_OVERSIZE_AST_JSON")
            try:
                body = json.loads(encoded)
            except (ValueError, RecursionError):
                return finish("MALFORMED", "INVALID_AST_JSON")
        text, out["text_truncated"] = _text(body)
        if not text:
            return finish("MISSING", "BODY_UNAVAILABLE_HEADLINE_IS_NOT_BODY")
        return finish("AVAILABLE", "UNTRUSTED_SOURCE_TEXT", {"text": text, **metadata})
    if name in _ROWS:
        key = next((key for key in _ROWS[name] if key in payload), None)
        if key is None:
            return finish("MISSING", "SUPPORTED_COLLECTION_ABSENT")
        rows = payload[key]
        if name == "get_symbol_data_batch" and isinstance(rows, Mapping):
            rows = [{**row, "symbol": symbol} for symbol, row in rows.items() if isinstance(row, Mapping)]
        if not isinstance(rows, list):
            return finish("MALFORMED", "EXPECTED_ROW_LIST")
        if not rows:
            facts = {"returned_count": 0}
            if name == "get_documents":
                facts.update(_document_catalog_metadata(payload))
            if requested_symbols is not None:
                facts.update(requested_count=len(set(requested_symbols)),
                             observed_symbols=[], missing_symbols=sorted(set(requested_symbols)))
            return finish("EMPTY", "RETURNED_EMPTY_NOT_PROOF_OF_NO_EVENTS", facts)
        normalize_row = _document_row if name == "get_documents" else _row
        valid = [normalize_row(row) for row in rows[:100] if isinstance(row, Mapping) and normalize_row(row)]
        if not valid:
            return finish("MALFORMED", "NO_OBJECT_ROWS")
        facts = {"rows": valid, "returned_count": len(rows)}
        partial = len(valid) != len(rows)
        if name == "get_documents":
            facts.update(_document_catalog_metadata(payload))
            partial = partial or facts.get("provider_total", len(rows)) != len(rows)
            partial = partial or any(row.get("symbol_membership_truncated") for row in valid)
        if requested_symbols is not None:
            requested = set(requested_symbols)
            returned = {row.get("symbol") for row in valid if isinstance(row.get("symbol"), str)}
            if name == "get_documents":
                returned.update(symbol for row in valid for symbol in row.get("symbols", []))
            facts.update(requested_count=len(requested),
                         observed_symbols=sorted(requested & returned),
                         missing_symbols=sorted(requested - returned))
            partial = partial or bool(requested - returned)
        if name == "get_ohlcv":
            out["reasons"].append("PRICE_BASIS_AND_DELAY_REQUIRE_EXPLICIT_SOURCE_METADATA")
        return finish("PARTIAL" if partial else "AVAILABLE", "COLLECTION_NOT_COMPLETENESS_PROOF", facts)
    return finish("UNSUPPORTED", "NO_VALIDATED_SCHEMA_ADAPTER")
