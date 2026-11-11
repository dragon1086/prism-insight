"""S5 live lane: slot rules, order/fill/reject bookkeeping, runner flag. Fakes only; no KIS."""

from __future__ import annotations

from datetime import date, timedelta

from prism_inverse.adapters.fakes import FakeBroker, FakeEvents, MemoryStore
from prism_inverse.adapters.registry import load_us_inverse, load_us_sectors
from prism_inverse.adapters.sqlite_store import SqliteStore
from prism_inverse.app.live_cycle import kr_product_map, run_live, us_product_map
from prism_inverse.domain.lane import (CLOSED, OPEN, ORDERED_BUY, ORDERED_SELL, REJECTED, KR_LANE, US_LANE, LivePosition,
                                       Product, plan_entries)
from prism_inverse.domain.virtual import PENDING_ENTRY, PENDING_EXIT, VirtualTrade

D = [date(2026, 11, 10) + timedelta(days=i) for i in range(6)]


def _t(fam, day, etf="251340", und="229200", status=PENDING_ENTRY, exit_day=None, reason=None):
    return VirtualTrade(f"{fam}-{day.isoformat()}", fam, etf, und, day, 100.0, status=status,
                        exit_signal_date=exit_day, exit_reason=reason)


def _pos(pid, product, status, **kw):
    return LivePosition(pid, "acct", "KR", pid, "KQ_FR", product, status=status, **kw)


def test_plan_entries_caps_slots_dedups_products_and_keeps_priority():
    c = [(_t("KQ_FR", D[0]), Product("251340", -1)), (_t("KQ_X10", D[0]), Product("251340", -1)),
         (_t("K200_SEMI", D[0], "252670", "069500"), Product("252670", -2))]
    key = lambda x: (x[0].underlying, x[0].family)
    assert [x[0].family for x in plan_entries(KR_LANE, [], c, key)] == ["K200_SEMI"]           # KOSPI200 first, one slot
    assert [x[0].family for x in plan_entries(US_LANE, [], c, key)] == ["K200_SEMI", "KQ_FR"]  # same product only once
    held = [_pos("p", "252670", ORDERED_SELL)]                                                   # a pending sell still occupies
    assert [x[0].family for x in plan_entries(KR_LANE, held, c, key)] == []
    assert [x[0].family for x in plan_entries(US_LANE, held, c, key)] == ["KQ_FR"]


def test_kr_live_follows_the_virtual_trade_from_order_to_close():
    store, events, b = MemoryStore(), FakeEvents(), FakeBroker()
    pm = kr_product_map()
    # day 0: two KOSDAQ signals on the same product + nothing else -> one buy order for the full slot
    r = run_live("KR", D[0], [_t("KQ_FR", D[0]), _t("KQ_X10", D[0])], pm, KR_LANE, [b], store, events)
    assert b.orders == [("BUY", "251340", 1_200_000, None, None)]
    (p,) = r.positions
    assert p.status == ORDERED_BUY and p.order_no == "B1" and p.position_id == f"KQ_FR-{D[0]}@acct"
    # same day re-run: nothing ordered twice
    run_live("KR", D[0], [_t("KQ_FR", D[0])], pm, KR_LANE, [b], store, events)
    assert len(b.orders) == 1
    # day 1: filled; a new KOSPI200 signal cannot enter (one position at a time)
    b.holdings["251340"] = (300, 3990.0)
    t1 = _t("KQ_FR", D[0], status=OPEN)
    r = run_live("KR", D[1], [t1, _t("K200_SEMI", D[1], "252670", "069500")], pm, KR_LANE, [b], store, events)
    (p,) = r.positions
    assert (p.status, p.qty, p.entry_price, p.filled) == (OPEN, 300, 3990.0, D[1]) and len(b.orders) == 1
    # day 2: the virtual trade hits its stop -> sell the whole position at the next open
    t2 = _t("KQ_FR", D[0], status=PENDING_EXIT, exit_day=D[2], reason="STOP")
    r = run_live("KR", D[2], [t2], pm, KR_LANE, [b], store, events)
    (p,) = r.positions
    assert p.status == ORDERED_SELL and p.exit_reason == "STOP" and b.orders[-1] == ("SELL", "251340", 300, None)
    # day 3: still held (sell not filled) -> sold again; day 4: gone -> CLOSED and the slot takes a new signal
    r = run_live("KR", D[3], [t2], pm, KR_LANE, [b], store, events)
    assert r.positions[0].status == ORDERED_SELL and b.orders[-1][0] == "SELL" and len(b.orders) == 3
    del b.holdings["251340"]
    r = run_live("KR", D[4], [t2, _t("K200_SEMI", D[4], "252670", "069500")], pm, KR_LANE, [b], store, events)
    assert {p.status for p in r.positions} == {CLOSED, ORDERED_BUY} and b.orders[-1][:2] == ("BUY", "252670")
    kinds = [e for e, _ in events.events]
    assert kinds.count("inverse.live_order") == 4 and "inverse.live_fill" in kinds and "inverse.live_exit" in kinds
    assert all(f["attributes"]["live"] is True for _, f in events.events)


