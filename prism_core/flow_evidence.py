"""Dated, deterministic flow context. No signals, scores, gates, or I/O."""

import hashlib
import json

import numpy as np
import pandas as pd

FLOW_EVIDENCE_VERSION = "flow-evidence-v1"


def completed_daily_frame(df, *, asof_utc, calendar_name="NYSE",
                          session_timezone="America/New_York", required_sessions=31):
    """Return date-indexed completed daily rows and calendar provenance.

    Daily labels must be midnight in the exchange timezone (naive labels are
    exchange dates). Intraday rows are not silently collapsed to daily bars.
    Missing expected sessions remain missing; older rows never fill their place.
    Numeric validation is left to the consumer (OHLCV vs investor quantities).
    """
    meta = {"calendar": calendar_name, "session_timezone": session_timezone,
            "expected_sessions": [], "excluded_rows": 0, "reason": None}
    empty = pd.DataFrame()
    try:
        import pandas_market_calendars as mcal

        asof = pd.Timestamp(asof_utc)
        if asof.tzinfo is None or pd.isna(asof):
            raise ValueError("asof_utc must be timezone-aware")
        asof = asof.tz_convert("UTC")
        meta["asof_utc"] = asof.isoformat()
        end = asof.tz_convert(session_timezone).date()
        schedule = mcal.get_calendar(calendar_name).schedule(
            start_date=end - pd.Timedelta(days=max(120, required_sessions * 3)),
            end_date=end)
        schedule = schedule[schedule["market_close"] <= asof]
        expected = [str(d.date()) for d in schedule.index[-required_sessions:]]
        meta["expected_sessions"] = expected
        meta["completed_through"] = expected[-1] if expected else None
        if not expected:
            meta["reason"] = "calendar_unavailable"
            return empty, meta
        if df is None or df.empty:
            meta["reason"] = "missing_input"
            return empty, meta
        d = df.copy()
        dates = []
        keep = []
        sessions = {str(v.date()) for v in schedule.index}
        for idx in d.index:
            if isinstance(idx, (int, float, np.integer, np.floating)):
                raise TypeError("daily date index required")
            stamp = pd.Timestamp(idx)
            if pd.isna(stamp):
                raise ValueError("invalid daily date")
            if stamp.tzinfo is not None:
                stamp = stamp.tz_convert(session_timezone)
            label = str(stamp.date())
            valid = stamp == stamp.normalize() and label in sessions
            dates.append(label)
            keep.append(valid)
        meta["excluded_rows"] = len(keep) - sum(keep)
        d.index = dates
        d = d.loc[keep].sort_index()
        # Duplicates in the relevant window make the input ambiguous.
        d = d.loc[d.index >= expected[0]]
        if d.index.duplicated().any():
            meta["reason"] = "duplicate_sessions"
            return empty, meta
        meta["input_start"] = d.index[0] if len(d) else None
        meta["input_end"] = d.index[-1] if len(d) else None
        return d, meta
    except Exception:  # noqa: BLE001 - optional context fails closed to unknown
        # Public facts must never leak exception text, filesystem or vendor data.
        meta["reason"] = "invalid_dated_input"
        return empty, meta


