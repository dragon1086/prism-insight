"""Read-only KIS adapters for KR and US, built on the repo's existing KIS clients.

KR wraps ``cores.market_data.kis_source.KisSource`` (shared token, range walking).
US reuses ``prism_core.us_market_data`` helpers but always passes the registry's
exchange: KIS answers an ETF queried on the wrong exchange with blank rows (QID on NAS,
2026-11-05), and the lane must not fall back to a guessed exchange.
Nothing here places orders.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from prism_inverse.app.inputs import snapshot_from_bars
from prism_inverse.domain.models import Bar, EtfSnapshot, EtfSpec, Instrument
from prism_inverse.domain.ticks import etf_tick
from prism_inverse.ports import Unavailable, Unsupported

ETF_PRICE_URL = "/uapi/etfetn/v1/quotations/inquire-price"
ETF_PRICE_TR = "FHPST02400000"  # ETF/ETN 현재가: stck_prpr, nav, dprt(괴리율), trc_errt(추적 오차율)


def _num(value) -> Optional[float]:
    try:
        out = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def frame_to_bars(frame, *, index_volume: bool = False) -> list[Bar]:
    """OHLCV frame -> bars. Holiday filler rows (flat OHLC, no volume) are dropped.

    ``index_volume``: an index never trades zero, so 0 means "not published" -> None.
    """
    bars = []
    for ts, row in frame.sort_index().iterrows():
        o, h, l, c = (_num(row.get(k)) for k in ("Open", "High", "Low", "Close"))
        if None in (o, h, l, c) or c <= 0:
            continue
        v = _num(row.get("Volume"))
        if v is not None and v <= 0 and index_volume:
            v = None
        if o == h == l == c and not v:
            continue
        bars.append(Bar(ts.date(), o, h, l, c, v))
    if not bars:
        raise Unavailable("no usable daily bars")
    return bars


def _wrap(call, what: str):
    try:
        return call()
    except (Unavailable, Unsupported):
        raise
    except Exception as exc:
        # The shared clients raise their own Unavailable/Unsupported types; normalise them.
        name = type(exc).__name__
        if name == "Unsupported":
            raise Unsupported(f"{what}: {exc}") from exc
        raise Unavailable(f"{what}: {name}: {exc}") from exc


class KisKrData:
    def __init__(self, source=None):
        self._source = source

    @property
    def source(self):
        if self._source is None:
            from cores.market_data.kis_source import KisSource

            self._source = KisSource()
        return self._source

    def daily_bars(self, instrument: Instrument, start: date, end: date) -> list[Bar]:
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        if instrument.kind == "index":
            frame = _wrap(lambda: self.source.index_history(instrument.code, s, e), instrument.code)
            return frame_to_bars(frame, index_volume=True)
        frame = _wrap(lambda: self.source.price_history(instrument.code, s, e, adjusted=True), instrument.code)
        return frame_to_bars(frame)

    def snapshot(self, spec: EtfSpec, day: date) -> EtfSnapshot:
        # shortcut: borrows KisSource._fetch (shared retry/token path); promote to a public
        # KisSource method if a second caller needs ETF quotes.
        body = _wrap(lambda: self.source._fetch(
            ETF_PRICE_URL, ETF_PRICE_TR, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": spec.code}), spec.code)
        out = getattr(body, "output", None) or {}
        price = _num(out.get("stck_prpr"))
        if not price or price <= 0:
            raise Unavailable(f"KIS ETF quote {spec.code} has no price")
        premium = _num(out.get("dprt"))
        bars = self.daily_bars(Instrument("KR", spec.code, "etf"), date.fromordinal(day.toordinal() - 45), day)
        return EtfSnapshot(
            price=price, tick=etf_tick("KR", price), value_20d=snapshot_from_bars(spec, bars).value_20d,
            premium_pct=abs(premium) if premium is not None else None,
            tracking_error=_num(out.get("trc_errt")),
        )


class KisUsData:
    def daily_bars(self, instrument: Instrument, start: date, end: date) -> list[Bar]:
        from prism_core import us_market_data as m

        if instrument.kind == "index":
            frame = _wrap(lambda: m.daily_history(instrument.code, start, end), instrument.code)
            return frame_to_bars(frame, index_volume=True)
        if not instrument.exchange:
            raise Unsupported(f"{instrument.code}: no exchange in the registry")
        first, last = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        symbol = instrument.code.replace("-", "/")

        def frame(modp: str):
            def page(cursor):
                body = m._fetch(m.STOCK_URL, m.STOCK_TR_ID, {
                    "AUTH": "", "EXCD": instrument.exchange, "SYMB": symbol, "GUBN": "0", "BYMD": cursor,
                    "MODP": modp})
                return getattr(body, "output2", None) or []
            return m.frame_from_rows(m._walk(first, last, m._STOCK_FIELDS[0], page), m._STOCK_FIELDS,
                                     symbol=instrument.code)

        # Same basis as the runtime US provider: split-adjusted, not dividend-adjusted.
        adjusted = _wrap(lambda: m.yfinance_basis(frame("0"), frame("1"), adjusted=False), instrument.code)
        return frame_to_bars(adjusted)

    def snapshot(self, spec: EtfSpec, day: date) -> EtfSnapshot:
        bars = self.daily_bars(Instrument("US", spec.code, "etf", spec.exchange),
                               date.fromordinal(day.toordinal() - 45), day)
        return snapshot_from_bars(spec, bars)  # NAV premium is not published here -> stays None
