#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드 (krx_data_client import 전에 필요)

import sys
import datetime
import pandas as pd
import numpy as np
import logging
from krx_data_client import (
    get_market_ohlcv_by_ticker,
    get_nearest_business_day_in_a_week,
)

# pykrx 호환 래퍼 (기존 코드 호환성)
class stock_api:
    get_market_ohlcv_by_ticker = staticmethod(get_market_ohlcv_by_ticker)
    get_nearest_business_day_in_a_week = staticmethod(get_nearest_business_day_in_a_week)

# 로거 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)


# --- 데이터 수집 및 캐싱 함수 ---
def get_snapshot(trade_date: str) -> pd.DataFrame:
    """
    지정 거래일의 전체 종목 OHLCV 스냅샷을 반환합니다.
    컬럼: "시가", "고가", "저가", "종가", "거래량", "거래대금"
    """
    logger.debug(f"get_snapshot 호출: {trade_date}")
    df = stock_api.get_market_ohlcv_by_ticker(trade_date)
    if df.empty:
        logger.error(f"{trade_date}에 대한 OHLCV 데이터가 없습니다.")
        raise ValueError(f"{trade_date}에 대한 OHLCV 데이터가 없습니다.")

    # 데이터 확인용
    logger.debug(f"스냅샷 데이터 샘플: {df.head()}")
    logger.debug(f"스냅샷 데이터 컬럼: {df.columns}")

    return df

def get_previous_snapshot(trade_date: str) -> (pd.DataFrame, str):
    """
    지정 거래일의 직전 영업일을 구한 후, 해당일의 OHLCV 스냅샷과 날짜를 반환합니다.
    """
    # 날짜 객체로 변환
    date_obj = datetime.datetime.strptime(trade_date, '%Y%m%d')

    # 하루 전으로 이동
    prev_date_obj = date_obj - datetime.timedelta(days=1)

    # 영업일 체크를 위해 문자열로 변환
    prev_date_str = prev_date_obj.strftime('%Y%m%d')

    # 직전 영업일 구하기
    prev_date = stock_api.get_nearest_business_day_in_a_week(prev_date_str, prev=True)

    logger.debug(f"이전 거래일 확인 - 기준일: {trade_date}, 하루 전: {prev_date_str}, 직전 영업일: {prev_date}")

    df = stock_api.get_market_ohlcv_by_ticker(prev_date)
    if df.empty:
        logger.error(f"{prev_date}에 대한 OHLCV 데이터가 없습니다.")
        raise ValueError(f"{prev_date}에 대한 OHLCV 데이터가 없습니다.")

    # 데이터 확인용
    logger.debug(f"이전 거래일 데이터 샘플: {df.head()}")
    logger.debug(f"이전 거래일 데이터 컬럼: {df.columns}")

    return df, prev_date


def get_multi_day_snapshots(trade_date: str, days: int = 5) -> dict:
    """
    최근 N 영업일간의 OHLCV 스냅샷을 모두 조회합니다.

    Args:
        trade_date: 기준 거래일 (YYYYMMDD)
        days: 조회할 일수 (기본값: 5)

    Returns:
        dict: {날짜: DataFrame} 형태의 딕셔너리
              최신 날짜가 첫 번째 (trade_date가 가장 최신)
    """
    logger.debug(f"get_multi_day_snapshots 호출: 기준일 {trade_date}, {days}일간")

    snapshots = {}
    current_date = trade_date

    for i in range(days):
        try:
            df = stock_api.get_market_ohlcv_by_ticker(current_date)
            if not df.empty:
                snapshots[current_date] = df
                logger.debug(f"Day-{i} 스냅샷 조회 성공: {current_date} ({len(df)}개 종목)")
            else:
                logger.warning(f"Day-{i} 스냅샷 비어있음: {current_date}")
        except Exception as e:
            logger.warning(f"Day-{i} 스냅샷 조회 실패: {current_date} - {e}")

        # 직전 영업일로 이동
        try:
            date_obj = datetime.datetime.strptime(current_date, '%Y%m%d')
            prev_date_obj = date_obj - datetime.timedelta(days=1)
            prev_date_str = prev_date_obj.strftime('%Y%m%d')
            current_date = stock_api.get_nearest_business_day_in_a_week(prev_date_str, prev=True)
        except Exception as e:
            logger.warning(f"직전 영업일 조회 실패: {current_date} - {e}")
            break

    logger.info(f"멀티데이 스냅샷 조회 완료: {len(snapshots)}일치 데이터")
    return snapshots

def get_market_cap_df(trade_date: str, market: str = "ALL") -> pd.DataFrame:
    """
    지정 거래일의 전체 종목에 대한 시가총액 데이터를 DataFrame으로 반환합니다.
    인덱스는 종목 코드이며, "시가총액" 컬럼을 포함합니다.
    """
    logger.debug(f"get_market_cap_df 호출: {trade_date}, market={market}")
    cap_df = stock_api.get_market_cap_by_ticker(trade_date, market=market)
    if cap_df.empty:
        logger.error(f"{trade_date}의 시가총액 데이터가 없습니다.")
        raise ValueError(f"{trade_date}의 시가총액 데이터가 없습니다.")
    return cap_df

def filter_low_liquidity(df: pd.DataFrame, threshold: float = 0.2) -> pd.DataFrame:
    """
    거래량 하위 N% 종목을 제외합니다 (저유동성 종목 필터링)
    """
    volume_cutoff = np.percentile(df['거래량'], threshold * 100)
    return df[df['거래량'] > volume_cutoff]

def apply_absolute_filters(df: pd.DataFrame, min_value: int = 500000000) -> pd.DataFrame:
    """
    절대적 기준 필터링:
    - 최소 거래대금 (5억원 이상)
    - 유동성 충분한 종목
    """
    # 최소 거래대금 필터 (5억원 이상)
    filtered_df = df[df['거래대금'] >= min_value]

    # 시장 평균의 20% 이상 거래량 필터
    avg_volume = df['거래량'].mean()
    min_volume = avg_volume * 0.2
    filtered_df = filtered_df[filtered_df['거래량'] >= min_volume]

    return filtered_df

