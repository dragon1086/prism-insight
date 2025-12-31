#!/usr/bin/env python3
from holidays.countries import KR
from datetime import date
import sys
import logging
from pathlib import Path

# 프로젝트 루트 디렉토리 자동 감지
PROJECT_ROOT = Path(__file__).resolve().parent

# 로깅 설정
logging.basicConfig(
    filename=PROJECT_ROOT / 'stock_scheduler.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def is_market_day():
    """한국 주식 시장 영업일인지 확인"""
    today = date.today()

    # 주말 체크 (5:토요일, 6:일요일)
    if today.weekday() >= 5:
        logger.debug(f"{today}은 주말입니다.")
        return False

    # 한국 공휴일 체크
    kr_holidays = KR()
    if today in kr_holidays:
        holiday_name = kr_holidays.get(today)
        logger.debug(f"{today}은 공휴일({holiday_name})입니다.")
        return False

    # 노동절(5월 1일) 체크 - 증권시장 휴장
    if today.month == 5 and today.day == 1:
        logger.debug(f"{today}은 노동절(근로자의 날)입니다.")
        return False

    # 연말(12월 31일) 체크 - 증권시장 휴장
    if today.month == 12 and today.day == 31:
        logger.debug(f"{today}은 연말 휴장일입니다.")
        return False

    # 2025년 특별 공휴일/대체휴일 체크
    if today.year == 2025:
        # 임시공휴일
        if ((today.month == 1 and today.day == 27) or  # 설날 연휴 임시공휴일
                (today.month == 3 and today.day == 3) or   # 삼일절 대체공휴일
                (today.month == 5 and today.day == 6) or   # 어린이날/부처님오신날 대체공휴일
                (today.month == 10 and today.day == 8)):   # 추석 대체공휴일
            logger.debug(f"{today}은 2025년 임시공휴일/대체공휴일입니다.")
            return False

        # 대통령선거일 - 2025년 6월 3일
        if today.month == 6 and today.day == 3:
            logger.debug(f"{today}은 대통령선거일입니다.")
            return False

        # 임시공휴일 가능성 있음 (뉴스에 언급됨)
        # if today.month == 10 and today.day == 10:
        #     logger.debug(f"{today}은 가능성 있는 임시공휴일입니다.")
        #     return False

    # 2026년 이후 특별 휴일 체크 (매년 업데이트 필요)
    elif today.year == 2026:
        # 여기에 2026년 특별 휴일 추가
        pass

    # 영업일
    return True

if __name__ == "__main__":
    if is_market_day():
        # 영업일이면 종료 코드 0 (정상)
        sys.exit(0)
    else:
        # 영업일이 아니면 종료 코드 1 (비정상)
        sys.exit(1)