"""
Standalone multi-turn tool-call verification for the OAuth/Codex backend.

Drives StockTrackingAgent._extract_trading_scenario() for ONE ticker through the
full Responses-API tool-call loop (time -> kospi_kosdaq -> sqlite, etc.) WITHOUT
executing any trade or writing the holdings/journal DB. Confirms we get a real
scenario JSON (not the default_scenario fallback) and that no
"No tool call found for function call output" / 400 occurs under store=False.

Run on the server (OAuth account active):
    /root/.pyenv/shims/python tools/verify_oauth_multiturn_scenario.py
"""
import asyncio
import json
import sys

from stock_tracking_agent import StockTrackingAgent, app

# Minimal but realistic KR report so the CAN SLIM agent must call market tools
# (time-get_current_time, kospi_kosdaq-get_index_ohlcv/get_stock_ohlcv) to decide.
REPORT = """
# 삼성전자(005930) 분석 리포트

## 종목 개요
- 종목명: 삼성전자
- 종목코드: 005930
- 업종: 전기·전자

## 최근 동향
- 최근 거래대금 상위권 진입, 거래량 급증.
- 외국인/기관 동반 순매수 전환.
- 신고가 부근 돌파 시도 중, 모멘텀 활성.

## 펀더멘털
- 반도체 업황 회복 사이클 초입.
- 영업이익 전년比 대폭 증가 가이던스.

(이 리포트에는 KOSPI 지수 20일 데이터/분산일 정보가 없으므로 시장 레짐 판단을
위해 kospi_kosdaq 도구로 지수 데이터를 직접 조회해야 함.)
"""


async def main():
    async with app.run():
        agent = StockTrackingAgent(db_path="stock_tracking_db.sqlite")
        await agent.initialize(language="ko")
        try:
            scenario = await agent._extract_trading_scenario(
                report_content=REPORT,
                rank_change_msg="거래대금 순위 전일 대비 +30위 상승",
                ticker="005930",
                sector="전기·전자",
                trigger_type="Volume Surge Top Stocks",
                trigger_mode="morning",
            )
        finally:
            if agent.conn:
                agent.conn.close()

    default_decision = "No entry"
    decision = scenario.get("decision", "")
    is_default = (
        scenario.get("sector") in (None, "Unknown")
        and decision == default_decision
        and not scenario.get("scenario_summary")
        and not scenario.get("rationale")
    )

    print("===== SCENARIO RESULT =====")
    print(json.dumps(scenario, ensure_ascii=False, indent=2)[:1500])
    print("===========================")
    print(f"decision={decision!r} sector={scenario.get('sector')!r}")
    if is_default:
        print("RESULT: FAIL - default_scenario fallback (tool-call loop broke)")
        sys.exit(1)
    print("RESULT: PASS - real scenario JSON produced via multi-turn tool calls")


if __name__ == "__main__":
    asyncio.run(main())