def _input_hash(df):
    """Stable hash of dates, source column names and values, including missing."""
    payload = df.to_json(orient="split", date_format="iso", double_precision=15)
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_us_flow_evidence(df, *, asof_utc, source="yfinance.history"):
    """Compute OBV-normalized 5/20-session ratios and CMF20 on closed sessions."""
    d, meta = completed_daily_frame(df, asof_utc=asof_utc, required_sessions=21)
    result = {"version": FLOW_EVIDENCE_VERSION, "source": source,
              "classification": "computed_price_volume_proxy",
              "unit": "dimensionless_ratio", "volume_unit": "source_share_volume",
              "daily_institutional_flow": "MISSING_not_supplied",
              **meta, "input_hash": _input_hash(d), "metrics": {}}
    d = d.rename(columns=lambda col: str(col).lower().replace(" ", "_"))
    for name, window, warmup in (("signed_volume_ratio_5", 5, 1),
                                 ("signed_volume_ratio_20", 20, 1),
                                 ("cmf_20", 20, 0)):
        expected = meta["expected_sessions"][-(window + warmup):]
        observed = [day for day in expected if day in d.index]
        metric = {"value": None, "status": "MISSING", "window_sessions": window,
                  "required_rows": window + warmup, "observed_rows": len(observed),
                  "window_start": expected[warmup] if len(expected) > warmup else None,
                  "window_end": expected[-1] if expected else None,
                  "reason": meta["reason"]}
        result["metrics"][name] = metric
        if metric["reason"]:
            continue
        if len(observed) != window + warmup:
            metric["reason"] = "missing_window_sessions"
            continue
        cols = ["open", "high", "low", "close", "volume"]
        if any(col not in d.columns for col in cols) or d.columns.duplicated().any():
            metric["reason"] = "missing_or_duplicate_ohlcv_columns"
            continue
        raw = d.loc[expected, cols]
        if any(isinstance(value, (bool, np.bool_)) for value in raw.to_numpy().flat):
            metric["reason"] = "invalid_ohlcv"
            continue
        if "stock_splits" in d.columns:
            splits = pd.to_numeric(d.loc[expected, "stock_splits"], errors="coerce")
            if splits.fillna(0).ne(0).any():
                # yfinance adjusts prices, not historical share volume units.
                metric["reason"] = "known_split_unadjusted_volume"
                continue
        try:
            bars = raw.astype(float)
        except (ValueError, TypeError):
            metric["reason"] = "invalid_ohlcv"
            continue
        if (not np.isfinite(bars.to_numpy()).all() or (bars.volume < 0).any()
                or (bars[["open", "high", "low", "close"]] <= 0).any().any()
                or (bars.high < bars[["open", "low", "close"]].max(axis=1)).any()
                or (bars.low > bars[["open", "high", "close"]].min(axis=1)).any()):
            metric["reason"] = "invalid_ohlcv"
            continue
        volumes = bars.volume.iloc[warmup:]
        denominator = float(volumes.sum())
        if not np.isfinite(denominator) or denominator <= 0:
            metric["reason"] = "zero_or_invalid_total_volume"
            continue
        if warmup:
            terms = np.sign(bars.close.diff().iloc[1:])
        else:
            span = bars.high - bars.low
            terms = ((2 * bars.close - bars.high - bars.low)
                     / span.replace(0, np.nan)).fillna(0)
            metric["high_equals_low_zero_contribution_rows"] = int((span == 0).sum())
        value = float((terms * volumes).sum() / denominator)
        if not np.isfinite(value):
            metric["reason"] = "nonfinite_result"
            continue
        metric.update(value=round(value, 8), status="computed", reason=None)
    return result


def describe_us_holdings(holders, *, asof_utc):
    """Date coverage only; retain original holder tables separately, unmodified."""
    asof = pd.Timestamp(asof_utc).tz_convert("UTC")
    cutoff = asof.tz_convert("America/New_York").date()
    dates, unknown, future = set(), 0, 0
    for frame in holders.values():
        if frame is None or frame.empty:
            continue
        date_cols = [c for c in frame.columns
                     if str(c).lower().replace(" ", "_") in ("date_reported", "report_date")]
        if not date_cols:
            unknown += len(frame)
            continue
        for value in frame[date_cols[0]]:
            try:
                date = pd.Timestamp(value)
                if pd.isna(date) or isinstance(value, (int, float)):
                    raise ValueError("unknown date")
                if date.date() > cutoff:
                    future += 1
                else:
                    dates.add(date.date().isoformat())
            except (ValueError, TypeError):
                unknown += 1
    return {"version": FLOW_EVIDENCE_VERSION, "source": "yfinance.holders",
            "fetched_at_utc": asof.isoformat(), "report_dates": sorted(dates),
            "source_hashes": {name: _input_hash(frame) for name, frame in holders.items()
                              if frame is not None and not frame.empty},
            "unknown_report_date_rows": unknown, "future_report_date_rows": future,
            "classification": "lagged_ownership_snapshot_not_daily_flow",
            "daily_institutional_flow": "MISSING_not_supplied"}


