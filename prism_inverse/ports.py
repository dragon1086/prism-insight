"""Contracts with outside systems. Swap a vendor by adding one adapter, not by editing ``app``.

Adapters raise ``Unavailable`` for a failed or empty answer and ``Unsupported`` for a
capability they lack. They never return an empty list or zeros in place of data.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Protocol, Sequence

from prism_inverse.domain.models import Bar, Decision, EtfSnapshot, EtfSpec, Instrument, LeaderState, Position


class Unavailable(Exception):
    """The source could not answer now (network, rate limit, empty or malformed reply)."""


class Unsupported(Exception):
    """The source does not offer this capability or instrument."""


class MarketData(Protocol):
    def daily_bars(self, instrument: Instrument, start: date, end: date) -> list[Bar]: ...


class EtfInfo(Protocol):
    def snapshot(self, spec: EtfSpec, day: date) -> EtfSnapshot: ...


class HoldingsSource(Protocol):
    def index_weights(self, underlying_code: str, as_of: date) -> dict[str, float]: ...


class LongLaneBook(Protocol):
    def tickers(self, market: str) -> frozenset: ...


class Store(Protocol):
    def start_run(self, run_id: str, market: str, day: date, mode: str, policy_version: str,
                  config_hash: str) -> None: ...
    def finish_run(self, run_id: str, status: str, error: Optional[str] = None) -> None: ...
    def record_snapshot(self, run_id: str, spec: EtfSpec, snap: Optional[EtfSnapshot], error: Optional[str]) -> None: ...
    def record_market(self, run_id: str, underlying: str, pulse_today: Optional[str],
                      pulse_yesterday: Optional[str], leader: Optional[LeaderState]) -> None: ...
    def record_decision(self, run_id: str, decision_id: str, trace_id: Optional[str], d: Decision) -> None: ...
    def open_positions(self, market: str) -> list[Position]: ...
    def attempts(self, market: str) -> dict[str, int]: ...
    def open_position(self, market: str, pos: Position, trace_id: str) -> None: ...
    def close_position(self, position_id: str, day: date, price: Optional[float], reason: str) -> None: ...


class SignalStore(Protocol):
    """Signal recorder: one row per family per run, and the virtual trade book (no orders)."""
    def start_run(self, run_id: str, market: str, day: date, mode: str, policy_version: str,
                  config_hash: str) -> None: ...
    def finish_run(self, run_id: str, status: str, error: Optional[str] = None) -> None: ...
    def record_signal_day(self, run_id: str, day: date, family: str, status: str, inputs: dict,
                          error: Optional[str]) -> None: ...
    def active_virtual_trades(self) -> list: ...
    def save_virtual_trade(self, trade) -> None: ...
    def add_virtual_trade(self, trade) -> bool:
        """Insert a new trade; False (and no change) when the trade_id already exists."""


class OrderResult(Protocol):
    ok: bool
    order_no: Optional[str]
    message: str


class Broker(Protocol):
    """One account. Every order is a next-open (reserved) order placed after the close."""
    name: str
    slot_amount: float   # the account's configured one-slot amount (KRW for KR, USD for US)

    def buy_next_open(self, product: str, amount: float, exchange: Optional[str], ref_price: Optional[float]) -> OrderResult: ...
    def sell_next_open(self, product: str, qty: Optional[int], exchange: Optional[str]) -> OrderResult:
        """``qty`` None sells the whole holding as the broker sees it now (splits never leave a remainder)."""
    def holding(self, product: str) -> Optional[tuple[int, Optional[float]]]:
        """(qty, average price) of the product in this account, None when not held. Raises Unavailable."""


class LiveStore(Protocol):
    def live_positions(self, market: str) -> list: ...
    def save_live_position(self, pos) -> None: ...


class EventSink(Protocol):
    def emit(self, event_type: str, **fields) -> None:
        """Must never raise: observability cannot block a decision."""


class Clock(Protocol):
    def today(self) -> date: ...


BarsByCode = dict[str, Sequence[Bar]]
