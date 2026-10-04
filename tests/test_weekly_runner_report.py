"""Weekly leader report: fixture DB only, no network and no Telegram."""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import weekly_runner_report as wrr  # noqa: E402

AS_OF = date(2026, 10, 4)

KR_SCHEMA = [
    ("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT, company_name TEXT, "
     "buy_price REAL, buy_date TEXT, current_price REAL, scenario TEXT, trigger_type TEXT)"),
    ("CREATE TABLE trading_history (id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT, company_name TEXT, "
     "buy_price REAL, buy_date TEXT, sell_price REAL, sell_date TEXT, profit_rate REAL, holding_days INTEGER, "
     "scenario TEXT, trigger_type TEXT, exit_kind TEXT)"),
    ("CREATE TABLE watchlist_history (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, current_price REAL, "
     "analyzed_date TEXT, buy_score INTEGER, min_score INTEGER, skip_reason TEXT, trigger_type TEXT, "
     "was_traded INTEGER)"),
    ("CREATE TABLE holding_decisions (id INTEGER PRIMARY KEY, ticker TEXT, decision_date TEXT, should_sell INTEGER, "
     "sell_reason TEXT)"),
    ("CREATE TABLE analysis_performance_tracker (id INTEGER PRIMARY KEY, ticker TEXT, analyzed_date TEXT, "
     "tracked_7d_return REAL, tracked_14d_return REAL, tracked_30d_return REAL)"),
]
LEGS = [{"kind": "INITIAL", "allocation": "0.25", "price": "10000", "at": "2026-09-20T10:00:00"},
        {"kind": "ADD", "allocation": "0.25", "price": "11000", "at": "2026-09-25T10:00:00"}]


def _scenario(**extra):
    return json.dumps(dict({"max_portfolio_size": 8}, **extra))


def _micro(legs=LEGS, allocation="0.5"):
    return {"contract": "micro-split-live-v1", "allocation": allocation, "unit_amount": "1000000", "legs": legs}


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "tracking.sqlite"
    conn = sqlite3.connect(path)
    for statement in KR_SCHEMA:
        conn.execute(statement)
    holding = ("INSERT INTO stock_holdings (account_key, ticker, company_name, buy_price, buy_date, current_price, "
               "scenario, trigger_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    conn.execute(holding, ("a", "100001", "대박전자", 10000, "2026-09-20 10:00:00", 13000,
                           _scenario(micro_split=_micro(), highest_price=14000,
                                     runner={"active": True}, reentry={"attempt": 2, "attempt_label": "2/3"}),
                           "거래량 급증 상위주"))
    history = ("INSERT INTO trading_history (account_key, ticker, company_name, buy_price, buy_date, sell_price, "
               "sell_date, profit_rate, holding_days, scenario, trigger_type, exit_kind) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    rows = [
        ("a", "200001", "손절일", 1000, "2026-09-01 09:30:00", 940, "2026-09-02 09:10:00", -6.0, 1, _scenario(),
         "거래량 급증 상위주", "stop"),
        ("a", "200002", "손절이", 1000, "2026-09-03 09:30:00", 950, "2026-09-04 09:10:00", -5.0, 1, _scenario(),
         "거래량 급증 상위주", "stop"),
        ("b", "200002", "손절이", 1000, "2026-09-03 09:30:00", 950, "2026-09-04 09:10:00", -5.0, 1, _scenario(),
         "거래량 급증 상위주", "stop"),    # second account, same entry: must not double count
        ("a", "200003", "손절삼", 1000, "2026-09-07 09:30:00", 930, "2026-09-08 09:10:00", -7.0, 1, _scenario(),
         "일중 상승률 상위주", None),
        ("a", "300001", "조기청산", 2000, "2026-09-10 09:30:00", 2200, "2026-09-30 14:00:00", 10.0, 20, _scenario(),
         "일중 상승률 상위주", "trend_exit"),
        ("a", "300002", "오래전", 2000, "2026-05-01 09:30:00", 2200, "2026-05-20 14:00:00", 10.0, 20, _scenario(),
         "일중 상승률 상위주", None),
        ("a", "300003", "재진입됨", 500, "2026-08-20 09:30:00", 550, "2026-09-01 14:00:00", 10.0, 12, _scenario(),
         "일중 상승률 상위주", "stop"),
        ("a", "300003", "재진입됨", 700, "2026-09-02 09:30:00", 770, "2026-09-29 14:00:00", 10.0, 27, _scenario(),
         "일중 상승률 상위주", None),
    ]
    conn.executemany(history, rows)
    watch = ("INSERT INTO watchlist_history (ticker, company_name, current_price, analyzed_date, buy_score, min_score, "
             "skip_reason, trigger_type, was_traded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)")
    conn.executemany(watch, [
        ("400001", "놓친종목", 1000, "2026-09-15 10:00:00", 5, 8, "점수 부족 (5/8)", "마감 강도 상위주"),
        ("400002", "조용한종목", 1000, "2026-09-15 10:00:00", 5, 8, "점수 부족 (5/8)", "마감 강도 상위주"),
    ])
    conn.execute("INSERT INTO holding_decisions (ticker, decision_date, should_sell, sell_reason) "
                 "VALUES ('200003', '2026-09-08', 1, '20일선 이탈과 거래량 동반 하락으로 전량 매도')")
    conn.commit()
    conn.close()
    return str(path)


