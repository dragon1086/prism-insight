"""법인 이벤트 기반 강제청산(TIER0) 판정 — 상폐/공개매수/거래정지/관리종목 등.

기술분석(추세·손절·트레일링) 중심 매도 로직이 못 잡는 '이벤트성' 청산을
매도/홀딩 판단로직에서 강제청산(TIER0)으로 유도한다. 가격/레짐과 무관하게
최우선으로 should_sell=True를 만들어, 다음 평가 사이클에 시뮬레이터(보유DB)와
KIS 실매매 양쪽이 자동 정리되게 한다.

판정 소스(둘 중 하나라도 해당하면 강제청산):
  1) override 목록(cores/event_force_exit.json): 자진상폐/공개매수/감사의견거절 등
     KIS 상태플래그로 자동 포착이 어려운 이벤트를 종목코드로 등록 → 즉시 대응.
     (운영자가 종목만 추가하면 매도는 100% 자동. 수동 매도 불필요.)
  2) KIS 종목상태코드(iscd_stat_cls_code): 관리종목/투자위험/거래정지 자동 탐지.
     (호출측이 코드를 주입할 때만 작동. 미주입이면 override만 평가.)

순수 stdlib. KR/US 공용(override는 market 무관 ticker 기반).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# KIS 주식현재가(FHKST01010100) output.iscd_stat_cls_code → 강제청산 대상.
#   51 관리종목 / 52 투자위험 / 58 거래정지 = 청산.
#   ★ 자동 강제청산은 '명백한 부실/상폐위험'인 51 관리종목만.
#   52 투자위험·53 투자경고·54 투자주의 = '시장경고' 단계로 단기 이상급등(투기과열)에
#     붙는 신호 → 크게 상승 중인 종목(승자)이 걸릴 수 있어 강제청산하면 불필요한
#     수익반납(anti-O'Neil). 제외.
#   58 거래정지 = 모호(급등 1일정지 재개 시 상승 / 상폐직전 정지). 정지 중엔 어차피
#     주문 미체결 + 시뮬 선삭제로 불일치 위험 → 자동청산 제외(상폐는 override가 담당).
#   상폐/공개매수/감사거절 등은 KIS코드로 안 잡히므로(예: 더존=57 증거금100%)
#     event_force_exit.json override로 처리한다.
FORCE_EXIT_STAT_CODES = {
    "51": "관리종목",
}

_DEFAULT_OVERRIDE_FILE = os.path.join(os.path.dirname(__file__), "event_force_exit.json")


def _override_file_path() -> str:
    return os.environ.get("EVENT_FORCE_EXIT_FILE", _DEFAULT_OVERRIDE_FILE)


def load_overrides() -> dict:
    """이벤트 강제청산 종목 목록 로드. 파일 없거나 파싱 오류면 빈 dict(안전: 청산 안 함)."""
    path = _override_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        tickers = data.get("tickers", {})
        return tickers if isinstance(tickers, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"event_force_exit override 로드 실패({path}): {e}")
        return {}


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
    """법인 이벤트 강제청산 판정(TIER0). 가격/레짐 무관 최우선.

    Args:
        ticker: 종목코드
        kis_status_code: KIS iscd_stat_cls_code (주입 시 자동탐지, None이면 override만)
        market: 'KR'|'US' (현재는 로깅/확장용. override는 market 무관)

    Returns:
        (should_sell, reason_key). 해당 없으면 (False, "").
    """
    t = str(ticker or "").strip()
    if not t:
        return False, ""

    # 1) override 목록 (상폐/공개매수 등 등록 이벤트)
    ov = load_overrides().get(t)
    if ov:
        if isinstance(ov, dict):
            # market 지정돼 있으면 일치할 때만 적용(오등록 방지). 없으면 무관 적용.
            ov_market = str(ov.get("market", "")).strip().upper()
            if ov_market and market and ov_market != str(market).upper():
                pass
            else:
                reason = ov.get("reason", "corporate_event")
                return True, f"TIER0_EVENT:OVERRIDE:{reason}"
        else:
            return True, f"TIER0_EVENT:OVERRIDE:{ov}"

    # 2) KIS 종목상태코드 자동탐지 (주입된 경우만)
    if kis_status_code is not None:
        forced, reason = classify_kis_status(kis_status_code)
        if forced:
            return True, reason

    return False, ""


async def fetch_status_codes(tickers, account_name: Optional[str] = None) -> dict:
    """보유종목들의 KIS 종목상태코드(iscd_stat_cls_code)를 일괄 조회.

    KIS 토큰 발급 레이트리밋을 피하려고 **AsyncTradingContext를 1회만** 열고
    종목별 시세조회(quotation, 계좌 무관)로 상태코드만 수집한다.
    어떤 단계가 실패해도(자격증명 없음/네트워크/레이트리밋) 절대 예외를 올리지
    않고, 가능한 만큼만 채운 dict를 반환한다(override 경로는 독립적으로 동작).

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