def normalize_and_score(df: pd.DataFrame, ratio_col: str, abs_col: str,
                        ratio_weight: float = 0.6, abs_weight: float = 0.4,
                        ascending: bool = False) -> pd.DataFrame:
    """
    특정 컬럼에 대해 정규화 후 가중치를 적용한 복합 점수를 계산합니다.

    ratio_col: 상대적 비율 컬럼 (예: 거래량비율)
    abs_col: 절대적 수치 컬럼 (예: 거래량)
    ratio_weight: 상대적 비율의 가중치 (기본값: 0.6)
    abs_weight:.절대적 수치의 가중치 (기본값: 0.4)
    ascending: 정렬 방향 (기본값: False, 내림차순)
    """
    if df.empty:
        return df

    # 정규화를 위한 최대/최소값 계산
    ratio_max = df[ratio_col].max()
    ratio_min = df[ratio_col].min()
    abs_max = df[abs_col].max()
    abs_min = df[abs_col].min()

    # 0으로 나누기 방지
    ratio_range = ratio_max - ratio_min if ratio_max > ratio_min else 1
    abs_range = abs_max - abs_min if abs_max > abs_min else 1

    # 각 컬럼 정규화 (0-1 사이의 값으로)
    df[f"{ratio_col}_norm"] = (df[ratio_col] - ratio_min) / ratio_range
    df[f"{abs_col}_norm"] = (df[abs_col] - abs_min) / abs_range

    # 복합 점수 계산
    df["복합점수"] = (df[f"{ratio_col}_norm"] * ratio_weight) + (df[f"{abs_col}_norm"] * abs_weight)

    # 복합 점수 기준 정렬
    return df.sort_values("복합점수", ascending=ascending)

