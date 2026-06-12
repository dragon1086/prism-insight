# tests/test_journal.py — 매매일지 파이프라인 (학습 기어) 테스트
#
# 원칙: LLM/네트워크 없음. postmortem 은 모킹. 모든 수치는 합성 데이터로 손계산 대조.
from __future__ import annotations

import json

import pandas as pd
import pytest

from live import journal, postmortem, tracking
from live.tracking import TradeRow


# ---------------------------------------------------------------------------
# 헬퍼 — 합성 트레이드 / 합성 30m 데이터
# ---------------------------------------------------------------------------

def _conn():
    conn = tracking.get_connection(":memory:")
    tracking.ensure_schema(conn)
    journal.ensure_journal_schema(conn)
    return conn


def _trade_row(**over) -> TradeRow:
    base = dict(
        trade_id=1, side="long",
        entry_time="2026-01-01 00:00:00+00:00", entry_price=100.0,
        exit_time="2026-01-01 12:00:00+00:00", exit_price=110.0,
        qty=1.0, leverage=10.0, sl_price=104.0,  # 트레일된 최종 스탑 (초기 아님!)
        exit_reason="trail", r_multiple=2.0, fee_paid=0.5, funding_paid=0.0,
        tranche_index=0, liq_price=90.0,
        net_pnl=10.0, gross_pnl=11.0, gross_r_multiple=2.2, num_legs=2,
        mode="shadow",
    )
    base.update(over)
    return TradeRow(**base)


def _trade_dict(**over) -> dict:
    row = _trade_row(**over)
    d = dict(row.__dict__)
    d["id"] = over.get("id", 1)
    return d


def _bars_30m() -> pd.DataFrame:
    """진입 100 → 고점 112(6h 시점) → 저점 98 → 청산 110 시나리오."""
    idx = pd.date_range("2026-01-01 00:00:00+00:00", periods=25, freq="30min")
    close = [100 + i * 0.4 for i in range(25)]
    high = [c + 1 for c in close]
    low = [c - 1 for c in close]
    high[12] = 112.0   # MFE: +12 at 6h
    low[3] = 98.0      # MAE: -2
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close},
                        index=idx)


# ---------------------------------------------------------------------------
# 결정적 사실 추출
# ---------------------------------------------------------------------------

class TestInitialRisk:
    def test_inversion_from_net(self):
        # 초기리스크 = |net_pnl / r_multiple| = 10/2 = 5 (sl_price 와 무관해야 함)
        assert journal._initial_risk_usd(_trade_dict()) == pytest.approx(5.0)

    def test_fallback_to_gross(self):
        d = _trade_dict(r_multiple=0.0, net_pnl=0.0)
        assert journal._initial_risk_usd(d) == pytest.approx(11.0 / 2.2)

    def test_unknown_when_no_signal(self):
        d = _trade_dict(r_multiple=0.0, net_pnl=0.0, gross_pnl=0.0, gross_r_multiple=0.0)
        assert journal._initial_risk_usd(d) is None

    def test_negative_r_loss_trade(self):
        d = _trade_dict(r_multiple=-1.0, net_pnl=-5.0)
        assert journal._initial_risk_usd(d) == pytest.approx(5.0)


class TestExcursion:
    def test_long_mfe_mae_in_r(self):
        exc = journal._excursion(_trade_dict(), _bars_30m(), risk_usd=5.0)
        # unit stop = 5/1 = 5 → MFE_R = 12/5 = 2.4, MAE_R = 2/5 = 0.4
        assert exc["mfe_r"] == pytest.approx(2.4)
        assert exc["mae_r"] == pytest.approx(0.4)
        assert exc["mfe_pct"] == pytest.approx(12.0)
        assert exc["time_to_mfe_hours"] == pytest.approx(6.0)
        assert exc["holding_hours"] == pytest.approx(12.0)
        # capture = net_r / mfe_r = 2.0 / 2.4
        assert exc["capture_ratio"] == pytest.approx(2.0 / 2.4, abs=1e-3)

    def test_short_side_mirrors(self):
        d = _trade_dict(side="short")
        exc = journal._excursion(d, _bars_30m(), risk_usd=5.0)
        # 숏: 유리 = entry - low → 최대 (100-98)=2 → 0.4R / 불리 = high-entry → 12 → 2.4R
        assert exc["mfe_r"] == pytest.approx(0.4)
        assert exc["mae_r"] == pytest.approx(2.4)

    def test_no_bars_degrades(self):
        exc = journal._excursion(_trade_dict(), pd.DataFrame(), risk_usd=5.0)
        assert exc["mfe_r"] is None
        assert exc["holding_hours"] == pytest.approx(12.0)

    def test_unknown_risk_gives_pct_only(self):
        exc = journal._excursion(_trade_dict(), _bars_30m(), risk_usd=None)
        assert exc["mfe_r"] is None
        assert exc["mfe_pct"] == pytest.approx(12.0)


