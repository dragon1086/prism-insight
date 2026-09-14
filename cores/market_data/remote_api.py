"""Bounded, explicitly allowlisted read-only KIS endpoint for archive_api."""

import json
import logging
import subprocess  # nosec B404 - fixed read-only worker with a hard deadline
import sys
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cores.market_data.kis_source import KisSource
from cores.market_data.remote_source import MAX_PAYLOAD, encode_result
from cores.market_data.source import Unsupported

logger = logging.getLogger(__name__)


class BoundedMarketDataRoute(APIRoute):
    """Limit input before JSON parsing, including chunked request bodies."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4096:
                    raise HTTPException(status_code=413, detail="Market data request too large")

            async def receive():
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            return await handler(Request(request.scope, receive))

        return bounded


class MarketDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Literal["price_history", "index_history", "market_cap_history",
                        "investor_flows", "fundamentals", "intraday_investor_estimate", "ticker_name"]
    ticker: str = Field(pattern=r"^[0-9]{4,6}$", max_length=6)
    start: str | None = Field(default=None, pattern=r"^[0-9]{8}$", max_length=8)
    end: str | None = Field(default=None, pattern=r"^[0-9]{8}$", max_length=8)
    adjusted: bool = Field(default=True, strict=True)
    as_of: datetime | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if self.capability != "index_history" and len(self.ticker) != 6:
            raise ValueError("Invalid stock ticker")
        if self.capability not in {"ticker_name", "intraday_investor_estimate"}:
            if self.start is None or self.end is None:
                raise ValueError("Date range required")
            start = date.fromisoformat(f"{self.start[:4]}-{self.start[4:6]}-{self.start[6:]}")
            end = date.fromisoformat(f"{self.end[:4]}-{self.end[4:6]}-{self.end[6:]}")
            if not 0 <= (end - start).days <= 1096:
                raise ValueError("Invalid date range")
        elif self.start is not None or self.end is not None:
            raise ValueError("Date range not allowed")
        if self.as_of is not None and self.capability != "intraday_investor_estimate":
            raise ValueError("As-of not allowed")
        return self


_lock = Lock()
_source = None


def execute_market_data(request: MarketDataRequest):
    """Worker-only dispatch: one direct KIS instance, never the default chain."""
    global _source
    try:
        if _source is None:
            _source = KisSource()  # Never default_chain: remote configuration cannot recurse.
        operations = {
            "price_history": lambda: _source.price_history(request.ticker, request.start, request.end, adjusted=request.adjusted),
            "index_history": lambda: _source.index_history(request.ticker, request.start, request.end),
            "market_cap_history": lambda: _source.market_cap_history(request.ticker, request.start, request.end),
            "investor_flows": lambda: _source.investor_flows(request.ticker, request.start, request.end),
            "fundamentals": lambda: _source.fundamentals(request.ticker, request.start, request.end),
            "intraday_investor_estimate": lambda: _source.intraday_investor_estimate(request.ticker, as_of=request.as_of),
            "ticker_name": lambda: _source.ticker_name(request.ticker),
        }
        return encode_result(operations[request.capability]())
    except Unsupported:
        raise HTTPException(status_code=422, detail="KIS capability unavailable for this request") from None
    except Exception:  # noqa: BLE001 - never expose broker exceptions at this boundary
        raise HTTPException(status_code=503, detail="Market data unavailable") from None


def _run_worker(request: MarketDataRequest):
    # subprocess.run kills and reaps the child on TimeoutExpired; no shell or
    # user-controlled executable/arguments. Worker stdout contains bounded JSON.
    result = subprocess.run(  # nosec B603 - fixed argv; validated request goes only to stdin
        [sys.executable, "-m", "cores.market_data.remote_worker"],
        input=request.model_dump_json(), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=25, check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    if len(result.stdout.encode()) > MAX_PAYLOAD:
        raise ValueError("Result too large")
    payload = json.loads(result.stdout)
    if payload.get("error") == "unsupported":
        raise HTTPException(status_code=422, detail="KIS capability unavailable for this request")
    if payload.get("kind") not in {"frame", "name"}:
        raise ValueError("Invalid worker response")
    return payload


def fetch_market_data(request: MarketDataRequest):
    # Bound admission plus broker work below the MCP tool's 45-second deadline.
    if not _lock.acquire(timeout=5):
        raise HTTPException(status_code=503, detail="Market data busy")
    try:
        return _run_worker(request)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - subprocess and decoding errors are private
        logger.warning("Remote KIS worker failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Market data unavailable") from None
    finally:
        _lock.release()