def enhance_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    종목명, 업종 등 추가 정보를 DataFrame에 추가합니다
    """
    if not df.empty:
        df = df.copy()  # 명시적으로 복사본 생성하여 SettingWithCopyWarning 방지
        df["종목명"] = df.index.map(lambda ticker: stock_api.get_market_ticker_name(ticker))
    return df

# --- 오전 트리거 함수 (장 시작 스냅샷 기준) ---
def trigger_morning_volume_surge(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 10) -> pd.DataFrame:
    """
    [오전 트리거1] 당일 거래량 급증 상위주
    - 절대적 기준: 최소 거래대금 5억원 이상 + 시장 평균 거래량의 20% 이상
    - 추가 필터: 거래량 30% 이상 증가
    - 복합 점수: 거래량 증가율(60%) + 절대 거래량(40%)
    - 2차 필터링: 상승세 종목만 선별 (시가 대비 현재가 상승)
    - 동전주 필터: 시가총액 500억원 이상
    """
    logger.debug("trigger_morning_volume_surge 시작")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()
    
    # 시가총액 데이터 병합 및 동전주 필터링
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # 시가총액 500억원 이상만 선별 (동전주 제외)
        snap = snap[snap["시가총액"] >= 50000000000]
        logger.debug(f"시가총액 필터링 후 종목 수: {len(snap)}")
        if snap.empty:
            logger.warning("시가총액 필터링 후 종목이 없습니다")
            return pd.DataFrame()

    # 디버깅 정보
    logger.debug(f"전일 종가 데이터 샘플: {prev['종가'].head()}")
    logger.debug(f"당일 종가 데이터 샘플: {snap['종가'].head()}")

    # 절대적 기준 적용 (최소 거래대금, 거래량)
    snap = apply_absolute_filters(snap)

    # 거래량 비율 계산
    snap["거래량비율"] = snap["거래량"] / prev["거래량"].replace(0, np.nan)
    # 거래량 증가율 계산 (백분율)
    snap["거래량증가율"] = (snap["거래량비율"] - 1) * 100

    # 두 가지 등락률 계산
    snap["장중등락률"] = (snap["종가"] / snap["시가"] - 1) * 100  # 시가 대비 현재가

    # 전일대비등락률 계산 - 변경된 방식으로
    snap["전일대비등락률"] = ((snap["종가"] - prev["종가"]) / prev["종가"]) * 100

    # 첫 10개 종목의 전일대비등락률 계산 과정 디버깅
    for ticker in snap.index[:5]:
        try:
            today_close = snap.loc[ticker, "종가"]
            yesterday_close = prev.loc[ticker, "종가"]
            change_rate = ((today_close - yesterday_close) / yesterday_close) * 100
            logger.debug(f"종목 {ticker} - 오늘종가: {today_close}, 전일종가: {yesterday_close}, 전일대비등락률: {change_rate:.2f}%")
        except Exception as e:
            logger.debug(f"디버깅 중 오류: {e}")

    snap["상승여부"] = snap["종가"] > snap["시가"]

    # 거래량 증가율 30% 이상 필터링
    snap = snap[snap["거래량증가율"] >= 30.0]

    if snap.empty:
        logger.debug("trigger_morning_volume_surge: 거래량 증가 종목 없음")
        return pd.DataFrame()

    # 1차 필터링: 복합 점수 기준 상위 종목 선정
    scored = normalize_and_score(snap, "거래량증가율", "거래량", 0.6, 0.4)
    candidates = scored.head(top_n)

    # 2차 필터링: 상승세 종목만 선별
    result = candidates[candidates["상승여부"] == True].copy()

    if result.empty:
        logger.debug("trigger_morning_volume_surge: 조건 충족 종목 없음")
        return pd.DataFrame()

    logger.debug(f"거래량 급증 포착 종목 수: {len(result)}")
    return enhance_dataframe(result.sort_values("복합점수", ascending=False).head(3))

def trigger_morning_gap_up_momentum(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 15) -> pd.DataFrame:
    """
    [오전 트리거2] 갭 상승 모멘텀 상위주
    - 절대적 기준: 최소 거래대금 5억원 이상
    - 복합 점수: 갭상승률(50%) + 당일상승률(30%) + 거래대금(20%)
    - 2차 필터링: 현재가가 시가보다 높은 종목만 선별 (상승 지속)
    - 동전주 필터: 시가총액 500억원 이상
    """
    logger.debug("trigger_morning_gap_up_momentum 시작")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()
    
    # 시가총액 데이터 병합 및 동전주 필터링
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # 시가총액 500억원 이상만 선별 (동전주 제외)
        snap = snap[snap["시가총액"] >= 50000000000]
        logger.debug(f"시가총액 필터링 후 종목 수: {len(snap)}")
        if snap.empty:
            logger.warning("시가총액 필터링 후 종목이 없습니다")
            return pd.DataFrame()

    # 절대적 기준 적용
    snap = apply_absolute_filters(snap)

    # 갭 상승률 계산
    snap["갭상승률"] = (snap["시가"] / prev["종가"] - 1) * 100
    snap["장중등락률"] = (snap["종가"] / snap["시가"] - 1) * 100  # 당일 시가 대비 등락률
    snap["전일대비등락률"] = ((snap["종가"] - prev["종가"]) / prev["종가"]) * 100  # 전일 종가 대비 등락률
    snap["상승지속"] = snap["종가"] > snap["시가"]

    # 1차 필터링: 갭상승률 1% 이상인 종목만 선택
    snap = snap[snap["갭상승률"] >= 1.0]

    # 점수 계산 (커스텀 복합 점수)
    if not snap.empty:
        # 각 지표별 정규화
        for col in ["갭상승률", "장중등락률", "거래대금"]:
            col_max = snap[col].max()
            col_min = snap[col].min()
            col_range = col_max - col_min if col_max > col_min else 1
            snap[f"{col}_norm"] = (snap[col] - col_min) / col_range

        # 복합 점수 계산 (가중치 적용)
        snap["복합점수"] = (
                snap["갭상승률_norm"] * 0.5 +
                snap["장중등락률_norm"] * 0.3 +
                snap["거래대금_norm"] * 0.2
        )

        # 점수 기준 상위 종목 선정
        candidates = snap.sort_values("복합점수", ascending=False).head(top_n)
    else:
        candidates = snap

    # 2차 필터링: 상승 지속 종목만 선별
    result = candidates[candidates["상승지속"] == True].copy()

    if result.empty:
        logger.debug("trigger_morning_gap_up_momentum: 조건 충족 종목 없음")
        return pd.DataFrame()

    # 추가 정보 계산
    result["종합모멘텀"] = result["갭상승률"] + result["장중등락률"]

    logger.debug(f"갭 상승 모멘텀 포착 종목 수: {len(result)}")
    return enhance_dataframe(result.sort_values("복합점수", ascending=False).head(3))


def trigger_morning_pullback_buy(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame,
                                  cap_df: pd.DataFrame, multi_day_snapshots: dict = None, top_n: int = 15) -> pd.DataFrame:
    """
    [오전 트리거2 대체] 눌림목 매수 대기 종목

    trading_agents.py와 궁합을 맞추기 위한 새로운 트리거:
    - 기존 갭상승 트리거는 "이미 상승한" 종목을 선별 → 손익비 불리
    - 새 트리거는 "추세 확인 후 조정 시점"의 종목을 선별 → 손익비 유리

    선별 기준:
    1. 최근 5일 중 3일 이상 양봉 (상승 추세 확인)
    2. 당일 갭다운 또는 보합 시작 (눌림목/조정)
    3. 당일 반등 중 (매수세 유입 확인)
    4. 거래량 전일 대비 70% 이상 유지 (관심 지속)
    5. RSI 70 미만 (과열 종목 제외)
    6. 시가총액 500억원 이상

    복합 점수: 추세강도(40%) + 반등강도(30%) + 거래량안정성(30%)

    장점:
    - 손절가(최근 저점)가 가까움 → 손절폭 축소
    - 상승 추세 확인 후 조정에서 진입 → 손익비 2:1+ 확보 용이
    - 급등 직후가 아닌 조정 시점 → 상대적 저평가
    """
    logger.debug("trigger_morning_pullback_buy 시작")

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # 시가총액 데이터 병합 및 동전주 필터링
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        snap = snap[snap["시가총액"] >= 50000000000]  # 500억원 이상
        logger.debug(f"시가총액 필터링 후 종목 수: {len(snap)}")
        if snap.empty:
            logger.warning("시가총액 필터링 후 종목이 없습니다")
            return pd.DataFrame()

    # 절대적 기준 적용 (최소 거래대금 5억원)
    snap = apply_absolute_filters(snap)
    if snap.empty:
        logger.debug("절대적 기준 필터링 후 종목 없음")
        return pd.DataFrame()

    # === 멀티데이 추세 분석 ===
    if multi_day_snapshots and len(multi_day_snapshots) >= 3:
        logger.debug(f"멀티데이 추세 분석 시작: {len(multi_day_snapshots)}일치 데이터")

        # 날짜 정렬 (최신 → 과거)
        sorted_dates = sorted(multi_day_snapshots.keys(), reverse=True)

        # 각 종목별 양봉 일수 계산
        bullish_days = pd.Series(0, index=snap.index)
        total_gain = pd.Series(0.0, index=snap.index)  # 누적 상승률
        recent_high = pd.Series(0.0, index=snap.index)  # 최근 고점
        recent_low = pd.Series(float('inf'), index=snap.index)  # 최근 저점

        for i, date in enumerate(sorted_dates[:-1]):  # 마지막 날은 비교 대상 없음
            if date not in multi_day_snapshots:
                continue

            current_df = multi_day_snapshots[date]
            next_date = sorted_dates[i + 1] if i + 1 < len(sorted_dates) else None

            if next_date and next_date in multi_day_snapshots:
                prev_df = multi_day_snapshots[next_date]

                # 공통 종목만
                common_tickers = snap.index.intersection(current_df.index).intersection(prev_df.index)

                for ticker in common_tickers:
                    try:
                        # 양봉 여부 (종가 > 시가)
                        if current_df.loc[ticker, "종가"] > current_df.loc[ticker, "시가"]:
                            bullish_days[ticker] += 1

                        # 전일 대비 등락률
                        daily_return = (current_df.loc[ticker, "종가"] - prev_df.loc[ticker, "종가"]) / prev_df.loc[ticker, "종가"]
                        total_gain[ticker] += daily_return * 100

                        # 고점/저점 업데이트
                        recent_high[ticker] = max(recent_high[ticker], current_df.loc[ticker, "고가"])
                        if current_df.loc[ticker, "저가"] > 0:
                            recent_low[ticker] = min(recent_low[ticker], current_df.loc[ticker, "저가"])
                    except (KeyError, ZeroDivisionError):
                        continue

        snap["양봉일수"] = bullish_days
        snap["누적상승률"] = total_gain
        snap["최근5일고점"] = recent_high
        snap["최근5일저점"] = recent_low.replace(float('inf'), np.nan)

        # RSI 간이 계산 (5일 기준)
        snap["RSI추정"] = snap.apply(
            lambda row: min(100, max(0, 50 + row["누적상승률"] * 2)) if pd.notna(row["누적상승률"]) else 50,
            axis=1
        )

    else:
        # 멀티데이 데이터 없으면 전일 데이터만으로 간이 판단
        logger.debug("멀티데이 데이터 부족, 전일 데이터로 간이 분석")
        snap["양봉일수"] = (prev["종가"] > prev["시가"]).astype(int)  # 전일 양봉이면 1
        snap["누적상승률"] = ((prev["종가"] - prev["시가"]) / prev["시가"]) * 100
        snap["최근5일고점"] = prev["고가"]
        snap["최근5일저점"] = prev["저가"]
        snap["RSI추정"] = 50  # 기본값

    # === 핵심 조건 계산 ===

    # 1. 상승 추세 확인 (5일 중 3일 이상 양봉)
    snap["추세상승"] = snap["양봉일수"] >= 3

    # 2. 당일 눌림목 시작 (갭다운 또는 보합: 시가 <= 전일 종가)
    snap["눌림목시작"] = snap["시가"] <= prev["종가"]
    snap["갭률"] = ((snap["시가"] - prev["종가"]) / prev["종가"]) * 100

    # 3. 당일 반등 중 (현재가 > 시가)
    snap["장중등락률"] = ((snap["종가"] - snap["시가"]) / snap["시가"]) * 100
    snap["반등중"] = snap["종가"] > snap["시가"]

    # 4. 거래량 유지 (전일 대비 70% 이상)
    snap["거래량비율"] = snap["거래량"] / prev["거래량"].replace(0, np.nan)
    snap["거래량유지"] = snap["거래량비율"] >= 0.7

    # 5. 과열 종목 제외 (RSI 70 미만)
    snap["과열아님"] = snap["RSI추정"] < 70

    # 전일대비등락률 계산 (표시용)
    snap["전일대비등락률"] = ((snap["종가"] - prev["종가"]) / prev["종가"]) * 100

    # 지지선까지 거리 (손절폭 추정)
    snap["지지선거리"] = ((snap["종가"] - snap["최근5일저점"]) / snap["종가"]) * 100

    # === 필터링 ===
    # 멀티데이 데이터가 충분하면 엄격한 필터, 아니면 완화된 필터
    if multi_day_snapshots and len(multi_day_snapshots) >= 3:
        candidates = snap[
            (snap["추세상승"]) &           # 5일 중 3일+ 양봉
            (snap["눌림목시작"]) &         # 당일 갭다운/보합 시작
            (snap["반등중"]) &             # 장중 반등
            (snap["거래량유지"]) &         # 거래량 유지
            (snap["과열아님"]) &           # RSI 70 미만
            (snap["장중등락률"] >= 0.3) &  # 최소 0.3% 이상 반등
            (snap["지지선거리"] <= 10)     # 손절폭 10% 이내 (손익비 확보)
        ]
    else:
        # 완화된 필터 (전일 데이터만 사용)
        candidates = snap[
            (snap["눌림목시작"]) &         # 당일 갭다운/보합 시작
            (snap["반등중"]) &             # 장중 반등
            (snap["거래량유지"]) &         # 거래량 유지
            (snap["장중등락률"] >= 0.5)    # 최소 0.5% 이상 반등
        ]

    if candidates.empty:
        logger.debug("trigger_morning_pullback_buy: 조건 충족 종목 없음")
        return pd.DataFrame()

    # === 복합 점수 계산 ===
    score_cols = []

    # 추세 강도 (양봉일수 기반)
    if "양봉일수" in candidates.columns:
        col_max = candidates["양봉일수"].max()
        col_min = candidates["양봉일수"].min()
        col_range = col_max - col_min if col_max > col_min else 1
        candidates["추세강도_norm"] = (candidates["양봉일수"] - col_min) / col_range
        score_cols.append(("추세강도_norm", 0.4))

    # 반등 강도 (장중등락률 기반)
    col_max = candidates["장중등락률"].max()
    col_min = candidates["장중등락률"].min()
    col_range = col_max - col_min if col_max > col_min else 1
    candidates["반등강도_norm"] = (candidates["장중등락률"] - col_min) / col_range
    score_cols.append(("반등강도_norm", 0.3))

    # 거래량 안정성 (거래량비율 기반)
    col_max = candidates["거래량비율"].max()
    col_min = candidates["거래량비율"].min()
    col_range = col_max - col_min if col_max > col_min else 1
    candidates["거래량안정성_norm"] = (candidates["거래량비율"] - col_min) / col_range
    score_cols.append(("거래량안정성_norm", 0.3))

    # 복합 점수 계산
    candidates["복합점수"] = sum(candidates[col] * weight for col, weight in score_cols)

    result = candidates.sort_values("복합점수", ascending=False).head(top_n)

    # 상위 종목 로깅
    for ticker in result.index[:3]:
        logger.debug(
            f"눌림목 후보 - {ticker}: "
            f"양봉{result.loc[ticker, '양봉일수']}일, "
            f"갭{result.loc[ticker, '갭률']:.1f}%, "
            f"반등{result.loc[ticker, '장중등락률']:.1f}%, "
            f"지지선거리{result.loc[ticker, '지지선거리']:.1f}%"
        )

    logger.debug(f"눌림목 매수 대기 포착 종목 수: {len(result)}")
    return enhance_dataframe(result.head(3))


def trigger_morning_value_to_cap_ratio(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    [오전 트리거3] 시총 대비 집중 자금 유입 상위주
    - 절대적 기준: 최소 거래대금 5억원 이상
    - 복합 점수: 거래대금비율(50%) + 절대거래대금(30%) + 당일등락률(20%)
    - 2차 필터링: 상승세 종목만 선별 (시가 대비 현재가 상승)
    """
    logger.info("시총 대비 집중 자금 유입 상위주 분석 시작")

    # 방어 코드 1: 입력 데이터 유효성 검사
    if snapshot.empty:
        logger.error("snapshot 데이터가 비어있습니다")
        return pd.DataFrame()

    if prev_snapshot.empty:
        logger.error("prev_snapshot 데이터가 비어있습니다")
        return pd.DataFrame()

    if cap_df.empty:
        logger.error("cap_df 데이터가 비어있습니다")
        return pd.DataFrame()

    # 방어 코드 2: 시가총액 컬럼 존재 확인
    if '시가총액' not in cap_df.columns:
        logger.error(f"cap_df에 '시가총액' 컬럼이 없습니다. 실제 컬럼: {list(cap_df.columns)}")
        return pd.DataFrame()

    logger.info(f"입력 데이터 검증 완료 - snapshot: {len(snapshot)}개, cap_df: {len(cap_df)}개")

    try:
        # 시가총액 데이터와 OHLCV 데이터 병합
        logger.debug("시가총액 데이터 병합 시작")
        merged = snapshot.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner").copy()
        logger.info(f"데이터 병합 완료: {len(merged)}개 종목")

        # 방어 코드 3: 병합 후 시가총액 컬럼 재확인
        if '시가총액' not in merged.columns:
            logger.error(f"병합 후 '시가총액' 컬럼이 없습니다. 병합 후 컬럼: {list(merged.columns)}")
            return pd.DataFrame()

        # 이전 거래일 데이터와 병합
        common = merged.index.intersection(prev_snapshot.index)
        if len(common) == 0:
            logger.error("공통 종목이 없습니다")
            return pd.DataFrame()

        if len(common) < 50:
            logger.warning(f"공통 종목이 {len(common)}개로 적습니다. 결과 품질이 낮을 수 있습니다")

        merged = merged.loc[common].copy()
        prev = prev_snapshot.loc[common].copy()
        logger.debug(f"전일 데이터 병합 완료 - 공통 종목: {len(common)}개")

        # 절대적 기준 적용
        logger.debug("절대적 기준 필터링 시작")
        merged = apply_absolute_filters(merged)
        if merged.empty:
            logger.warning("절대적 기준 필터링 후 종목이 없습니다")
            return pd.DataFrame()

        logger.info(f"필터링 완료: {len(merged)}개 종목")

        # 방어 코드 4: 필수 컬럼 재확인
        required_columns = ['거래대금', '시가총액', '종가', '시가']
        missing_columns = [col for col in required_columns if col not in merged.columns]
        if missing_columns:
            logger.error(f"필수 컬럼이 없습니다: {missing_columns}")
            return pd.DataFrame()

        # 거래대금/시가총액 비율 계산
        logger.debug("거래대금비율 계산 시작")
        merged["거래대금비율"] = (merged["거래대금"] / merged["시가총액"]) * 100

        # 두 가지 등락률 계산
        merged["장중등락률"] = (merged["종가"] / merged["시가"] - 1) * 100  # 시가 대비 현재가
        merged["전일대비등락률"] = ((merged["종가"] - prev["종가"]) / prev["종가"]) * 100  # 증권사 앱과 동일
        merged["상승여부"] = merged["종가"] > merged["시가"]

        # 시총 필터링 - 최소 500억원 이상 종목 (동전주 제외)
        merged = merged[merged["시가총액"] >= 50000000000]
        if merged.empty:
            logger.warning("시총 필터링 후 종목이 없습니다")
            return pd.DataFrame()

        logger.debug(f"시총 필터링 완료 - 남은 종목: {len(merged)}개")

        # 복합 점수 계산
        if not merged.empty:
            # 각 지표별 정규화
            for col in ["거래대금비율", "거래대금", "장중등락률"]:
                col_max = merged[col].max()
                col_min = merged[col].min()
                col_range = col_max - col_min if col_max > col_min else 1
                merged[f"{col}_norm"] = (merged[col] - col_min) / col_range

            # 복합 점수 계산
            merged["복합점수"] = (
                    merged["거래대금비율_norm"] * 0.5 +
                    merged["거래대금_norm"] * 0.3 +
                    merged["장중등락률_norm"] * 0.2
            )

            # 상위 종목 선별
            candidates = merged.sort_values("복합점수", ascending=False).head(top_n)
        else:
            candidates = merged

        # 2차 필터링: 상승세 종목만 선별
        result = candidates[candidates["상승여부"] == True].copy()

        if result.empty:
            logger.info("조건 충족 종목 없음")
            return pd.DataFrame()

        logger.info(f"분석 완료: {len(result)}개 종목 선별")
        return enhance_dataframe(result.sort_values("복합점수", ascending=False).head(3))

    except Exception as e:
        logger.error(f"함수 실행 중 예외 발생: {e}")
        import traceback
        logger.debug(f"상세 에러:\n{traceback.format_exc()}")
        return pd.DataFrame()

