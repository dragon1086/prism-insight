"""Offline prompt contracts for the executive summary and investment strategy.

They pin the reader-facing structure only; they do not prove model compliance.
"""
import re
from unittest.mock import Mock

import pytest

import cores.report_generation as generation
from cores.llm.ports import LLMResult

REPORTS = {
    "price_volume_analysis": "price section",
    "peer_comparison": "### 경쟁사 비교 분석\n| 기업 | PER |",
    "dart_deep_analysis": "### 5-1. 실적·현금흐름·차입과 회계 판단\n**핵심 포인트**",
    "investment_strategy": "### 5-1. 투자 전략 및 의견\n#### 핵심 투자 논리: 겉과 속",
}


async def _capture(monkeypatch, call):
    seen = []

    class Backend:
        async def run(self, spec, user_input):
            seen.append((spec.instructions, user_input))
            return LLMResult(text="## 핵심 요약\n\n**한 줄 결론** 조건부 판단입니다.")

    monkeypatch.setattr(generation, "_report_backend", Backend())
    await call()
    assert len(seen) == 1
    return seen[0]


SUMMARY_LEADS = {
    "ko": ("**한 줄 결론**", "**지금 무슨 일이 일어나고 있나**", "**숫자 뒤에 숨은 이야기**",
           "**경쟁사와 비교하면**", "**공시와 주가·수급의 연결**", "**앞으로 확인할 것**"),
    "en": ("**Bottom line**", "**What is happening now**", "**The story behind the numbers**",
           "**Versus peers**", "**Filings vs. price and flows**", "**What to watch next**"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_summary_prompt_structure_and_guards(monkeypatch, language):
    instruction, message = await _capture(monkeypatch, lambda: generation.generate_summary(
        REPORTS, "Example", "123456", "20260926", Mock(), language))
    prompt = instruction + message
    title = "## 핵심 요약" if language == "ko" else "## Executive Summary"
    assert f'"{title}"' in prompt
    positions = [message.index(lead) for lead in SUMMARY_LEADS[language]]
    assert positions == sorted(positions)
    # Lead-ins are bold paragraph leads, never new headings the renderers would split on.
    assert not re.search(r"(?m)^#{1,4}\s*\*\*", message)
    if language == "ko":
        for required in ("900~1,400자", "겉으로 보면 …, 하지만 공시를 들여다보면 …", "전환사채(나중에 주식으로 바꿀 수 있는 채권)",
                         "억원·조원", "통째로 생략", "외국인·기관·개인", "합쇼체",
                         "새로운 가격대나 매매 규칙을 만들지 않습니다", "INVESTMENT_STRATEGY",
                         "내부 라벨, 상태 코드, 영어 필드명", "새로운 ##·### 제목은 만들지 마세요",
                         "DART 주요 재무·사업 위험 분석", "경쟁사 비교 분석", "투자를 권유하지 않습니다"):
            assert required in prompt, required
        assert "500-800자" not in prompt and "3-5개의 핵심 포인트" not in prompt
    else:
        for required in ("On the surface …, but the filings show …", "convertible bond (a bond that can later",
                         "omit this item entirely", "do not introduce new price levels or trading rules",
                         "INVESTMENT_STRATEGY", "internal labels, status codes or English field names",
                         "Do not create any other ## or ### headings", "official results/guidance",
                         "do not solicit investment"):
            assert required in prompt, required
        assert "500-800 characters" not in prompt
    # Existing shared contracts stay appended.
    assert "종합 입력 계약" in instruction or "Synthesis evidence contract" in instruction
    assert "### 경쟁사 비교 분석" in message and "**핵심 포인트**" in message  # all chapters are supplied


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_strategy_prompt_adds_thesis_and_depth_inputs_without_heading_drift(monkeypatch, language):
    instruction, message = await _capture(monkeypatch, lambda: generation.generate_investment_strategy(
        REPORTS, "combined", "Example", "123456", "20260926", Mock(), language))
    if language == "ko":
        assert '"### 5-1. 투자 전략 및 의견"' in instruction
        assert '"#### 소제목명"' in instruction
        assert '"#### 핵심 투자 논리: 겉과 속"' in instruction
        assert ("이어서 종합 투자 관점, 투자자 유형별 전략, 주요 매매 포인트, 핵심 모니터링 요소, "
                "리스크 요소, 결론 순서로") in instruction
        for required in ("6. DART 공시 심층분석", "이익의 질·일회성 요인", "차입 만기·이자 부담", "희석·자본변동",
                         "담보·보증·우발위험", "특수관계자 거래", "7. 경쟁사 비교 분석 표", "8. 공식 실적·가이던스",
                         "입력에 있을 때만 사용", "강세 논리와 약세 논리", "이미 반영한 것",
                         "괄호 안에 쉬운 말로", "본 보고서는 투자 참고용이며", "\"투자 권유\"가 아닌"):
            assert required in instruction, required
        assert "3800자 이내" in message and "3000자" not in message
    else:
        assert '"### 5-1. Investment Strategy and Opinion"' in instruction
        assert '"#### Sub-section Title"' in instruction
        assert '"#### Core Investment Thesis: Surface vs. Substance"' in instruction
        for required in ("6. DART Filing-Depth Analysis", "7. Competitor Comparison Table",
                         "8. Official Results and Guidance", "only when they are present in the input",
                         "bull case and the bear case", "already reflects", "in plain words in parentheses",
                         "for investment reference only", "not \"investment solicitation\""):
            assert required in instruction, required
        assert "under 3800 characters" in message and "3000 characters" not in message
    assert "combined" in message
