import ast
import importlib.util
from pathlib import Path

import pytest

from prism_core.market_report_context import public_market_analysis

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
                 "language": "ko", "base_sections": ["market_index_analysis"]}
    exec(compile(ast.Module(body=block[start:end], type_ignores=[]), "assembly", "exec"), namespace)  # noqa: S102 - trusted checked-in AST only
    combined = namespace["combined_reports"]
    assert "25bp" not in combined and "[10]" not in combined
    assert "제외했습니다" in combined and "공통 시장 근거" in combined


def test_provider_price_only_prose_needs_no_fabricated_url():
    prose = "S&P 500 6,200포인트, VIX 18포인트. 제공된 yfinance 시세 기준입니다."
    assert public_market_analysis(prose, {"market_intelligence": {}}) == prose


def test_off_preserves_legacy_market_body():
    assert public_market_analysis("Claim [1]", None) == "Claim [1]"


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prefetched_index_agent_uses_only_provided_prices(language):
    spec = importlib.util.spec_from_file_location("test_us_market_prompt", ROOT / "prism-us/cores/agents/market_index_agents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = module.create_us_market_index_analysis_agent("20260918", "20250918", 1, language,
                                                        prefetched_indices="PROVIDED_PRICE_CANARY")
    assert agent.server_names == []
    assert "PROVIDED_PRICE_CANARY" in agent.instruction
    assert "perplexity_ask" not in agent.instruction
    assert "모든 가격 참조에 USD" not in agent.instruction
    assert "Use USD for all price references" not in agent.instruction
    assert "미확인" in agent.instruction if language == "ko" else "remain unknown" in agent.instruction
