"""Resolve user input to a ticker using Prism's shared resolver.

This is one of the two places Kakao code is allowed to import Prism (design
§3.2). The KR name/code maps prefer mutable runtime data and fall back to the
tracked `stock_map.json` seed; there is no equivalent name map for US listings,
so a US query is taken as the ticker itself.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from kakao_bot.ports.analysis import (
    ResolvedTicker,
    TickerResolution,
)
from prism_core.runtime_paths import resolve_stock_map_read_path
from prism_core.ticker_resolver import resolve_ticker

logger = logging.getLogger(__name__)

_US_TICKER_MAX_LENGTH = 5
_UNRESOLVED_US = "미국 종목은 티커로 입력해주세요. 예: 리포트 AAPL"


class PrismTickerResolver:
    """Adapter implementing :class:`TickerResolverPort`."""

    def __init__(self, stock_map_path: str | Path | None = None) -> None:
        self._path = (
            Path(stock_map_path)
            if stock_map_path is not None
            else resolve_stock_map_read_path()
        )
        self._code_to_name: Mapping[str, str] = {}
        self._name_to_code: Mapping[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            with self._path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            logger.error("Stock map not found: %s", self._path)
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Stock map could not be read (%s): %s", self._path, exc)
            return

        self._code_to_name = data.get("code_to_name", {}) or {}
        self._name_to_code = data.get("name_to_code", {}) or {}
        logger.info("Loaded %d KR stock entries", len(self._code_to_name))

    @property
    def is_loaded(self) -> bool:
        return bool(self._code_to_name)

    def resolve(self, query: str, *, market: str | None) -> TickerResolution:
        text = (query or "").strip()
        if not text:
            return TickerResolution(error_message="종목명이나 코드를 입력해주세요.")

        if market == "us":
            return self._resolve_us(text)
        return self._resolve_kr(text)

    def _resolve_us(self, text: str) -> TickerResolution:
        candidate = text.upper()
        if not candidate.isalpha() or len(candidate) > _US_TICKER_MAX_LENGTH:
            return TickerResolution(error_message=_UNRESOLVED_US)
        # No US name map exists; the ticker stands in for the company name and
        # the report itself carries the real name.
        return TickerResolution(
            ticker=ResolvedTicker(
                ticker=candidate,
                company_name=candidate,
                market="us",
            )
        )

    def _resolve_kr(self, text: str) -> TickerResolution:
        code, name, error = resolve_ticker(
            text,
            code_to_name=self._code_to_name,
            name_to_code=self._name_to_code,
        )
        if error or not code:
            return TickerResolution(
                error_message=error or "종목을 찾을 수 없습니다."
            )
        return TickerResolution(
            ticker=ResolvedTicker(
                ticker=code,
                company_name=name or code,
                market="kr",
            )
        )
