"""Two-week review: fixture DB, events, runtime state and logs only (no network, no Telegram)."""
import gzip
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import two_week_review as twr  # noqa: E402

START, END = "2026-10-05", "2026-10-18"

SCHEMA = [
    "CREATE TABLE {p}stock_holdings (id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT, company_name TEXT, "
    "buy_price REAL, buy_date TEXT, current_price REAL, scenario TEXT, trigger_type TEXT)",
    "CREATE TABLE {p}trading_history (id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT, company_name TEXT, "
    "buy_price REAL, buy_date TEXT, sell_price REAL, sell_date TEXT, profit_rate REAL, holding_days INTEGER, "
    "scenario TEXT, trigger_type TEXT, exit_kind TEXT)",
    "CREATE TABLE {p}watchlist_history (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, current_price REAL, "
    "analyzed_date TEXT, buy_score INTEGER, min_score INTEGER, decision TEXT, skip_reason TEXT, scenario TEXT, "
    "trigger_type TEXT, trigger_mode TEXT, was_traded INTEGER DEFAULT 0)",
    "CREATE TABLE {p}holding_decisions (id INTEGER PRIMARY KEY, ticker TEXT, decision_date TEXT, "
    "should_sell INTEGER, sell_reason TEXT)",
]
TRACKERS = [
    "CREATE TABLE analysis_performance_tracker (id INTEGER PRIMARY KEY, ticker TEXT, analyzed_date TEXT, "
    "tracked_7d_return REAL, tracked_14d_return REAL, tracked_30d_return REAL)",
    "CREATE TABLE us_analysis_performance_tracker (id INTEGER PRIMARY KEY, ticker TEXT, analysis_date TEXT, "
    "return_7d REAL, return_14d REAL, return_30d REAL)",
]
HOLD = ("INSERT INTO {p}stock_holdings (account_key, ticker, company_name, buy_price, buy_date, current_price, "
        "scenario, trigger_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
HIST = ("INSERT INTO {p}trading_history (account_key, ticker, company_name, buy_price, buy_date, sell_price, "
        "sell_date, profit_rate, holding_days, scenario, trigger_type, exit_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
WATCH = ("INSERT INTO {p}watchlist_history (ticker, company_name, current_price, analyzed_date, buy_score, "
         "min_score, decision, skip_reason, scenario, trigger_type, trigger_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)")


def _micro(legs, allocation, tilt=None):
    block = {"contract": "micro-split-live-v1", "allocation": allocation, "unit_amount": "1000000", "legs": legs}
    if tilt:
        block["conviction_tilt"] = tilt
    return block


@pytest.fixture()
def world(tmp_path):
    db = tmp_path / "tracking.sqlite"
    conn = sqlite3.connect(db)
    for statement in SCHEMA:
        conn.execute(statement.format(p=""))
        conn.execute(statement.format(p="us_"))
    for statement in TRACKERS:
        conn.execute(statement)
    runner = {"status": "RUNNER", "hold_until": "2026-12-01", "entry_ref": 10000}
    legs = [{"kind": "INITIAL", "allocation": "0.65", "price": "10000", "at": "2026-10-06T01:00:00+00:00"},
            {"kind": "ADD", "allocation": "0.20", "price": "11000", "at": "2026-10-08T01:00:00+00:00",
             "scenario_id": "breakout_1"},
            {"kind": "ADD", "allocation": "0.15", "price": "11500", "at": "2026-10-08T02:00:00+00:00",
             "scenario_id": "breakout_1", "rail": "ACCELERATION"}]
    tilt = {"base": "0.45", "tilted": "0.65", "reason": "CONVICTION_TOP_SETUP"}
    conn.execute(HOLD.format(p=""), ("a", "100001", "가속전자", 10000, "2026-10-06 10:00:00", 13000,
                                     json.dumps({"buy_score": 8, "micro_split": _micro(legs, "1.0", tilt),
                                                 "runner": runner}), "일중 상승률 상위주"))
    relaxed = {"buy_score": 5, "_entry_score_policy": {"mode": "micro_split_floor", "required_score": 5,
                                                       "legacy_required_score": 8},
               "_decision_context": {"slots_used": 7},
               "micro_split": _micro([{"kind": "INITIAL", "allocation": "0.3", "price": "5000",
                                       "at": "2026-10-07T01:00:00+00:00"}], "0.3")}
    conn.execute(HOLD.format(p=""), ("a", "100002", "완화화학", 5000, "2026-10-07 10:00:00", 4800,
                                     json.dumps(relaxed), "갭 상승 모멘텀 상위주"))
    conn.execute(HIST.format(p=""), ("a", "100003", "손절소재", 1000, "2026-10-06 09:30:00", 930,
                                     "2026-10-08 09:10:00", -7.0, 2, json.dumps({"buy_score": 7}),
                                     "거래량 급증 상위주", "stop"))
    conn.execute(HIST.format(p=""), ("a", "100004", "재진입중공업", 2000, "2026-10-09 10:00:00", 2060,
                                     "2026-10-12 10:00:00", 3.0, 3,
                                     json.dumps({"reentry": {"version": "reentry_v3", "attempt": 1}}),
                                     "재진입(기준 가격 재돌파 매수)", "ai"))
    conn.execute(HIST.format(p=""), ("a", "100005", "직전손절", 1000, "2026-09-25 09:30:00", 950,
                                     "2026-09-28 09:10:00", -5.0, 3, "{}", "거래량 급증 상위주", "stop"))
    conn.execute(WATCH.format(p=""), ("200001", "무사유", 100, "2026-10-06 15:00:00", 8, 8, "Skip",
                                      "AI 판단: Skip", "{}", "갭 상승 모멘텀 상위주", "morning"))
    conn.execute(WATCH.format(p=""), ("200002", "점수부족", 100, "2026-10-06 15:00:00", 7, 8, "Skip",
                                      "AI 판단: Skip / 점수 부족 (7/8)", json.dumps({"rejection_reason": "x"}),
                                      "갭 상승 모멘텀 상위주", "morning"))
    conn.execute(WATCH.format(p=""), ("200003", "보유많음", 100, "2026-10-07 15:00:00", 5, 5, "Skip",
                                      "AI 판단: Skip", json.dumps({"rejection_reason": "보유 7종목 6점 규칙",
                                                                    "_decision_context": {"slots_used": 7}}),
                                      "갭 상승 모멘텀 상위주", "afternoon"))
    conn.execute(WATCH.format(p=""), ("100001", "가속전자", 0, "2026-10-08 15:00:00", 0, 8, "Already holding",
                                      "AI 판단: Already holding", "{}", "x", "morning"))
    conn.execute(HOLD.format(p="us_"), ("a", "AAPL", "Apple", 100, "2026-10-07 23:30:00", 104,
                                        json.dumps({"buy_score": 7, "micro_split": _micro(
                                            [{"kind": "INITIAL", "allocation": "0.5", "price": "100",
                                              "at": "2026-10-07T14:30:00+00:00"}], "0.5")}),
                                        "Gap Up Momentum Top"))
    conn.execute(WATCH.format(p="us_"), ("MSFT", "Microsoft", 400, "2026-10-08 03:40:00", 3, 5, "no_entry",
                                         "AI judgment: no_entry / Insufficient score (3/5)", "{}",
                                         "Volume Surge Top", "afternoon"))
    conn.commit()
    conn.close()

    def event(kind, day, market, ticker, attrs, hour=1):
        return {"event_type": kind, "timestamp": f"{day}T0{hour}:00:00Z", "market": market, "ticker": ticker,
                "severity": "INFO", "attributes": attrs}

    events = [
        event("runner.detected", "2026-10-09", "KR", "100001", {"status": "RUNNER"}),
        event("runner.sell_blocked", "2026-10-10", "KR", "100001",
              {"code": "TARGET", "source": "llm", "current_price": 12000}),
        event("runner.sell_blocked", "2026-10-10", "KR", "100001",
              {"code": "TARGET", "source": "llm", "current_price": 12100}, hour=2),
        event("runner.sell_blocked", "2026-10-11", "KR", "100001", {"code": "TIME", "source": "final"}),
        event("micro_split.add_planned", "2026-10-06", "KR", "100001",
              {"plan_status": "ACTIVE", "dropped": ["CHASE_CLAMPED"]}),
        event("micro_split.add_executed", "2026-10-08", "KR", "100001",
              {"rail": "ACCELERATION", "acceleration": {"gain_pct": 10.0, "volume_pace": 1.8}, "add_price": 11500,
               "allocation_before": 0.85, "allocation_after": 1.0, "broker_success": True}),
        event("micro_split.add_blocked", "2026-10-09", "KR", "100001",
              {"scenario_id": "breakout_2", "block": "RISK_LIMIT", "price": 12500}),
        event("trigger_quality.selection", "2026-10-06", "KR", None,
              {"trade_date": "20261006", "trigger_mode": "morning", "excluded_triggers": ["거래량 급증 상위주"],
               "displaced": [{"ticker": "300001", "trigger": "거래량 급증 상위주", "reference_price": 1000.0}],
               "fill_picks": [{"ticker": "300002", "trigger": "갭 상승 모멘텀 상위주", "reference_price": 2000.0}]}),
        event("sell.fallback_used", "2026-10-08", "US", "AAPL",
              {"should_sell": True, "legacy_ten_pct_rule": True, "trigger": "parse_failed"}),
        event("kis.rate_limited", "2026-10-07", None, None, {"path": "/uapi/x/inquire-price"}),
        event("reentry_v3.shadow_recheck", "2026-10-13", "KR", "100004",
              {"approved": False, "rejection_reason": "추세 약함"}),
        event("runner.sell_blocked", "2026-09-01", "KR", "100001", {"code": "OLD", "source": "llm"}),
    ]
    spool = tmp_path / "events.jsonl"
    spool.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\nnot json\n", encoding="utf-8")

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    watch = {"watch_id": "w1", "status": "ACTIVE", "ticker": "100004", "source": "STOP_EXIT",
             "decisions": {"2026-10-09": {"trigger": "REBREAK", "event_id": "e1"},
                           "2026-10-10": {"trigger": None}},
             "rechecks": {"e1": {"status": "OK", "approved": True}},
             "live": {"2026-10-09": {"status": "BOUGHT", "result": "BOUGHT"}},
             "ledger": {"campaigns": {"L97": {"attempts": [
                 {"date": "2026-10-09", "status": "CLOSED", "ret": 0.03},
                 {"date": "2026-10-13", "status": "CLOSED", "ret": -0.02}]}}}}
    (runtime / "reentry_v3_state_kr.json").write_text(json.dumps({"watches": [watch]}), encoding="utf-8")

    logs = tmp_path / "repo"
    (logs / "logs").mkdir(parents=True)
    (logs / "prism-us").mkdir()
    (logs / "orchestrator_20261006.log").write_text(
        "2026-10-06 09:31:00,000 - t - INFO - [SCREENING-FILTER] market=KR trigger=morning_volume_surge "
        "trade_date=20261006 reason=close_below_previous_close rejected=4 sample=a\n"
        "2026-10-06 10:00:00,000 - t - WARNING - 005930 Empty response from LLM, falling back to legacy algorithm\n"
        'Error Code : 500 | {"msg_cd":"EGW00201"}\n', encoding="utf-8")
    undated = logs / "logs" / "us_morning.log"
    undated.write_text(
        "2026-09-01 10:00:00,000 - p - WARNING - ChatGPT API error (429): old\n"
        "2026-10-07 23:20:00,000 - p - WARNING - ChatGPT API error (429): quota\n", encoding="utf-8")
    stamp = datetime(2026, 10, 10).timestamp()
    os.utime(undated, (stamp, stamp))
    with gzip.open(logs / "prism-us" / "us_orchestrator_20261008.log.gz", "wt", encoding="utf-8") as handle:
        handle.write("2026-10-08 00:10:00,000 - u - WARNING - [NEG_EQUITY_F2] AAPL facts unavailable: x\n")
    (logs / "orchestrator_20260901.log").write_text(
        "2026-09-01 10:00:00,000 - t - WARNING - x falling back to legacy algorithm\n", encoding="utf-8")
    return {"db": db, "spool": spool, "runtime": runtime, "logs": logs, "out": tmp_path / "reviews"}


PRICES = {"100001": (13000, 13500), "300001": (1300, 1400), "300002": (1900, 2100), "100002": (4800, 5100),
          "AAPL": (104, 106)}


class FakePrices:
    calls = []

    def __call__(self, market, ticker, start, end):
        FakePrices.calls.append((market, ticker, start))
        close, high = PRICES.get(ticker, (100, 101))
        day, last = date.fromisoformat(start), date.fromisoformat(end)
        bars = []
        while day <= last:
            bars.append({"date": day.isoformat(), "high": float(high), "close": float(close)})
            day += timedelta(days=1)
        return bars


def _args(world, *extra):
    return ["--start", START, "--end", END, "--db", str(world["db"]), "--events", str(world["spool"]),
            "--runtime", str(world["runtime"]), "--log-root", str(world["logs"]), "--out-dir", str(world["out"]),
            *extra]


def _run(world, monkeypatch, capsys, *extra):
    monkeypatch.setattr(twr.WR, "LivePrices", FakePrices)
    monkeypatch.setattr(twr.WR, "send_private", lambda messages: pytest.fail("must not send"))
    code = twr.main(_args(world, *extra))
    return code, capsys.readouterr().out


def test_dry_run_covers_every_change_with_counterfactuals(world, monkeypatch, capsys):
    world["db"].chmod(0o444)                               # read-only DB must be enough
    code, out = _run(world, monkeypatch, capsys, "--dry-run")
    assert code == 0
    for heading in ("0) 근거 자료 상태", "1) 초분할", "2) 증액", "3) 상위 셋업", "4) 주도주", "5) 재진입", "6) 트리거",
                    "7) 매수 판단", "8) 오류", "9) 북극성"):
        assert heading in out
    # 1. micro-split, score floor, crowded-book rule
    assert "[KR] 신규 진입 4건 중 초분할(처음에 일부만 매수) 2건" in out
    assert "최소 점수 5점 덕분에 들어간 진입(예전 기준 미달) 1건" in out
    assert "보유 7종목 이상일 때 5점 후보 보류 1건" in out and "규칙 위반 의심: 완화화학(100002)" in out
    # 2. adds, acceleration, risk rail
    assert "가속 구간 두 번째 증액 1건" in out and "최초가 대비 +10.0%, 거래량 1.8배" in out
    assert "막힌 증액 1건(RISK_LIMIT 1건)" in out.replace("조건은 맞았지만 안전장치로 ", "")
    assert "막힌 증액의 그 뒤 가격: 평균 +4.0%" in out
    # 3. conviction tilt: +20%p of a slot on a +30% first leg = +0.60%p
    assert "가중 진입 1건, 가중분(기본 비중 대비 더 산 몫) 손익 합계 +0.60%p" in out
    # 4. runner hold: two blocks after de-dup, one priced (12000 -> 13000)
    assert "매도 보류 2건" in out and "보류가 도움 1건 / 손해 0건" in out and "보류 시점 가격이 없는 기록 1건" in out
    assert "가속전자(100001) 최고 +29.9%→현재 +25.1%" in out
    # 5. re-entry: approved virtual +3%, unreviewed -2%, one real entry
    assert "신호 1건" in out and "AI 승인 신호 평균 +3.0%(1건)" in out and "평균 -2.0%(1건)" in out
    assert "실제 재진입 1건" in out and "추세 약함" in out and "BOUGHT 1건" in out
    # 6. trigger quality: displaced +30% vs fill pick -5%
    assert "자리를 잃은 후보 1건: 그 뒤 현재가 평균 +30.0%" in out
    assert "점수 경쟁으로 뽑힌 후보 1건: 현재가 평균 -5.0%" in out
    # 7. BUY: missing reason only for the bare skip, held rows filtered, screening filter totals
    assert "사유 글이 없는 보류 1건(200001)" in out and "분석 7건 중 진입 4건" in out
    assert "제외 종목 합계 4건" in out and "조회 실패·시간 초과 1회" in out
    # 8. errors and fallbacks (out-of-window lines and events ignored)
    assert "[US] AI 매도 판단 대체 규칙 사용 1회(매도로 이어짐 1회, 옛 +10% 익절 규칙 1회)" in out
    assert "KIS 초당 호출 한도 초과 이벤트 1건" in out and "429 1건" in out
    assert "AI 매도 판단 실패 → 옛 규칙 대체(KR) 1" in out and "KIS 초당 호출 한도 초과(EGW00201) 1" in out
    # 9. scorecard compares with the prior window
    assert "손절 규칙 1→1건" in out
    chunks = out.split("-" * 40)
    assert all(len(chunk.strip()) <= twr.WR.MESSAGE_LIMIT for chunk in chunks)
    assert not world["out"].exists()                      # dry run writes nothing


def test_send_mode_writes_the_full_report_file(world, monkeypatch, capsys):
    monkeypatch.setenv("PRISM_DISABLE_SIGNAL_PUBLISH", "1")
    code, _ = _run(world, monkeypatch, capsys)
    assert code == 0
    (path,) = world["out"].iterdir()
    assert path.name == f"two_week_review_{START}_{END}.txt"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("[PRISM 2주 점검]") and "9) 북극성 성적표" in text and "(1/" not in text


def test_price_lookups_are_capped_per_market(world, monkeypatch, capsys):
    FakePrices.calls = []
    _run(world, monkeypatch, capsys, "--dry-run", "--max-price-calls", "2")
    by_market = {}
    for market, _, _ in FakePrices.calls:
        by_market[market] = by_market.get(market, 0) + 1
    assert by_market and max(by_market.values()) <= 2


def test_missing_sources_degrade_to_notes(tmp_path, monkeypatch, capsys):
    db = tmp_path / "empty.sqlite"
    sqlite3.connect(db).close()
    world = {"db": db, "spool": tmp_path / "none.jsonl", "runtime": tmp_path / "rt", "logs": tmp_path / "logs",
             "out": tmp_path / "out"}
    code, out = _run(world, monkeypatch, capsys, "--dry-run", "--no-logs")
    assert code == 0 and "보유 테이블이 없어 건너뜁니다" in out and "상태 파일이 없어" in out


def test_skip_classification():
    assert twr.classify_skip({"skip_reason": "AI 판단: Skip", "scenario": {}}) == ("AI 거절", True)
    assert twr.classify_skip({"skip_reason": "AI 판단: Skip", "scenario": {"rejection_reason": "R/R"}}) == (
        "AI 거절", False)
    assert twr.classify_skip({"skip_reason": "결정론적 게이트: x", "scenario": {}})[0] == "결정론 게이트"
    assert twr.classify_skip({"skip_reason": "AI judgment: no_entry / Insufficient score (3/5)",
                              "scenario": {}}) == ("점수 부족", False)
