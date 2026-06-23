# test_trade_history.py

"""
Phase 6 S6 — past-trade lookup + insight-image marker tests (ROOT session).

Mock-only: a TEMP sqlite DB is populated with the real KR/US trade-table
schemas (``trading_history`` / ``stock_holdings`` and ``us_trading_history`` /
``us_stock_holdings``). NO network, NO pykrx, NO vision. matplotlib runs on Agg.

Covers:
- get_trade_events for KR and US (table selection per market).
- summarize_trades produces a concise Korean round-trip line.
- _map_trades_to_x maps trade dates to mplfinance candle-index positions.
- _draw_trade_markers + render_insight_image overlay markers without crashing.
- Empty DB / missing ticker -> no-op (empty list, None summary).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # headless; must precede pyplot import
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from cores.llm.features import trade_history as th  # noqa: E402
from cores.llm.features.trade_history import (  # noqa: E402
    TradeEvent,
    get_trade_events,
    summarize_trades,
)
from cores.llm.features import insight_image  # noqa: E402
from cores.llm.features.insight_image import (  # noqa: E402
    _draw_trade_markers,
    _map_trades_to_x,
    render_insight_image,
)
from cores.llm.features.buy_quality import BaseAnalysis  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                           #
# --------------------------------------------------------------------------- #
def _make_db(tmp_path) -> str:
    """Create a temp sqlite DB with the KR + US trade tables + sample rows."""
    path = str(tmp_path / "stock_tracking_db.sqlite")
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    # KR closed round-trips.
    cur.execute(
        "CREATE TABLE trading_history (id INTEGER PRIMARY KEY, ticker TEXT, "
        "company_name TEXT, buy_price REAL, buy_date TEXT, sell_price REAL, "
        "sell_date TEXT, profit_rate REAL, holding_days INTEGER, scenario TEXT)"
    )
    cur.execute(
        "CREATE TABLE stock_holdings (ticker TEXT PRIMARY KEY, company_name TEXT, "
        "buy_price REAL, buy_date TEXT, current_price REAL)"
    )
    # US closed round-trips.
    cur.execute(
        "CREATE TABLE us_trading_history (id INTEGER PRIMARY KEY, ticker TEXT, "
        "company_name TEXT, buy_price REAL, buy_date TEXT, sell_price REAL, "
        "sell_date TEXT, profit_rate REAL, holding_days INTEGER, scenario TEXT)"
    )
    cur.execute(
        "CREATE TABLE us_stock_holdings (ticker TEXT PRIMARY KEY, "
        "company_name TEXT, buy_price REAL, buy_date TEXT, current_price REAL)"
    )
    cur.execute(
        "INSERT INTO trading_history (ticker, company_name, buy_price, buy_date, "
        "sell_price, sell_date, profit_rate, holding_days) VALUES "
        "('005930','삼성전자',86000.0,'2025-10-01 16:19:21',95300.0,"
        "'2025-10-14 10:02:15',10.81,12)"
    )
    cur.execute(
        "INSERT INTO us_trading_history (ticker, company_name, buy_price, buy_date, "
        "sell_price, sell_date, profit_rate, holding_days) VALUES "
        "('CCL','Carnival',31.15,'2026-01-30 07:07:43',29.92,"
        "'2026-01-31 00:51:59',-3.93,0)"
    )
    conn.commit()
    conn.close()
    return path


def _analysis() -> BaseAnalysis:
    return BaseAnalysis(
        base_type="cup-handle", base_length_weeks=8, depth_pct=22.0,
        handle_present=True, handle_in_upper_half=True, tightness="tight",
        volume_dryup_in_handle=True, pivot_price=72000.0, dist_to_pivot_pct=1.5,
        rs_line_new_high=True, proper_or_faulty="proper", quality_score=82,
        confidence=75, rationale="tight cup", support_levels=[68000.0],
        resistance_levels=[75000.0], buy_point=72000.0, stop_loss=66000.0,
    )


def _price_fig():
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(range(10), [70000 + i * 300 for i in range(10)])
    ax.set_xlim(-0.5, 9.5)
    ax.set_ylim(68000, 76000)
    return fig


# --------------------------------------------------------------------------- #
# get_trade_events                                                             #
# --------------------------------------------------------------------------- #
class TestGetTradeEvents:
    def test_kr_round_trip_yields_buy_and_sell(self, tmp_path):
        events = get_trade_events("005930", market="KOSPI",
                                  db_path=_make_db(tmp_path))
        sides = sorted(e.side for e in events)
        assert sides == ["buy", "sell"]
        sell = next(e for e in events if e.side == "sell")
        assert sell.price == 95300.0
        assert sell.profit_rate == 10.81

    def test_us_uses_us_table(self, tmp_path):
        events = get_trade_events("CCL", market="us",
                                  db_path=_make_db(tmp_path))
        assert {e.side for e in events} == {"buy", "sell"}
        buy = next(e for e in events if e.side == "buy")
        assert buy.price == 31.15

    def test_unknown_ticker_returns_empty(self, tmp_path):
        assert get_trade_events("NOPE", market="us",
                                db_path=_make_db(tmp_path)) == []

    def test_missing_db_returns_empty_not_raises(self):
        assert get_trade_events("005930", db_path="/no/such/db.sqlite") == []


# --------------------------------------------------------------------------- #
# summarize_trades                                                             #
# --------------------------------------------------------------------------- #
class TestSummarizeTrades:
    def test_kr_round_trip_line(self):
        events = [
            TradeEvent(datetime(2025, 10, 1), 86000.0, "buy"),
            TradeEvent(datetime(2025, 10, 14), 95300.0, "sell", profit_rate=10.8),
        ]
        text = summarize_trades(events, currency_symbol="₩", price_decimals=0)
        assert text is not None
        assert "과거 매매 이력" in text
        assert "매수 2025-10-01 @₩86,000" in text
        assert "매도 2025-10-14 @₩95,300" in text
        assert "+10.8%" in text

    def test_us_dollar_symbol_plain(self):
        events = [
            TradeEvent(datetime(2026, 1, 30), 31.15, "buy"),
            TradeEvent(datetime(2026, 1, 31), 29.92, "sell", profit_rate=-3.93),
        ]
        text = summarize_trades(events, currency_symbol="$", price_decimals=2)
        assert "$31.15" in text and "$29.92" in text

    def test_empty_returns_none(self):
        assert summarize_trades([]) is None


# --------------------------------------------------------------------------- #
# _map_trades_to_x                                                             #
# --------------------------------------------------------------------------- #
class TestMapTradesToX:
    def test_maps_to_nearest_candle_index(self):
        idx = pd.date_range("2025-10-01", periods=10, freq="D")
        df = pd.DataFrame({"Close": range(10)}, index=idx)
        events = [
            TradeEvent(datetime(2025, 10, 1), 100.0, "buy"),   # -> pos 0
            TradeEvent(datetime(2025, 10, 10), 110.0, "sell"),  # -> pos 9
        ]
        xy = _map_trades_to_x(events, df)
        positions = sorted(x for x, _, _ in xy)
        assert positions == [0.0, 9.0]

    def test_empty_df_returns_empty(self):
        assert _map_trades_to_x([TradeEvent(datetime(2025, 1, 1), 1.0, "buy")],
                                None) == []


# --------------------------------------------------------------------------- #
# Marker rendering                                                             #
# --------------------------------------------------------------------------- #
class TestTradeMarkers:
    def test_draw_markers_adds_collections_and_legend(self):
        fig = _price_fig()
        ax = fig.axes[0]
        before = len(ax.collections)
        _draw_trade_markers(
            ax,
            [(1.0, 70500.0, "buy"), (7.0, 74000.0, "sell")],
            price_min=68000.0, price_max=76000.0,
        )
        assert len(ax.collections) > before  # scatter collections added
        assert ax.get_legend() is not None
        plt.close(fig)

    def test_render_with_trades_returns_bytes(self):
        out = render_insight_image(
            _price_fig(), _analysis(), ticker="005930", company_name="삼성전자",
            price_min=68000.0, price_max=76000.0,
            trades=[(1.0, 70500.0, "buy"), (7.0, 74000.0, "sell")],
        )
        assert isinstance(out, bytes) and len(out) > 0

    def test_render_with_no_trades_still_renders(self):
        out = render_insight_image(
            _price_fig(), _analysis(), ticker="005930", company_name="삼성전자",
            price_min=68000.0, price_max=76000.0, trades=[],
        )
        assert isinstance(out, bytes) and len(out) > 0

    def test_out_of_band_trade_price_dropped_no_crash(self):
        fig = _price_fig()
        ax = fig.axes[0]
        # Price far outside the visible band must be silently skipped.
        _draw_trade_markers(ax, [(2.0, 999999.0, "buy")],
                            price_min=68000.0, price_max=76000.0)
        plt.close(fig)