def _bars(**peaks):
    def fetch(market, ticker, start, end):
        peak = peaks.get(ticker)
        if peak is None:
            return []
        return [{"date": day, "high": peak if day == "2026-10-01" else peak * 0.9, "close": peak * 0.9}
                for day in ("2026-09-02", "2026-09-21", "2026-10-01")]
    return fetch


def _analysis(db_path, fetch=None):
    conn = wrr.connect_readonly(db_path)
    try:
        return wrr.analyze(conn, "KR", AS_OF, 8, fetch or _bars(), 80)
    finally:
        conn.close()


def test_load_dedupes_accounts_and_reads_micro_split(db):
    result = _analysis(db)
    keys = [(t.ticker, t.buy_date) for t in result.trades]
    assert len(keys) == len(set(keys))
    holding = next(t for t in result.trades if t.ticker == "100001")
    assert holding.is_open and holding.path == [25, 50] and holding.fraction == 0.5
    assert holding.entry == pytest.approx(0.5 / (0.25 / 10000 + 0.25 / 11000))
    assert holding.runner == "적용" and holding.reentry["attempt_label"] == "2/3"
    assert result.max_slots == 10 and result.regime_cap == 8   # book stays 10 slots; scenario cap is display only


def test_mfe_uses_scenario_highest_and_price_bars(db):
    result = _analysis(db, _bars(**{"300001": 3000}))
    holding = next(t for t in result.trades if t.ticker == "100001")
    assert holding.mfe == pytest.approx((14000 / holding.entry - 1) * 100)
    early = next(t for t in result.trades if t.ticker == "300001")
    assert early.mfe == pytest.approx(35.0)    # the 10/01 spike is after the 9/30 exit, so it is excluded


def test_missed_winners_cover_skips_and_early_exits_but_not_regained(db):
    result = _analysis(db, _bars(**{"300001": 3000, "400001": 1500, "400002": 1100, "300003": 2000}))
    found = {(m.ticker, m.kind) for m in result.missed}
    assert ("400001", "미진입") in found and ("300001", "조기청산") in found
    assert not any(m.ticker == "400002" for m in result.missed)                # only +10%
    # 300003 was sold on 9/1 and bought again on 9/2 (regained); only its final 9/29 exit counts as early
    assert [m.when for m in result.missed if m.ticker == "300003"] == ["2026-09-29"]
    assert result.missed[0].gain >= result.missed[-1].gain


def test_tracker_hint_is_used_when_prices_are_missing(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO analysis_performance_tracker (ticker, analyzed_date, tracked_30d_return) "
                 "VALUES ('400001', '2026-09-15', 45.0)")
    conn.commit()
    conn.close()
    result = _analysis(db, lambda *_: [])
    assert [(m.ticker, round(m.gain)) for m in result.missed] == [("400001", 45)]


def test_report_has_all_six_sections_in_polite_korean(db):
    text = "\n".join(wrr.render(_analysis(db, _bars(**{"300001": 3000, "400001": 1500}))))
    for title in ("1) 이번 주 요약", "2) 주도주 현황", "3) 놓친 대박", "4) 손절 비용", "5) 트리거별 성적",
                  "6) 방향 점검", "같이 볼 로그"):
        assert title in text
    assert "대박전자(100001)" in text and "보유 규칙 적용" in text and "배분 25%→50%" in text
    assert "재진입 2/3" in text
    assert "놓친종목(400001) 미진입" in text and "점수 부족" in text
    assert "연속 손실 청산은 최대 3건" in text
    assert "20일선 이탈" in text                       # the sell-decision reason is surfaced for the loss log
    assert "손실 청산 3건(손절 규칙 2건)" in text       # deduped: the second account's row is not counted twice


def test_week_summary_counts_only_the_last_seven_days(db):
    text = wrr.section_summary(_analysis(db))
    assert "신규 진입 0건, 청산 2건" in text and "슬롯 사용 0.5/10(5%, 시장 국면상 최대 8종목)" in text