class TestExtractFacts:
    def test_r_decomposition_self_check(self):
        facts = journal.extract_facts(_trade_dict(), {"30m": _bars_30m()})
        rd = facts["r_decomposition"]
        assert rd["net_r"] == 2.0
        assert rd["fee_r"] == pytest.approx(0.1)       # 0.5 / 5
        assert rd["funding_r"] == pytest.approx(0.0)
        # gross - fee - funding - net = 2.2 - 0.1 - 0 - 2.0 = 0.1 (모델 잔차 노출)
        assert rd["self_check_residual"] == pytest.approx(0.1)
        assert rd["initial_risk_usd"] == pytest.approx(5.0)

    def test_baseline_structure(self):
        facts = journal.extract_facts(_trade_dict(), None)
        base = facts["baseline"]
        assert base["expectation"]["rr"] == 2.29
        assert "r_percentile" in base and "n_backtest_trades" in base

    def test_no_tf_data_contexts_none(self):
        facts = journal.extract_facts(_trade_dict(), None)
        assert facts["entry_context"] is None
        assert facts["exit_context"] is None


# ---------------------------------------------------------------------------
# 파이프라인 — facts 저장 / LLM 모킹 / 실패 경로
# ---------------------------------------------------------------------------

def _record_closed_trade(conn, **over):
    tracking.record_trade(conn, _trade_row(**over))


class TestProcessPending:
    def test_facts_phase_and_idempotency(self):
        conn = _conn()
        _record_closed_trade(conn)
        res = journal.process_pending(conn, {"30m": _bars_30m()}, do_llm=False)
        assert res["facts_created"] == 1
        # 재실행해도 중복 생성 없음 (UNIQUE + LEFT JOIN)
        res2 = journal.process_pending(conn, {"30m": _bars_30m()}, do_llm=False)
        assert res2["facts_created"] == 0
        row = conn.execute("SELECT status, facts FROM btc_journal").fetchone()
        assert row[0] == "facts_only"
        assert json.loads(row[1])["identity"]["trade_id"] == 1

    def test_llm_phase_success(self, monkeypatch):
        conn = _conn()
        _record_closed_trade(conn)

        def fake_analyze(facts, lessons):
            assert facts["identity"]["trade_id"] == 1  # facts 가 그대로 전달되는지
            return ({
                "situation_analysis": "ok", "judgment_evaluation": "ok",
                "execution_quality": "ok",
                "one_line_summary": "설계 의도대로 작동",
                "confidence_score": 0.8,
                "pattern_tags": ["trail_exit"],
                "lessons": [
                    {"category": "exit", "text": "검증가능 교훈", "testable": True,
                     "suggested_backtest": {"param": "TS_MIN", "value": 2.5}},
                    {"category": "execution", "text": "관찰만"},
                ],
            }, "mock", 5)

        monkeypatch.setattr(postmortem, "analyze", fake_analyze)
        res = journal.process_pending(conn, {"30m": _bars_30m()})
        assert res == {"facts_created": 1, "analyzed": 1, "failed": 0, "lessons": 2}
        st, conf, prov = conn.execute(
            "SELECT status, confidence, llm_provider FROM btc_journal").fetchone()
        assert (st, conf, prov) == ("analyzed", 0.8, "mock")
        # 교훈 수명주기: testable+spec → hypothesis, 아니면 observation
        rows = dict(conn.execute(
            "SELECT lesson, status FROM btc_lessons").fetchall())
        assert rows["검증가능 교훈"] == "hypothesis"
        assert rows["관찰만"] == "observation"

    def test_llm_failure_marks_failed_and_caps_attempts(self, monkeypatch):
        conn = _conn()
        _record_closed_trade(conn)

        def boom(facts, lessons):
            raise postmortem.PostmortemFailed("bad json")

        monkeypatch.setattr(postmortem, "analyze", boom)
        for _ in range(journal.MAX_LLM_ATTEMPTS + 2):
            journal.process_pending(conn, {"30m": _bars_30m()})
        st, attempts = conn.execute(
            "SELECT status, attempts FROM btc_journal").fetchone()
        assert st == "failed"
        assert attempts == journal.MAX_LLM_ATTEMPTS  # 상한에서 멈춤

    def test_unavailable_keeps_pending_no_attempt_burn(self, monkeypatch):
        conn = _conn()
        _record_closed_trade(conn)

        def unavailable(facts, lessons):
            raise postmortem.PostmortemUnavailable("no provider")

        monkeypatch.setattr(postmortem, "analyze", unavailable)
        journal.process_pending(conn, {"30m": _bars_30m()})
        st, attempts = conn.execute(
            "SELECT status, attempts FROM btc_journal").fetchone()
        assert (st, attempts) == ("facts_only", 0)  # 보류 유지 — 다음 틱 재시도