def holdings_asof_frame(frame, *, asof_utc):
    """Quarantine future report dates from prompts; retain unknown dates labeled."""
    cutoff = pd.Timestamp(asof_utc).tz_convert("America/New_York").date()
    result = frame.copy()
    date_cols = [c for c in frame.columns
                 if str(c).lower().replace(" ", "_") in ("date_reported", "report_date")]
    statuses = []
    for value in frame[date_cols[0]] if date_cols else [None] * len(frame):
        try:
            date = pd.Timestamp(value)
            if pd.isna(date) or isinstance(value, (int, float)):
                raise ValueError("unknown date")
            statuses.append("future_quarantined" if date.date() > cutoff else "dated_snapshot")
        except (ValueError, TypeError):
            statuses.append("unknown_report_date")
    result["Report date status"] = statuses
    return result.loc[result["Report date status"] != "future_quarantined"]


def render_flow_evidence(evidence):
    """Compact, identical machine facts for report and BUY inputs."""
    return ("\n### Flow evidence (computed context, NOT institutional trades)\n"
            + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n")


def us_flow_interpretation_contract(language="ko"):
    if language == "ko":
        return """
## US 수급 근거의 한계 (다른 기관 수급 표현보다 우선)
- yfinance 기관·펀드 보유 표는 지연된 보유 스냅샷입니다. 보유 기준일과 조회 시각을 구분하고 기준일 불명·미래 행은 판단 근거에서 제외하십시오. 13F는 분기말 기준이며 분기 종료 후 45일 이내 제출하므로 오늘 매수나 기관 3/5일 연속 순매수를 뜻하지 않습니다. 비교 가능한 복수 기준일이 없으면 보유 변화도 알 수 없습니다.
- 현재 입력은 일별 기관 순매수/순매도를 제공하지 않습니다. 보고서에 문장만 있다는 이유로 확인된 수급으로 취급하지 말고 기존 기관 연속 매수/매도 조건은 검증된 일별 원천이 없으면 미확인으로 남기십시오. 결측을 0·매도·매수로 치환하지 마십시오.
- signed_volume_ratio_5/20은 종가 방향별 거래량을 기간 거래량으로 나눈 OBV형 proxy이며, cmf_20은 일중 종가 위치 기반 proxy입니다. 확정 세션·창·단위를 그대로 인용하십시오. 갭 하락 후 상단 마감이면 CMF 양수와 signed ratio 음수가 공존할 수 있으며 기관 매집 확정이 아닙니다. H=L은 CMF 기여 0입니다.
- 새 CMF·signed-volume 창은 기존 거래량 근거와 겹치는 참고 맥락입니다. 이를 기존 거래량 신호에 더해 독립 확인 여러 개로 중복 가점하거나 새 점수·매수/매도 차단·손절·비중 규칙으로 사용하지 마십시오. 기존에 정의된 모멘텀·시장 분산일 계산과 기준은 그대로 유지합니다.
"""
    return """
## US flow evidence limits (override other institutional-flow wording)
- yfinance holder/fund tables are lagged ownership snapshots. Separate report dates from fetch time; exclude unknown/future report dates from inference. 13F is quarter-end ownership filed within 45 days after quarter end, NOT today's buying or 3/5 consecutive sessions of institutional net buying. Without comparable multiple report dates, ownership change is unknown too.
- Daily institutional net buying/selling is NOT supplied. A report assertion alone is not verified flow: leave existing consecutive institutional buying/selling conditions unconfirmed without dated daily source evidence. MISSING is unknown, not zero, selling, or buying.
- signed_volume_ratio_5/20 is an OBV-style normalized signed-volume proxy; cmf_20 is an intraday close-location proxy. Preserve completed sessions, windows and units. A gap-down/high-close session can yield positive CMF and negative signed volume; neither proves institutional accumulation. H=L contributes zero to CMF.
- New CMF and signed-volume windows overlap existing price-volume evidence and are NOT independent additional confirmations. No duplicate score credit, new score, autonomous BUY/SELL veto, stop, or sizing rule may be introduced. Preserve the already-defined momentum and market distribution-day calculations and existing criteria.
"""
