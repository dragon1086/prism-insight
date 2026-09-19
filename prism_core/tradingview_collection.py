"""Opt-in, injected read-only research collection. No production transport.

Byte budgets constrain decoded JSON admission, not network/receive memory.
Timeouts depend on cooperative asyncio cancellation. No evidence authorizes orders.
"""

import asyncio
import json
import math
from dataclasses import dataclass, fields

READ_TOOLS = frozenset({
    "get_ohlcv", "get_documents", "get_document_view", "get_news", "get_news_story",
    "get_financial_history", "get_forecasts", "get_earnings_calendar",
    "get_dividends_calendar", "get_economic_calendar",
})
_MAXIMA = {"max_calls": 8, "per_call_seconds": 30, "total_seconds": 90,
           "max_response_bytes": 1048576, "max_evidence_bytes": 24000,
           "total_evidence_bytes": 72000}
_MAX_REQUEST_BYTES = 4096


@dataclass(frozen=True)
class TradingViewOptions:
    max_calls: int = 4
    per_call_seconds: float = 15
    total_seconds: float = 45
    max_response_bytes: int = 262144
    max_evidence_bytes: int = 12000
    total_evidence_bytes: int = 36000

    @classmethod
    def parse(cls, config):
        """Malformed or unspecified configuration fails closed, without I/O."""
        if type(config) is not dict or config.get("enabled") is not True:
            return None
        if set(config) - {"enabled", *_MAXIMA}:
            return None
        values = {}
        for field in fields(cls):
            value = config.get(field.name, field.default)
            duration = field.name.endswith("seconds")
            types = (int, float) if duration else (int,)
            if type(value) not in types or not 0 < value <= _MAXIMA[field.name]:
                return None
            if duration and not math.isfinite(value):
                return None
            values[field.name] = value
        return cls(**values)


@dataclass(frozen=True)
class ReadRequest:
    tool: str
    arguments: dict
    requested_symbols: tuple[str, ...] | None = None


def _json_bytes(value):
    """Reject Python-only values and non-string keys rather than coercing them."""
    def validate(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise ValueError("INVALID_JSON")

    validate(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _requests(requests, options):
    if type(requests) not in (list, tuple) or len(requests) > options.max_calls:
        raise ValueError("INVALID_REQUEST")
    copied = []
    for request in requests:
        if not isinstance(request, ReadRequest):
            raise TypeError("INVALID_REQUEST")
        if type(request.tool) is not str or request.tool not in READ_TOOLS:
            raise ValueError("INVALID_REQUEST")
        if type(request.arguments) is not dict:
            raise ValueError("INVALID_REQUEST")
        symbols = request.requested_symbols
        if symbols is not None and (type(symbols) is not tuple or not all(
                type(symbol) is str and symbol.strip() for symbol in symbols)):
            raise ValueError("INVALID_REQUEST")
        encoded = _json_bytes({"tool": request.tool, "arguments": request.arguments,
                               "requested_symbols": list(symbols) if symbols is not None else None})
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise ValueError("INVALID_REQUEST")
        copied.append(ReadRequest(request.tool, json.loads(encoded)["arguments"], symbols))
    return copied


async def collect_tradingview(config, requests, transport_factory):
    """Return None OFF; otherwise collect canonical reads through async CM.call.

    Factory, enter, sequential calls and exit share a total deadline. Error receipts
    contain only bounded codes, never raw arguments, exceptions or failed payloads.
    Every admitted evidence envelope (including metadata) counts toward byte limits.
    """
    options = TradingViewOptions.parse(config)
    if options is None:
        return None
    result = {"status": "FAILED", "reason": None, "research_only": True,
              "fact_validated": False, "execution_authorized": False, "results": [],
              "metrics": {"calls_attempted": 0, "raw_bytes_processed": 0, "evidence_bytes": 0}}
    try:
        admitted = _requests(requests, options)
    except (ValueError, TypeError, OverflowError, RecursionError):
        result.update(status="INVALID_REQUEST", reason="INVALID_REQUEST")
        return result
    if not admitted:
        result["status"] = "EMPTY"
        return result

    # OFF never imports a provider adapter or evaluates caller-supplied requests.
    from prism_core.tradingview_evidence import normalize_evidence

    metrics = result["metrics"]

    async def collect():
        async with transport_factory() as transport:
            for index, request in enumerate(admitted):
                receipt = {"index": index, "tool": request.tool, "reason": "CALL_INCOMPLETE"}
                result["results"].append(receipt)
                metrics["calls_attempted"] += 1
                try:
                    payload = await asyncio.wait_for(
                        transport.call(request.tool, request.arguments), options.per_call_seconds)
                except asyncio.TimeoutError:
                    receipt["reason"] = "CALL_TIMEOUT"
                    continue
                except Exception:  # noqa: BLE001 - untrusted transport errors must not leak
                    receipt["reason"] = "CALL_ERROR"
                    continue
                try:
                    if type(payload) is not dict:
                        raise ValueError("INVALID_RESPONSE")
                    encoded = _json_bytes(payload)
                except (ValueError, TypeError, OverflowError, RecursionError):
                    receipt["reason"] = "INVALID_RESPONSE"
                    continue
                metrics["raw_bytes_processed"] += len(encoded)
                if len(encoded) > options.max_response_bytes:
                    receipt["reason"] = "RESPONSE_LIMIT"
                    continue
                try:
                    evidence = normalize_evidence(request.tool, json.loads(encoded),
                                                  requested_symbols=request.requested_symbols)
                    size = len(_json_bytes(evidence))
                except Exception:  # noqa: BLE001 - isolate malformed provider payloads
                    receipt["reason"] = "NORMALIZATION_ERROR"
                    continue
                if size > options.max_evidence_bytes:
                    receipt["reason"] = "EVIDENCE_LIMIT"
                elif metrics["evidence_bytes"] + size > options.total_evidence_bytes:
                    receipt["reason"] = "RUN_EVIDENCE_LIMIT"
                else:
                    receipt.update(reason="ADMITTED", evidence=evidence)
                    metrics["evidence_bytes"] += size

    try:
        await asyncio.wait_for(collect(), options.total_seconds)
    except asyncio.TimeoutError:
        result["reason"] = "TOTAL_TIMEOUT"
    except Exception:  # noqa: BLE001 - factory/open/close failures are code-only receipts
        result["reason"] = "TRANSPORT_ERROR"
    for index in range(len(result["results"]), len(admitted)):
        result["results"].append({"index": index, "tool": admitted[index].tool,
                                  "reason": "NOT_ATTEMPTED"})
    available = sum(row.get("evidence", {}).get("status") == "AVAILABLE"
                    for row in result["results"])
    useful = any(row.get("evidence", {}).get("status") in {"AVAILABLE", "PARTIAL"}
                 for row in result["results"])
    result["status"] = ("COMPLETE" if available == len(admitted) and result["reason"] is None
                        else "PARTIAL" if useful else "FAILED")
    return result
