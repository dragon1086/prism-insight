"""리포트 에이전트용 MCP 서버 — 소스 체인 위에서 돈다.

## 왜 만들었나

리포트 생성 에이전트는 `kospi_kosdaq` MCP 서버(PyPI `kospi-kosdaq-stock-server`)로
시세·수급을 받는다. 그 서버는 **KRX Data Marketplace 를 카카오 로그인으로 스크래핑**
한다(`KAKAO_ID`/`KAKAO_PW`). pykrx 폴백도 결국 같은 KRX 다.

2026-08-05 KRX 가 db-server IP 를 막자 리포트에서 **기술적 지표가 통째로 비었다**:

    5·20·60·120일 이동평균   산출 불가
    RSI(14일)                산출 불가
    MACD / 시그널선          산출 불가
    볼린저밴드(20일)         산출 불가
    지지선·저항선            산출 불가
    투자자별 순매수 수량     확인 불가

차트는 정상으로 그려졌다. `cores/stock_chart.py` 는 체인을 타는데 **리포트 텍스트만
이 MCP 경로를 타기 때문**이다. 스크리닝 쪽 KRX 호출을 42→4회로 줄여놓고도 정작
상품인 리포트는 그대로였다.

## 무엇이 다른가

도구 이름·인자·반환 형태를 기존 서버와 **동일하게** 맞췄다. 서버 이름도
`kospi_kosdaq` 그대로 쓰므로 `report_generator` 의 프롬프트는 한 글자도 안 바뀐다.
바뀌는 것은 데이터가 어디서 오느냐뿐이다:

    기존   KRX Data Marketplace 스크래핑 (카카오 로그인)
    지금   cores.market_data 체인 (기본 kis -> fdr -> krx)

`PRISM_MARKET_DATA_SOURCES` 로 순서를 바꾼다. KRX 자격증명이 필요 없다.

## 실패를 숨기지 않는다

체인이 전 소스 소진 시 빈 프레임을 주므로, 여기서는 그것을 `{"error": ...}` 로
바꿔 돌려준다. 빈 dict 를 그냥 주면 LLM 이 "데이터가 없다"와 "0 이다"를 구분하지
못한다 — 2026-08-05 의 MA200 건이 정확히 그 실패 모드였다(없는 줄을 '언급할 가치
없음'으로 읽고 스스로 지어냈다).

실행:

    python -m cores.market_data.mcp_server
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Union

from mcp.server.fastmcp import FastMCP

# 이 파일은 MCP 서버로 **별도 프로세스에서** 실행된다. 리포 루트가 sys.path 에
# 없으면 cores 를 못 찾는다.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("market_data_mcp")


def _load_repo_env() -> None:
    """리포의 ``.env`` 를 읽어 KIS 자격증명을 확보한다.

    MCP 자식 프로세스가 부모의 환경을 온전히 물려받는다는 보장이 없다 —
    레지스트리가 env 블록을 명시적으로 넘긴다. 자격증명이 없으면 체인이 **조용히**
    KIS 를 건너뛰고 다음 소스로 내려간다. 그 조용함이 문제라서, 실패하면 반드시
    말하게 둔다(삼키고 pass 하면 8/4 장애를 숨긴 것과 같은 모양이 된다).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("python-dotenv 없음 — 상속된 환경변수만 사용한다")
        return

    env_path = os.path.join(_REPO_ROOT, ".env")
    if not os.path.exists(env_path):
        logger.warning("%s 없음 — 상속된 환경변수만 사용한다", env_path)
        return

    try:
        load_dotenv(env_path)
    except OSError as exc:
        logger.warning("%s 읽기 실패(%s) — 상속된 환경변수만 사용한다", env_path, exc)


_load_repo_env()

from cores.market_data import (  # noqa: E402
    get_index_ohlcv_by_date,
    get_market_cap_by_date,
    get_market_fundamental_by_date,
    get_market_ohlcv_by_date,
    get_market_ticker_name,
    get_market_trading_volume_by_date,
)

mcp = FastMCP("kospi_kosdaq")


