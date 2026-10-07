import json
import sqlite3

from messaging.korean_trading_message import hold_reason_display, render_korean_trading_message
from prism_core.sector_cap import deferred_decision_line, sector_cap_note


def _cursor(sectors, table="us_stock_holdings"):
    con = sqlite3.connect(":memory:")
    con.execute(f"CREATE TABLE {table} (account_key TEXT, scenario TEXT)")
    con.executemany(f"INSERT INTO {table} VALUES ('a', ?)", [(json.dumps({"sector": s}),) for s in sectors])
    return con.cursor()


def test_ratio_cap_note_matches_the_sndk_case():
    cur = _cursor(["Technology", "Industrials", "Technology", "Utilities"])
    note = sector_cap_note(cur, "us_stock_holdings", "Technology", max_same=3, ratio=0.3, account_key="a")
    assert note == "Technology 2/4종목(50%) 보유, 한 업종 30% 한도"


def test_absolute_cap_note_and_no_note_when_not_capped():
    cur = _cursor(["전기·전자"] * 3, table="stock_holdings")
    assert sector_cap_note(cur, "stock_holdings", "전기·전자", max_same=3, ratio=0.3) == "전기·전자 3종목 보유, 업종당 3종목 한도"
    cur = _cursor(["Technology", "Energy"])
    assert sector_cap_note(cur, "us_stock_holdings", "Technology", max_same=3, ratio=0.3) == ""
    assert sector_cap_note(cur, "other_table", "Technology", max_same=3, ratio=0.3) == ""


def test_decision_line_and_rendered_message():
    assert deferred_decision_line(True, "Skip") == "AI는 진입 판단 → 규칙으로 보류"
    assert deferred_decision_line(False, "Skip") == "Skip"
    reason = hold_reason_display("Sector concentration (Technology 2/4종목(50%) 보유, 한 업종 30% 한도)")
    text = render_korean_trading_message(
        f"⚠️ 매수 보류: Sandisk(SNDK)\n결정: {deferred_decision_line(True, 'Skip')}\n보류 사유: {reason}")
    assert "결정: AI는 진입 판단 → 규칙으로 보류" in text
    assert "보류 사유: 섹터 집중 (Technology 2/4종목(50%) 보유, 한 업종 30% 한도)" in text
    assert "결정: 미진입" in render_korean_trading_message("결정: Skip")