# --- 오후 트리거 함수 (장 마감 스냅샷 기준) ---
def trigger_afternoon_daily_rise_top(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 15) -> pd.DataFrame:
    """
    [오후 트리거1] 일중 상승률 상위주
    - 절대적 기준: 최소 거래대금 10억원 이상
    - 복합 점수: 일중상승률(60%) + 거래대금(40%)
    - 추가 필터: 등락률 3% 이상
    - 동전주 필터: 시가총액 500억원 이상
    """
    logger.debug("trigger_afternoon_daily_rise_top 시작")

    # 이전 거래일 데이터 연결
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()
    
    # 시가총액 데이터 병합 및 동전주 필터링
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # 시가총액 500억원 이상만 선별 (동전주 제외)
        snap = snap[snap["시가총액"] >= 50000000000]
        logger.debug(f"시가총액 필터링 후 종목 수: {len(snap)}")
        if snap.empty:
            logger.warning("시가총액 필터링 후 종목이 없습니다")
            return pd.DataFrame()

    # 절대적 기준 적용 (최소 거래대금 10억 이상)
    snap = apply_absolute_filters(snap.copy(), min_value=1000000000)

    # 두 가지 등락률 계산
    snap["장중등락률"] = (snap["종가"] / snap["시가"] - 1) * 100  # 시가 대비 현재가
    snap["전일대비등락률"] = ((snap["종가"] - prev["종가"]) / prev["종가"]) * 100  # 증권사 앱과 동일

    # 추가 필터: 장중등락률 3% 이상
    snap = snap[snap["장중등락률"] >= 3.0]

    if snap.empty:
        logger.debug("trigger_afternoon_daily_rise_top: 조건 충족 종목 없음")
        return pd.DataFrame()

    # 복합 점수 계산
    scored = normalize_and_score(snap, "장중등락률", "거래대금", 0.6, 0.4)

    # 상위 종목 선별
    result = scored.head(top_n).copy()

    logger.debug(f"일중 상승률 상위 포착 종목 수: {len(result)}")
    return enhance_dataframe(result.head(3))

