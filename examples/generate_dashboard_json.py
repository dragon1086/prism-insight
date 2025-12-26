#!/usr/bin/env python3
"""
주식 포트폴리오 대시보드용 JSON 데이터 생성 스크립트
Cron으로 주기적 실행 (예: */5 * * * * - 5분마다)

Usage:
    python generate_dashboard_json.py

Output:
    ./dashboard/public/dashboard_data.json - 대시보드에서 사용할 모든 데이터
"""

import sqlite3
import json
import sys
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any
import logging
import os

# 로깅 설정 (다른 import 전에 먼저 설정)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 경로 설정 (다른 모듈 import 전에 먼저 설정)
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TRADING_DIR = PROJECT_ROOT / "trading"
sys.path.insert(0, str(SCRIPT_DIR))  # examples/ 폴더 추가 (translation_utils용)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TRADING_DIR))

# pykrx import for market index data
try:
    from pykrx import stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    logger.warning("pykrx 패키지가 설치되어 있지 않습니다. 시장 지수 데이터를 가져올 수 없습니다.")

# 번역 유틸리티 import (경로 설정 후에 import)
try:
    from translation_utils import DashboardTranslator
    TRANSLATION_AVAILABLE = True
except ImportError:
    TRANSLATION_AVAILABLE = False
    logger.warning("번역 유틸리티를 찾을 수 없습니다. 영어 번역이 비활성화됩니다.")

# 설정파일 로딩
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
try:
    with open(CONFIG_FILE, encoding="UTF-8") as f:
        _cfg = yaml.load(f, Loader=yaml.FullLoader)
except FileNotFoundError:
    _cfg = {"default_mode": "demo"}
    logger.warning(f"설정 파일을 찾을 수 없습니다: {CONFIG_FILE}. 기본 모드(demo)를 사용합니다.")

# 한국투자증권 API 모듈 import
try:
    from trading.domestic_stock_trading import DomesticStockTrading
    KIS_AVAILABLE = True
except ImportError:
    KIS_AVAILABLE = False
    logger.warning("한국투자증권 API 모듈을 찾을 수 없습니다. 실전투자 데이터를 가져올 수 없습니다.")


