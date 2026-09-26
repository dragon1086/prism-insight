import ast
import importlib.util
from pathlib import Path

import pytest

from prism_core.market_report_context import public_market_analysis
from prism_core.us_report_public_inputs import render_public_source_receipt

ROOT = Path(__file__).resolve().parents[1]


def test_actual_downstream_assembly_excludes_dangling_claim_before_strategy():
    source = ast.parse((ROOT / "prism-us/cores/us_analysis.py").read_text())
    function = next(node for node in ast.walk(source) if isinstance(node, ast.AsyncFunctionDef)
                    and any(isinstance(child, ast.Name) and child.id == "combined_reports" for child in ast.walk(node)))
    block = next(node.body for node in ast.walk(function) if isinstance(getattr(node, "body", None), list)
                 and any(isinstance(child, ast.ImportFrom) and child.module == "prism_core.market_report_context"
                         for child in node.body))
    start = next(i for i, node in enumerate(block) if isinstance(node, ast.ImportFrom)
                 and node.module == "prism_core.market_report_context")
    end = next(i for i in range(start, len(block)) if isinstance(block[i], ast.Try))
    # Execute the production block that builds downstream strategy/summary inputs.
    namespace = {"section_reports": {"market_index_analysis": "연준 25bp 인하[1][10]"},
                 "macro_context": {"market_intelligence": {}, "market_regime": "sideways"},
                 "language": "ko", "base_sections": ["market_index_analysis"],
                 "shared_reference": "REFERENCE_SENTINEL\n", "prefetched": {},
                 "render_public_source_receipt": render_public_source_receipt}
    exec(compile(ast.Module(body=block[start:end], type_ignores=[]), "assembly", "exec"), namespace)  # noqa: S102 - trusted checked-in AST only
    combined = namespace["combined_reports"]
    assert "25bp" not in combined and "[10]" not in combined
    assert "제외했습니다" in combined and "공통 시장 근거" in combined
    assert combined.startswith("REFERENCE_SENTINEL\n")


def test_provider_price_only_prose_needs_no_fabricated_url():
    prose = "S&P 500 6,200포인트, VIX 18포인트. 제공된 yfinance 시세 기준입니다."
    assert public_market_analysis(prose, {"market_intelligence": {}}) == prose


def test_off_preserves_legacy_market_body():
    assert public_market_analysis("Claim [1]", None) == "Claim [1]"


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_explicit_standalone_guard_removes_claim_not_only_citation_markers(language):
    prose = 'Fed raised rates by 0.25 to 3.75–4.00 [5][4][7][1]'
    text = public_market_analysis(prose, None, language, require_citation_integrity=True)
    assert '0.25' not in text and '3.75' not in text and '[5]' not in text
    assert ('제외했습니다' if language == 'ko' else 'omitted') in text
    assert '공통 시장 근거' not in text and 'shared market evidence below' not in text


@pytest.mark.parametrize('prose', [
    'Provided index calculation: KOSPI 7,080.92, RSI 41.0.',
    'Public announcement [1](https://www.federalreserve.gov/newsevents/pressreleases/monetary.htm)',
    'Public announcement [1]\n\n[1]: https://www.federalreserve.gov/newsevents/pressreleases/monetary.htm',
])
def test_standalone_guard_retains_calculations_and_resolvable_public_references(prose):
    assert public_market_analysis(prose, None, require_citation_integrity=True) == prose


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_kr_prompt_requires_real_reference_mappings_not_bare_numbers(language):
    from cores.agents.market_index_agents import create_market_index_analysis_agent
    agent = create_market_index_analysis_agent('20260925', '20250925', 1, language)
    assert '[1]: <' in agent.instruction and 'HTTPS URL>' in agent.instruction
    assert ('URL을 만들지' if language == 'ko' else 'Never invent URLs') in agent.instruction


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prefetched_index_agent_uses_only_provided_prices(language):
    spec = importlib.util.spec_from_file_location("test_us_market_prompt", ROOT / "prism-us/cores/agents/market_index_agents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = module.create_us_market_index_analysis_agent("20260918", "20250918", 1, language,
                                                        prefetched_indices="PROVIDED_PRICE_CANARY", shared_macro_available=True)
    assert agent.server_names == []
    assert "PROVIDED_PRICE_CANARY" in agent.instruction
    assert "perplexity_ask" not in agent.instruction
    assert "모든 가격 참조에 USD" not in agent.instruction
    assert "Use USD for all price references" not in agent.instruction
    assert "미확인" in agent.instruction if language == "ko" else "remain unknown" in agent.instruction
    import hashlib
    expected = {"ko": "1a2ecc49379262c74e8112b15f008dee897a501d026ca722af9e3052e1566b73",
                "en": "25071fe9561e1233a236b5b20eda7c818a3e240e5b5fc8ded44a727f3dc1bb20"}
    # Preserve the exact ON prompt used by the real run4 while restoring standalone behavior.
    assert hashlib.sha256(agent.instruction.encode()).hexdigest() == expected[language]
    standalone = module.create_us_market_index_analysis_agent("20260918", "20250918", 1, language,
                                                             prefetched_indices="PROVIDED_PRICE_CANARY")
    assert standalone.server_names == ["perplexity"]


def test_real_factory_only_deduplicates_macro_when_shared_context_exists():
    spec = importlib.util.spec_from_file_location("test_shared_macro_factory", ROOT / "prism-us/cores/agents/__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for available in (False, True):
        agent = module.get_us_agent_directory("Test", "TEST", "20260917", ["market_index_analysis"],
            prefetched_data={"market_indices": {"SPX": "SOURCE_DATA"}, "shared_macro_available": available})["market_index_analysis"]
        assert agent.server_names == ([] if available else ["perplexity"])
    source = (ROOT / "prism-us/cores/us_analysis.py").read_text()
    assert 'prefetched["shared_macro_available"] = bool(macro_context)' in source


def test_one_dangling_citation_drops_only_its_paragraph_not_the_section():
    linked = ('### 4. 시장 분석\n\n' + 'KOSPI는 7,080.92로 0.90% 상승했고 반도체 강세가 이어졌습니다. ' * 4
              + '[1](https://www.newspim.com/news/view/20260923000946)')
    table = '| 지수 | 종가 |\n|---|---|\n| KOSPI | 7,080.92 |'
    prose = linked + '\n\n연준이 25bp 인하했습니다[2][3].\n\n' + table
    text = public_market_analysis(prose, None, require_citation_integrity=True)
    assert text.startswith(linked) and table in text
    assert '25bp' not in text and '[2]' not in text
    assert text.endswith('출처 연결이 확인되지 않은 일부 시장 서술은 제외했습니다.')


@pytest.mark.parametrize('prose', [
    'Claim [1, 2]\n\n[1]: https://www.bok.or.kr/a\n[2]: https://www.newspim.com/b',
    'Claim [1-2]\n\n[1]: <https://www.bok.or.kr/a>\n[2]: <https://www.newspim.com/b>',
    'Claim [1](<https://www.bok.or.kr/a>)',
])
def test_grouped_ranged_and_angle_bracket_references_resolve(prose):
    assert public_market_analysis(prose, None, require_citation_integrity=True) == prose


def test_grouped_citation_with_one_missing_number_is_unresolved():
    prose = 'Claim [1, 3]\n\n[1]: https://www.bok.or.kr/a'
    assert '제외했습니다' in public_market_analysis(prose, None, require_citation_integrity=True)
