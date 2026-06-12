# tests/test_research.py — 자동 연구공장 (자가개선 닫힌 루프) 테스트
#
# 원칙: 백테스트/LLM 실호출 없음 (factory 의 실행기는 모킹). 핵심 검증 대상:
#   1. 화이트리스트 울타리 (범위 밖 = 어떤 경로로도 적용 불가)
#   2. 패치가 실제 의사결정 함수까지 닿는가 (runtime reach)
#   3. 결정적 합격 게이트의 각 차원
#   4. 판정 → 활성 → 재검증 은퇴의 전체 흐름 + 기각 메모리
from __future__ import annotations

import json

import pytest

from live import journal, tracking
from research import factory, overrides


def _conn():
    conn = tracking.get_connection(":memory:")
    tracking.ensure_schema(conn)
    journal.ensure_journal_schema(conn)
    factory.ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 1. 화이트리스트 울타리
# ---------------------------------------------------------------------------

class TestWhitelist:
    def test_float_in_range(self):
        assert overrides.validate("TS_MIN", "2.5") == 2.5

    def test_float_out_of_range(self):
        with pytest.raises(overrides.OverrideError):
            overrides.validate("TS_MIN", 0.5)       # 동결 2.0 미만 완화 한계 밖
        with pytest.raises(overrides.OverrideError):
            overrides.validate("ENTRY_SCORE_MIN", 95)  # 라운드4 기각영역

    def test_enum(self):
        assert overrides.validate("TRAILING_TF", "1d") == "1d"
        with pytest.raises(overrides.OverrideError):
            overrides.validate("TRAILING_TF", "1w")

    def test_unknown_param(self):
        with pytest.raises(overrides.OverrideError):
            overrides.validate("RISK_PER_TRADE", 0.05)  # 의도적으로 메뉴 밖


# ---------------------------------------------------------------------------
# 2. 패치 도달성 — 오버라이드가 실제 의사결정 코드에 보이는가
# ---------------------------------------------------------------------------

class TestApplyReach:
    def test_config_gate_patched_and_restored(self):
        import engine.config as cfg
        orig = cfg.TS_MIN
        with overrides.apply({"TS_MIN": 3.0}):
            assert cfg.TS_MIN == 3.0
        assert cfg.TS_MIN == orig

    def test_chop_filter_sees_patched_ts_min(self):
        """signal.chop_filter_passed 는 함수-로컬 임포트 — 패치가 런타임에 닿는다."""
        from engine.regime import TFState
        from engine.signal import chop_filter_passed
        # trend_strength = |102-100|/0.97 ≈ 2.06 — 동결 TS_MIN 2.0 은 통과
        st = TFState(trend="up", candle_position="above_both",
                     ma10=102.0, ma35=100.0, close=101.0, atr14=0.97)
        tf_states = {"4h": st, "1d": st}
        assert chop_filter_passed(tf_states) is True
        with overrides.apply({"TS_MIN": 3.0}):
            assert chop_filter_passed(tf_states) is False  # 같은 입력, 다른 게이트
        assert chop_filter_passed(tf_states) is True       # 복원 확인

    def test_trail_params_patch_both_namespaces(self):
        import backtest.engine as be
        import live.shadow as sh
        o_be, o_sh = be.TRAILING_TF, sh.TRAILING_TF
        with overrides.apply({"TRAILING_TF": "1d", "BE_TRAIL_ACTIVATE_R": 2.0}):
            assert be.TRAILING_TF == "1d" and sh.TRAILING_TF == "1d"
            assert be.BE_TRAIL_ACTIVATE_R == 2.0 and sh.BE_TRAIL_ACTIVATE_R == 2.0
        assert (be.TRAILING_TF, sh.TRAILING_TF) == (o_be, o_sh)

    def test_apply_rejects_invalid_without_partial_patch(self):
        import engine.config as cfg
        orig = cfg.TS_MIN
        with pytest.raises(overrides.OverrideError):
            with overrides.apply({"TS_MIN": 99.0}):
                pass
        assert cfg.TS_MIN == orig


