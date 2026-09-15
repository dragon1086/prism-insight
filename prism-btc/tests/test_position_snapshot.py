"""Notification semantics, realistic cross-account FX and missing broker fields."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from live import notifier, swing, tracking
from live.position_snapshot import capture_swing_snapshot, snapshot_lines, wallet_fields
from live.swing_entry_notice import build_recovered_message


def position():
    return tracking.PositionRow(side="long", entry_price=78188.2, qty=.089,
        leverage=.724, sl_price=76623.73, tp1_price=0, tp2_price=0, tp3_price=0,
        liq_price=0, entry_time="2026-09-15T00:02:00+00:00", tranche_index=0,
        entry_bar_idx=0, initial_risk=139.23783, mode="swing")


class Session:
    def __init__(self):
        self.calls = []
        self.margin_mode = "REGULAR_MARGIN"
        self.wallet = {"accountType": "UNIFIED", "totalEquity": "178829.49570181",
            "totalWalletBalance": "98427.56223226", "totalAvailableBalance": "97011.54123638",
            "totalInitialMargin": "1389.15732343", "totalMaintenanceMargin": "36.18",
            "coin": [{"coin": "USDT", "equity": "48421.01641882", "usdValue": "48411.42905756"}]}
        self.row = {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": ".089",
            "avgPrice": "78188.2", "markPrice": "77886.3", "positionValue": "6931.8807",
            "leverage": "5", "positionIM": "1389.43798991", "positionMM": "36.18",
            "liqPrice": "", "unrealisedPnl": "-26.8691", "stopLoss": "76623.73",
            "tradeMode": 1}  # deprecated field deliberately contradicts account mode

    def get_account_info(self):
        self.calls.append("get_account_info")
        return {"retCode": 0, "result": {"marginMode": self.margin_mode}}

    def get_wallet_balance(self, **kwargs):
        assert kwargs == {"accountType": "UNIFIED"}
        self.calls.append("get_wallet_balance")
        return {"retCode": 0, "result": {"list": [self.wallet]}}

    def get_positions(self, **kwargs):
        assert kwargs == {"category": "linear", "symbol": "BTCUSDT"}
        self.calls.append("get_positions")
        return {"retCode": 0, "result": {"list": [self.row]}}


def capture(session=None):
    return capture_swing_snapshot(SimpleNamespace(name="exchange", sess=session or Session()), position())


def test_realistic_cross_whole_account_fx_official_margin_and_time():
    session = Session()
    snap = capture(session)
    text = "\n".join(snapshot_lines(snap, position()))
    assert session.calls == ["get_account_info", "get_wallet_balance", "get_positions"]
    assert "교차마진(Cross)" in text and "격리마진" not in text
    assert "거래소 레버리지: 5배" in text and "0.724배" not in text
    assert "전체 거래계좌 평가액: 178,829.50 USD" in text
    assert "현재 명목 포지션: 6,931.88 USDT (전체 계좌의 약 3.88%" in text
    assert "positionIM): 1,389.44 USDT (전체 계좌의 약 0.78%" in text
    assert "고정 투입원금이 아닙니다" in text and "진입 당시 값 아님" in text
    assert snap["captured_at"] != position().entry_time
    assert "거래소 청산가: 확인 불가" in text


def test_operating_margin_ratio_precedes_broker_account_reference():
    snap = capture()
    snap["position"]["position_im"] = 1388
    snap["wallet"]["usdt_usd_rate"] = 1
    text = "\n".join(snapshot_lines(snap, position(), operating_capital=9600))
    assert "운용자금 대비 증거금 사용 비중: 약 14.5%" in text
    assert text.index("약 14.5%") < text.index("전체 거래계좌 평가액")
    assert "최대 손실 비중이 아닙니다" in text


@pytest.mark.parametrize("capital", [None, 0, -1, float("nan")])
def test_missing_operating_capital_never_borrows_large_swing_wallet(capital):
    text = "\n".join(snapshot_lines(capture(), position(), operating_capital=capital))
    assert "운용자금 대비 증거금 사용 비중: 확인 불가" in text


def test_operating_margin_ratio_requires_fx_and_owned_position():
    snap = capture()
    snap["wallet"].pop("usdt_usd_rate")
    text = "\n".join(snapshot_lines(snap, position(), operating_capital=9600))
    assert "운용자금 대비 증거금 사용 비중: 확인 불가" in text
    text = "\n".join(snapshot_lines(capture(), position(), operating_capital=9600,
                                   include_position=False))
    assert "운용자금 대비 증거금 사용 비중: 확인 불가" in text


@pytest.mark.parametrize("missing", ["", None, "NaN", "Infinity", "broken"])
def test_missing_margin_leverage_never_zero_or_strategy_fallback(missing):
    session = Session()
    session.row.update(positionIM=missing, leverage=missing)
    session.wallet["totalInitialMargin"] = missing
    text = "\n".join(snapshot_lines(capture(session), position()))
    assert "positionIM): 확인 불가" in text
    assert "전체 초기증거금: 확인 불가" in text
    assert "거래소 레버리지: 확인 불가" in text
    assert "positionIM): 0.00" not in text


def test_portfolio_mode_does_not_report_invalid_position_margin():
    session = Session()
    session.margin_mode = "PORTFOLIO_MARGIN"
    session.row.update(positionIM="0", leverage="0")
    text = "\n".join(snapshot_lines(capture(session), position()))
    assert "포트폴리오마진" in text
    assert "positionIM): 확인 불가" in text


@pytest.mark.parametrize("field,value", [("symbol", "ETHUSDT"), ("side", "Sell"), ("positionIdx", 1)])
def test_identity_mismatch_never_borrows_position(field, value):
    session = Session()
    session.row[field] = value
    assert capture(session)["position"] == {}


def test_no_fx_no_scope_no_time_no_ratio():
    snap = capture()
    for key in ("account_scope", "captured_at"):
        bad = deepcopy(snap)
        bad.pop(key)
        assert "전체 계좌의 약" not in "\n".join(snapshot_lines(bad, position()))
    snap["wallet"].pop("usdt_usd_rate")
    assert "전체 계좌의 약" not in "\n".join(snapshot_lines(snap, position()))
    assert wallet_fields({"coin": [{"coin": "USDT", "equity": "0", "usdValue": "50"}]}).get("usdt_usd_rate") is None


def test_optional_get_failures_are_unknown_no_order_calls():
    class Failing:
        def __getattr__(self, name):
            assert name in {"get_account_info", "get_wallet_balance", "get_positions"}
            return lambda **kw: (_ for _ in ()).throw(TimeoutError())
    snap = capture(Failing())
    assert snap["wallet"] == {} and snap["position"] == {}
    assert "확인 불가" in "\n".join(snapshot_lines(snap, position()))


def test_all_trade_builders_share_rich_block_preserve_specific_details():
    pos, snap = position(), capture()
    row = dict(pos.__dict__)
    context = {"snapshot": snap}
    messages = [notifier._build_entry_message(row, "demo", context)]
    row["tranche_index"] = 1
    messages.append(notifier._build_entry_message(row, "demo", context))
    row.update(exit_price=79000, exit_time="2026-09-15T02:00:00Z", r_multiple=1,
               exit_reason="tp1", fee_paid=2, funding_paid=0, net_pnl=70)
    messages.append(notifier._build_exit_message(row, "demo", context))
    row["exit_reason"] = "trail"
    messages.append(notifier._build_exit_message(row, "demo", context))
    signal = {key: 78000 for key in ("prev_ma10_4h", "prev_ma35_4h", "ma10_4h",
              "ma35_4h", "ma10_1d", "ma35_1d", "close_4h")}
    messages.append(swing._build_entry_message(pos, "exchange", 9611.57, signal,
                    account_snapshot=snap))
    messages.append(swing._build_exit_message(pos, 79000, "swing_trend", "exchange", 70,
                    2, 1, 9611.57, 178829, "2026-09-15T02:00:00Z", account_snapshot=snap))
    record = {**pos.__dict__, "initial_sl": pos.sl_price}
    messages.append(build_recovered_message(record, pos, "demo", snap, 9611.57))
    for index, message in enumerate(messages):
        assert "178,829.50 USD" in message
        if index in (2, 3, 5):
            assert "positionIM): 확인 불가" in message
            assert "현재 다른 포지션을 대입하지 않습니다" in message
        else:
            assert "거래소 레버리지: 5배" in message
            assert "positionIM): 1,389.44 USDT" in message
        assert "조회 시각:" in message and "손절 위험" in message
        assert len(message) < 4096
    assert "전체 위험예산" in messages[0] and "비중 추가" in messages[1]
    assert "순손익:" in messages[2]
    assert "진입 근거" in messages[4] and "9,611.57 USD (거래소 전체 잔고 아님)" in messages[6]
    assert "새 진입이 아닙니다" in messages[6]


@pytest.mark.parametrize("malformed", [[], [1], "broken", 42, None])
def test_optional_metadata_types_never_break_rendering(malformed):
    assert "확인 불가" in "\n".join(snapshot_lines(malformed, position()))
    assert "확인 불가" in "\n".join(snapshot_lines(
        {"wallet": malformed, "account": malformed, "position": malformed}, position()))


def test_paginated_active_then_blank_terminal_uses_existing_complete_reader():
    session = Session()
    session.row.update(createdTime="1785875525065", updatedTime="1789430529487",
                       positionStatus="Normal", takeProfit="")
    def paginated(**kwargs):
        session.calls.append("get_positions")
        if kwargs.get("cursor"):
            blank = {**session.row, "size": "0", **{key: "" for key in
                ("side", "avgPrice", "stopLoss", "takeProfit", "createdTime", "updatedTime", "positionStatus")}}
            return {"retCode": 0, "result": {"list": [blank], "nextPageCursor": ""}}
        return {"retCode": 0, "result": {"list": [session.row], "nextPageCursor": "next"}}
    session.get_positions = paginated
    assert capture(session)["position"]["position_im"] == pytest.approx(1389.43798991)
    assert session.calls.count("get_positions") == 3


def test_isolated_principal_caveat_and_historical_risk_are_not_cross_current():
    session = Session()
    session.margin_mode = "ISOLATED_MARGIN"
    text = "\n".join(snapshot_lines(capture(session), position()))
    assert "격리 담보 투입" in text and "교차계좌는" not in text
    assert "진입 기록 손절 위험(당시 수량" in text


def test_swing_fill_notional_not_mark_value():
    pos = position()
    signal = {key: 78000 for key in ("prev_ma10_4h", "prev_ma35_4h", "ma10_4h",
              "ma35_4h", "ma10_1d", "ma35_1d", "close_4h")}
    text = swing._build_entry_message(pos, "exchange", 9611, signal,
        execution_context={"position_value": 1, "qty": .089, "entry_price": 78188.2})
    assert "체결가 기준 명목 포지션: 6,958.75 USDT" in text


def test_normal_notice_capture_runs_after_execution_lock(tmp_path, monkeypatch):
    from live.entry_reservations import execution_mutex
    from .test_swing import _make_tf_data
    conn = tracking.get_connection(tmp_path / "root.sqlite")
    tracking.ensure_schema(conn)
    captures, messages = [], []
    def capture_unlocked(backend, pos):
        with execution_mutex(str(tmp_path / "root.sqlite") + ".btc-execution.lock"):
            captures.append(pos.entry_time)
        return {}
    monkeypatch.setattr(swing, "capture_swing_snapshot", capture_unlocked)
    monkeypatch.setattr(swing, "_notify", lambda mode, text: messages.append(text) or True)
    result = swing.process(conn, _make_tf_data(), "demo", backend=swing.VirtualBackend(conn))
    assert result["events"] == 1 and len(captures) == len(messages) == 1
    conn.close()


def test_notice_failure_preserves_result_and_later_jobs(tmp_path, monkeypatch):
    conn = tracking.get_connection(tmp_path / "root.sqlite")
    tracking.ensure_schema(conn)
    calls = []
    def locked(*args, notice_jobs=None):
        notice_jobs.append(lambda: (_ for _ in ()).throw(TimeoutError()))
        notice_jobs.append(lambda: calls.append("second"))
        return {"events": 2}
    monkeypatch.setattr(swing, "_process_locked", locked)
    assert swing.process(conn, {}) == {"events": 2}
    assert calls == ["second"]
    conn.close()


def fail_optional_snapshot_writes(monkeypatch):
    original = tracking.set_meta
    def save(conn, key, *args, **kwargs):
        if "snapshot" in key:
            raise OSError("optional storage unavailable")
        return original(conn, key, *args, **kwargs)
    monkeypatch.setattr(tracking, "set_meta", save)


def test_main_snapshot_write_failure_does_not_drop_entry_or_exit(monkeypatch):
    from .test_notifier import _root_conn, _mk_position, _mk_trade
    conn = _root_conn()
    messages = []
    notifier.notify_new_events(conn, "demo")
    fail_optional_snapshot_writes(monkeypatch)
    monkeypatch.setattr(notifier, "_dispatch", lambda msgs, mode: messages.extend(msgs))
    tracking.save_position(conn, _mk_position())
    tracking.record_trade(conn, _mk_trade())
    result = notifier.notify_new_events(conn, "demo")
    assert result == {"entries": 1, "exits": 1} and len(messages) == 2
    conn.close()


def test_swing_snapshot_write_failure_keeps_normal_entry_and_exit(tmp_path, monkeypatch):
    from .test_swing import _make_tf_data
    conn = tracking.get_connection(tmp_path / "root.sqlite")
    tracking.ensure_schema(conn)
    fail_optional_snapshot_writes(monkeypatch)
    messages = []
    monkeypatch.setattr(swing, "_notify", lambda mode, text: messages.append(text) or True)
    backend = swing.VirtualBackend(conn)
    assert swing.process(conn, _make_tf_data(), "demo", backend=backend)["events"] == 1
    pos = tracking.load_open_positions(conn, "swing")[0]
    swing._close_position(conn, backend, pos, pos.entry_price + 100, .00055,
                          "swing_ma35_exit", "2026-09-15T03:00:00Z", 10000, 0, "demo")
    assert len(messages) == 2 and "새 진입" in messages[0] and "포지션 정리" in messages[1]
    conn.close()