class DashboardDataGenerator:
    def __init__(self, db_path: str = None, output_path: str = None, trading_mode: str = None, enable_translation: bool = True):
        # db_path 기본값: 프로젝트 루트의 stock_tracking_db.sqlite
        if db_path is None:
            db_path = str(PROJECT_ROOT / "stock_tracking_db.sqlite")
        
        # output_path 기본값: examples/dashboard/public/dashboard_data.json
        if output_path is None:
            output_path = str(SCRIPT_DIR / "dashboard" / "public" / "dashboard_data.json")
        
        self.db_path = db_path
        self.output_path = output_path
        self.trading_mode = trading_mode if trading_mode is not None else _cfg.get("default_mode", "demo")
        self.enable_translation = enable_translation and TRANSLATION_AVAILABLE
        
        # 번역기 초기화
        if self.enable_translation:
            try:
                self.translator = DashboardTranslator(model="gpt-5-nano")
                logger.info("번역 기능이 활성화되었습니다.")
            except Exception as e:
                self.enable_translation = False
                logger.error(f"번역기 초기화 실패: {str(e)}")
        else:
            logger.info("번역 기능이 비활성화되었습니다.")
        
    def connect_db(self):
        """DB 연결"""
        return sqlite3.connect(self.db_path)
    
    def get_kis_trading_data(self) -> Dict[str, Any]:
        """한국투자증권 API로부터 실전투자 데이터 가져오기"""
        if not KIS_AVAILABLE:
            logger.warning("한국투자증권 API를 사용할 수 없습니다.")
            return {"portfolio": [], "account_summary": {}}
        
        try:
            logger.info(f"한국투자증권 데이터 조회 중... (모드: {self.trading_mode})")
            trader = DomesticStockTrading(mode=self.trading_mode)
            
            # 포트폴리오 데이터 조회
            portfolio = trader.get_portfolio()
            logger.info(f"포트폴리오 조회 완료: {len(portfolio)}개 종목")
            
            # 계좌 요약 데이터 조회
            account_summary = trader.get_account_summary()
            logger.info("계좌 요약 조회 완료")
            
            # 데이터 변환 (dashboard 형식에 맞게)
            formatted_portfolio = []
            for stock in portfolio:
                formatted_stock = {
                    "ticker": stock.get("stock_code", ""),
                    "name": stock.get("stock_name", ""),
                    "quantity": stock.get("quantity", 0),
                    "avg_price": stock.get("avg_price", 0),
                    "current_price": stock.get("current_price", 0),
                    "value": stock.get("eval_amount", 0),
                    "profit": stock.get("profit_amount", 0),
                    "profit_rate": stock.get("profit_rate", 0),
                    "sector": "실전투자",  # 섹터 정보가 없으면 기본값
                    "weight": 0  # 나중에 계산
                }
                formatted_portfolio.append(formatted_stock)
            
            # 포트폴리오 비중 계산
            total_value = sum(s["value"] for s in formatted_portfolio)
            if total_value > 0:
                for stock in formatted_portfolio:
                    stock["weight"] = (stock["value"] / total_value) * 100
            
            return {
                "portfolio": formatted_portfolio,
                "account_summary": account_summary
            }
            
        except Exception as e:
            logger.error(f"한국투자증권 데이터 조회 중 오류: {str(e)}")
            return {"portfolio": [], "account_summary": {}}
        
    def connect_db(self):
        """DB 연결"""
        return sqlite3.connect(self.db_path)
    
    def parse_json_field(self, json_str: str) -> Dict:
        """JSON 문자열 파싱 (에러 처리 포함)"""
        if not json_str:
            return {}
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 파싱 실패: {str(e)}")
            return {}
    
    def dict_from_row(self, row, cursor) -> Dict:
        """SQLite Row를 Dictionary로 변환"""
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    
    def get_stock_holdings(self, conn) -> List[Dict]:
        """현재 보유 종목 데이터 가져오기"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticker, company_name, buy_price, buy_date, current_price, 
                   last_updated, scenario, target_price, stop_loss
            FROM stock_holdings
            ORDER BY buy_date DESC
        """)
        
        holdings = []
        for row in cursor.fetchall():
            holding = self.dict_from_row(row, cursor)
            
            # scenario JSON 파싱
            holding['scenario'] = self.parse_json_field(holding.get('scenario', ''))
            
            # 수익률 계산
            buy_price = holding.get('buy_price', 0)
            current_price = holding.get('current_price', 0)
            if buy_price > 0:
                holding['profit_rate'] = ((current_price - buy_price) / buy_price) * 100
            else:
                holding['profit_rate'] = 0
            
            # 투자 기간 계산
            buy_date = holding.get('buy_date', '')
            if buy_date:
                try:
                    buy_dt = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
                    holding['holding_days'] = (datetime.now() - buy_dt).days
                except:
                    holding['holding_days'] = 0
            else:
                holding['holding_days'] = 0
            
            holdings.append(holding)
        
        return holdings
    
    def get_trading_history(self, conn) -> List[Dict]:
        """거래 이력 데이터 가져오기"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, ticker, company_name, buy_price, buy_date, sell_price, 
                   sell_date, profit_rate, holding_days, scenario
            FROM trading_history
            ORDER BY sell_date DESC
        """)
        
        history = []
        for row in cursor.fetchall():
            trade = self.dict_from_row(row, cursor)
            
            # scenario JSON 파싱
            trade['scenario'] = self.parse_json_field(trade.get('scenario', ''))
            
            history.append(trade)
        
        return history
    
    def get_watchlist_history(self, conn) -> List[Dict]:
        """관망 종목 데이터 가져오기"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, ticker, company_name, current_price, analyzed_date, 
                   buy_score, min_score, decision, skip_reason, target_price, 
                   stop_loss, investment_period, sector, scenario, 
                   portfolio_analysis, valuation_analysis, sector_outlook, 
                   market_condition, rationale
            FROM watchlist_history
            ORDER BY analyzed_date DESC
        """)
        
        watchlist = []
        for row in cursor.fetchall():
            item = self.dict_from_row(row, cursor)
            
            # scenario JSON 파싱
            item['scenario'] = self.parse_json_field(item.get('scenario', ''))
            
            watchlist.append(item)
        
        return watchlist
    
    def get_market_condition(self, conn) -> List[Dict]:
        """시장 상황 데이터 가져오기 - pykrx를 사용하여 Season2 시작(2025-09-29)부터 데이터 수집"""
        # Season2 시작일
        SEASON2_START_DATE = "20250929"

        if not PYKRX_AVAILABLE:
            logger.warning("pykrx를 사용할 수 없습니다. DB에서 데이터를 가져옵니다.")
            return self._get_market_condition_from_db(conn)

        try:
            # 오늘 날짜
            today = datetime.now().strftime("%Y%m%d")

            logger.info(f"pykrx로 시장 지수 데이터 조회 중... ({SEASON2_START_DATE} ~ {today})")

            # KOSPI 지수 데이터 가져오기 (ticker: 1001)
            kospi_df = stock.get_index_ohlcv_by_date(SEASON2_START_DATE, today, "1001")

            # KOSDAQ 지수 데이터 가져오기 (ticker: 2001)
            kosdaq_df = stock.get_index_ohlcv_by_date(SEASON2_START_DATE, today, "2001")

            if kospi_df.empty or kosdaq_df.empty:
                logger.warning("pykrx에서 지수 데이터를 가져오지 못했습니다. DB fallback.")
                return self._get_market_condition_from_db(conn)

            # 데이터 병합
            market_data = []

            for date_idx in kospi_df.index:
                date_str = date_idx.strftime("%Y-%m-%d")

                kospi_close = kospi_df.loc[date_idx, '종가']

                # KOSDAQ은 같은 날짜가 있을 때만 사용
                if date_idx in kosdaq_df.index:
                    kosdaq_close = kosdaq_df.loc[date_idx, '종가']
                else:
                    kosdaq_close = 0

                market_data.append({
                    'date': date_str,
                    'kospi_index': float(kospi_close),
                    'kosdaq_index': float(kosdaq_close),
                    'condition': 0,  # 기본값
                    'volatility': 0  # 기본값
                })

            # 날짜 오름차순 정렬 (차트에서 사용하기 위해)
            market_data.sort(key=lambda x: x['date'])

            logger.info(f"시장 지수 데이터 {len(market_data)}일치 수집 완료")
            return market_data

        except Exception as e:
            logger.error(f"pykrx 시장 지수 데이터 조회 중 오류: {str(e)}")
            return self._get_market_condition_from_db(conn)

    def _get_market_condition_from_db(self, conn) -> List[Dict]:
        """DB에서 시장 상황 데이터 가져오기 (fallback)"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, kospi_index, kosdaq_index, condition, volatility
            FROM market_condition
            ORDER BY date ASC
        """)

        market_data = []
        for row in cursor.fetchall():
            market = self.dict_from_row(row, cursor)
            market_data.append(market)

        return market_data
    
    def get_holding_decisions(self, conn) -> List[Dict]:
        """보유 종목 매도 판단 데이터 가져오기 (오늘 날짜만, 종목명 포함)"""
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")

            # stock_holdings와 LEFT JOIN하여 company_name도 함께 가져옴
            cursor.execute("""
                SELECT hd.id, hd.ticker, hd.decision_date, hd.decision_time, hd.current_price,
                       hd.should_sell, hd.sell_reason, hd.confidence, hd.technical_trend,
                       hd.volume_analysis, hd.market_condition_impact, hd.time_factor,
                       hd.portfolio_adjustment_needed, hd.adjustment_reason,
                       hd.new_target_price, hd.new_stop_loss, hd.adjustment_urgency,
                       hd.full_json_data, hd.created_at,
                       sh.company_name
                FROM holding_decisions hd
                LEFT JOIN stock_holdings sh ON hd.ticker = sh.ticker
                WHERE hd.decision_date = ?
                ORDER BY hd.created_at DESC
            """, (today,))

            decisions = []
            for row in cursor.fetchall():
                decision = self.dict_from_row(row, cursor)

                # full_json_data 파싱
                decision['full_json_data'] = self.parse_json_field(decision.get('full_json_data', ''))

                decisions.append(decision)

            return decisions
        except Exception as e:
            logger.warning(f"holding_decisions 테이블 조회 실패 (테이블이 없을 수 있음): {str(e)}")
            return []
    
    def calculate_portfolio_summary(self, holdings: List[Dict]) -> Dict:
        """포트폴리오 요약 통계 계산"""
        if not holdings:
            return {
                'total_stocks': 0,
                'total_profit': 0,
                'avg_profit_rate': 0,
                'slot_usage': '0/10',
                'slot_percentage': 0
            }
        
        total_profit = sum(h.get('profit_rate', 0) for h in holdings)
        avg_profit_rate = total_profit / len(holdings) if holdings else 0
        
        # 섹터별 분포
        sector_distribution = {}
        for h in holdings:
            scenario = h.get('scenario', {})
            sector = scenario.get('sector', '기타')
            sector_distribution[sector] = sector_distribution.get(sector, 0) + 1
        
        # 투자기간별 분포
        period_distribution = {}
        for h in holdings:
            scenario = h.get('scenario', {})
            period = scenario.get('investment_period', '단기')
            period_distribution[period] = period_distribution.get(period, 0) + 1
        
        return {
            'total_stocks': len(holdings),
            'total_profit': total_profit,
            'avg_profit_rate': avg_profit_rate,
            'slot_usage': f'{len(holdings)}/10',
            'slot_percentage': (len(holdings) / 10) * 100,
            'sector_distribution': sector_distribution,
            'period_distribution': period_distribution
        }
    
    def calculate_trading_summary(self, history: List[Dict]) -> Dict:
        """거래 이력 요약 통계 계산"""
        if not history:
            return {
                'total_trades': 0,
                'win_count': 0,
                'loss_count': 0,
                'win_rate': 0,
                'avg_profit_rate': 0,
                'avg_holding_days': 0
            }
        
        win_count = sum(1 for h in history if h.get('profit_rate', 0) > 0)
        loss_count = len(history) - win_count
        win_rate = (win_count / len(history)) * 100 if history else 0
        
        avg_profit_rate = sum(h.get('profit_rate', 0) for h in history) / len(history)
        avg_holding_days = sum(h.get('holding_days', 0) for h in history) / len(history)
        
        return {
            'total_trades': len(history),
            'win_count': win_count,
            'loss_count': loss_count,
            'win_rate': win_rate,
            'avg_profit_rate': avg_profit_rate,
            'avg_holding_days': avg_holding_days
        }
    
    def get_ai_decision_summary(self, decisions: List[Dict]) -> Dict:
        """AI 판단 요약 통계"""
        if not decisions:
            return {
                'total_decisions': 0,
                'sell_signals': 0,
                'hold_signals': 0,
                'adjustment_needed': 0,
                'avg_confidence': 0
            }
        
        sell_signals = sum(1 for d in decisions if d.get('should_sell', False))
        hold_signals = len(decisions) - sell_signals
        adjustment_needed = sum(1 for d in decisions if d.get('portfolio_adjustment_needed', False))
        
        avg_confidence = sum(d.get('confidence', 0) for d in decisions) / len(decisions) if decisions else 0
        
        return {
            'total_decisions': len(decisions),
            'sell_signals': sell_signals,
            'hold_signals': hold_signals,
            'adjustment_needed': adjustment_needed,
            'avg_confidence': avg_confidence
        }
    
    def calculate_real_trading_summary(self, real_portfolio: List[Dict], account_summary: Dict) -> Dict:
        """실전투자 요약 통계 계산 (현금 정보 포함)"""
        if not real_portfolio and not account_summary:
            return {
                'total_stocks': 0,
                'total_eval_amount': 0,
                'total_profit_amount': 0,
                'total_profit_rate': 0,
                'deposit': 0,
                'total_cash': 0,
                'available_amount': 0
            }

        # total_cash (D+2 포함)를 사용하고, 없으면 deposit으로 fallback
        total_cash = account_summary.get('total_cash', account_summary.get('deposit', 0))

        return {
            'total_stocks': len(real_portfolio),
            'total_eval_amount': account_summary.get('total_eval_amount', 0),
            'total_profit_amount': account_summary.get('total_profit_amount', 0),
            'total_profit_rate': account_summary.get('total_profit_rate', 0),
            'deposit': account_summary.get('deposit', 0),  # 예수금 (D+0)
            'total_cash': total_cash,  # 총 현금 (D+2 포함)
            'available_amount': account_summary.get('available_amount', 0)
        }

    def calculate_cumulative_realized_profit(self, trading_history: List[Dict], market_data: List[Dict]) -> List[Dict]:
        """
        날짜별 프리즘 시뮬레이터 누적 실현 수익률 계산

        - 10개 슬롯 기준으로 수익률 계산 (매도된 종목의 profit_rate 합계 / 10)
        - 각 시장 거래일에 맞춰 해당일까지의 누적 수익률 반환
        """
        SEASON2_START_DATE = "2025-09-29"

        # 거래 이력을 날짜 기준으로 정렬 (sell_date 기준)
        sorted_trades = sorted(
            [t for t in trading_history if t.get('sell_date')],
            key=lambda x: x.get('sell_date', '')
        )

        # 날짜별 누적 수익률 계산
        cumulative_profit = 0.0
        cumulative_by_date = {}

        for trade in sorted_trades:
            sell_date = trade.get('sell_date', '')
            if sell_date:
                # datetime 형식일 수 있으므로 날짜만 추출
                if ' ' in sell_date:
                    sell_date = sell_date.split(' ')[0]

                profit_rate = trade.get('profit_rate', 0)
                cumulative_profit += profit_rate
                cumulative_by_date[sell_date] = cumulative_profit

        # 시장 데이터의 각 날짜에 맞춰 프리즘 수익률 데이터 생성
        result = []
        last_cumulative = 0.0

        for market_item in market_data:
            date = market_item.get('date', '')

            if date < SEASON2_START_DATE:
                continue

            # 해당 날짜까지의 누적 실현 수익률 찾기
            for trade_date, cum_profit in cumulative_by_date.items():
                if trade_date <= date:
                    last_cumulative = cum_profit

            # 10개 슬롯 기준 수익률 계산
            prism_return = last_cumulative / 10

            result.append({
                'date': date,
                'cumulative_realized_profit': last_cumulative,
                'prism_simulator_return': prism_return
            })

        return result
    
    def get_operating_costs(self) -> Dict:
        """프로젝트 운영 비용 데이터 반환"""
        # 2025년 11월 기준 운영 비용
        return {
            'server_hosting': 31.68,
            'openai_api': 113.40,
            'anthropic_api': 16.52,
            'firecrawl_api': 19.0,
            'perplexity_api': 9.9,
            'month': '2025-11'
        }
    
    def get_jeoningu_data(self, conn) -> Dict:
        """전인구 역발상 투자 실험실 데이터 가져오기"""
        try:
            logger.info("전인구 실험실 데이터 수집 중...")
            
            # 실시간 가격 조회를 위한 import
            try:
                sys.path.insert(0, str(PROJECT_ROOT / "events"))
                from jeoningu_price_fetcher import get_current_price
                PRICE_FETCHER_AVAILABLE = True
            except ImportError:
                PRICE_FETCHER_AVAILABLE = False
                logger.warning("jeoningu_price_fetcher를 찾을 수 없습니다. 실시간 가격 조회가 비활성화됩니다.")
            
            # 1. 전체 거래 이력 조회
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM jeoningu_trades
                ORDER BY id ASC
            """)
            
            trade_history = []
            for row in cursor.fetchall():
                trade = self.dict_from_row(row, cursor)
                trade_history.append(trade)
            
            logger.info(f"전인구 거래 이력: {len(trade_history)}건")
            
            # 2. 현재 포지션 확인
            current_position = None
            latest_balance = 10000000  # 기본값
            initial_capital = 10000000
            
            if trade_history:
                # 마지막 BUY 찾기
                last_buy = None
                for trade in reversed(trade_history):
                    if trade.get('trade_type') == 'BUY':
                        last_buy = trade
                        break
                
                # 해당 BUY에 연결된 SELL이 있는지 확인
                if last_buy:
                    has_sell = any(
                        t.get('trade_type') == 'SELL' and 
                        t.get('related_buy_id') == last_buy.get('id')
                        for t in trade_history
                    )
                    
                    if not has_sell:
                        # 실시간 현재가 조회
                        stock_code = last_buy.get('stock_code')
                        buy_price = last_buy.get('price', 0)
                        quantity = last_buy.get('quantity', 0)
                        buy_amount = last_buy.get('amount', 0)
                        
                        if PRICE_FETCHER_AVAILABLE and stock_code:
                            try:
                                current_price = get_current_price(stock_code)
                                logger.info(f"실시간 현재가 조회: {stock_code} = {current_price:,}원")
                            except Exception as e:
                                logger.warning(f"현재가 조회 실패: {e}, 매수가 사용")
                                current_price = buy_price
                        else:
                            current_price = buy_price
                        
                        # 평가금액 및 손익 계산
                        current_value = quantity * current_price
                        unrealized_pl = current_value - buy_amount
                        unrealized_pl_pct = (unrealized_pl / buy_amount * 100) if buy_amount > 0 else 0
                        
                        current_position = {
                            'stock_code': stock_code,
                            'stock_name': last_buy.get('stock_name'),
                            'quantity': quantity,
                            'buy_price': buy_price,
                            'buy_amount': buy_amount,
                            'current_price': current_price,
                            'current_value': current_value,
                            'unrealized_pl': unrealized_pl,
                            'unrealized_pl_pct': unrealized_pl_pct,
                            'buy_date': last_buy.get('analyzed_date'),
                            'video_id': last_buy.get('video_id'),
                            'video_title': last_buy.get('video_title')
                        }
                
                # 최신 잔액
                latest_balance = trade_history[-1].get('balance_after', initial_capital)
            
            # 3. 성과 지표 계산
            sell_trades = [t for t in trade_history if t.get('trade_type') == 'SELL']
            
            winning_trades = sum(1 for t in sell_trades if t.get('profit_loss', 0) > 0)
            losing_trades = sum(1 for t in sell_trades if t.get('profit_loss', 0) < 0)
            draw_trades = sum(1 for t in sell_trades if t.get('profit_loss', 0) == 0)
            total_trades = len(sell_trades)
            
            # 승률 계산: 무승부 제외하고 승/(승+패)
            decided_trades = winning_trades + losing_trades
            win_rate = (winning_trades / decided_trades * 100) if decided_trades > 0 else 0
            
            # 실현손익 계산
            realized_pl = sum(t.get('profit_loss', 0) for t in sell_trades)
            
            # 미실현손익 (현재 포지션)
            unrealized_pl = current_position.get('unrealized_pl', 0) if current_position else 0
            
            # 총 손익 = 실현 + 미실현
            total_pl = realized_pl + unrealized_pl
            cumulative_return = (total_pl / initial_capital * 100) if initial_capital > 0 else 0
            
            # 총 자산
            if current_position:
                total_assets = realized_pl + current_position.get('current_value', 0)
            else:
                total_assets = latest_balance
            
            avg_return_per_trade = 0
            if sell_trades:
                avg_return_per_trade = sum(t.get('profit_loss_pct', 0) for t in sell_trades) / len(sell_trades)
            
            # 4. 타임라인 데이터 생성 (영상별)
            timeline = []
            for trade in trade_history:
                timeline_entry = {
                    'video_id': trade.get('video_id'),
                    'video_title': trade.get('video_title'),
                    'video_date': trade.get('video_date'),
                    'video_url': trade.get('video_url'),
                    'analyzed_date': trade.get('analyzed_date'),
                    'jeon_sentiment': trade.get('jeon_sentiment'),
                    'jeon_reasoning': trade.get('jeon_reasoning'),
                    'contrarian_action': trade.get('contrarian_action'),
                    'trade_type': trade.get('trade_type'),
                    'stock_code': trade.get('stock_code'),
                    'stock_name': trade.get('stock_name'),
                    'notes': trade.get('notes'),
                    'profit_loss': trade.get('profit_loss'),
                    'profit_loss_pct': trade.get('profit_loss_pct')
                }
                timeline.append(timeline_entry)
            
            # 5. 누적 수익률 차트 데이터
            cumulative_chart = []
            for trade in trade_history:
                if trade.get('cumulative_return_pct') is not None:
                    cumulative_chart.append({
                        'date': trade.get('analyzed_date'),
                        'cumulative_return': trade.get('cumulative_return_pct'),
                        'balance': trade.get('balance_after')
                    })
            
            return {
                'enabled': True,
                'summary': {
                    'total_trades': total_trades,
                    'winning_trades': winning_trades,
                    'losing_trades': losing_trades,
                    'draw_trades': draw_trades,
                    'win_rate': win_rate,
                    'cumulative_return': cumulative_return,
                    'realized_pl': realized_pl,
                    'unrealized_pl': unrealized_pl,
                    'total_pl': total_pl,
                    'total_assets': total_assets,
                    'avg_return_per_trade': avg_return_per_trade,
                    'initial_capital': initial_capital,
                    'current_balance': latest_balance
                },
                'current_position': current_position,
                'timeline': timeline,
                'cumulative_chart': cumulative_chart,
                'trade_history': trade_history
            }
            
        except sqlite3.OperationalError as e:
            if "no such table: jeoningu_trades" in str(e):
                logger.warning("전인구 실험실 테이블이 없습니다. 비활성화 상태로 반환합니다.")
                return {
                    'enabled': False,
                    'message': '전인구 실험실 데이터가 아직 생성되지 않았습니다.'
                }
            else:
                raise
        except Exception as e:
            logger.error(f"전인구 실험실 데이터 수집 중 오류: {str(e)}")
            return {
                'enabled': False,
                'error': str(e)
            }
    
    def generate(self) -> Dict:
        """전체 대시보드 데이터 생성"""
        try:
            logger.info(f"DB 연결 중: {self.db_path}")
            conn = self.connect_db()
            conn.row_factory = sqlite3.Row
            
            logger.info("데이터 수집 시작...")
            
            # 각 테이블 데이터 수집
            holdings = self.get_stock_holdings(conn)
            trading_history = self.get_trading_history(conn)
            watchlist = self.get_watchlist_history(conn)
            market_condition = self.get_market_condition(conn)
            holding_decisions = self.get_holding_decisions(conn)
            
            # 한국투자증권 실전투자 데이터 수집
            kis_data = self.get_kis_trading_data()
            real_portfolio = kis_data.get("portfolio", [])
            account_summary = kis_data.get("account_summary", {})
            
            # 전인구 실험실 데이터 수집
            jeoningu_lab = self.get_jeoningu_data(conn)
            
            # 요약 통계 계산
            portfolio_summary = self.calculate_portfolio_summary(holdings)
            trading_summary = self.calculate_trading_summary(trading_history)
            ai_decision_summary = self.get_ai_decision_summary(holding_decisions)
            
            # 실전투자 요약 계산
            real_trading_summary = self.calculate_real_trading_summary(real_portfolio, account_summary)

            # 날짜별 프리즘 시뮬레이터 누적 수익률 계산
            prism_performance = self.calculate_cumulative_realized_profit(
                trading_history, market_condition
            )

            # 전체 데이터 구성
            dashboard_data = {
                'generated_at': datetime.now().isoformat(),
                'trading_mode': self.trading_mode,
                'summary': {
                    'portfolio': portfolio_summary,
                    'trading': trading_summary,
                    'ai_decisions': ai_decision_summary,
                    'real_trading': real_trading_summary
                },
                'holdings': holdings,
                'real_portfolio': real_portfolio,  # 실전투자 포트폴리오 추가
                'account_summary': account_summary,  # 계좌 요약 추가
                'operating_costs': self.get_operating_costs(),  # 운영 비용 추가
                'trading_history': trading_history,
                'watchlist': watchlist,
                'market_condition': market_condition,
                'prism_performance': prism_performance,  # 날짜별 프리즘 시뮬레이터 수익률 추가
                'holding_decisions': holding_decisions,
                'jeoningu_lab': jeoningu_lab  # 전인구 실험실 데이터 추가
            }
            
            conn.close()
            
            logger.info(f"데이터 수집 완료: 보유 {len(holdings)}개, 실전 {len(real_portfolio)}개, 거래 {len(trading_history)}건, 관망 {len(watchlist)}개")
            if jeoningu_lab.get('enabled'):
                logger.info(f"전인구 실험실: 거래 {jeoningu_lab['summary']['total_trades']}건, 수익률 {jeoningu_lab['summary']['cumulative_return']:.2f}%")
            
            return dashboard_data
            
        except Exception as e:
            logger.error(f"데이터 생성 중 오류: {str(e)}")
            raise
    
    def save(self, data: Dict, output_file: str = None):
        """JSON 파일로 저장"""
        try:
            if output_file is None:
                output_file = self.output_path
            
            output_path = Path(output_file)
            
            # 디렉토리가 없으면 생성
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            file_size = output_path.stat().st_size
            logger.info(f"JSON 파일 저장 완료: {output_path} ({file_size:,} bytes)")
            
        except Exception as e:
            logger.error(f"파일 저장 중 오류: {str(e)}")
            raise


def main():
    """메인 실행 함수"""
    import argparse
    import asyncio
    
    parser = argparse.ArgumentParser(description="대시보드 JSON 생성")
    parser.add_argument("--mode", choices=["demo", "real"], 
                       help=f"트레이딩 모드 (demo: 모의투자, real: 실전투자, 기본값: {_cfg.get('default_mode', 'demo')})")
    parser.add_argument("--no-translation", action="store_true",
                       help="영어 번역 비활성화 (한국어 버전만 생성)")
    
    args = parser.parse_args()
    
    async def async_main():
        try:
            logger.info("=== 대시보드 JSON 생성 시작 ===")
            
            enable_translation = not args.no_translation
            generator = DashboardDataGenerator(
                trading_mode=args.mode,
                enable_translation=enable_translation
            )
            
            # 한국어 데이터 생성
            logger.info("한국어 데이터 생성 중...")
            dashboard_data_ko = generator.generate()
            
            # 한국어 JSON 파일 저장
            ko_output = str(SCRIPT_DIR / "dashboard" / "public" / "dashboard_data.json")
            generator.save(dashboard_data_ko, ko_output)
            
            # 영어 번역 및 저장
            if generator.enable_translation:
                try:
                    logger.info("영어 번역 시작...")
                    dashboard_data_en = await generator.translator.translate_dashboard_data(dashboard_data_ko)
                    
                    # 영어 JSON 파일 저장
                    en_output = str(SCRIPT_DIR / "dashboard" / "public" / "dashboard_data_en.json")
                    generator.save(dashboard_data_en, en_output)
                    
                    logger.info("영어 번역 완료!")
                except Exception as e:
                    logger.error(f"영어 번역 중 오류 발생: {str(e)}")
                    logger.warning("한국어 버전만 생성되었습니다.")
            else:
                logger.info("번역 기능이 비활성화되어 한국어 버전만 생성되었습니다.")
            
            logger.info("=== 대시보드 JSON 생성 완료 ===")
            
        except Exception as e:
            logger.error(f"실행 중 오류 발생: {str(e)}")
            exit(1)
    
    # asyncio 이벤트 루프 실행
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