# --- 입력 정규화 (기존 서버와 동일 규칙) -------------------------------------


def validate_date(date_str: Union[str, int]) -> str:
    """YYYYMMDD 8자리로 정규화한다. LLM 이 정수나 하이픈을 섞어 보낸다."""
    text = str(date_str).strip().replace("-", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"date must be YYYYMMDD, got {date_str!r}")
    return text


def validate_ticker(ticker_str: Union[str, int]) -> str:
    """6자리 종목코드로 정규화한다. 정수로 오면 앞의 0 이 날아가 있다."""
    text = str(ticker_str).strip()
    if text.isdigit():
        return text.zfill(6)
    return text


def _frame_to_dated_dict(df, reverse: bool = True) -> Dict[str, Any]:
    """DataFrame 을 날짜 키 dict 로 바꾼다 (기존 서버의 출력 형태)."""
    if df is None or df.empty:
        return {}

    formatted = {}
    for key, row in df.to_dict(orient="index").items():
        label = key.strftime("%Y-%m-%d") if hasattr(key, "strftime") else str(key)
        formatted[label] = row

    return dict(sorted(formatted.items(), reverse=reverse))


def _answer(df, what: str) -> Dict[str, Any]:
    """빈 결과를 침묵이 아니라 명시적 오류로 돌려준다.

    LLM 에게 빈 dict 는 '없다'가 아니라 '언급할 가치 없다'로 읽힌다. 그러면
    스스로 채운다 — 2026-08-05 MA200 건이 그랬다. 그래서 사유를 문장으로 준다.
    """
    payload = _frame_to_dated_dict(df)
    if payload:
        return payload
    return {
        "error": (
            f"{what} 데이터를 어떤 소스에서도 받지 못했습니다. "
            f"이 값이 0 이라는 뜻이 아니라 조회 불가라는 뜻이며, "
            f"추정치로 서술하지 마십시오."
        )
    }


def _guard(what: str, fn, *args, **kwargs) -> Dict[str, Any]:
    try:
        return _answer(fn(*args, **kwargs), what)
    except Exception as exc:  # noqa: BLE001 - MCP 도구는 예외를 못 넘긴다
        logger.error("%s failed: %s", what, exc)
        return {"error": f"{what} 조회 실패: {exc}"}


# --- MCP Tools ---------------------------------------------------------------


@mcp.tool()
def get_stock_ohlcv(
    fromdate: Union[str, int],
    todate: Union[str, int],
    ticker: Union[str, int],
    adjusted: bool = True,
) -> Dict[str, Any]:
    """Retrieves OHLCV (Open/High/Low/Close/Volume) data for a specific stock.

    Args:
        fromdate (str): Start date for retrieval (YYYYMMDD)
        todate   (str): End date for retrieval (YYYYMMDD)
        ticker   (str): Stock ticker symbol
        adjusted (bool, optional): Whether to use adjusted prices. Defaults to True.

    Returns:
        Dict with date keys containing OHLCV data.
    """
    return _guard(
        f"{validate_ticker(ticker)} OHLCV",
        get_market_ohlcv_by_date,
        validate_date(fromdate),
        validate_date(todate),
        validate_ticker(ticker),
        adjusted=adjusted,
    )


@mcp.tool()
def get_stock_market_cap(
    fromdate: Union[str, int],
    todate: Union[str, int],
    ticker: Union[str, int],
) -> Dict[str, Any]:
    """Retrieves market capitalization data for a specific stock.

    Args:
        fromdate (str): Start date for retrieval (YYYYMMDD)
        todate   (str): End date for retrieval (YYYYMMDD)
        ticker   (str): Stock ticker symbol

    Returns:
        Dict with date keys containing market cap data.
    """
    return _guard(
        f"{validate_ticker(ticker)} 시가총액",
        get_market_cap_by_date,
        validate_date(fromdate),
        validate_date(todate),
        validate_ticker(ticker),
    )