def trigger_afternoon_closing_strength(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 15) -> pd.DataFrame:
    """
    [오후 트리거2] 마감 강도 상위주
    - 절대적 기준: 최소 거래대금 5억원 이상 + 전일 대비 거래량 증가
    - 복합 점수: 마감강도(50%) + 거래량증가율(30%) + 거래대금(20%)
    - 2차 필터링: 상승세 종목만 선별 (시가 대비 종가 상승)
    - 동전주 필터: 시가총액 500억원 이상
    """
    logger.debug("trigger_afternoon_closing_strength 시작")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()
    
    # 시가총액 데이터 병합 및 동전주 필터링
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # 시가총액 500억원 이상만 선별 (동전주 제외)
        snap = snap[snap["시가총액"] >= 50000000000]
        logger.debug(f"시가총액 필터링 후 종목 수: {len(snap)}")
        if snap.empty:
            logger.warning("시가총액 필터링 후 종목이 없습니다")
            return pd.DataFrame()

    # 절대적 기준 적용
    snap = apply_absolute_filters(snap)

    # 마감 강도 계산 (종가가 고가에 가까울수록 1에 가까움)
    snap["마감강도"] = 0.0  # 기본값 설정
    valid_range = (snap["고가"] != snap["저가"])  # 0으로 나누기 방지
    snap.loc[valid_range, "마감강도"] = (snap.loc[valid_range, "종가"] - snap.loc[valid_range, "저가"]) / (snap.loc[valid_range, "고가"] - snap.loc[valid_range, "저가"])

    # 거래량 증가 여부 계산
    snap["거래량증가율"] = (snap["거래량"] / prev["거래량"].replace(0, np.nan) - 1) * 100

    # 두 가지 등락률 계산
    snap["장중등락률"] = (snap["종가"] / snap["시가"] - 1) * 100  # 시가 대비 현재가
    snap["전일대비등락률"] = ((snap["종가"] - prev["종가"]) / prev["종가"]) * 100  # 증권사 앱과 동일

    snap["거래량증가"] = (snap["거래량"] - prev["거래량"].replace(0, np.nan)) > 0
    snap["상승여부"] = snap["종가"] > snap["시가"]

    # 1차 필터링: 거래량 증가 종목만 선별
    candidates = snap[snap["거래량증가"] == True].copy()

    # 복합 점수 계산
    if not candidates.empty:
        # 각 지표별 정규화
        for col in ["마감강도", "거래량증가율", "거래대금"]:
            col_max = candidates[col].max()
            col_min = candidates[col].min()
            col_range = col_max - col_min if col_max > col_min else 1
            candidates[f"{col}_norm"] = (candidates[col] - col_min) / col_range

        # 복합 점수 계산
        candidates["복합점수"] = (
                candidates["마감강도_norm"] * 0.5 +
                candidates["거래량증가율_norm"] * 0.3 +
                candidates["거래대금_norm"] * 0.2
        )

        # 점수 기준 상위 종목 선정
        candidates = candidates.sort_values("복합점수", ascending=False).head(top_n)

    # 2차 필터링: 상승세 종목만 선별
    result = candidates[candidates["상승여부"] == True].copy()

    if result.empty:
        logger.debug("trigger_afternoon_closing_strength: 조건 충족 종목 없음")
        return pd.DataFrame()

    logger.debug(f"마감 강도 상위 포착 종목 수: {len(result)}")
    return enhance_dataframe(result.sort_values("복합점수", ascending=False).head(3))