# ---------------------------------------------------------------------------
# 관측 데이터 — 신호 평가 로그 (기각 포함 전수 기록)
# ---------------------------------------------------------------------------

class TestSignalLog:
    def test_log_signal_roundtrip(self):
        conn = tracking.get_connection(":memory:")
        tracking.ensure_schema(conn)
        tracking.log_signal(conn, "2026-01-01 00:00:00+00:00",
                            score=72.5, ts_4h=2.1, ts_1d=1.0,
                            side="none", reason="추세강도 미달(횡보 게이트)", n_open=1)
        row = conn.execute(
            "SELECT ts, score, ts_4h, ts_1d, side, reason, n_open "
            "FROM btc_signal_log").fetchone()
        assert tuple(row) == ("2026-01-01 00:00:00+00:00", 72.5, 2.1, 1.0,
                              "none", "추세강도 미달(횡보 게이트)", 1)


# ---------------------------------------------------------------------------
# postmortem 계약 파서 (LLM 호출 없음)
# ---------------------------------------------------------------------------

class TestPostmortemContract:
    def test_extract_plain_json(self):
        obj = postmortem._extract_json('{"a": 1}')
        assert obj == {"a": 1}

    def test_extract_fenced_with_chatter(self):
        text = '부검 결과입니다.\n```json\n{"a": {"b": 2}}\n```\n끝.'
        assert postmortem._extract_json(text) == {"a": {"b": 2}}

    def test_extract_unfenced_with_prefix(self):
        text = '결과: {"one_line_summary": "ok", "x": [1, 2]} 입니다'
        assert postmortem._extract_json(text)["x"] == [1, 2]

    def test_no_json_raises(self):
        with pytest.raises(postmortem.PostmortemFailed):
            postmortem._extract_json("JSON 없음")

    def test_validate_missing_keys_raises(self):
        with pytest.raises(postmortem.PostmortemFailed):
            postmortem._validate_analysis({"one_line_summary": "x"})

    def test_validate_clamps_confidence_and_types(self):
        obj = postmortem._validate_analysis({
            "situation_analysis": "s", "judgment_evaluation": "j",
            "one_line_summary": "o", "confidence_score": 7,
            "lessons": "not-a-list", "pattern_tags": None,
        })
        assert obj["confidence_score"] == 1.0
        assert obj["lessons"] == [] and obj["pattern_tags"] == []

    def test_prompt_contains_contract(self):
        facts = journal.extract_facts(_trade_dict(), None)
        prompt = postmortem._build_prompt(facts, [{"status": "hypothesis",
                                                   "category": "exit",
                                                   "lesson": "기존교훈"}])
        assert "숫자" in prompt and "동결" in prompt and "기존교훈" in prompt
        assert '"trade_id": 1' in prompt  # facts 임베드 확인

    def test_strategy_brief_gate_matches_config(self):
        """브리프의 게이트 서술이 engine/config 와 어긋나면 LLM 이 가짜
        이상징후를 보고한다 (E2E 실증) — 동적 생성으로 드리프트 차단."""
        from engine.config import ENTRY_SCORE_MIN, TS_MIN, TS_GATE_TFS
        brief = postmortem._strategy_brief()
        assert f"score>={ENTRY_SCORE_MIN:.0f}" in brief
        assert f"추세강도>={TS_MIN:.1f}" in brief
        assert f"적용 TF: {'/'.join(TS_GATE_TFS)} 만" in brief
        # 현 동결 스펙: 4h 단독 게이트 — 1d 가 게이트로 서술되면 안 됨
        assert "4h" in "/".join(TS_GATE_TFS)
