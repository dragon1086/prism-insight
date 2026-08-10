#!/usr/bin/env python3
"""Stance 서버 기동 스크립트 — PRISM 의 KIS 시세를 물려서 띄운다.

`stance/` 는 시세를 어디서 가져오는지 모른다. 이 파일이 그것을 주입한다.
그래서 `stance/` 는 여전히 PRISM 과 무관하게 분리 가능한 상태로 남는다.

실행
    STANCE_DB=/var/lib/prism-stance/ledger.db python -m stance_server

    # 시세 없이 (모든 선언이 보류로 쌓인다 — 스키마 확인용)
    STANCE_QUOTES=none python -m stance_server

환경변수
    STANCE_DB       원장 파일 경로. 반드시 영속 경로를 줄 것
    STANCE_HOST     기본 127.0.0.1
    STANCE_PORT     기본 8800
    STANCE_QUOTES   kis (기본) | none
    STANCE_KIS_MODE real (기본) | vps

⚠️ 워커를 늘리지 말 것. 장부를 프로세스 메모리에 캐시하므로 워커가 여럿이면
   한쪽에서 접수한 선언이 다른 쪽 장부에 반영되지 않아 현금 판정이 어긋난다.
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=os.getenv("STANCE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
logger = logging.getLogger("stance_server")


def build_quote_provider():
    """KIS 시세 제공자를 만든다. 실패하면 None 을 돌려준다.

    시세를 못 붙여도 서버는 뜬다 — 선언은 보류(pending)로 원장에 쌓이고,
    나중에 시세가 붙으면 재계산할 수 있다. 접수 자체를 막는 것이 더 나쁘다.
    """
    mode = os.getenv("STANCE_QUOTES", "kis").lower()
    if mode == "none":
        logger.warning("시세 제공자 없이 기동합니다 — 모든 선언이 보류로 쌓입니다.")
        return None

    try:
        from prism_core.stance_quotes import KisQuoteProvider
        from trading.domestic_stock_trading import DomesticStockTrading

        client = DomesticStockTrading(mode=os.getenv("STANCE_KIS_MODE", "real"))
        logger.info("KIS 시세 제공자를 연결했습니다.")
        return KisQuoteProvider(client)
    except Exception:
        logger.exception(
            "KIS 시세 제공자를 만들지 못했습니다 — 선언은 보류로 접수됩니다. "
            "자격증명과 trading/config 설정을 확인하십시오."
        )
        return None


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        logger.error("uvicorn 이 필요합니다:  pip install fastapi uvicorn")
        return 1

    from stance.server import api
    from stance.server.ledger import Ledger
    from stance.server.service import StanceService

    db = os.getenv("STANCE_DB") or api.DEFAULT_DB
    if db == ":memory:":
        logger.error("STANCE_DB=:memory: — 프로세스가 죽으면 원장이 사라집니다.")

    service = StanceService(ledger=Ledger(db), quote_provider=build_quote_provider())
    api.set_service(service)
    logger.info("원장: %s", db)

    uvicorn.run(
        api.app,
        host=os.getenv("STANCE_HOST", "127.0.0.1"),
        port=int(os.getenv("STANCE_PORT", "8800")),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