def test_trigger_rows_group_cohort_entries(db):
    rows = {name: (count, big) for name, count, big, _, _ in wrr.trigger_rows(_analysis(db))}
    assert rows["거래량 급증 상위주"][0] == 3 and rows["일중 상승률 상위주"][0] == 4


def test_drawdown_and_streak_helpers():
    def trade(ret):
        return wrr.Trade("KR", "1", "x", "2026-09-01", "2026-09-02", 100, 100, ret, 1.0, "t", None, [100], {}, None,
                         None, max_slots=10)
    exits = [trade(5), trade(-4), trade(-6), trade(3), trade(-1)]
    assert wrr.max_drawdown(exits) == pytest.approx(-1.0)
    assert wrr.loss_streaks(exits) == (2, 1)
    assert wrr.max_drawdown([]) == 0 and wrr.loss_streaks([]) == (0, 0)


def test_long_report_is_split_under_the_telegram_limit():
    sections = [f"{i}) 구간\n" + "\n".join("- " + "가" * 60 for _ in range(12)) for i in range(1, 8)]
    messages = wrr.pack_messages("[헤더]", sections, limit=800)
    assert len(messages) > 1
    assert all(len(m) <= 800 for m in messages)
    assert messages[0].endswith(f"(1/{len(messages)})") and messages[-1].endswith(f"({len(messages)}/{len(messages)})")
    joined = "\n".join(messages)
    assert all(f"{i}) 구간" in joined for i in range(1, 8))


def test_oversized_single_section_is_cut_at_line_boundaries():
    section = "제목\n" + "\n".join("- " + "나" * 100 for _ in range(30))
    messages = wrr.pack_messages("[헤더]", [section], limit=600)
    assert len(messages) > 1 and all(len(m) <= 600 for m in messages)


def test_missing_market_tables_are_reported_not_fatal(db):
    conn = wrr.connect_readonly(db)
    result = wrr.analyze(conn, "US", AS_OF, 8, _bars(), 80)
    conn.close()
    assert "건너뜁니다" in "\n".join(wrr.render(result))


def test_connection_is_read_only(db):
    conn = wrr.connect_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM stock_holdings")
    conn.close()


def test_price_calls_are_capped(db):
    calls = []

    def fetch(market, ticker, start, end):
        calls.append(ticker)
        return []
    conn = wrr.connect_readonly(db)
    result = wrr.analyze(conn, "KR", AS_OF, 8, fetch, 2)
    conn.close()
    assert len(calls) == 2 and result.price_gaps >= 4


def test_one_missed_line_per_winner_and_clean_clipping(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO watchlist_history (ticker, company_name, current_price, analyzed_date, buy_score, "
                 "min_score, skip_reason, trigger_type, was_traded) VALUES "
                 "('200001', '손절일', 950, '2026-09-05 10:00:00', 4, 8, ?, '마감 강도 상위주', 0)", ("사" * 90,))
    conn.commit()
    conn.close()
    result = _analysis(db, _bars(**{"200001": 2000}))
    rows = [m for m in result.missed if m.ticker == "200001"]
    assert len(rows) == 1 and rows[0].gain > 100
    assert wrr._clip("사" * 90).endswith("…") and len(wrr._clip("사" * 90)) == 60
    assert wrr._clip("짧은 사유") == "짧은 사유"


def test_runner_label_is_layout_tolerant():
    assert wrr.runner_label(None) is None and wrr.runner_label({}) is None
    assert wrr.runner_label({"state": "HOLD"}) == "적용(HOLD)"
    assert wrr.runner_label({"active": True}) == "적용"
    assert wrr.runner_label({"note": "x"}) == "기록만 있음"


def test_dry_run_prints_and_never_sends(db, capsys, monkeypatch):
    monkeypatch.setattr(wrr, "send_private", lambda messages: pytest.fail("dry run must not send"))
    assert wrr.main(["--db", db, "--market", "kr", "--as-of", "2026-10-04", "--no-prices", "--dry-run"]) == 0
    assert "[PRISM 주도주 리포트] KR" in capsys.readouterr().out


def test_kill_switch_blocks_sending(db, monkeypatch):
    monkeypatch.setenv("PRISM_DISABLE_SIGNAL_PUBLISH", "1")
    monkeypatch.setattr(wrr, "send_private", lambda messages: pytest.fail("kill switch must not send"))
    assert wrr.main(["--db", db, "--market", "kr", "--as-of", "2026-10-04", "--no-prices"]) == 0


def test_send_uses_private_alert_only(monkeypatch):
    sent = []

    async def fake(text):
        sent.append(text)
        return True
    import prism_core.ops_alert as ops_alert
    monkeypatch.setattr(ops_alert, "send_ops_alert", fake)
    assert wrr.send_private(["a", "b"]) is True and sent == ["a", "b"]