# ---------------------------------------------------------------------------
# 3. 영속 — 활성/교체/슬롯/은퇴
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_activate_load_replace(self):
        conn = _conn()
        overrides.activate(conn, "TS_MIN", 2.5, None, {"e": 1})
        assert overrides.load_active(conn) == {"TS_MIN": 2.5}
        overrides.activate(conn, "TS_MIN", 2.2, None, {"e": 2})  # 교체
        assert overrides.load_active(conn) == {"TS_MIN": 2.2}
        n_retired = conn.execute(
            "SELECT COUNT(*) FROM btc_overrides WHERE status='retired'").fetchone()[0]
        assert n_retired == 1

    def test_slot_cap(self):
        conn = _conn()
        overrides.activate(conn, "TS_MIN", 2.5, None, {})
        overrides.activate(conn, "ENTRY_SCORE_MIN", 75.0, None, {})
        with pytest.raises(overrides.OverrideError):
            overrides.activate(conn, "TRAILING_TF", "1d", None, {})

    def test_retire(self):
        conn = _conn()
        oid = overrides.activate(conn, "TS_MIN", 2.5, None, {})
        overrides.retire(conn, oid, "test")
        assert overrides.load_active(conn) == {}


# ---------------------------------------------------------------------------
# 4. 결정적 합격 게이트
# ---------------------------------------------------------------------------

def _m(pf=2.0, mdd=10.0, ret=100.0, n=120, liq=0):
    return {"profit_factor": pf, "mdd_pct": mdd, "total_return_pct": ret,
            "trade_count": n, "liq_approach_count": liq}


class TestGate:
    def test_pass_case(self):
        ok, checks = factory.evaluate_gate(
            _m(), _m(pf=2.2), _m(n=20), _m(pf=2.0, n=20))
        assert ok, checks

    @pytest.mark.parametrize("variant_train,variant_oos,bad_key", [
        (_m(pf=2.05), _m(n=20), "train_pf_improve"),          # +2.5% < +5%
        (_m(pf=2.2, mdd=11.5), _m(n=20), "train_mdd_cap"),    # MDD +15%
        (_m(pf=2.2, ret=90.0), _m(n=20), "train_return_keep"),
        (_m(pf=2.2, n=30), _m(n=20), "train_min_trades"),
        (_m(pf=2.2), _m(n=5), "oos_min_trades"),
        (_m(pf=2.2), _m(pf=1.2, n=20), "oos_pf_floor"),
        (_m(pf=2.2, liq=1), _m(n=20), "train_no_liq"),
    ])
    def test_each_fail_dimension(self, variant_train, variant_oos, bad_key):
        ok, checks = factory.evaluate_gate(_m(), variant_train, _m(n=20), variant_oos)
        assert not ok
        assert checks[bad_key] is False

    def test_tie_is_rejection(self):
        """동률 = 기각 (변경은 비용)."""
        ok, _ = factory.evaluate_gate(_m(), _m(), _m(n=20), _m(n=20))
        assert not ok


# ---------------------------------------------------------------------------
# 5. 공장 전체 흐름 (백테스트 모킹)
# ---------------------------------------------------------------------------

def _add_hypothesis(conn, param, value, mode="shadow"):
    journal._insert_lessons(conn, mode, None, [{
        "category": "entry", "text": f"{param}={value} 가설", "testable": True,
        "suggested_backtest": {"param": param, "value": value},
    }])
    row = conn.execute("SELECT id, status FROM btc_lessons ORDER BY id DESC").fetchone()
    assert row[1] == "hypothesis"
    return row[0]


def _mock_runs(improving: bool):
    """variant(cfg 에 후보 포함) 면 개선/악화 메트릭을 돌려주는 가짜 실행기."""
    def fake(market_db_path, cfg):
        if cfg:  # 후보 포함 variant (테스트에선 champion={} 가정)
            return (_m(pf=2.4 if improving else 1.5),
                    _m(pf=2.0 if improving else 1.0, n=20))
        return _m(), _m(n=20)
    return fake


