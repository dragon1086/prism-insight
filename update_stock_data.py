#!/usr/bin/env python3
"""
종목 정보 업데이트 스크립트

매일 주기적으로 실행하여 주식 종목 정보(코드, 이름)를 최신화
"""
from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드

import os
import json
import logging
import argparse
from datetime import datetime

try:
    from krx_data_client import _get_client
except ImportError:
    print("krx_data_client 패키지가 설치되어 있지 않습니다. 'pip install kospi-kosdaq-stock-server'로 설치하세요.")
    exit(1)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("stock_data_update.log")
    ]
)
logger = logging.getLogger(__name__)

def update_stock_data(output_file="stock_map.json"):
    """
    종목 정보 업데이트

    Args:
        output_file (str): 저장할 파일 경로

    Returns:
        bool: 성공 여부
    """
    try:
        # 오늘 날짜
        today = datetime.now().strftime("%Y%m%d")
        logger.info(f"종목 데이터 업데이트 시작: {today}")

        # 클라이언트 초기화
        client = _get_client()

        # 전체 종목 코드-이름 매핑을 한 번에 가져오기 (효율적!)
        logger.info("전체 종목 정보 조회 중...")
        code_to_name = client.get_market_ticker_name(market="ALL")
        logger.info(f"전체 종목 {len(code_to_name)}개 로드 완료")

        # 역매핑 생성
        name_to_code = {name: code for code, name in code_to_name.items()}

        # 데이터 저장
        data = {
            "code_to_name": code_to_name,
            "name_to_code": name_to_code,
            "updated_at": datetime.now().isoformat()
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"종목 데이터 업데이트 완료: {len(code_to_name)}개 종목, 파일: {output_file}")
        return True
    except Exception as e:
        logger.error(f"종목 데이터 업데이트 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def main():
    parser = argparse.ArgumentParser(description="종목 정보 업데이트")
    parser.add_argument("--output", default="stock_map.json", help="저장할 파일 경로")

    args = parser.parse_args()
    update_stock_data(args.output)

if __name__ == "__main__":
    main()