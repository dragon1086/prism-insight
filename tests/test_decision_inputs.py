from datetime import date, datetime, timedelta, timezone

import pandas as pd

from observability import decision_inputs as D
from prism_core import decision_input_features as F


def _frame(n=40, today=None, forming_volume=None, columns=("Open", "High", "Low", "Close", "Volume")):
    days, day = [], date(2026, 7, 1)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    if today:
        days = days[:-1] + [today]
    rows = []
    for i, d in enumerate(days):
        close = 100 + i
        rows.append({columns[0]: close - 0.5, columns[1]: close + 1, columns[2]: close - 1, columns[3]: close,
                     columns[4]: 1000 + (i % 2) * 500})
    if forming_volume is not None:
        rows[-1][columns[4]] = forming_volume
    return pd.DataFrame(rows, index=pd.to_datetime(days))


def test_completed_only_features_and_missing_forming_bar():
    now = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)  # 10:00 KST, session open
    bars = F.bars_from_frame(_frame(40))
    out = F.compute(bars, market="KR", observed_at=now, current_price=140.0)
    f = out["features"]
    assert out["status"] == "PARTIAL" and "rvol_time_scaled_linear" in out["missing"]
    assert f["forming_bar_present"] is False and f["atr20_pct"] > 0
    assert f["accumulation_days_25"] + f["distribution_days_25"] <= 25
    assert f["up_down_volume_ratio_20"] is None  # no down closes


def test_forming_bar_time_scaled_rvol_and_gap():
    now = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)  # 10:00 KST = 2/13 of session
    frame = _frame(40, today=date(2026, 9, 28), forming_volume=1000)
    out = F.compute(F.bars_from_frame(frame), market="KR", observed_at=now, current_price=140.0,
                    scenario={"buy_score": 6, "momentum_signal_count": 1, "decision": "x"})
    f = out["features"]
    assert f["forming_bar_present"] is True
    frac = F.elapsed_fraction("KR", now)
    assert abs(frac - 60 / 390) < 1e-6
    assert f["rvol_time_scaled_linear"] > 2 and f["rubric_probe"]["volume_item_time_scaled"] is True
    assert f["scenario_buy_score"] == 6 and f["scenario_momentum_signal_count"] == 1
    assert "gap_open_pct" in f


def test_us_lowercase_columns_and_after_close_counts_today_completed():
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)  # 17:00 ET
    frame = _frame(40, today=date(2026, 9, 28), columns=("open", "high", "low", "close", "volume"))
    out = F.compute(F.bars_from_frame(frame), market="US", observed_at=now, current_price=None)
    assert out["features"]["forming_bar_present"] is False
    assert out["features"]["price_basis"] == 139.0


def test_too_few_bars_is_missing():
    out = F.compute(F.bars_from_frame(_frame(10)), market="KR", observed_at=datetime.now(timezone.utc))
    assert out["status"] == "MISSING"


def test_peer_summary():
    packet = {"ready": True, "period": "2025/12", "price_basis": "전일종가",
              "peers": [{"per": 10, "pbr": 1.0}, {"per": 20, "pbr": 2.0}, {"per": 30, "pbr": None}, {"per": -5, "pbr": 3.0}]}
    out = D.peer_valuation_summary(packet)
    assert out["peer_median_per"] == 25 and out["peer_median_pbr"] == 2.5
    assert out["per_discount_vs_median_pct"] == 60.0
    assert D.peer_valuation_summary({"ready": False, "skip_reason": "x"})["status"] == "MISSING"


class _Agent:
    pass


def test_emit_is_fail_open_idempotent_and_skips_isolated(monkeypatch):
    sent = []
    monkeypatch.setattr(D, "emit_event", lambda event, **kw: sent.append((event, kw)) or {"ok": 1})
    agent = _Agent()
    D.capture_frame(agent, "AAPL", _frame(40), market="US")
    agent._report_meta = {}
    now = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    payload = D.emit_decision_inputs(agent, market="US", ticker="AAPL", decision_id="d-1", scenario={},
                                     current_price=140.0, decision="no_entry", source="t", now=now,
                                     earnings_lookup=lambda t, d: {"status": "OK", "next_earnings_date": "2026-10-30"})
    assert payload["trading_impact"] == "none" and payload["earnings"]["status"] == "OK"
    assert payload["peer_valuation"]["status"] == "MISSING"
    D.emit_decision_inputs(agent, market="US", ticker="AAPL", decision_id="d-1", scenario={}, current_price=140.0,
                           decision="no_entry", source="t", now=now, earnings_lookup=lambda t, d: {})
    assert sent[0][1]["event_id"] == sent[1][1]["event_id"] and sent[0][1]["decision_id"] == "d-1"
    agent._no_order_effects = object()
    assert D.emit_decision_inputs(agent, market="US", ticker="AAPL", decision_id="d-2", scenario={},
                                  current_price=1, decision="x", source="t") is None
    broken = _Agent()
    broken._decision_input_bars = {"AAPL": {"market": "US", "bars": "garbage"}}
    assert D.emit_decision_inputs(broken, market="US", ticker="AAPL", decision_id="d-3", scenario={},
                                  current_price=1, decision="x", source="t", earnings_lookup=lambda t, d: {}) is None


def test_disabled_flag(monkeypatch):
    monkeypatch.setenv("DECISION_INPUT_SHADOW_ENABLED", "false")
    agent = _Agent()
    D.capture_frame(agent, "X", _frame(40), market="KR")
    assert not hasattr(agent, "_decision_input_bars")