def test_rejected_or_unfilled_buys_are_recorded_and_free_the_slot():
    store, events = MemoryStore(), FakeEvents()
    pm = kr_product_map()
    sub = FakeBroker("subscriber", 600_000, reject=frozenset({"252670"}))   # deposit rule refuses the 2x product
    r = run_live("KR", D[0], [_t("K200_SEMI", D[0], "252670", "069500")], pm, KR_LANE, [sub], store, events)
    (p,) = r.positions
    assert p.status == REJECTED and p.error.startswith("BUY_FAILED") and p.closed == D[0]
    r = run_live("KR", D[1], [_t("KQ_FR", D[1])], pm, KR_LANE, [sub], store, events)   # next signal is not blocked
    assert any(p.status == ORDERED_BUY and p.product == "251340" for p in r.positions)
    # an accepted order with no holding the next day is NO_FILL, never silently OPEN
    r = run_live("KR", D[2], [_t("KQ_FR", D[1], status=OPEN)], pm, KR_LANE, [sub], store, events)
    assert [p for p in r.positions if p.product == "251340"][0].status == REJECTED
    # a failed balance query changes nothing (no reject, no sell) and is recorded
    main = FakeBroker("main")
    run_live("KR", D[0], [_t("KQ_FR", D[0])], pm, KR_LANE, [main], store, events)
    main.unavailable = True
    r = run_live("KR", D[1], [_t("KQ_FR", D[0], status=OPEN)], pm, KR_LANE, [main], store, events)
    (p,) = [p for p in r.positions if p.account == "main"]
    assert p.status == ORDERED_BUY and p.error.startswith("HOLDING_UNAVAILABLE") and len(main.orders) == 1


def test_us_live_splits_the_slot_in_three_and_sizes_minus_three_x_at_two_thirds():
    store, events, b = MemoryStore(), FakeEvents(), FakeBroker("us", 3000.0)
    pm = us_product_map(load_us_inverse())
    fired = [_t(f"US_2BR_{s}", D[0], s, s) for s in ("GDX", "SOXX", "XLE", "XLK", "IGV")]   # IGV has no product
    prices = {"DUST": 37.0, "SOXS": 53.0, "ERY": 9.2, "TECS": None}
    r = run_live("US", D[0], fired, pm, US_LANE, [b], store, events, lambda p: prices.get(p.code))
    buys = [o for o in b.orders if o[0] == "BUY"]
    assert [o[1] for o in buys] == ["DUST", "SOXS", "ERY"]                       # alphabetical sectors, three slots
    assert [round(o[2], 2) for o in buys] == [1000.0, round(1000 * 2 / 3, 2), 1000.0]
    assert buys[1][3:] == ("AMS", 53.0)
    assert {p.product: p.status for p in r.positions} == {"DUST": ORDERED_BUY, "SOXS": ORDERED_BUY, "ERY": ORDERED_BUY}
    # XLK (TECS) was fourth by order and never reached; a missing reference price is a recorded reject
    r = run_live("US", D[1], [_t("US_2BR_XLK", D[1], "XLK", "XLK")] + [_t(f"US_2BR_{s}", D[0], s, s, status=OPEN) for s in ("GDX",)],
                 pm, US_LANE, [FakeBroker("us2", 3000.0)], store, events, lambda p: None)
    assert [p for p in r.positions if p.account == "us2"][0].error == "MISSING_REF_PRICE"


def test_sqlite_live_positions_round_trip(tmp_path):
    store = SqliteStore(tmp_path / "lane.sqlite")
    p = LivePosition("KQ_FR-2026-11-10@acct", "acct", "KR", "KQ_FR-2026-11-10", "KQ_FR", "251340", status=OPEN, qty=3,
                     entry_price=3990.0, opened=D[0], filled=D[1], order_no="B1")
    store.save_live_position(p)
    assert store.live_positions("KR") == [p] and store.live_positions("US") == []
    store.save_live_position(p.__class__(**{**p.__dict__, "status": CLOSED, "closed": D[2]}))
    assert store.live_positions("KR") == []


def test_us_inverse_products_are_in_the_universe_and_liquid_only():
    inv, sectors = load_us_inverse(), load_us_sectors()
    assert set(inv) <= set(sectors) and len(inv) == 9
    assert inv["SOXX"] == Product("SOXS", -3, "AMS") and inv["GDX"].size_factor == 1.0 and inv["SOXX"].size_factor == 2 / 3
    assert "IGV" not in inv and "KRE" not in inv


def test_runner_live_flag_never_orders_in_dry_run_or_off(monkeypatch, capsys):
    from tools import run_inverse_signals as R

    monkeypatch.setenv("INVERSE_LANE_LIVE_MARKETS", "KR,us")
    assert R.live_markets() == {"KR", "US"}
    monkeypatch.delenv("INVERSE_SIGNALS_MODE", raising=False)
    assert R.main(["--market", "kr"]) == 0 and "off" in capsys.readouterr().out        # off: nothing at all
    monkeypatch.setattr(R, "run_live_lane", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ordered in dry-run")))
    monkeypatch.setattr("prism_inverse.adapters.kis_data.KisKrData.daily_bars", lambda self, inst, s, e: [])
    monkeypatch.setattr("prism_inverse.adapters.kis_data.KisKrData.__init__", lambda self, **k: None)
    assert R.main(["--market", "kr", "--dry-run"]) == 0 and "LIVE  skipped" in capsys.readouterr().out


def test_default_session_date_is_the_market_zone_date():
    from datetime import datetime, timezone

    from tools.run_inverse_signals import default_date

    at_0710_kst = datetime(2026, 11, 10, 22, 10, tzinfo=timezone.utc)      # 2026-11-11 07:10 KST, the US cron slot
    assert default_date("kr", at_0710_kst) == date(2026, 11, 11)
    assert default_date("us", at_0710_kst) == date(2026, 11, 10)          # the US session that just closed
    at_1710_kst = datetime(2026, 11, 11, 8, 10, tzinfo=timezone.utc)
    assert default_date("kr", at_1710_kst) == date(2026, 11, 11) and default_date("us", at_1710_kst) == date(2026, 11, 11)