def trigger_afternoon_volume_surge_flat(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 20) -> pd.DataFrame:
    """
    [오후 트리거3] 거래량 증가 상위 횡보주
    - 절대적 기준: 최소 거래대금 5억원 이상 + 시장 평균 대비 거래량
    - 복합 점수: 거래량증가율(60%) + 거래대금(40%)
    - 2차 필터링: 등락률이 ±5% 이내인 횡보 종목만 선별
    - 동전주 필터: 시가총액 500억원 이상
    """
    logger.debug("trigger_afternoon_volume_surge_flat 시작")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()
    
    # 시가총액 데이터 병합 및 동전주 필터링
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # 시가총액 500억원 이상만 선별 (동전주 제외)
        snap = snap[snap["시가총액"] >= 50000000000]
        logger.debug(f"시가총액 필터링 후 종목 수: {len(snap)}")
        if snap.empty:
            logger.warning("시가총액 필터링 후 종목이 없습니다")
            return pd.DataFrame()

    # 절대적 기준 적용
    snap = apply_absolute_filters(snap)

    # 거래량 증가율 계산
    snap["거래량증가율"] = (snap["거래량"] / prev["거래량"].replace(0, np.nan) - 1) * 100

    # 두 가지 등락률 계산
    snap["장중등락률"] = (snap["종가"] / snap["시가"] - 1) * 100  # 시가 대비 현재가
    snap["전일대비등락률"] = ((snap["종가"] - prev["종가"]) / prev["종가"]) * 100  # 증권사 앱과 동일

    # 횡보주 여부 판단 (장중등락률 ±5% 이내)
    snap["횡보여부"] = (snap["장중등락률"].abs() <= 5)

    # 추가 필터: 전일 대비 거래량 50% 이상 증가한 종목만
    snap = snap[snap["거래량증가율"] >= 50]

    if snap.empty:
        logger.debug("trigger_afternoon_volume_surge_flat: 조건 충족 종목 없음")
        return pd.DataFrame()

    # 복합 점수 계산
    scored = normalize_and_score(snap, "거래량증가율", "거래대금", 0.6, 0.4)

    # 1차 필터링: 복합 점수 기준 상위 종목
    candidates = scored.head(top_n)

    # 2차 필터링: 횡보 종목만 선별
    result = candidates[candidates["횡보여부"] == True].copy()

    if result.empty:
        logger.debug("trigger_afternoon_volume_surge_flat: 조건 충족 종목 없음")
        return pd.DataFrame()

    # 디버깅용 로그 추가
    for ticker in result.index[:3]:
        logger.debug(f"횡보주 디버깅 - {ticker}: 거래량증가율 {result.loc[ticker, '거래량증가율']:.2f}%, "
                     f"장중등락률 {result.loc[ticker, '장중등락률']:.2f}%, 전일대비등락률 {result.loc[ticker, '전일대비등락률']:.2f}%, "
                     f"거래량 {result.loc[ticker, '거래량']:,}주, 전일거래량 {prev.loc[ticker, '거래량']:,}주")

    logger.debug(f"거래량 증가 횡보 포착 종목 수: {len(result)}")
    return enhance_dataframe(result.sort_values("복합점수", ascending=False).head(3))

