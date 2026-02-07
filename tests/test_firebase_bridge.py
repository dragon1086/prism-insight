"""Tests for Firebase Bridge - message parsing functions only (no Firebase needed)."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from firebase_bridge import (
    detect_market,
    detect_type,
    extract_title,
    extract_preview,
    extract_stock_info,
)


def test_detect_market_kr():
    """Korean market detection."""
    msg = "삼성전자(005930) 급등 포착\n현재가: 82,500원 (+5.2%)"
    assert detect_market(msg) == 'kr'

    msg2 = "코스피 3,200 돌파! 외국인 매수세 강화"
    assert detect_market(msg2) == 'kr'


def test_detect_market_us():
    """US market detection."""
    msg = "AAPL earnings beat expectations. $198.50 (+3.2%)"
    assert detect_market(msg) == 'us'

    msg2 = "나스닥 NVIDIA $950 신고가 경신"
    assert detect_market(msg2) == 'us'


def test_detect_type_trigger():
    """Trigger type detection (default)."""
    msg = "삼성전자 급등 포착! 82,500원 (+5.2%)"
    assert detect_type(msg) == 'trigger'


def test_detect_type_analysis():
    """Analysis type detection."""
    msg = "시장 분석 요약: 코스피 전망 긍정적"
    assert detect_type(msg) == 'analysis'

    msg2 = "Weekly Market Analysis Summary"
    assert detect_type(msg2) == 'analysis'


def test_detect_type_portfolio():
    """Portfolio type detection."""
    msg = "포트폴리오 수익률 현황: +12.5%"
    assert detect_type(msg) == 'portfolio'


def test_detect_type_pdf():
    """PDF type detection."""
    msg = "삼성전자 분석 리포트 PDF 생성 완료"
    assert detect_type(msg) == 'pdf'


def test_extract_title():
    """Title extraction."""
    msg = "삼성전자 급등 포착\n82,500원 (+5.2%)\n매수 신호 발생"
    title = extract_title(msg)
    assert title == '삼성전자 급등 포착'

    # Skip empty/separator lines
    msg2 = "\n---\n실제 제목\n내용"
    assert extract_title(msg2) == '실제 제목'


def test_extract_title_markdown():
    """Title extraction with markdown."""
    msg = "**삼성전자** 급등 포착"
    title = extract_title(msg)
    assert '삼성전자' in title
    assert '*' not in title


def test_extract_preview_short():
    """Preview extraction for short messages."""
    msg = "짧은 메시지"
    assert extract_preview(msg) == '짧은 메시지'


def test_extract_preview_long():
    """Preview extraction for long messages - truncated to 100 chars."""
    msg = "A" * 200
    preview = extract_preview(msg)
    assert len(preview) <= 100
    assert preview.endswith('...')


def test_extract_stock_info_kr():
    """Korean stock info extraction."""
    msg = "삼성전자(005930) 급등 포착"
    code, name = extract_stock_info(msg)
    assert code == '005930'
    assert name is not None and '삼성' in name


def test_extract_stock_info_none():
    """No stock info in message."""
    msg = "시장 전반 요약 리포트"
    code, name = extract_stock_info(msg)
    assert code is None


if __name__ == '__main__':
    tests = [v for k, v in globals().items() if k.startswith('test_')]
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  PASS: {test_fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test_fn.__name__} - {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test_fn.__name__} - {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests")
    exit(1 if failed else 0)
