"""Roadmap stage 4: financial-only F2 rule reaches the KR BUY prompt; others stay byte-identical."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from test_agent_virtual_initialization import SCRIPT as INITIALIZATION_SCRIPT

from prism_core.sector_trading_criteria import (
    MODE_ENV, buy_sector_block, f2_rule, sector_f2_mode, sector_profile_stamp,
)

ROOT = Path(__file__).resolve().parents[1]
BANK = {'kind': 'financial', 'subtype': 'bank', 'industry_name': '국내 은행', 'basis': 'ksic_financial+financial_layout'}
NON_FINANCIAL = [
    None, {}, {'kind': 'general', 'subtype': None, 'basis': 'ksic_general'},
    {'kind': 'construction', 'subtype': None}, {'kind': 'holding', 'subtype': None},
    {'kind': 'order_backlog', 'subtype': 'shipbuilding'}, {'kind': 'loss_biotech', 'subtype': None},
    {'kind': 'reit', 'subtype': None}, {'kind': 'financial', 'subtype': 'unknown_subtype'},
]


def test_mode_is_live_only_when_explicit():
    assert sector_f2_mode({}) == 'off'
    assert sector_f2_mode({MODE_ENV: 'LIVE '}) == 'live'
    for value in ('', 'on', 'true', '1', 'shadow', 'off'):
        assert sector_f2_mode({MODE_ENV: value}) == 'off'


@pytest.mark.parametrize('profile', NON_FINANCIAL)
@pytest.mark.parametrize('language', ['ko', 'en'])
def test_non_financial_issuers_get_no_block_even_when_live(profile, language):
    assert buy_sector_block(profile, language, 'live') == ''
    assert f2_rule(profile, 'live') == 'default'


@pytest.mark.parametrize('subtype, marker', [
    ('bank', 'CET1) 11%'), ('financial_group', 'CET1) 11%'), ('insurance', 'K-ICS) 150%'),
    ('securities', 'NCR) 150%'), ('card_capital', '조정자기자본비율 10%'),
])
def test_financial_block_requires_disclosed_capital_ratio(subtype, marker):
    profile = {'kind': 'financial', 'subtype': subtype, 'basis': 'ksic_financial'}
    assert buy_sector_block(profile, 'ko', 'off') == '' and f2_rule(profile, 'off') == 'default'
    ko = buy_sector_block(profile, 'ko', 'live')
    assert marker in ko and 'NOT_IN_INPUT' in ko and '추정하지 마십시오' in ko
    assert '부채비율' in ko and 'F1·F3·F4' in ko
    assert '`perplexity-ask`' in ko and '12개월 이내' in ko and '최대 1회' in ko and '재시도하지 마십시오' in ko
    en = buy_sector_block(profile, 'en', 'live')
    assert en.startswith('\n\n### Sector-specific F2 rule') and 'never compute or estimate' in en
    assert '`perplexity-ask`' in en and 'within 12 months' in en and 'at-most-one' in en
    assert f2_rule(profile, 'live') == 'financial_capital_ratio'


def test_stamp_keeps_stable_identifiers_only():
    assert sector_profile_stamp(BANK) == {'kind': 'financial', 'subtype': 'bank',
                                          'basis': 'ksic_financial+financial_layout'}
    assert sector_profile_stamp(None) == {'kind': None, 'subtype': None, 'basis': 'not_provided'}
    assert sector_profile_stamp({}) == {'kind': None, 'subtype': None, 'basis': 'not_provided'}


def test_generate_reports_collects_sector_profile_per_ticker(monkeypatch, tmp_path):
    import stock_analysis_orchestrator as orchestrator_module
    from stock_analysis_orchestrator import StockAnalysisOrchestrator

    async def fake_analyze_stock(company_code, *, report_meta=None, **_kwargs):
        if company_code == '003':
            return ''  # failed report: its profile must not reach tracking
        if company_code != '002':  # 002: DART inputs unavailable, nothing recorded
            report_meta['sector_profile'] = dict(BANK) if company_code == '001' else {'kind': 'general'}
        return f'report-{company_code}'

    fake_cores_main = ModuleType('cores.main')
    fake_cores_main.analyze_stock = fake_analyze_stock
    monkeypatch.setitem(sys.modules, 'cores.main', fake_cores_main)
    monkeypatch.setattr(orchestrator_module, 'REPORTS_DIR', tmp_path)
    orchestrator = StockAnalysisOrchestrator.__new__(StockAnalysisOrchestrator)
    tickers = [{'code': c, 'name': n} for c, n in (('001', '은행'), ('002', '둘째'), ('003', '셋째'), ('004', '넷째'))]
    reports = asyncio.run(orchestrator.generate_reports(tickers, 'morning'))
    assert len(reports) == 3
    assert orchestrator.report_meta == {'001': {'sector_profile': BANK}, '004': {'sector_profile': {'kind': 'general'}}}


SCRIPT = INITIALIZATION_SCRIPT.split("async def main():", 1)[0] + r'''
from types import SimpleNamespace
import hashlib
import stock_tracking_agent as module

report = "# 보고서\n2-1 부채비율 1,453.76%, CET1 13.74% (2026년 2분기)\n"
scenario = {"decision": "No Entry", "entry_price": 100, "stop_loss": 95,
            "target_price": 120, "buy_score": 0, "rationale": "synthetic"}
BANK = {"kind": "financial", "subtype": "bank", "basis": "ksic_financial+financial_layout"}

async def main():
    agent = cls(**kwargs)
    assert await agent.initialize(language=sys.argv[3])
    agent._get_trend_facts = lambda ticker: "SYNTHETIC_TREND_NO_PROVIDER"
    agent._pipeline_market_regime = "moderate_bull"
    captured = []
    async def capture_buy(**request):
        captured.append((request["system_prompt"], request["user_prompt"]))
        return SimpleNamespace(text=json.dumps(scenario), latency_s=0, mcp_calls=[])
    module.generate_codex_fast_async = capture_buy

    async def prompt_for(mode, profile, **extra):
        os.environ.pop("PRISM_KR_SECTOR_F2_MODE", None)
        if mode is not None:
            os.environ["PRISM_KR_SECTOR_F2_MODE"] = mode
        result = await agent._extract_trading_scenario(report, ticker="316140", **extra)
        return captured[-1], result

    (system, baseline), base_result = await prompt_for(None, None)
    assert "_sector_profile" not in base_result
    others = [None, {"kind": "general", "subtype": None, "basis": "ksic_general"},
              {"kind": "construction", "subtype": None, "basis": "x"}, {"kind": "reit", "subtype": None, "basis": "x"},
              {"kind": "holding", "subtype": None, "basis": "x"}, {"kind": "loss_biotech", "subtype": None, "basis": "x"}]
    for mode in (None, "off", "live"):
        for profile in others:
            (sys_prompt, prompt), result = await prompt_for(mode, profile, sector_profile=profile)
            assert sys_prompt == system and prompt == baseline, (mode, profile)
        (sys_prompt, prompt), result = await prompt_for(mode, BANK, sector_profile=dict(BANK))
        assert sys_prompt == system
        if mode == "live":
            assert prompt != baseline and prompt.startswith(baseline)
            assert prompt[len(baseline):] == module_block(sys.argv[3])
            assert result["_sector_profile"]["f2_rule"] == "financial_capital_ratio"
        else:
            assert prompt == baseline
            assert result["_sector_profile"] == {**BANK, "f2_rule": "default"}

    # The pre-pass hands report_meta to the scenario by ticker.
    seen = []
    async def fake_extract(report_content, rank_change_msg="", **kw):
        seen.append((kw["ticker"], kw.get("sector_profile")))
        return dict(scenario)
    async def ticker_info(path):
        return Path(path).name.split("_")[0], "Example"
    async def price(ticker):
        return 100.0
    async def rank(ticker):
        return 0.0, ""
    agent._extract_ticker_info = ticker_info
    agent._get_current_stock_price = price
    agent._get_trading_value_rank_change = rank
    agent._extract_trading_scenario = fake_extract
    sys.modules["pdf_converter"] = SimpleNamespace(pdf_to_markdown_text=lambda path: report)
    agent._report_meta = {"316140": {"sector_profile": BANK}}
    await agent._analyze_report_core("316140_Example.pdf")
    await agent._analyze_report_core("005930_Example.pdf")
    assert seen == [("316140", BANK), ("005930", None)]

    assert agent.cursor.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0
    assert blocked == [] and calls == []
    agent.conn.close()
    print(json.dumps({"instruction_sha256": hashlib.sha256(system.encode()).hexdigest(), "ok": True}))

def module_block(language):
    from prism_core.sector_trading_criteria import buy_sector_block
    return buy_sector_block(BANK, language, "live")

asyncio.run(main())
'''


def _run_script(tmp_path, language):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.pop(MODE_ENV, None)
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               PRISM_KR_CODEX_FAST_TRADING="1", PRISM_BUY_CODEX_MODEL="gpt-6-astra",
               PRISM_BUY_CODEX_EFFORT="low", PRISM_BUY_CODEX_TIMEOUT="90")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), "KR", language],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("language", ["ko", "en"])
def test_buy_prompt_changes_only_for_live_financial_issuers(tmp_path, language):
    assert _run_script(tmp_path, language)["ok"] is True