# --- 종합 선별 함수 ---
def select_final_tickers(triggers: dict) -> dict:
    """
    각 트리거에서 선별된 종목들을 종합하여 최종 종목을 선택합니다.
    - 최대 3개 종목 (여러 트리거에서 종합하여 채움)
    """
    final_result = {}

    # 1. 모든 결과 모으기 (종목 중복 제거)
    all_tickers = set()
    all_tickers_with_scores = []  # (트리거명, 종목코드, 복합점수) 튜플 리스트

    # 각 트리거별 결과와 복합점수 수집
    for name, df in triggers.items():
        if not df.empty:
            for ticker in df.index:
                if ticker not in all_tickers:
                    all_tickers.add(ticker)
                    score = df.loc[ticker, "복합점수"] if "복합점수" in df.columns else 0
                    all_tickers_with_scores.append((name, ticker, score))

    # 2. 복합 점수 기준 내림차순 정렬
    all_tickers_with_scores.sort(key=lambda x: x[2], reverse=True)

    # 3. 최대 3개까지 선택
    selected_tickers = set()  # 이미 선택된 종목 추적

    # 우선 각 트리거별로 최상위 종목 1개씩 선택 (최대 3개 트리거)
    for name, df in triggers.items():
        if not df.empty and len(selected_tickers) < 3:
            # 해당 트리거에서 가장 점수가 높은 종목
            top_ticker = df.index[0]
            if top_ticker not in selected_tickers:
                final_result[name] = df.loc[[top_ticker]]
                selected_tickers.add(top_ticker)

    # 만약 3개가 채워지지 않았다면, 점수 순으로 추가 종목 채우기
    if len(selected_tickers) < 3:
        for trigger_name, ticker, _ in all_tickers_with_scores:
            if ticker not in selected_tickers and len(selected_tickers) < 3:
                # 이미 해당 트리거가 결과에 있는지 확인
                if trigger_name in final_result:
                    # 기존 결과에 추가
                    final_result[trigger_name] = pd.concat([
                        final_result[trigger_name],
                        triggers[trigger_name].loc[[ticker]]
                    ])
                else:
                    # 새 트리거 결과 추가
                    final_result[trigger_name] = triggers[trigger_name].loc[[ticker]]
                selected_tickers.add(ticker)

    return final_result

