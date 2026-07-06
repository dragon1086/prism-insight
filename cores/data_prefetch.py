"""
Data Prefetch Module for Korean Stock Analysis

Pre-fetches stock data by calling kospi_kosdaq MCP server's library functions directly
(not via MCP protocol), eliminating MCP tool call round-trips during analysis.

Architecture:
- Direct call: import kospi_kosdaq_stock_server module → call functions → Dict → markdown
- MCP fallback: if import fails, agents use MCP tool calls as before (no prefetch)

This mirrors the US module's pattern (us_data_client.py direct import).
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _dict_to_markdown(data: dict, title: str = "") -> str:
    """Convert MCP server's dict response to markdown table string.

    The kospi_kosdaq MCP server functions return Dict[str, Any] with date keys.
    This converts them back to DataFrame for markdown rendering.

    Args:
        data: Date-keyed dict from MCP server functions (e.g., {"2026-02-09": {"Open": ..., ...}})
        title: Optional title to prepend

    Returns:
        Markdown table string, or empty string if data is empty/error
    """
    if not data or "error" in data:
        return ""

    df = pd.DataFrame.from_dict(data, orient='index')
    if df.empty:
        return ""

    df.index.name = "Date"

    result = ""
    if title:
        result += f"### {title}\n\n"

    result += df.to_markdown(index=True) + "\n"
    return result


def _get_mcp_server_module():
    """Import kospi_kosdaq_stock_server module for direct library calls.

    Returns:
        The kospi_kosdaq_stock_server module, or None if import fails
    """
    try:
        import kospi_kosdaq_stock_server as server
        return server
    except ImportError:
        logger.warning("kospi_kosdaq_stock_server module not available, prefetch disabled")
        return None


def prefetch_stock_ohlcv(company_code: str, start_date: str, end_date: str) -> str:
    """Prefetch stock OHLCV data via kospi_kosdaq MCP server library.

    Args:
        company_code: 6-digit stock code (e.g., "005930")
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted OHLCV data string, or empty string on error
    """
    try:
        server = _get_mcp_server_module()
        if not server:
            return ""

        data = server.get_stock_ohlcv(start_date, end_date, company_code)

        return _dict_to_markdown(data, f"Stock OHLCV: {company_code} ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching OHLCV for {company_code}: {e}")
        return ""


def prefetch_stock_trading_volume(company_code: str, start_date: str, end_date: str) -> str:
    """Prefetch investor trading volume data via kospi_kosdaq MCP server library.

    Args:
        company_code: 6-digit stock code
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted trading volume data string, or empty string on error
    """
    try:
        server = _get_mcp_server_module()
        if not server:
            return ""

        data = server.get_stock_trading_volume(start_date, end_date, company_code)

        return _dict_to_markdown(data, f"Investor Trading Volume: {company_code} ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching trading volume for {company_code}: {e}")
        return ""


def prefetch_index_ohlcv(index_ticker: str, start_date: str, end_date: str) -> str:
    """Prefetch market index OHLCV data via kospi_kosdaq MCP server library.

    Args:
        index_ticker: Index ticker ("1001" for KOSPI, "2001" for KOSDAQ)
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)

    Returns:
        Markdown formatted index data string, or empty string on error
    """
    try:
        server = _get_mcp_server_module()
        if not server:
            return ""

        index_name = "KOSPI" if index_ticker == "1001" else "KOSDAQ" if index_ticker == "2001" else index_ticker

        data = server.get_index_ohlcv(start_date, end_date, index_ticker)

        return _dict_to_markdown(data, f"{index_name} Index ({start_date}~{end_date})")
    except Exception as e:
        logger.error(f"Error prefetching index OHLCV for {index_ticker}: {e}")
        return ""


def _log_regime_snapshot(market: str, computed: dict) -> None:
    """Append a regime snapshot to logs/regime_history.jsonl for distribution analysis.

    사이클당 1회 기록 → 운영에서 regime 분포/휩쏘 관측용. 실패해도 무해(파이프라인 영향 0).
    """
    try:
        if not computed:
            return
        import json as _json
        import os as _os
        from datetime import datetime as _dt
        rec = {
            "ts": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "market": market,
            "regime": computed.get("market_regime"),
            "confidence": computed.get("regime_confidence"),
        }
        s = computed.get("index_summary") or {}
        for k in ("sp500_vs_50d_ma", "sp500_vs_200d_ma", "sp500_ma_50_200_cross",
                  "sp500_4w_change_pct", "vix_level",
                  "kospi_vs_60d_ma", "kospi_vs_120d_ma", "kospi_ma_60_120_cross",
                  "kospi_2w_change_pct"):
            if k in s:
                rec[k] = s[k]
        log_dir = _os.path.join(_os.getcwd(), "logs")
        _os.makedirs(log_dir, exist_ok=True)
        with open(_os.path.join(log_dir, "regime_history.jsonl"), "a") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info(f"[regime] {market}: {rec['regime']} (conf {rec['confidence']})")
    except Exception as e:
        logger.warning(f"[regime] snapshot log failed: {e}")


def prefetch_macro_intelligence_data(reference_date: str) -> dict:
    """Prefetch data for macro intelligence analysis.

    Fetches KOSPI/KOSDAQ index data and sector mapping, then computes market regime
    programmatically from price data (not LLM-based).

    Args:
        reference_date: Analysis date (YYYYMMDD)

    Returns:
        Dictionary with:
        - "kospi_ohlcv_md": KOSPI 20-day OHLCV as markdown
        - "kosdaq_ohlcv_md": KOSDAQ 20-day OHLCV as markdown
        - "sector_map": ticker → sector mapping dict
        - "computed_regime": programmatically computed regime info dict
    """
    from datetime import datetime, timedelta

    result = {}

    server = _get_mcp_server_module()
    if not server:
        return result

    ref_dt = datetime.strptime(reference_date, "%Y%m%d")
    start_date = (ref_dt - timedelta(days=45)).strftime("%Y%m%d")
    # regime 계산용 별도 장기 구간(60/120일선 필요). 마크다운(start_date, 45일)은 불변.
    regime_start_date = (ref_dt - timedelta(days=250)).strftime("%Y%m%d")

    # 1. KOSPI index OHLCV
    kospi_md = prefetch_index_ohlcv("1001", start_date, reference_date)
    if kospi_md:
        result["kospi_ohlcv_md"] = kospi_md

    # 2. KOSDAQ index OHLCV
    kosdaq_md = prefetch_index_ohlcv("2001", start_date, reference_date)
    if kosdaq_md:
        result["kosdaq_ohlcv_md"] = kosdaq_md

    # 3. Sector map (ticker → sector name) via get_sector_info
    try:
        import json as _json
        # Fetch KOSPI + KOSDAQ sector classifications
        kospi_sectors = server.get_sector_info("KOSPI")
        kosdaq_sectors = server.get_sector_info("KOSDAQ")
        sector_data = {}
        for raw in [kospi_sectors, kosdaq_sectors]:
            parsed = _json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict) and "error" not in parsed:
                sector_data.update(parsed)
        if sector_data:
            result["sector_map"] = sector_data
            logger.info(f"Prefetched sector_map: {len(sector_data)} tickers")
        else:
            logger.warning("Sector map not available from get_sector_info")
    except Exception as e:
        logger.error(f"Error fetching sector map: {e}")

    # 4. Compute regime from raw KOSPI data
    try:
        kospi_raw = server.get_index_ohlcv(regime_start_date, reference_date, "1001")
        kosdaq_raw = server.get_index_ohlcv(regime_start_date, reference_date, "2001")
        if kospi_raw:
            result["computed_regime"] = _compute_kr_regime(kospi_raw, kosdaq_raw)
            _log_regime_snapshot("KR", result["computed_regime"])
    except Exception as e:
        logger.error(f"Error computing regime: {e}")

    if result:
        logger.info(f"Prefetched macro intelligence data: {list(result.keys())}")

    return result


# --- O'Neil Distribution Day (deterministic, 정보 주입 전용) ----------------
# 설계 결정(tasks/distribution_day_design.md): 분산일은 결정론적으로 '계산'해 index_summary에
# 정보로만 주입하고, regime의 기계적 강등은 하지 않는다. (강등은 매수+매도 양쪽을 뒤집어
# US melt-up에서 조기청산 손실을 유발했고, 시장별 임계는 과최적화 위험이 컸다.) 분산일을
# 어떻게 가중할지(신규매수 보수화 등)는 프롬프트에서 LLM이 판단한다 — O'Neil 본래의 재량적 용법.
# 분산일 파라미터 (O'Neil/IBD). drop=-0.2% 종가, 거래량 전일 초과, 25거래일 윈도우, +5% 회복 만료.
DISTRIBUTION_WINDOW = 25
DISTRIBUTION_DROP_PCT = 0.2
DISTRIBUTION_RECOVERY_PCT = 5.0

# 고변동·낙폭 override 파라미터 (TUNABLE — 배포 전 tools/regime_backtest.py 로 검증/튜닝 권장).
# 문제: 장기이평(60/120일선) 위에 '가격 레벨이 지연되어' 떠 있으나 실제로는 급락형 고변동
# (whipsaw) 국면이 strong/moderate_bull 로 관대하게 분류돼 고점매수→손절이 반복되는 것.
# 분산일과 달리 이건 '강등'을 하되, melt-up(급등, 신고가 부근 고변동)은 건드리지 않는다:
#   낙폭 조건(20일 고점 대비 하락)이 있어야만 발동하므로 신고가 부근 급등장은 배제된다.
#   → 247-250줄에서 우려한 'melt-up 조기청산' 문제를 구조적으로 회피(과최적화 경계 존중).
HIVOL_DD_VOL_PCT = 2.5       # 최근 10일 일간수익률 표준편차 임계(%). KOSPI 평시 ~1%.
HIVOL_DD_DRAWDOWN_PCT = 8.0  # 최근 20일 고점 대비 낙폭 임계(%). melt-up(≈0%) 배제용.
HIVOL_DD_CONFIDENCE = 0.55   # 강등 시 신뢰도(낮춤).
_BULL_REGIMES_KR = ("strong_bull", "moderate_bull")


def _high_vol_drawdown_override(closes, regime: str, confidence: float):
    """급락형 고변동 국면이면 bull 라벨을 sideways 로 강등(순수 함수, 부작용 없음).

    발동 조건(모두 충족):
      1) regime 이 bull 계열(strong_bull/moderate_bull)
      2) 최근 10일 실현변동성(일수익률 std) >= HIVOL_DD_VOL_PCT
      3) 최근 20일 고점 대비 낙폭 >= HIVOL_DD_DRAWDOWN_PCT  (= melt-up 이 아님)

    강등은 sideways 까지만(약세장 아님) — 기존 REGIME 매수 문턱/슬롯/가중치가
    자동으로 보수화된다. melt-up 은 낙폭 조건 때문에 절대 발동하지 않는다.

    Returns:
        (regime, confidence, reason) — 미발동 시 reason 은 None.
    """
    if regime not in _BULL_REGIMES_KR:
        return regime, confidence, None
    if closes is None or len(closes) < 11:
        return regime, confidence, None
    arr = np.asarray(closes, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 11:
        return regime, confidence, None
    recent = arr[-11:]
    rets = np.diff(recent) / recent[:-1]          # 최근 10 일간수익률
    vol_pct = float(np.std(rets) * 100)
    window = arr[-20:] if len(arr) >= 20 else arr
    peak = float(np.max(window))
    drawdown_pct = (peak - float(arr[-1])) / peak * 100 if peak > 0 else 0.0
    if vol_pct >= HIVOL_DD_VOL_PCT and drawdown_pct >= HIVOL_DD_DRAWDOWN_PCT:
        reason = (
            f"{regime}->sideways (vol10d={vol_pct:.1f}%>={HIVOL_DD_VOL_PCT}, "
            f"dd20d={drawdown_pct:.1f}%>={HIVOL_DD_DRAWDOWN_PCT})"
        )
        return "sideways", min(confidence, HIVOL_DD_CONFIDENCE), reason
    return regime, confidence, None


def _count_distribution_days(df, close_col, volume_col=None,
                             window: int = DISTRIBUTION_WINDOW,
                             drop_threshold_pct: float = DISTRIBUTION_DROP_PCT,
                             recovery_pct: float = DISTRIBUTION_RECOVERY_PCT):
    """O'Neil 분산일 카운트 (결정론적).

    분산일 = 지수가 전일 종가 대비 >= drop_threshold_pct% 하락 마감 AND 거래량이 전일 초과.
    만료: (1) window 거래일 경과 시 윈도우 밖으로 자동 제외, (2) 분산일 이후 어떤 종가가
    그 분산일 종가 대비 +recovery_pct% 이상 상승하면 카운트에서 제거.

    Args:
        df: 정렬 가능한 OHLCV DataFrame (인덱스=날짜).
        close_col: 종가 컬럼명.
        volume_col: 거래량 컬럼명. None이면 자동 탐지.

    Returns:
        {"count": int, "window": int, "raw_count": int} 또는 거래량 불가 시 None.
    """
    try:
        d = df.sort_index()
        if volume_col is None:
            for c in ["Volume", "거래량", "volume"]:
                if c in d.columns:
                    volume_col = c
                    break
        if volume_col is None or close_col not in d.columns:
            return None
        closes = d[close_col].astype(float).values
        vols = d[volume_col].astype(float).values
        n = len(closes)
        if n < 2:
            return None
        # 거래량이 전부 0/NaN이면 분산일 판정 불가 → graceful skip
        import math as _math
        valid_vol = [v for v in vols if not _math.isnan(v) and v > 0]
        if not valid_vol:
            return None
        latest_max_after = closes[-1]  # i 이후 최대 종가를 뒤에서부터 누적
        # 후보: 최근 window 거래일 (각 후보는 직전일 필요 → idx>=1)
        start = max(1, n - window)
        raw = 0
        kept = 0
        # 뒤에서 앞으로 스캔하며 'i 이후 최대 종가' 유지
        running_max_after = -1.0
        flags = []  # (idx, is_dist)
        for i in range(n - 1, start - 1, -1):
            prev_c = closes[i - 1]
            cur_c = closes[i]
            if prev_c <= 0:
                flags.append((i, False, running_max_after))
                running_max_after = max(running_max_after, cur_c)
                continue
            pct = (cur_c - prev_c) / prev_c * 100.0
            vol_up = vols[i] > vols[i - 1]
            is_dist = (pct <= -drop_threshold_pct) and vol_up
            flags.append((i, is_dist, running_max_after))
            running_max_after = max(running_max_after, cur_c)
        for (i, is_dist, max_after) in flags:
            if not is_dist:
                continue
            raw += 1
            # 회복 만료: 이후 종가가 분산일 종가 +recovery_pct% 이상이면 제외
            if max_after >= closes[i] * (1 + recovery_pct / 100.0):
                continue
            kept += 1
        return {"count": kept, "window": window, "raw_count": raw}
    except Exception:
        return None


def _inject_distribution_days(index_summary, df, close_col) -> None:
    """분산일 카운트를 결정론적으로 계산해 index_summary에 정보로 주입(강등 없음).

    거래량 결측/불가 시 distribution_days=None. regime/confidence는 변경하지 않는다.
    """
    dist = _count_distribution_days(df, close_col)
    index_summary["distribution_window"] = DISTRIBUTION_WINDOW
    index_summary["distribution_days"] = None if dist is None else dist["count"]


def _compute_kr_regime(kospi_ohlcv: dict, kosdaq_ohlcv: dict = None) -> dict:
    """Compute KR market regime programmatically from KOSPI OHLCV data.

    Uses 20-day MA position and 2-week change rate for classification.

    Returns:
        Dict with regime classification, index summary, and confidence.
    """
    df = pd.DataFrame.from_dict(kospi_ohlcv, orient='index')
    if df.empty or len(df) < 10:
        return {"market_regime": "sideways", "regime_confidence": 0.3, "simple_ma_regime": "sideways"}

    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df_20d = df.tail(20)

    # Determine close column name (could be "Close" or "종가")
    close_col = None
    for col_name in ["Close", "종가"]:
        if col_name in df.columns:
            close_col = col_name
            break
    if not close_col:
        close_col = df.columns[3]  # fallback to 4th column (typically Close)

    current_price = float(df_20d[close_col].iloc[-1])
    ma_20d = float(df_20d[close_col].mean())

    # 2-week change (last 10 trading days)
    if len(df_20d) >= 10:
        price_2w_ago = float(df_20d[close_col].iloc[-10])
    else:
        price_2w_ago = float(df_20d[close_col].iloc[0])
    change_2w_pct = ((current_price - price_2w_ago) / price_2w_ago) * 100

    # MA position (short-term, 20-day = 생명선)
    ma_diff_pct = ((current_price - ma_20d) / ma_20d) * 100
    above_ma = current_price > ma_20d

    # Trend MAs (60/120) from FULL history — needs ~250d fetch.
    # KR 관례: 60일선=수급선(중기), 120일선=경기선(중장기 추세 분기).
    closes_full = df[close_col].dropna()
    ma_60 = float(closes_full.tail(60).mean()) if len(closes_full) >= 60 else None
    ma_120 = float(closes_full.tail(120).mean()) if len(closes_full) >= 120 else None
    above_60 = (current_price > ma_60) if ma_60 is not None else None
    above_120 = (current_price > ma_120) if ma_120 is not None else None
    # golden = 60일선 > 120일선 (정배열 경향); False = 데드크로스
    golden = (ma_60 > ma_120) if (ma_60 is not None and ma_120 is not None) else None

    # simple_ma_regime (pure index-based)
    if abs(ma_diff_pct) <= 0.5:
        simple_ma_regime = "sideways"
    elif above_ma:
        simple_ma_regime = "bull"
    else:
        simple_ma_regime = "bear"

    # KOSPI 20d trend
    if change_2w_pct > 2:
        kospi_trend = "up"
    elif change_2w_pct < -2:
        kospi_trend = "down"
    else:
        kospi_trend = "sideways"

    # Market regime classification.
    # Trend-template (120일선 primary divider) when 120MA available; else legacy 20MA logic.
    # Output strings unchanged (6 regimes) so downstream buy-matrix/prompts stay compatible.
    if ma_120 is not None:
        if above_120:
            # 중장기 상승추세 (가격 > 120일선)
            if above_60 and golden and change_2w_pct > 5:
                regime = "strong_bull"
                confidence = 0.90
            elif above_60 or change_2w_pct >= 0:
                regime = "moderate_bull"
                confidence = 0.78
            else:
                regime = "sideways"
                confidence = 0.62
        else:
            # 120일선 아래 = 중장기 하락추세 → 'bull' 금지. 약세장 반등 방어:
            # 120선 아래에서의 단기 급반등은 strong_bull 이 아니라 sideways(보수)로 분류.
            if golden is False and change_2w_pct < -5:
                regime = "strong_bear"
                confidence = 0.90
            elif change_2w_pct < 0:
                regime = "moderate_bear"
                confidence = 0.78
            else:
                regime = "sideways"
                confidence = 0.55
    else:
        # Legacy 20MA logic (insufficient history for 120MA) — backward compatible
        if above_ma and change_2w_pct > 5:
            regime = "strong_bull"
            confidence = 0.85
        elif above_ma and change_2w_pct >= 0:
            regime = "moderate_bull"
            confidence = 0.75
        elif abs(ma_diff_pct) <= 1 and abs(change_2w_pct) < 2:
            regime = "sideways"
            confidence = 0.65
        elif not above_ma and change_2w_pct < -5:
            regime = "strong_bear"
            confidence = 0.85
        else:
            regime = "moderate_bear"
            confidence = 0.75

    # KOSDAQ trend (if available)
    kosdaq_trend = "sideways"
    if kosdaq_ohlcv:
        try:
            kd_df = pd.DataFrame.from_dict(kosdaq_ohlcv, orient='index')
            kd_df.index = pd.to_datetime(kd_df.index)
            kd_df = kd_df.sort_index().tail(20)
            kd_close = None
            for col_name in ["Close", "종가"]:
                if col_name in kd_df.columns:
                    kd_close = col_name
                    break
            if kd_close and len(kd_df) >= 10:
                kd_current = float(kd_df[kd_close].iloc[-1])
                kd_prev = float(kd_df[kd_close].iloc[-10])
                kd_change = ((kd_current - kd_prev) / kd_prev) * 100
                if kd_change > 2:
                    kosdaq_trend = "up"
                elif kd_change < -2:
                    kosdaq_trend = "down"
        except Exception:
            pass

    index_summary = {
        "kospi_20d_trend": kospi_trend,
        "kospi_vs_20d_ma": "above" if above_ma else "below",
        "kospi_2w_change_pct": round(change_2w_pct, 2),
        "kospi_current": round(current_price, 2),
        "kospi_20d_ma": round(ma_20d, 2),
        "kosdaq_20d_trend": kosdaq_trend,
    }
    # Trend MA fields (additive — present only when enough history)
    if ma_60 is not None:
        index_summary["kospi_60d_ma"] = round(ma_60, 2)
        index_summary["kospi_vs_60d_ma"] = "above" if above_60 else "below"
    if ma_120 is not None:
        index_summary["kospi_120d_ma"] = round(ma_120, 2)
        index_summary["kospi_vs_120d_ma"] = "above" if above_120 else "below"
    if golden is not None:
        index_summary["kospi_ma_60_120_cross"] = "golden" if golden else "dead"

    # O'Neil 분산일 결정론 카운트를 index_summary에 정보로 주입(강등 없음 — LLM이 프롬프트에서 판단)
    _inject_distribution_days(index_summary, df, close_col)

    # 고변동·낙폭 override: 장기이평 위이나 '급락형 휩쏘'면 bull->sideways 강등(melt-up 배제).
    regime, confidence, _hivol_reason = _high_vol_drawdown_override(
        closes_full.to_numpy(), regime, confidence
    )
    index_summary["highvol_drawdown_override"] = _hivol_reason

    return {
        "market_regime": regime,
        "regime_confidence": confidence,
        "simple_ma_regime": simple_ma_regime,
        "index_summary": index_summary,
    }


def prefetch_kr_analysis_data(company_code: str, reference_date: str, max_years_ago: str) -> dict:
    """Prefetch all data needed for KR stock analysis agents.

    Calls kospi_kosdaq MCP server's library functions directly (not via MCP protocol).
    If the library is unavailable, returns empty dict and agents fall back to MCP tool calls.

    Args:
        company_code: 6-digit stock code
        reference_date: Analysis reference date (YYYYMMDD)
        max_years_ago: Start date for data collection (YYYYMMDD)

    Returns:
        Dictionary with prefetched data:
        - "stock_ohlcv": OHLCV data as markdown
        - "trading_volume": Investor trading volume as markdown
        - "kospi_index": KOSPI index data as markdown
        - "kosdaq_index": KOSDAQ index data as markdown
        Returns empty dict on total failure.
    """
    result = {}

    # 1. Stock OHLCV data
    stock_ohlcv = prefetch_stock_ohlcv(company_code, max_years_ago, reference_date)
    if stock_ohlcv:
        result["stock_ohlcv"] = stock_ohlcv

    # 2. Investor trading volume data
    trading_volume = prefetch_stock_trading_volume(company_code, max_years_ago, reference_date)
    if trading_volume:
        result["trading_volume"] = trading_volume

    # 3. KOSPI index data
    kospi_index = prefetch_index_ohlcv("1001", max_years_ago, reference_date)
    if kospi_index:
        result["kospi_index"] = kospi_index

    # 4. KOSDAQ index data
    kosdaq_index = prefetch_index_ohlcv("2001", max_years_ago, reference_date)
    if kosdaq_index:
        result["kosdaq_index"] = kosdaq_index

    if result:
        logger.info(f"Prefetched KR data for {company_code}: {list(result.keys())}")
    else:
        logger.warning(f"Failed to prefetch any KR data for {company_code}")

    return result