@mcp.tool()
def get_stock_fundamental(
    fromdate: Union[str, int],
    todate: Union[str, int],
    ticker: Union[str, int],
) -> Dict[str, Any]:
    """Retrieves fundamental data (PER/PBR/Dividend Yield) for a specific stock.

    Args:
        fromdate (str): Start date for retrieval (YYYYMMDD)
        todate   (str): End date for retrieval (YYYYMMDD)
        ticker   (str): Stock ticker symbol

    Returns:
        Dict with date keys containing fundamental data.
    """
    return _guard(
        f"{validate_ticker(ticker)} 펀더멘털(PER/PBR)",
        get_market_fundamental_by_date,
        validate_date(fromdate),
        validate_date(todate),
        validate_ticker(ticker),
    )


@mcp.tool()
def get_stock_trading_volume(
    fromdate: Union[str, int],
    todate: Union[str, int],
    ticker: Union[str, int],
    detail: bool = False,
) -> Dict[str, Any]:
    """Retrieves trading volume by investor type for a specific stock.

    Args:
        fromdate (str): Start date for retrieval (YYYYMMDD)
        todate   (str): End date for retrieval (YYYYMMDD)
        ticker   (str): Stock ticker symbol
        detail   (bool, optional): Unused; kept for call compatibility.

    Returns:
        Dict with date keys containing investor-type net buying data.
    """
    return _guard(
        f"{validate_ticker(ticker)} 투자자별 수급",
        get_market_trading_volume_by_date,
        validate_date(fromdate),
        validate_date(todate),
        validate_ticker(ticker),
    )


@mcp.tool()
def get_index_ohlcv(
    fromdate: Union[str, int],
    todate: Union[str, int],
    ticker: Union[str, int],
    freq: str = "d",
) -> Dict[str, Any]:
    """Retrieves OHLCV data for a specific index.

    Args:
        fromdate (str): Start date for retrieval (YYYYMMDD)
        todate   (str): End date for retrieval (YYYYMMDD)
        ticker   (str): Index code (e.g. 1001 KOSPI, 2001 KOSDAQ)
        freq     (str, optional): Unused; daily only.

    Returns:
        Dict with date keys containing index OHLCV data.
    """
    return _guard(
        f"지수 {ticker} OHLCV",
        get_index_ohlcv_by_date,
        validate_date(fromdate),
        validate_date(todate),
        str(ticker).strip(),
    )


@mcp.tool()
def get_ticker_name(ticker: Union[str, int]) -> Dict[str, Any]:
    """Resolves a ticker symbol to its company name.

    Args:
        ticker (str): Stock ticker symbol

    Returns:
        Dict with `ticker` and `name`.
    """
    code = validate_ticker(ticker)
    try:
        name = get_market_ticker_name(code)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{code} 종목명 조회 실패: {exc}"}
    # 체인은 답할 소스가 없으면 티커를 그대로 돌려준다. 그걸 이름으로 속이지 않는다.
    if name == code:
        return {"error": f"{code} 종목명을 어떤 소스에서도 받지 못했습니다."}
    return {"ticker": code, "name": name}


def apply_report_source_order() -> str:
    """리포트 전용 소스 순서가 지정돼 있으면 그것을 쓴다.

    리포트에 실리는 수치는 배포되므로 스크리닝과 다른 소스를 쓰고 싶을 수 있다
    (설계서 §3). 그래서 ``PRISM_REPORT_DATA_SOURCES`` 를 별도로 둔다. 비어 있으면
    일반 체인 순서(``PRISM_MARKET_DATA_SOURCES``)를 그대로 따른다.

    YAML 치환기가 중첩 ``${A:-${B}}`` 를 못 다뤄서 우선순위 판단을 여기서 한다.
    """
    override = (os.getenv("PRISM_REPORT_DATA_SOURCES") or "").strip()
    if override:
        os.environ["PRISM_MARKET_DATA_SOURCES"] = override
    return os.getenv("PRISM_MARKET_DATA_SOURCES", "krx,fdr (chain default)")


def main() -> None:
    logger.info("market data MCP server starting (sources=%s)", apply_report_source_order())
    mcp.run()


if __name__ == "__main__":
    main()
