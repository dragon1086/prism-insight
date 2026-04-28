"""Regression tests for gh #263 — duplicate investing warning on /us_theme etc.

The Firecrawl handlers in `telegram_ai_bot.py` always append a canonical
`_DISCLAIMER_KR` / `_DISCLAIMER_US` line. The LLM frequently appends its own
investment-warning line on top, producing two near-identical disclaimers.
`cores.disclaimer_utils.strip_trailing_disclaimer` removes the LLM-emitted
block before the canonical one is appended.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cores.disclaimer_utils import strip_trailing_disclaimer as _strip


# --- Cases that MUST be stripped (the gh #263 reproductions) ----------------

def test_strips_kr_recommendation_phrasing():
    """The exact phrasing reported in gh #263."""
    body = "🟢 AI 데이터센터 테마는 과열 구간입니다.\n대장주: NVDA, AVGO, MRVL"
    text = body + "\n\n⚠️ 본 내용은 투자 권유가 아닌 정보 제공 목적이며, 최종 투자 판단은 본인 책임입니다."
    assert _strip(text) == body


def test_strips_kr_reference_phrasing():
    """The other duplicate variant — same wording as our canonical disclaimer."""
    body = "🟢 바이오 테마 진단 결과..."
    text = body + "\n\n⚠️ 본 내용은 투자 참고용이며, 투자 판단의 책임은 본인에게 있습니다."
    assert _strip(text) == body


def test_strips_kr_responsibility_only():
    body = "테마 온도: 🔴 냉각"
    text = body + "\n⚠️ 투자 결정은 본인 책임입니다."
    assert _strip(text) == body


def test_strips_en_phrasing():
    body = "Theme temperature: 🟢 Hot"
    text = body + "\n\n⚠️ This is for informational purposes only. Investment decisions are your own responsibility."
    assert _strip(text) == body


def test_strips_not_financial_advice():
    body = "Bullet 1\nBullet 2"
    text = body + "\n\n⚠️ This is not financial advice."
    assert _strip(text) == body


def test_strips_multiple_trailing_warning_lines():
    body = "본문 내용입니다."
    text = body + (
        "\n\n⚠️ 본 내용은 투자 권유가 아닙니다."
        "\n⚠️ 투자 판단의 책임은 본인에게 있습니다."
    )
    assert _strip(text) == body


def test_handles_trailing_whitespace():
    body = "본문"
    text = body + "\n\n⚠️ 본 내용은 투자 참고용이며, 투자 판단의 책임은 본인에게 있습니다.\n   \n"
    assert _strip(text) == body


# --- Cases that MUST be preserved ------------------------------------------

def test_preserves_warning_in_middle_of_body():
    """A ⚠️ inside the body (e.g. inline risk callout) must not be stripped."""
    text = (
        "1. 테마 온도: 🟡 적정\n"
        "⚠️ 단, 단기 과열 우려가 있습니다.\n"
        "2. 대장주: NVDA"
    )
    assert _strip(text) == text


def test_preserves_text_without_warning():
    text = "🟢 AI 데이터센터 테마 진단 완료.\n대장주: NVDA, AVGO"
    assert _strip(text) == text


def test_preserves_unrelated_warning_at_end():
    """A trailing ⚠️ that is not investment-related must be preserved."""
    text = "분석 완료.\n\n⚠️ 데이터 출처: 일부 종목은 거래량이 낮을 수 있습니다."
    assert _strip(text) == text


def test_preserves_empty_string():
    assert _strip("") == ""


def test_preserves_none_safely():
    assert _strip(None) is None