def test_payload_survives_event_sanitizer(monkeypatch):
    import json

    from observability.events import build_event
    captured = {}
    monkeypatch.setattr(D, "emit_event", lambda event, **kw: captured.update(kw) or {"ok": 1})
    agent = _Agent()
    D.capture_frame(agent, "AAPL", _frame(40, today=date(2026, 9, 28), forming_volume=900), market="US")
    D.emit_decision_inputs(agent, market="US", ticker="AAPL", decision_id="d", scenario={}, current_price=140.0,
                           decision="x", source="t", now=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
                           earnings_lookup=lambda t, d: {"status": "OK"})
    event = build_event("decision_inputs.shadow_captured", service="s", attributes=captured["attributes"])
    assert "[REDACTED]" not in json.dumps(event["attributes"])
    assert event["attributes"]["features"]["market_elapsed_fraction"] > 0


def test_prompt_block_uses_completed_sessions_only_and_no_accumulation_claim():
    now = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)  # KR session in progress
    frame = _frame(40, today=date(2026, 9, 28), forming_volume=50_000)  # huge forming volume
    out = F.compute(F.bars_from_frame(frame), market="KR", observed_at=now)
    block, flags = F.render_facts_block(out, None, None, market="KR", language="ko")
    # The forming bar's volume must not satisfy the rubric item (volume contract of 2026-09-27).
    assert flags["volume_item_completed"] is False
    assert "매집" not in block and "장중 추정" not in block
    assert out["features"]["last_completed_date"] in block


def test_completed_rvol_uses_each_sessions_own_prior_window():
    frame = _frame(40)
    frame.iloc[-2, frame.columns.get_loc("Volume")] = 5000  # spike two sessions ago
    out = F.compute(F.bars_from_frame(frame), market="KR", observed_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc))
    assert out["features"]["rvol_max_last3_completed"] > 3
    assert F.rubric_flags(out["features"])["volume_item_completed"] is True


def test_peer_and_earnings_lines_and_missing_wording():
    peer = {"status": "OK", "peer_count": 2, "period": "2025/12", "price_basis": "전일종가", "peer_median_per": 10,
            "target_per": 6, "per_discount_vs_median_pct": 40, "peer_median_pbr": 1, "target_pbr": 0.8}
    block, flags = F.render_facts_block({"features": {}}, peer, {"status": "MISSING"}, market="US", language="ko")
    assert flags["peer_usable"] is False and "업종 평균 대용 가능(지표별 유효값 3개 이상): PER 아니오 · PBR 아니오" in block
    assert "일정이 없다는 뜻이 아닙니다" in block
    block_kr, _ = F.render_facts_block({"features": {}}, None, None, market="KR", language="en")
    assert "earnings" not in block_kr.lower() and "missing" in block_kr


def test_peer_usability_counts_valid_values_per_metric():
    # 2026-10-01 003160: 3 selected peers, 2 loss-making -> one PER left (11.74).
    packet = {"ready": True, "period": "2025/12", "price_basis": "전일종가", "peers": [
        {"per": 40.0, "pbr": 3.0},                    # target
        {"per": 11.74, "pbr": 1.5}, {"per": -8.0, "pbr": 1.1}, {"per": None, "pbr": 2.2}]}
    summary = D.peer_valuation_summary(packet)
    assert summary["peer_count"] == 3 and summary["peer_valid_per"] == 1 and summary["peer_valid_pbr"] == 3
    block, flags = F.render_facts_block({"features": {}}, summary, None, market="KR", language="ko")
    assert flags["peer_usable"] is False and flags["peer_usable_pbr"] is True
    assert "PER 중앙값 11.74(유효값 1개" in block and "PER 아니오 · PBR 예" in block
    block_en, _ = F.render_facts_block({"features": {}}, summary, None, market="KR", language="en")
    assert "(1 valid," in block_en and "PER no · PBR yes" in block_en

    full = dict(packet, peers=packet["peers"][:2] + [{"per": 9.0, "pbr": 1.0}, {"per": 14.0, "pbr": 2.0}])
    assert F.rubric_flags({}, D.peer_valuation_summary(full))["peer_usable"] is True
    assert F.rubric_flags({}, {"status": "OK", "peer_count": 5})["peer_usable"] is False  # count unknown


def test_prompt_facts_is_fail_open_and_caches_what_prompt_saw(monkeypatch):
    agent = _Agent()
    assert D.prompt_facts(agent, "X", market="KR") == ""          # nothing captured
    D.capture_frame(agent, "X", _frame(40), market="KR")
    agent._report_meta = {"X": {"peer_valuation": {"status": "MISSING"}}}
    block = D.prompt_facts(agent, "X", market="KR", now=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc))
    assert block.startswith("### 📐") and agent._decision_input_prompt["X"]["block"] == block
    monkeypatch.setenv("PRISM_BUY_DECISION_FACTS", "false")
    assert D.prompt_facts(agent, "X", market="KR") == ""
    broken = _Agent()
    broken._decision_input_bars = {"X": {"market": "KR", "bars": None}}
    monkeypatch.setenv("PRISM_BUY_DECISION_FACTS", "true")
    assert D.prompt_facts(broken, "X", market="KR") == ""


def test_contract_is_appended_to_buy_instructions(monkeypatch):
    from cores.agents.trading_agents import create_trading_scenario_agent
    kr = create_trading_scenario_agent(language="ko").instruction
    assert "보조 수치 팩트 사용법" in kr and "새 가점·감점·진입 차단 조건이 아닙니다" in kr
    monkeypatch.setenv("PRISM_BUY_DECISION_FACTS", "false")
    assert "보조 수치 팩트 사용법" not in create_trading_scenario_agent(language="ko").instruction


def test_english_block_has_no_korean_missing_marker():
    block, _ = F.render_facts_block({"features": {"last_completed_date": "2026-09-25"}}, None, None,
                                    market="US", language="en")
    assert "결측" not in block and "missing" in block
