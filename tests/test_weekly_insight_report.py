"""Offline regression coverage at the rendered weekly-message boundary."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import types
from datetime import datetime

import pytest


@pytest.fixture
def report(monkeypatch):
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    trading = types.ModuleType("trading")
    trading.kis_auth = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    monkeypatch.setitem(sys.modules, "trading", trading)
    spec = importlib.util.spec_from_file_location(
        "weekly_report_under_test", Path(__file__).parents[1] / "weekly_insight_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_get_primary_account_key", lambda market: "demo")
    return module


def make_db(path):
    with sqlite3.connect(path) as conn:
        for prefix in ("", "us_"):
            conn.execute(f"CREATE TABLE {prefix}trading_history (ticker, company_name, buy_price, sell_price, profit_rate, holding_days, sell_date, account_key, scenario)")
            conn.execute(f"CREATE TABLE {prefix}stock_holdings (ticker, company_name, buy_price, buy_date, current_price, account_key, scenario)")
        conn.execute("CREATE TABLE analysis_performance_tracker (trigger_type, tracking_status, tracked_30d_return, was_traded, analyzed_date)")
        conn.execute("CREATE TABLE us_analysis_performance_tracker (trigger_type, return_30d, was_traded, analysis_date)")
        conn.execute("CREATE TABLE trading_principles (market, is_active, created_at)")
        conn.execute("CREATE TABLE trading_intuitions (condition, insight, confidence, success_rate, market, is_active, created_at, application_context)")
        conn.executemany("INSERT INTO analysis_performance_tracker VALUES ('횡보 거래량', 'completed', ?, 0, '2026-01-01')", [(0.2,), (0.1,), (-0.1,), (None,)])
        conn.executemany("INSERT INTO us_analysis_performance_tracker VALUES ('Volume', ?, ?, '2026-01-01')", [(0.2, 0), (0.3, 0), (-0.1, 0), (-0.9, None), (7.6, None)])
        context = {'version': 1, 'status': 'current_pipeline', 'market': 'KR',
                   'stage': 'batch_buy', 'required_capabilities': ['batch_report', 'entry_advisory'],
                   'reason': 'Existing batch inputs support this independently reviewed reference.'}
        conn.execute("INSERT INTO trading_intuitions VALUES ('조건', '직관', .97, NULL, 'KR', 1, '2026-01-01', ?)", (json.dumps(context),))


def test_current_memory_and_future_improvements_are_reported_separately(report, tmp_path):
    path = tmp_path / 'memory-applicability.sqlite'
    make_db(path)
    future = {'version': 1, 'status': 'improvement', 'market': 'KR',
              'stage': 'system_design', 'required_capabilities': [],
              'reason': 'A new monitoring and entry workflow is required.'}
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO trading_intuitions VALUES ('미래 기능', '자동 후속 진입 개발', .99, NULL, 'KR', 1, '2026-01-01', ?)", (json.dumps(future),))
        conn.execute("INSERT INTO trading_intuitions VALUES ('미분류', '검토 안 된 지시', .99, NULL, 'KR', 1, '2026-01-01', NULL)")
    message = asyncio.run(report.generate_weekly_report(str(path)))
    assert '현재 적용 참고: 1개' in message
    assert '적용성 검토 대기: 1개' in message
    assert '향후 시스템 개선 검토' in message
    before_future, future_section = message.split('향후 시스템 개선 검토', 1)
    assert '조건 → 직관' in before_future
    assert '자동 후속 진입 개발' not in before_future
    assert '자동 후속 진입 개발' in future_section
    assert '검토 안 된 지시' not in message


def test_memory_highlights_do_not_repeat_one_market_and_stage(report, tmp_path):
    path = tmp_path / 'memory-stage-diversity.sqlite'
    make_db(path)
    holding = {'version': 1, 'status': 'current_pipeline', 'market': 'KR',
               'stage': 'position_management', 'required_capabilities': ['position_review'],
               'reason': 'Preserve the existing protective position policy.'}
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO trading_intuitions VALUES ('보유 관리 대표', '기존 손절 정책 준수', .99, NULL, 'KR', 1, '2026-01-01', ?)", (json.dumps(holding),))
        conn.execute("INSERT INTO trading_intuitions VALUES ('보유 관리 반복', '손절 규율 유지', .98, NULL, 'KR', 1, '2026-01-01', ?)", (json.dumps(holding),))
    message = asyncio.run(report.generate_weekly_report(str(path)))
    assert '조건 → 직관' in message
    assert '보유 관리 대표' in message
    assert '보유 관리 반복' not in message
    assert message.index('[KR·진입 판단]') < message.index('[KR·보유 관리]')


def test_report_observations_do_not_imply_strategy_quality(report, tmp_path):
    path = tmp_path / "report.sqlite"
    make_db(path)
    message = asyncio.run(report.generate_weekly_report(str(path)))
    assert "누적 트리거 관측" in message
    assert "2/3건, 67%" in message
    assert "2026-01-01" in message
    assert "미매수 후 하락: 1건 (평균 -10.0%)" in message
    assert "최고 +760.0%" not in message
    assert "매수 기준을 약간 완화" not in message
    assert "가장 안정적" not in message
    assert "가장 정확한" not in message
    assert "모델 자체 평가" in message


def test_failed_queries_are_unknown_not_successful_zero(report, tmp_path):
    path = tmp_path / "missing.sqlite"
    sqlite3.connect(path).close()
    message = asyncio.run(report.generate_weekly_report(str(path)))
    assert "조회 실패" in message
    assert "0건" not in message
    assert "이번 주 매매 없음" not in message
    assert "안정적으로 운영" not in message
    assert "총 0개" not in message


def test_sell_verdict_is_a_price_observation(report):
    assert report._sell_verdict(1.7) == "매도 후 상승"
    assert report._sell_verdict(-1.7) == "매도 후 하락"
    assert report._sell_verdict(0) == "매도 후 보합"


@pytest.mark.parametrize("change", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_change_is_unknown(report, change):
    assert report._sell_verdict(change) == "매도 후 가격 변화 미확인"


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("field", ["sell", "current"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), 0, -1, None])
def test_invalid_sell_prices_are_unknown(report, tmp_path, monkeypatch, market, field, invalid):
    path = tmp_path / "invalid.sqlite"
    make_db(path)
    sell_price = invalid if field == "sell" else 232.46
    current_price = invalid if field == "current" else 236.32
    table = "trading_history" if market == "KR" else "us_trading_history"
    with sqlite3.connect(path) as conn:
        conn.execute(f"INSERT INTO {table} VALUES ('DGX', 'Quest', 236, ?, -1.5, 1, ?, 'demo', NULL)", (sell_price, datetime.now().isoformat()))
    yf = types.ModuleType("yfinance")
    yf.download = lambda *a, **kw: {"Close": types.SimpleNamespace(iloc={-1: current_price})}
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    helpers = types.ModuleType("tracking.helpers")
    helpers.get_requested_session_prices = lambda tickers: {"DGX": current_price}
    monkeypatch.setitem(sys.modules, "tracking.helpers", helpers)
    with sqlite3.connect(path) as conn:
        result = asyncio.run(report._get_sell_evaluation(conn.cursor(), "2026-01-01"))
    assert "가격" in result and "미확인" in result
    assert "보합" not in result
    assert "상승" not in result
    assert "하락" not in result
    assert "nan" not in result.lower()
    assert "inf" not in result.lower()


@pytest.mark.parametrize("price_available", [True, False])
def test_sell_observation_rendered_without_network(report, tmp_path, monkeypatch, price_available):
    path = tmp_path / "sell.sqlite"
    make_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO us_trading_history VALUES ('DGX', 'Quest', 236, 232.46, -1.5, 1, ?, 'demo', NULL)", (datetime.now().isoformat(),))
    yf = types.ModuleType("yfinance")

    def download(*args, **kwargs):
        if not price_available:
            raise RuntimeError("private diagnostic must not appear in message")
        return {"Close": types.SimpleNamespace(iloc={-1: 236.32})}

    yf.download = download
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    message = asyncio.run(report.generate_weekly_report(str(path)))
    assert "매도 후 가격 관측" in message
    assert "적절한 매도" not in message
    if price_available:
        assert "$236.32 (+1.7%) 매도 후 상승" in message
    else:
        assert "매도 후 가격 조회 실패" in message
        assert "private diagnostic" not in message


def test_zero_win_rate_is_still_reported_and_empty_data_distinct(report, tmp_path):
    path = tmp_path / "zero.sqlite"
    make_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE analysis_performance_tracker SET tracked_30d_return=-0.1 WHERE tracked_30d_return IS NOT NULL")
        conn.execute("DELETE FROM us_analysis_performance_tracker")
    message = asyncio.run(report.generate_weekly_report(str(path)))
    assert "0/3건, 0%" in message
    assert "30일 수익률이 확인된 누적 표본이 없습니다." in message
    assert "조회 실패" not in message


@pytest.mark.parametrize("unavailable", ["account", "table"])
def test_weekly_trades_preserve_available_market(report, tmp_path, monkeypatch, unavailable):
    path = tmp_path / "partial.sqlite"
    make_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO trading_history VALUES ('005930', '삼성전자', 70000, 72000, 2.9, 1, ?, 'demo', NULL)", (datetime.now().isoformat(),))
        if unavailable == "table":
            conn.execute("DROP TABLE us_stock_holdings")
            conn.execute("INSERT INTO us_trading_history VALUES ('DGX', 'Quest', 236, 232.46, -1.5, 1, ?, 'demo', NULL)", (datetime.now().isoformat(),))
    if unavailable == "account":
        monkeypatch.setattr(report, "_get_primary_account_key", lambda market: "demo" if market == "kr" else None)
    with sqlite3.connect(path) as conn:
        result = report._get_weekly_trades(conn.cursor(), "2026-01-01")
    assert "삼성전자(005930) 72,000원" in result
    assert "미국시장" in result and "조회 실패" in result
    assert "이번 주 매매 없음" not in result
    if unavailable == "table":
        assert "DGX $232.46" in result
