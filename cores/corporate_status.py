"""법인 이벤트 기반 강제청산(TIER0) 판정 — KIS 종목상태코드 자동탐지 전용.

기술분석(추세·손절·트레일링) 중심 매도 로직이 못 잡는 '명백한 부실' 청산을
결정론적으로 보조한다. 가격/레짐과 무관하게 최우선으로 should_sell=True를 만들어
다음 평가 사이클에 시뮬레이터(보유DB)와 KIS 실매매 양쪽이 자동 정리되게 한다.

설계 메모:
  - 상장폐지/공개매수/감사의견거절 같은 '뉴스성 이벤트'는 KIS 상태코드로 안 잡힌다
    (예: 더존 자진상폐 = KIS코드 57 증거금100%). → 그건 매도 AI 프롬프트의 '핵심-0'에서
    perplexity 뉴스 점검으로 **자율 처리**한다. (운영자 수동 리스트 없음.)
  - 여기(KIS 상태코드)는 '관리종목(51)' 같은 결정론적 부실 신호만 백업으로 처리한다.

순수 stdlib(분류) + KIS 시세조회(prefetch). KR 전용(US는 해외 상태필드가 달라 뉴스 프롬프트로 대응).
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# KIS 주식현재가(FHKST01010100) output.iscd_stat_cls_code → 강제청산 대상.
#   ★ 자동 강제청산은 '명백한 부실/상폐위험'인 51 관리종목만.
#   52 투자위험·53 투자경고·54 투자주의 = '시장경고' 단계로 단기 이상급등(투기과열)에
#     붙는 신호 → 크게 상승 중인 종목(승자)이 걸릴 수 있어 강제청산하면 불필요한
#     수익반납(anti-O'Neil). 제외.
#   58 거래정지 = 모호(급등 1일정지 재개 시 상승 / 상폐직전 정지). 정지 중엔 어차피
#     주문 미체결 + 시뮬 선삭제로 불일치 위험 → 자동청산 제외(상폐는 매도 AI 뉴스 핵심-0가 담당).
FORCE_EXIT_STAT_CODES = {
    "51": "관리종목",
}


def classify_kis_status(iscd_stat_cls_code: Optional[str]) -> Tuple[bool, str]:
    """KIS 종목상태코드 → (강제청산?, 사유). 코드 없거나 정상이면 (False, '')."""
    raw = str(iscd_stat_cls_code or "").strip()
    if not raw:
        return False, ""
    label = FORCE_EXIT_STAT_CODES.get(raw)
    if label:
        return True, f"TIER0_EVENT:KIS_STATUS:{raw}({label})"
    return False, ""


def check_event_exit(
    ticker: str,
    kis_status_code: Optional[str] = None,
    market: str = "KR",
) -> Tuple[bool, str]:
    """법인 이벤트 강제청산 판정(TIER0, 결정론). 가격/레짐 무관 최우선.

    KIS 종목상태코드(관리종목 등)만 평가한다. 상폐/공개매수 등 뉴스성 이벤트는
    매도 AI 프롬프트(핵심-0)에서 자율 처리하므로 여기서 다루지 않는다.

    Args:
        ticker: 종목코드 (로깅/일관 시그니처용)
        kis_status_code: KIS iscd_stat_cls_code (None이면 미평가)
        market: 'KR'|'US' (확장용)

    Returns:
        (should_sell, reason_key). 해당 없으면 (False, "").
    """
    if not str(ticker or "").strip():
        return False, ""
    if kis_status_code is not None:
        return classify_kis_status(kis_status_code)
    return False, ""


async def fetch_status_codes(tickers, account_name: Optional[str] = None) -> dict:
    """보유종목들의 KIS 종목상태코드(iscd_stat_cls_code)를 일괄 조회.

    KIS 토큰 발급 레이트리밋을 피하려고 **AsyncTradingContext를 1회만** 열고
    종목별 시세조회(quotation, 계좌 무관)로 상태코드만 수집한다.
    어떤 단계가 실패해도(자격증명 없음/네트워크/레이트리밋) 절대 예외를 올리지
    않고, 가능한 만큼만 채운 dict를 반환한다(자동탐지만 비활성, 매도 본로직은 정상).

    Returns: { ticker: "iscd_stat_cls_code" }  (조회 실패 종목은 누락)
    """
    import asyncio

    out: dict = {}
    uniq = [str(t).strip() for t in (tickers or []) if str(t or "").strip()]
    if not uniq:
        return out
    try:
        from trading.domestic_stock_trading import AsyncTradingContext
        async with AsyncTradingContext(account_name=account_name) as trading:
            for t in uniq:
                try:
                    info = await asyncio.to_thread(trading.get_current_price, t)
                    if info:
                        out[t] = str(info.get("iscd_stat_cls_code", "") or "")
                except Exception as e:  # 개별 종목 실패는 건너뜀
                    logger.warning(f"{t} KIS 상태코드 조회 실패: {e}")
    except Exception as e:  # 컨텍스트/자격증명 실패 → 전체 스킵(안전)
        logger.warning(f"KIS 상태코드 prefetch 스킵: {e}")
    return out