class TestFactoryFlow:
    def test_validated_activates_override(self, monkeypatch):
        conn = _conn()
        lesson_id = _add_hypothesis(conn, "TS_MIN", 2.5)
        monkeypatch.setattr(factory, "_run_train_oos", _mock_runs(improving=True))
        res = factory.run_factory(conn)
        assert res["validated"] == 1 and res["rejected"] == 0
        assert overrides.load_active(conn) == {"TS_MIN": 2.5}
        status, evidence = conn.execute(
            "SELECT status, evidence FROM btc_lessons WHERE id=?",
            (lesson_id,)).fetchone()
        assert status == "validated"
        assert json.loads(evidence)["gate"]["train_pf_improve"] is True
        # 판정 기록 (감사 가능)
        verdict = conn.execute(
            "SELECT verdict FROM btc_research_runs").fetchone()[0]
        assert verdict == "validated"

    def test_rejected_records_memory_and_skips_dup(self, monkeypatch):
        conn = _conn()
        _add_hypothesis(conn, "TS_MIN", 3.0)
        monkeypatch.setattr(factory, "_run_train_oos", _mock_runs(improving=False))
        res = factory.run_factory(conn)
        assert res["rejected"] == 1
        assert overrides.load_active(conn) == {}
        # 동일 (param,value) 가설 재등장 → 기각 메모리로 스킵 (재실행 0회)
        _add_hypothesis(conn, "TS_MIN", 3.0)
        calls = {"n": 0}
        def counting(market_db_path, cfg):
            calls["n"] += 1
            return _m(), _m(n=20)
        monkeypatch.setattr(factory, "_run_train_oos", counting)
        res2 = factory.run_factory(conn)
        assert res2["skipped"] == 1 and calls["n"] == 0

    def test_revalidation_retires_failing_active(self, monkeypatch):
        conn = _conn()
        overrides.activate(conn, "TS_MIN", 2.5, None, {})
        # 재검증에서 variant(=현 챔피언) 가 우위를 잃음 → 자동 은퇴
        def fake(market_db_path, cfg):
            return (_m(pf=2.0), _m(n=20)) if not cfg else (_m(pf=1.9), _m(pf=1.0, n=20))
        monkeypatch.setattr(factory, "_run_train_oos", fake)
        res = factory.run_factory(conn)
        assert res["retired"] == 1
        assert overrides.load_active(conn) == {}

    def test_revalidation_keeps_winning_active(self, monkeypatch):
        conn = _conn()
        overrides.activate(conn, "TS_MIN", 2.5, None, {})
        def fake(market_db_path, cfg):
            return (_m(pf=2.0), _m(n=20)) if not cfg else (_m(pf=2.3), _m(pf=2.0, n=20))
        monkeypatch.setattr(factory, "_run_train_oos", fake)
        res = factory.run_factory(conn)
        assert res["kept"] == 1
        assert overrides.load_active(conn) == {"TS_MIN": 2.5}

    def test_freetext_hypothesis_stays_for_human(self):
        """비구조 가설은 observation — 공장이 절대 집지 않는다."""
        conn = _conn()
        journal._insert_lessons(conn, "shadow", None, [{
            "category": "exit", "text": "구조 변경 아이디어", "testable": True,
            "suggested_backtest": "트레일을 ATR 기반으로 바꿔보자",  # 자유텍스트
        }])
        status = conn.execute("SELECT status FROM btc_lessons").fetchone()[0]
        assert status == "observation"
        assert factory._structured_candidates(conn, "shadow") == []


# ---------------------------------------------------------------------------
# 6. 프롬프트 메뉴 — LLM 에게 주는 울타리 안내
# ---------------------------------------------------------------------------

class TestPromptMenu:
    def test_menu_lists_all_tunables_with_current_values(self):
        from live.postmortem import _tunables_menu
        menu = _tunables_menu()
        for name in overrides.TUNABLES:
            assert name in menu
        assert "현재" in menu and "허용" in menu

    def test_postmortem_prompt_embeds_menu(self):
        from live import postmortem
        facts = {"identity": {"trade_id": 9}}
        prompt = postmortem._build_prompt(facts, [])
        assert "손잡이 메뉴" in prompt and "TS_MIN" in prompt
