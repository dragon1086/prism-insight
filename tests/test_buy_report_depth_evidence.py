"""PRISM_BUY_REPORT_DEPTH_EVIDENCE: OFF keeps BUY prompts byte-identical; ON only adds evidence sources."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cores.agents.trading_agents as kr_module  # noqa: E402
from prism_core import buy_report_depth_evidence as depth  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "us_trading_agents_depth_test", ROOT / "prism-us" / "cores" / "agents" / "trading_agents.py")
us_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(us_module)

BUILDERS = {"KR": (kr_module, "create_trading_scenario_agent"),
            "US": (us_module, "create_us_trading_scenario_agent")}
CASES = [(market, lang) for market in ("KR", "US") for lang in ("ko", "en")]
THRESHOLDS = {
    "ko": ["30% 이상 저평가", "2.5배", "부채비율 < 200%", "ROE ≥ 5%", "매출 성장 ≥ 10%"],
    "en": ["≥ 30% vs sector median", "2.5× industry average", "Debt ratio < 200%",
           "ROE ≥ 5%", "revenue growth ≥ 10%"],
}


def _build(market, lang, monkeypatch, flag=None):
    if flag is None:
        monkeypatch.delenv(depth.ENV_FLAG, raising=False)
    else:
        monkeypatch.setenv(depth.ENV_FLAG, flag)
    module, name = BUILDERS[market]
    return getattr(module, name)(language=lang).instruction


@pytest.mark.parametrize("market,lang", CASES)
@pytest.mark.parametrize("flag", [None, "", "0", "false", "off"])
def test_flag_off_is_byte_identical_to_base_instruction(market, lang, flag, monkeypatch):
    actual = _build(market, lang, monkeypatch, flag)
    # Base = the builder without the depth hook (the pre-change code path).
    monkeypatch.setattr(depth, "apply_buy_report_depth_evidence", lambda text, **_: text)
    assert actual == _build(market, lang, monkeypatch, flag)
    assert depth.MARKER not in actual
    assert "DART 5-1" not in actual and "경쟁사 비교 분석" not in actual


@pytest.mark.parametrize("flag", ["1", "true", "TRUE"])
@pytest.mark.parametrize("market,lang", CASES)
def test_flag_on_adds_evidence_sources_only(market, lang, flag, monkeypatch):
    off = _build(market, lang, monkeypatch)
    on = _build(market, lang, monkeypatch, flag)
    assert on != off and depth.report_depth_evidence_active(on)
    assert not depth.report_depth_evidence_active(off)
    assert on.count(depth.MARKER) == 1
    # Leader row and F4 cite the competitor table in both markets.
    assert "(+ '경쟁사 비교 분석'" in on
    assert "| 2-2 (+ 경쟁사 비교 분석) |" in on
    # Step 4 and 2.5x lines both allow the >=3 positive-PER peer median, else 2-1.
    peer_clause = "3개 이상인 '경쟁사 비교 분석'" if lang == "ko" else "≥3 peers having positive"
    assert on.count(peer_clause) == 2
    # Peer median must not replace F2's industry-average branch.
    assert ("F2의 '업종 평균 이하' 판정에 대체" if lang == "ko"
            else "F2's 'industry average' branch") in on
    assert "NOT_IN_INPUT" in on.split(depth.MARKER, 1)[1][:1200]
    if market == "KR":
        assert "| 2-1 (+ DART 5-1) |" in on
        assert "2-1 (+ DART 5-1·5-3:" in on
        assert on.count("(+ DART 5-1 key points" if lang == "en" else "(+ DART 5-1 핵심 포인트") == 2
        assert "5-1 실적·현금흐름·차입과 회계 판단" in on or "5-1 earnings/cash flow" in on
        assert "6-1" in on.split(depth.MARKER, 1)[1][:1500]
    else:
        assert "DART" not in on
    # Thresholds unchanged and still present exactly as before.
    for token in THRESHOLDS[lang]:
        assert off.count(token) == on.count(token) >= 1, token


@pytest.mark.parametrize("market,lang", CASES)
def test_flag_on_removes_nothing_from_base(market, lang, monkeypatch):
    off_lines = _build(market, lang, monkeypatch).splitlines()
    on = _build(market, lang, monkeypatch, "1")
    for line in off_lines:
        stripped = line.strip().rstrip("|").rstrip(")").rstrip()
        assert stripped in on, line


def test_pinned_perplexity_strings_survive_flag_on(monkeypatch):
    assert "동종업계 주요 경쟁사 비교" in _build("KR", "ko", monkeypatch, "1")
    assert "major peer competitors valuation comparison" in _build("KR", "en", monkeypatch, "1")
    assert "major peer competitors valuation comparison" in _build("US", "ko", monkeypatch, "1")
    assert "major peer competitors valuation comparison" in _build("US", "en", monkeypatch, "1")


def test_anchor_drift_is_strict_internally():
    # CI loud failure: the strict path still raises; the flag-on tests above prove real anchors match.
    with pytest.raises(ValueError):
        depth._apply_depth_edits("no anchors here", market="KR", language="ko")
    assert depth.apply_buy_report_depth_evidence("x", market="KR", language="ko", enabled=False) == "x"


def test_anchor_drift_returns_base_instruction_and_logs_critical(caplog):
    with caplog.at_level("CRITICAL", logger=depth.__name__):
        out = depth.apply_buy_report_depth_evidence("no anchors here", market="KR", language="ko", enabled=True)
    assert out == "no anchors here" and not depth.report_depth_evidence_active(out)
    assert any(r.levelname == "CRITICAL" and "[BUY_REPORT_DEPTH] anchor drift; using base instruction"
               in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("market,lang", CASES)
def test_forced_drift_does_not_break_buy_agent_construction(market, lang, monkeypatch, caplog):
    base = _build(market, lang, monkeypatch)
    monkeypatch.setitem(depth._F4, lang, ("| anchor that no longer exists |", "| x |"))
    with caplog.at_level("CRITICAL", logger=depth.__name__):
        drifted = _build(market, lang, monkeypatch, "1")
    assert drifted == base
    assert not depth.report_depth_evidence_active(drifted)  # enabled= log reports false
    assert any("anchor drift" in r.getMessage() for r in caplog.records)