# --- 배치 실행 함수 ---
def run_batch(trigger_time: str, log_level: str = "INFO", output_file: str = None, use_legacy_gap_trigger: bool = False):
    """
    trigger_time: "morning" 또는 "afternoon"
    log_level: "DEBUG", "INFO", "WARNING", 등 (운영 환경에서는 INFO 추천)
    output_file: 결과를 저장할 JSON 파일 경로 (선택 사항)
    use_legacy_gap_trigger: True면 기존 갭상승 트리거 사용, False면 새 눌림목 트리거 사용 (기본값: False)
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    ch.setLevel(numeric_level)
    logger.info(f"로그 레벨: {log_level.upper()}")

    today_str = datetime.datetime.today().strftime("%Y%m%d")
    trade_date = stock_api.get_nearest_business_day_in_a_week(today_str, prev=True)
    logger.info(f"배치 기준 거래일: {trade_date}")

    try:
        snapshot = get_snapshot(trade_date)
    except ValueError as e:
        logger.error(f"스냅샷 조회 실패: {e}")
        trade_date = stock_api.get_nearest_business_day_in_a_week(trade_date, prev=True)
        logger.info(f"재시도 배치 기준 거래일: {trade_date}")
        snapshot = get_snapshot(trade_date)

    prev_snapshot, prev_date = get_previous_snapshot(trade_date)
    logger.debug(f"전 거래일: {prev_date}")

    cap_df = get_market_cap_df(trade_date, market="ALL")
    logger.debug(f"시가총액 데이터 종목 수: {len(cap_df)}")

    # 오전 트리거용 멀티데이 스냅샷 조회 (눌림목 트리거 사용 시)
    multi_day_snapshots = None
    if trigger_time == "morning" and not use_legacy_gap_trigger:
        logger.info("멀티데이 스냅샷 조회 시작 (눌림목 트리거용)...")
        multi_day_snapshots = get_multi_day_snapshots(trade_date, days=5)

    if trigger_time == "morning":
        logger.info("=== 오전 배치 실행 ===")
        # 오전 트리거 실행 - cap_df 전달
        res1 = trigger_morning_volume_surge(trade_date, snapshot, prev_snapshot, cap_df)

        # 트리거2: 갭상승 vs 눌림목 선택
        if use_legacy_gap_trigger:
            logger.info("기존 갭상승 모멘텀 트리거 사용")
            res2 = trigger_morning_gap_up_momentum(trade_date, snapshot, prev_snapshot, cap_df)
            trigger2_name = "갭 상승 모멘텀 상위주"
        else:
            logger.info("새 눌림목 매수 트리거 사용 (trading_agents 궁합 최적화)")
            res2 = trigger_morning_pullback_buy(trade_date, snapshot, prev_snapshot, cap_df, multi_day_snapshots)
            trigger2_name = "눌림목 매수 대기 상위주"

        res3 = trigger_morning_value_to_cap_ratio(trade_date, snapshot, prev_snapshot, cap_df)
        triggers = {"거래량 급증 상위주": res1, trigger2_name: res2, "시총 대비 집중 자금 유입 상위주": res3}
    elif trigger_time == "afternoon":
        logger.info("=== 오후 배치 실행 ===")
        # 오후 트리거 실행 - cap_df 전달
        res1 = trigger_afternoon_daily_rise_top(trade_date, snapshot, prev_snapshot, cap_df)
        res2 = trigger_afternoon_closing_strength(trade_date, snapshot, prev_snapshot, cap_df)
        res3 = trigger_afternoon_volume_surge_flat(trade_date, snapshot, prev_snapshot, cap_df)
        triggers = {"일중 상승률 상위주": res1, "마감 강도 상위주": res2, "거래량 증가 상위 횡보주": res3}
    else:
        logger.error("잘못된 trigger_time 값입니다. 'morning' 또는 'afternoon'를 입력하세요.")
        return

    # 각 트리거별 결과 로깅
    for name, df in triggers.items():
        if df.empty:
            logger.info(f"{name}: 조건에 부합하는 종목이 없습니다.")
        else:
            logger.info(f"{name} 포착 종목 ({len(df)}개):")
            for ticker in df.index:
                종목명 = df.loc[ticker, "종목명"] if "종목명" in df.columns else ""
                logger.info(f"- {ticker} ({종목명})")

            # 상세 정보는 디버그 레벨에서만 출력
            logger.debug(f"상세 정보:\n{df}\n{'-'*40}")

    # 최종 선별 결과
    final_results = select_final_tickers(triggers)

    # 결과를 JSON으로 저장 (요청된 경우)
    if output_file:
        import json

        # 선별된 종목 상세 정보 포함
        output_data = {}

        # 트리거 타입별 처리
        for trigger_type, stocks_df in final_results.items():
            if not stocks_df.empty:
                if trigger_type not in output_data:
                    output_data[trigger_type] = []

                for ticker in stocks_df.index:
                    stock_info = {
                        "code": ticker,
                        "name": stocks_df.loc[ticker, "종목명"] if "종목명" in stocks_df.columns else "",
                        "current_price": float(stocks_df.loc[ticker, "종가"]) if "종가" in stocks_df.columns else 0,
                        "change_rate": float(stocks_df.loc[ticker, "전일대비등락률"]) if "전일대비등락률" in stocks_df.columns else 0,
                        "volume": int(stocks_df.loc[ticker, "거래량"]) if "거래량" in stocks_df.columns else 0,
                        "trade_value": float(stocks_df.loc[ticker, "거래대금"]) if "거래대금" in stocks_df.columns else 0,
                    }

                    # 트리거 타입별 특화 데이터 추가
                    if "거래량증가율" in stocks_df.columns and trigger_type == "거래량 급증 상위주":
                        stock_info["volume_increase"] = float(stocks_df.loc[ticker, "거래량증가율"])
                    elif "양봉일수" in stocks_df.columns and trigger_type == "눌림목 매수 대기 상위주":
                        # 눌림목 트리거 특화 데이터
                        stock_info["bullish_days"] = int(stocks_df.loc[ticker, "양봉일수"])
                        stock_info["gap_rate"] = float(stocks_df.loc[ticker, "갭률"]) if "갭률" in stocks_df.columns else 0
                        stock_info["intraday_change"] = float(stocks_df.loc[ticker, "장중등락률"]) if "장중등락률" in stocks_df.columns else 0
                        stock_info["support_distance"] = float(stocks_df.loc[ticker, "지지선거리"]) if "지지선거리" in stocks_df.columns else 0
                        stock_info["rsi_estimate"] = float(stocks_df.loc[ticker, "RSI추정"]) if "RSI추정" in stocks_df.columns else 50
                    elif "갭상승률" in stocks_df.columns:
                        stock_info["gap_rate"] = float(stocks_df.loc[ticker, "갭상승률"])
                    elif "거래대금비율" in stocks_df.columns:
                        stock_info["trade_value_ratio"] = float(stocks_df.loc[ticker, "거래대금비율"])
                        stock_info["market_cap"] = float(stocks_df.loc[ticker, "시가총액"])
                    elif "마감강도" in stocks_df.columns:
                        stock_info["closing_strength"] = float(stocks_df.loc[ticker, "마감강도"])

                    output_data[trigger_type].append(stock_info)

        # 실행 시간 및 메타데이터 추가
        output_data["metadata"] = {
            "run_time": datetime.datetime.now().isoformat(),
            "trigger_mode": trigger_time,
            "trade_date": trade_date,
            "trigger2_type": "pullback" if (trigger_time == "morning" and not use_legacy_gap_trigger) else "gap_momentum"
        }

        # JSON 파일 저장
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"선별 결과가 {output_file}에 저장되었습니다.")

    return final_results

if __name__ == "__main__":
    # 사용법: python trigger_batch.py morning [DEBUG|INFO|...] [--output 파일경로] [--legacy-gap]
    import argparse

    parser = argparse.ArgumentParser(description="트리거 배치 실행")
    parser.add_argument("mode", help="실행 모드 (morning 또는 afternoon)")
    parser.add_argument("log_level", nargs="?", default="INFO", help="로깅 레벨")
    parser.add_argument("--output", help="결과 저장 JSON 파일 경로")
    parser.add_argument("--legacy-gap", action="store_true",
                        help="기존 갭상승 트리거 사용 (기본값: 새 눌림목 트리거)")

    args = parser.parse_args()

    run_batch(args.mode, args.log_level, args.output, use_legacy_gap_trigger=args.legacy_gap)