"""BUY prompts must agree with the code they describe (2026-10-19 prompt/code alignment).

Audit: tasks/research-20261019/prompt-audit (kr_buy.md, us_buy_sell.md). Each check pins one stale
4/29-era sentence that contradicted the code, in all four BUY variants (KR/US x ko/en), the micro-split
BUY frame (ko/en) and the BUY scenario sell_triggers template. SELL prompts are out of scope here.
"""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def buy_prompts():
    from cores.agents import trading_agents as kr
    us = _load("prism-us/cores/agents/trading_agents.py", "us_trading_agents_alignment_20261019")
    out = {}
    for market, make in (("KR", kr.create_trading_scenario_agent), ("US", us.create_us_trading_scenario_agent)):
        for lang in ("ko", "en"):
            out[f"{market}-{lang}"] = make(language=lang).instruction
    return out


@pytest.fixture
def frames(monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.delenv("MICRO_SPLIT_LIVE_MARKETS", raising=False)
    monkeypatch.delenv("MICRO_SPLIT_MIN_SCORE", raising=False)
    from prism_core import micro_split_live
    return {"ko": micro_split_live.buy_prompt_block("KR", "ko"), "en": micro_split_live.buy_prompt_block("US", "en")}


STALE = (
    # A1: the re-entry system watches held-off / blocked / stopped names, so "no next chance" is false.
    "다음 기회", "next time", "next opportunity", "되돌릴 수 없습니다", "cannot be recovered", "will not fire again",
    "관심목록에 넣고 추적하는 기능이 없습니다", "NO watchlist tracking",
    # A2: sizing is set by the system (micro-split, rebound pilot half slot, per-account slot amount).
    "올인/올아웃", "All-in", "all-in", "분할매매는 불가능", "No partial fills", "포트폴리오의 10%", "10% of portfolio",
    # A3: reaching the target never sells in any regime (cores/oneil_fallback.py TIER 3 -> HOLD).
    "횡보·약세 국면 매도", "exit in sideways/bear", "수익에서 익절", "take profits at 20-25%",
    # A4: the computed regime never outputs parabolic; the LLM has no index 90/30-day returns.
    "90일 수익률", "30일 수익률", "90-day return", "30-day return", "parabolic 포지션 운영",
    "Parabolic position management",
    # A9: BUY never receives index_summary; the value is the Market Pulse facts line.
    "index_summary",
    # A10: exact exit-code bands and the SELL Tier 3 rule (all three), not ranges or "2 of 3".
    "-3~5%", "2개 이상 동시 충족", "2 or more of these hold",
)


def test_stale_phrases_are_gone_from_every_buy_prompt(buy_prompts, frames):
    texts = dict(buy_prompts)
    texts.update({f"frame-{k}": v for k, v in frames.items()})
    for key, text in texts.items():
        assert text, key
        for phrase in STALE:
            assert phrase not in text, (key, phrase)


def test_reentry_watch_is_stated_without_a_decision_lever(buy_prompts):
    # observability/reentry_shadow.py: STOP_EXIT, LOCATION_SKIP (fundamentals passed, min_score - buy_score <= 2),
    # ENTER_BLOCKED; prism_core/reentry_v3_live.py buys on REBREAK / RETEST signals. No watch duration stated.
    for key, text in buy_prompts.items():
        flat = " ".join(text.split())
        if key.endswith("ko"):
            assert "별도의 규칙 기반 재진입 배치" in flat and "재돌파·눌림 지지" in flat, key
            assert '"이번이 유일한 기회"라며 억지로 진입하지도' in flat and '"재진입이 잡아줄 것"이라며 미진입하지도' in flat, key
            assert "add_plan) 부록이 붙어 있으면 그 조건부 증액 시나리오는 막지 않습니다" in flat, key
        else:
            assert "separate rule-based re-entry batch" in flat and "re-breakout or pullback-support" in flat, key
            assert '"this is the only chance"' in flat and '"re-entry will catch it"' in flat, key
            assert "its conditional add scenarios are not forbidden" in flat, key
        assert "70일" not in flat and "70 days" not in flat, key  # no unverified watch duration


def test_frame_keeps_the_honest_cost_asymmetry(frames):
    assert "틀린 진입은 작게 잘리지만, 놓친 진입은 재진입 규칙이 신호를 낼 때에만 다시 잡히고 그때는 대개 더 높은 가격" in frames["ko"]
    assert "re-caught only when the re-entry rule fires, usually at a higher price" in frames["en"]


def test_sizing_constraint_matches_the_sizing_code(buy_prompts):
    # cores/regime_policy.py configured_entry_amount: buy_amount_krw / buy_amount_usd per account;
    # prism_core/entry_score_policy.py position_fraction 0.5 for the rebound pilot.
    for key, text in buy_prompts.items():
        if key.endswith("ko"):
            assert "1슬롯은 계좌별로 정해 둔 매수 금액" in text and "반등 시험매수는 0.5슬롯" in text, key
            assert "매도는 전량 매도입니다" in text, key
        else:
            assert "One slot is the account's fixed buy amount" in text and "half a slot" in text, key
            assert "sells are full exits" in text, key


def test_rebound_pilot_and_bull_step_down_are_documented_as_coded(buy_prompts):
    from prism_core.entry_score_policy import evaluate_entry_score_policy as policy
    # Rebound pilot: sideways + UPTREND, effective score 6 -> half slot (no distribution caution).
    pilot = policy(6, 5, "sideways", "UPTREND", "enter", pilot_budget_available=True, rule_min_score=5)
    assert pilot["rebound_pilot"] and pilot["position_fraction"] == 0.5 and pilot["required_score"] == 6
    # Bull stepped down to sideways by distribution days: the sideways row (5), not the strict floor 8.
    stepped = policy(5, 5, "moderate_bull", None, "enter", effective_regime="sideways",
                     distribution_caution=True, rule_min_score=5)
    assert stepped["required_score"] == 5
    assert policy(6, 5, "sideways", "UPTREND", "enter", distribution_caution=True,
                  pilot_budget_available=True, rule_min_score=5)["rebound_pilot"] is False
    for key, text in buy_prompts.items():
        if key.endswith("ko"):
            assert "effective_score가 정확히 6점인 진입은 7점 하한 대신 0.5슬롯 시험매수" in text, key
            assert "moderate_bull → sideways이면 min_score 5" in text, key
        else:
            flat = " ".join(text.split())
            assert "effective_score exactly 6 goes in as a half-slot pilot" in flat, key
            assert "moderate_bull → sideways uses min_score 5" in flat, key


def test_distribution_days_point_to_the_line_the_gate_reads(buy_prompts):
    from cores.buy_gate import _distribution_from_text
    line = "- Market Pulse: UPTREND | 분산일(distribution days, 최근 25세션): 6 (오닐 M 상태)"
    assert _distribution_from_text(line) == 6
    for key, text in buy_prompts.items():
        assert "Market Pulse … 분산일(distribution days, 최근 N세션)" in text, key


def test_opening_hour_rule_states_the_current_price_exceptions(buy_prompts):
    # 2026-11-11 prompt diet (audit D8): the opening-hour bullet points to the Step 1.5 and volume
    # lower-bound rules instead of restating them; both rules must still be stated once above.
    for key, text in buy_prompts.items():
        flat = " ".join(text.split())
        if key.endswith("ko"):
            assert "예외: 1.5단계 T1·T2 판정과 거래량 하한 판정(거래량 해석 기준)은 위 규칙대로 현재가·장중 누적 거래량을 씁니다" in flat, key
            assert flat.count("T1·T2는 시간대와 관계없이") == 1 and flat.count("예외(하한 판정)") == 1, key
        else:
            assert "Exception: the Step 1.5 T1/T2 check and the volume lower-bound rule (Volume Interpretation) use the current price and cumulative intraday volume as set above" in flat, key
            assert flat.count("Judge T1/T2 at any time of day") == 1 and flat.count("Exception (lower bound)") == 1, key


def test_ma_citation_guard_exists_in_every_language(buy_prompts):
    for key, text in buy_prompts.items():
        if key.endswith("ko"):
            assert "이동평균은 개별 추세 팩트 블록에 있는 것만 인용하십시오" in text, key
            assert "시장 지수의 200일선은 지수의 것이지 종목의 것이 아닙니다" in text, key
        else:
            assert "Only cite a moving average that appears in the facts block" in text, key


def test_journal_section_exists_wherever_journal_reflection_is_required(buy_prompts):
    for key, text in buy_prompts.items():
        assert '"journal_reflection"' in text, key
        assert ("매매일지·직관 활용" in text) or ("Using the Trading Journal & Intuitions" in text), key


def test_us_research_guidance_matches_kr(buy_prompts):
    flat = {k: " ".join(v.split()) for k, v in buy_prompts.items()}
    assert "통합 질의 최대 1회" in flat["US-ko"] and "NOT_REQUESTED로 남기십시오" in flat["US-ko"]
    assert "전체 보고서, 주입된 팩트, 이미 반환된 도구 결과" in flat["US-ko"]
    assert "at most ONE consolidated query" in flat["US-en"] and "record NOT_REQUESTED instead of researching" in flat["US-en"]
    assert "inspect the whole report" in flat["US-en"]


def _us_trigger_names():
    tree = ast.parse((ROOT / "prism-us/us_trigger_batch.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "TRIGGER_CRITERIA" for t in n.targets))
    return [k.value for k in node.value.keys if k.value != "default"]


def test_us_trigger_names_are_the_real_us_triggers(buy_prompts):
    names = _us_trigger_names()
    credited = ["Volume Surge Top", "Gap Up Momentum Top", "Intraday Rise Top", "Closing Strength Top",
                "Value-to-Cap Ratio Top", "Volume Surge Sideways"]
    # 2026-10-22: "Steady Leader Pullback" (T-B, default off) is a screening-only addition. The BUY
    # prompts are unchanged on purpose: it gets no trigger-type momentum credit and no analysis
    # points (a known gap, recorded in the T-B PR; fixing it is a separate BUY-layer decision).
    no_prompt_guidance = {"Steady Leader Pullback"}
    assert set(credited) | {"Macro Sector Leader", "Contrarian Value Pick"} | no_prompt_guidance == set(names)
    for key in ("US-ko", "US-en"):
        flat = " ".join(buy_prompts[key].split())
        assert " / ".join(credited) in flat, key
        assert all(name not in flat for name in no_prompt_guidance), key
        assert '"Contrarian Value Pick"' in flat and '"Macro Sector Leader"' in flat, key
        for stale in ('"Volume Surge / Gap Up / Daily Rise', "Capital Inflow / Volume Surge Flat"):
            assert stale not in flat, key


def _bands():
    kr = _load("cores/oneil_fallback.py", "kr_oneil_alignment_20261019")
    us = _load("prism-us/cores/oneil_fallback.py", "us_oneil_alignment_20261019")
    for name in ("TRAIL_ACTIVATION_PCT", "TRAIL_DROP_BULL", "TRAIL_DROP_WEAK", "TRAIL_DROP_UNKNOWN"):
        assert getattr(kr, name) == getattr(us, name), name
    return kr


def test_sell_trigger_example_and_template_use_exit_code_values(buy_prompts):
    # 2026-11-11 prompt diet (audit X3 / M5): apply_buy_scenario_contract always overwrites sell_triggers
    # with the template, so the BUY JSON example no longer spells out bands (the LLM's text was discarded);
    # the template carries the exit-code bands plus the non-bull entry band (trail_params +3%, x0.94/x0.96).
    kr = _bands()
    from prism_core.trading_scenario_contract import _SELL_TRIGGERS_EN, _SELL_TRIGGERS_KO
    for key, text in buy_prompts.items():
        assert ('"sell_triggers": ["시스템이 매도 정책 템플릿으로 대체합니다' in text
                or '"sell_triggers": ["replaced by the system SELL policy template' in text), key
        assert "-3~5%" not in text and "고점 × 0.92" not in text and "peak × 0.92" not in text, key
    for key, text in (("template-ko", "\n".join(_SELL_TRIGGERS_KO)), ("template-en", "\n".join(_SELL_TRIGGERS_EN))):
        for drop in (kr.TRAIL_DROP_BULL, kr.TRAIL_DROP_WEAK, kr.TRAIL_DROP_UNKNOWN):
            assert f"× {drop:.2f}" in text, (key, drop)
        assert f"+{kr.TRAIL_ACTIVATION_PCT:g}%" in text, key
        assert "+3%" in text and "× 0.94" in text and "× 0.96" in text, key
        assert "-3~5%" not in text, key
        assert ("3거래일 연속 종가 하락" in text) or ("3 consecutive lower" in text), key
        assert ("20일선 아래" in text) or ("below the 20d MA" in text), key


def test_target_reach_never_sells_in_the_exit_code():
    kr = _bands()
    sell, reason = kr.evaluate_oneil_sell(kr.SellInputs(
        buy_price=100.0, current_price=121.0, target_price=120.0, highest_price=121.0,
        market_condition="sideways", regime_is_live=True))
    assert sell is False and "target hit" in reason


def test_us_buy_appendices_follow_the_agent_language():
    src = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text(encoding="utf-8")
    assert 'add_plan_buy_block(self, market="US", ticker=ticker, language="en")' not in src
    assert 'official_event_block_async("US", ticker, language="en", purpose="buy")' not in src
    assert 'language=getattr(self, "language", "ko"), purpose="buy")' in src


def test_kr_journal_adjustment_is_marked_reference_only():
    src = (ROOT / "stock_tracking_agent.py").read_text(encoding="utf-8")
    assert "buy_score·effective_score에 더하지 마십시오" in src
    assert "do not add it to buy_score or effective_score" in src
