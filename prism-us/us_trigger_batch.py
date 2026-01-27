#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US Trigger Batch System

Surge stock detection system for US market.
Adapted from Korean trigger_batch.py for US market characteristics.

Key Differences from Korean Version:
- Data source: yfinance (vs pykrx)
- Market cap filter: $5B USD (vs 5000억 KRW)
- Trading value filter: $100M USD (vs 100억 KRW)
- Change rate filter: 20% max (same)
- Market hours: 09:30-16:00 EST (vs 09:00-15:30 KST)

Usage:
    python prism-us/us_trigger_batch.py morning INFO --output trigger_results_us.json
    python prism-us/us_trigger_batch.py afternoon INFO --output trigger_results_us.json
"""

from dotenv import load_dotenv
load_dotenv()

import sys
import os
import datetime
import logging
import json
import pandas as pd
import numpy as np

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cores.us_surge_detector import (
    get_snapshot,
    get_previous_snapshot,
    get_multi_day_ohlcv,
    get_market_cap_df,
    get_major_tickers,
    get_ticker_name,
    get_nearest_business_day,
    apply_absolute_filters,
    normalize_and_score,
    enhance_dataframe,
)

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(ch)


# v1.16.6: Trigger-type specific agent criteria (synced with trading_agents.py)
TRIGGER_CRITERIA = {
    "Volume Surge Top": {"rr_target": 1.2, "sl_max": 0.05},
    "Gap Up Momentum Top": {"rr_target": 1.2, "sl_max": 0.05},
    "Intraday Rise Top": {"rr_target": 1.2, "sl_max": 0.05},
    "Closing Strength Top": {"rr_target": 1.3, "sl_max": 0.05},
    "Value-to-Cap Ratio Top": {"rr_target": 1.3, "sl_max": 0.05},
    "Volume Surge Sideways": {"rr_target": 1.5, "sl_max": 0.07},
    "default": {"rr_target": 1.5, "sl_max": 0.07}
}

# Market cap filter: $5B USD
MIN_MARKET_CAP = 5_000_000_000

# Trading value filter: $100M USD
MIN_TRADING_VALUE = 100_000_000


def calculate_agent_fit_metrics(ticker: str, current_price: float, trade_date: str,
                                lookback_days: int = 10, trigger_type: str = None) -> dict:
    """
    Calculate metrics for buy/sell agent criteria.

    v1.16.6: Fixed stop-loss method (targeting 15% annual return)
    - Key change: 10-day support → Fixed % from current price
    - Reason: Allow surge stocks to pass agent criteria
    - R/R ratio: Maintain resistance-based target, minimum +15% guarantee

    Args:
        ticker: Stock ticker symbol
        current_price: Current price
        trade_date: Reference trading date
        lookback_days: Number of past trading days to analyze
        trigger_type: Trigger type for differentiated criteria

    Returns:
        dict with keys: stop_loss_price, target_price, stop_loss_pct,
                       risk_reward_ratio, agent_fit_score
    """
    result = {
        "stop_loss_price": 0,
        "target_price": 0,
        "stop_loss_pct": 1.0,  # Default: unfavorable
        "risk_reward_ratio": 0,
        "agent_fit_score": 0,
    }

    if current_price <= 0:
        return result

    # v1.16.6: Get trigger-type specific criteria
    criteria = TRIGGER_CRITERIA.get(trigger_type, TRIGGER_CRITERIA["default"])
    sl_max = criteria["sl_max"]
    rr_target = criteria["rr_target"]

    # v1.16.6: Fixed stop-loss method
    # Previous: 10-day low based → 48%+ stop-loss for surge stocks → agent rejection
    # Changed: Fixed % from current price → always meets agent criteria
    stop_loss_price = current_price * (1 - sl_max)
    stop_loss_pct = sl_max  # Fixed value (5% or 7%)

    # Target price: Keep resistance-based method
    multi_day_df = get_multi_day_ohlcv(ticker, trade_date, lookback_days)
    if multi_day_df.empty or len(multi_day_df) < 3:
        # Data insufficient: use current price + 15% default
        target_price = current_price * 1.15
        logger.debug(f"{ticker}: Insufficient data, using default target ({target_price:.2f})")
    else:
        # Column name check (yfinance uses capitalized English names)
        high_col = "High"
        if high_col not in multi_day_df.columns:
            target_price = current_price * 1.15
            logger.debug(f"{ticker}: No High column, using default target")
        else:
            # Filter out zero values (market closures or data errors)
            valid_highs = multi_day_df[high_col][multi_day_df[high_col] > 0]
            if valid_highs.empty:
                target_price = current_price * 1.15
            else:
                # Resistance (max of recent N-day highs)
                target_price = valid_highs.max()

    # Ensure target_price is a scalar
    if hasattr(target_price, 'item'):
        target_price = target_price.item()
    elif hasattr(target_price, 'iloc'):
        target_price = target_price.iloc[0]
    target_price = float(target_price)

    # v1.16.6: Minimum +15% target guarantee
    min_target = current_price * 1.15
    if target_price <= current_price:
        target_price = min_target
        logger.debug(f"{ticker}: Target <= current, applying minimum ({target_price:.2f})")
    elif target_price < min_target:
        logger.debug(f"{ticker}: Target {target_price:.2f} → minimum {min_target:.2f}")
        target_price = min_target

    # Risk/Reward calculation
    potential_gain = target_price - current_price
    potential_loss = current_price - stop_loss_price

    if potential_loss > 0 and potential_gain > 0:
        risk_reward_ratio = potential_gain / potential_loss
    else:
        risk_reward_ratio = 0

    # v1.16.6: Agent fit score (simplified)
    # Stop-loss always within criteria, so sl_score = 1.0
    rr_score = min(risk_reward_ratio / rr_target, 1.0) if risk_reward_ratio > 0 else 0
    sl_score = 1.0  # Fixed stop-loss always scores full points

    # Final score (R/R 60%, stop-loss 40%)
    agent_fit_score = rr_score * 0.6 + sl_score * 0.4

    result = {
        "stop_loss_price": stop_loss_price,
        "target_price": target_price,
        "stop_loss_pct": stop_loss_pct,
        "risk_reward_ratio": risk_reward_ratio,
        "agent_fit_score": agent_fit_score,
    }

    logger.debug(f"{ticker}: SL=${stop_loss_price:.2f}, TP=${target_price:.2f}, "
                f"SL%={stop_loss_pct*100:.1f}% (fixed), R/R={risk_reward_ratio:.2f}, "
                f"AgentScore={agent_fit_score:.3f}")

    return result


def score_candidates_by_agent_criteria(candidates_df: pd.DataFrame, trade_date: str,
                                       lookback_days: int = 10, trigger_type: str = None) -> pd.DataFrame:
    """
    Calculate agent criteria scores for all candidate stocks.

    Args:
        candidates_df: Candidate DataFrame (index: ticker, must have Close column)
        trade_date: Reference trading date
        lookback_days: Number of past trading days
        trigger_type: Trigger type for differentiated criteria

    Returns:
        DataFrame with agent criteria scores added
    """
    if candidates_df.empty:
        return candidates_df

    result_df = candidates_df.copy()

    # Initialize agent-related columns
    result_df["StopLossPrice"] = 0.0
    result_df["TargetPrice"] = 0.0
    result_df["StopLossPct"] = 0.0
    result_df["RiskRewardRatio"] = 0.0
    result_df["AgentFitScore"] = 0.0

    for ticker in result_df.index:
        current_price = result_df.loc[ticker, "Close"]
        # Ensure current_price is a scalar (not Series)
        if hasattr(current_price, 'item'):
            current_price = current_price.item()
        elif hasattr(current_price, 'iloc'):
            current_price = current_price.iloc[0]
        metrics = calculate_agent_fit_metrics(ticker, float(current_price), trade_date,
                                             lookback_days, trigger_type)

        result_df.loc[ticker, "StopLossPrice"] = metrics["stop_loss_price"]
        result_df.loc[ticker, "TargetPrice"] = metrics["target_price"]
        result_df.loc[ticker, "StopLossPct"] = metrics["stop_loss_pct"]
        result_df.loc[ticker, "RiskRewardRatio"] = metrics["risk_reward_ratio"]
        result_df.loc[ticker, "AgentFitScore"] = metrics["agent_fit_score"]

    return result_df


# === Morning Triggers (Market Open Snapshot) ===

def trigger_morning_volume_surge(trade_date: str, snapshot: pd.DataFrame,
                                 prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None,
                                 top_n: int = 10) -> pd.DataFrame:
    """
    [Morning Trigger 1] Volume Surge Top
    - Absolute criteria: Min trading value $100M + 20% of market average volume
    - Additional filter: Volume increase >= 30%
    - Composite score: Volume increase rate (60%) + Absolute volume (40%)
    - Secondary filter: Only rising stocks (current > open)
    - Market cap filter: >= $5B USD
    """
    logger.debug("trigger_morning_volume_surge started")

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Market cap filter
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["MarketCap"]], left_index=True, right_index=True, how="inner")
        snap = snap[snap["MarketCap"] >= MIN_MARKET_CAP]
        logger.debug(f"After market cap filter: {len(snap)} stocks")
        if snap.empty:
            logger.warning("No stocks after market cap filter")
            return pd.DataFrame()

    # Absolute filters
    snap = apply_absolute_filters(snap, min_value=MIN_TRADING_VALUE)

    # Volume ratio calculation
    snap["VolumeRatio"] = snap["Volume"] / prev["Volume"].replace(0, np.nan)
    snap["VolumeIncreaseRate"] = (snap["VolumeRatio"] - 1) * 100

    # Change rate calculations
    snap["IntradayChange"] = (snap["Close"] / snap["Open"] - 1) * 100
    snap["DailyChange"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100

    # v1.16.6: Max change rate filter (20%)
    snap = snap[snap["DailyChange"] <= 20.0]

    snap["IsRising"] = snap["Close"] > snap["Open"]

    # Volume increase >= 30% filter
    snap = snap[snap["VolumeIncreaseRate"] >= 30.0]

    if snap.empty:
        logger.debug("trigger_morning_volume_surge: No volume surge stocks")
        return pd.DataFrame()

    # Primary filter: Composite score top N
    scored = normalize_and_score(snap, "VolumeIncreaseRate", "Volume", 0.6, 0.4)
    candidates = scored.head(top_n)

    # Secondary filter: Only rising stocks
    result = candidates[candidates["IsRising"] == True].copy()

    if result.empty:
        logger.debug("trigger_morning_volume_surge: No qualifying stocks")
        return pd.DataFrame()

    logger.debug(f"Volume surge detected: {len(result)} stocks")
    return enhance_dataframe(result.sort_values("CompositeScore", ascending=False).head(10))


def trigger_morning_gap_up_momentum(trade_date: str, snapshot: pd.DataFrame,
                                    prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None,
                                    top_n: int = 15) -> pd.DataFrame:
    """
    [Morning Trigger 2] Gap Up Momentum Top
    - Absolute criteria: Min trading value $100M
    - Composite score: Gap up rate (50%) + Intraday change (30%) + Trading value (20%)
    - Secondary filter: Only stocks maintaining momentum (close > open)
    - Market cap filter: >= $5B USD
    """
    logger.debug("trigger_morning_gap_up_momentum started")

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Market cap filter
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["MarketCap"]], left_index=True, right_index=True, how="inner")
        snap = snap[snap["MarketCap"] >= MIN_MARKET_CAP]
        logger.debug(f"After market cap filter: {len(snap)} stocks")
        if snap.empty:
            return pd.DataFrame()

    # Absolute filters
    snap = apply_absolute_filters(snap, min_value=MIN_TRADING_VALUE)

    # Gap up calculation
    snap["GapUpRate"] = (snap["Open"] / prev["Close"] - 1) * 100
    snap["IntradayChange"] = (snap["Close"] / snap["Open"] - 1) * 100
    snap["DailyChange"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100
    snap["MomentumContinuing"] = snap["Close"] > snap["Open"]

    # Primary filter: Gap up >= 1%, Daily change <= 15%
    snap = snap[(snap["GapUpRate"] >= 1.0) & (snap["DailyChange"] <= 15.0)]

    if snap.empty:
        logger.debug("trigger_morning_gap_up_momentum: No gap up stocks")
        return pd.DataFrame()

    # Composite score calculation
    for col in ["GapUpRate", "IntradayChange", "Amount"]:
        col_max = snap[col].max()
        col_min = snap[col].min()
        col_range = col_max - col_min if col_max > col_min else 1
        snap[f"{col}_norm"] = (snap[col] - col_min) / col_range

    snap["CompositeScore"] = (
        snap["GapUpRate_norm"] * 0.5 +
        snap["IntradayChange_norm"] * 0.3 +
        snap["Amount_norm"] * 0.2
    )

    candidates = snap.sort_values("CompositeScore", ascending=False).head(top_n)

    # Secondary filter: Momentum continuing stocks only
    result = candidates[candidates["MomentumContinuing"] == True].copy()

    if result.empty:
        logger.debug("trigger_morning_gap_up_momentum: No qualifying stocks")
        return pd.DataFrame()

    result["TotalMomentum"] = result["GapUpRate"] + result["IntradayChange"]

    logger.debug(f"Gap up momentum detected: {len(result)} stocks")
    return enhance_dataframe(result.sort_values("CompositeScore", ascending=False).head(10))


def trigger_morning_value_to_cap_ratio(trade_date: str, snapshot: pd.DataFrame,
                                       prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame,
                                       top_n: int = 10) -> pd.DataFrame:
    """
    [Morning Trigger 3] Value-to-Cap Ratio Top (Concentrated Capital Inflow)
    - Absolute criteria: Min trading value $100M
    - Composite score: Trading value ratio (50%) + Absolute value (30%) + Intraday change (20%)
    - Secondary filter: Only rising stocks
    """
    logger.info("Value-to-Cap ratio analysis started")

    # Validation
    if snapshot.empty or prev_snapshot.empty or cap_df.empty:
        logger.error("Input data is empty")
        return pd.DataFrame()

    if 'MarketCap' not in cap_df.columns:
        logger.error(f"MarketCap column not found. Columns: {list(cap_df.columns)}")
        return pd.DataFrame()

    try:
        # Merge market cap data
        merged = snapshot.merge(cap_df[["MarketCap"]], left_index=True, right_index=True, how="inner").copy()
        logger.info(f"Merged data: {len(merged)} stocks")

        # Common stocks with previous day
        common = merged.index.intersection(prev_snapshot.index)
        if len(common) == 0:
            logger.error("No common stocks")
            return pd.DataFrame()

        merged = merged.loc[common].copy()
        prev = prev_snapshot.loc[common].copy()

        # Absolute filters
        merged = apply_absolute_filters(merged, min_value=MIN_TRADING_VALUE)
        if merged.empty:
            return pd.DataFrame()

        # Trading value / Market cap ratio
        merged["ValueCapRatio"] = (merged["Amount"] / merged["MarketCap"]) * 100

        # Change rate calculations
        merged["IntradayChange"] = (merged["Close"] / merged["Open"] - 1) * 100
        merged["DailyChange"] = ((merged["Close"] - prev["Close"]) / prev["Close"]) * 100
        merged["IsRising"] = merged["Close"] > merged["Open"]

        # v1.16.6: Max change rate filter (20%)
        merged = merged[merged["DailyChange"] <= 20.0]

        # Market cap filter ($5B+)
        merged = merged[merged["MarketCap"] >= MIN_MARKET_CAP]
        if merged.empty:
            return pd.DataFrame()

        # Composite score
        for col in ["ValueCapRatio", "Amount", "IntradayChange"]:
            col_max = merged[col].max()
            col_min = merged[col].min()
            col_range = col_max - col_min if col_max > col_min else 1
            merged[f"{col}_norm"] = (merged[col] - col_min) / col_range

        merged["CompositeScore"] = (
            merged["ValueCapRatio_norm"] * 0.5 +
            merged["Amount_norm"] * 0.3 +
            merged["IntradayChange_norm"] * 0.2
        )

        candidates = merged.sort_values("CompositeScore", ascending=False).head(top_n)

        # Secondary filter: Rising stocks only
        result = candidates[candidates["IsRising"] == True].copy()

        if result.empty:
            return pd.DataFrame()

        logger.info(f"Analysis complete: {len(result)} stocks selected")
        return enhance_dataframe(result.sort_values("CompositeScore", ascending=False).head(10))

    except Exception as e:
        logger.error(f"Error in value-to-cap analysis: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return pd.DataFrame()


# === Afternoon Triggers (Market Close Snapshot) ===

def trigger_afternoon_daily_rise_top(trade_date: str, snapshot: pd.DataFrame,
                                     prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None,
                                     top_n: int = 15) -> pd.DataFrame:
    """
    [Afternoon Trigger 1] Intraday Rise Top
    - Absolute criteria: Min trading value $100M
    - Composite score: Intraday change (60%) + Trading value (40%)
    - Additional filter: Change rate >= 3%
    - Market cap filter: >= $5B USD
    """
    logger.debug("trigger_afternoon_daily_rise_top started")

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Market cap filter
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["MarketCap"]], left_index=True, right_index=True, how="inner")
        snap = snap[snap["MarketCap"] >= MIN_MARKET_CAP]
        if snap.empty:
            return pd.DataFrame()

    # Absolute filters
    snap = apply_absolute_filters(snap.copy(), min_value=MIN_TRADING_VALUE)

    # Change rate calculations
    snap["IntradayChange"] = (snap["Close"] / snap["Open"] - 1) * 100
    snap["DailyChange"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100

    # Filter: 3% <= change <= 15%
    snap = snap[(snap["DailyChange"] >= 3.0) & (snap["DailyChange"] <= 15.0)]

    if snap.empty:
        logger.debug("trigger_afternoon_daily_rise_top: No qualifying stocks")
        return pd.DataFrame()

    # Composite score
    scored = normalize_and_score(snap, "IntradayChange", "Amount", 0.6, 0.4)
    result = scored.head(top_n).copy()

    logger.debug(f"Intraday rise top detected: {len(result)} stocks")
    return enhance_dataframe(result.head(10))


def trigger_afternoon_closing_strength(trade_date: str, snapshot: pd.DataFrame,
                                       prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None,
                                       top_n: int = 15) -> pd.DataFrame:
    """
    [Afternoon Trigger 2] Closing Strength Top
    - Absolute criteria: Min trading value $100M + Volume increase from previous day
    - Composite score: Closing strength (50%) + Volume increase (30%) + Trading value (20%)
    - Secondary filter: Only rising stocks (close > open)
    - Market cap filter: >= $5B USD
    """
    logger.debug("trigger_afternoon_closing_strength started")

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Market cap filter
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["MarketCap"]], left_index=True, right_index=True, how="inner")
        snap = snap[snap["MarketCap"] >= MIN_MARKET_CAP]
        if snap.empty:
            return pd.DataFrame()

    # Absolute filters
    snap = apply_absolute_filters(snap, min_value=MIN_TRADING_VALUE)

    # Closing strength (closer to high = closer to 1)
    snap["ClosingStrength"] = 0.0
    valid_range = (snap["High"] != snap["Low"])
    snap.loc[valid_range, "ClosingStrength"] = (
        (snap.loc[valid_range, "Close"] - snap.loc[valid_range, "Low"]) /
        (snap.loc[valid_range, "High"] - snap.loc[valid_range, "Low"])
    )

    # Volume increase
    snap["VolumeIncreaseRate"] = (snap["Volume"] / prev["Volume"].replace(0, np.nan) - 1) * 100

    # Change rate calculations
    snap["IntradayChange"] = (snap["Close"] / snap["Open"] - 1) * 100
    snap["DailyChange"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100

    snap["VolumeIncreased"] = (snap["Volume"] - prev["Volume"].replace(0, np.nan)) > 0
    snap["IsRising"] = snap["Close"] > snap["Open"]

    # Primary filter: Volume increase stocks
    candidates = snap[snap["VolumeIncreased"] == True].copy()

    if candidates.empty:
        logger.debug("trigger_afternoon_closing_strength: No volume increase stocks")
        return pd.DataFrame()

    # Composite score
    for col in ["ClosingStrength", "VolumeIncreaseRate", "Amount"]:
        col_max = candidates[col].max()
        col_min = candidates[col].min()
        col_range = col_max - col_min if col_max > col_min else 1
        candidates[f"{col}_norm"] = (candidates[col] - col_min) / col_range

    candidates["CompositeScore"] = (
        candidates["ClosingStrength_norm"] * 0.5 +
        candidates["VolumeIncreaseRate_norm"] * 0.3 +
        candidates["Amount_norm"] * 0.2
    )

    candidates = candidates.sort_values("CompositeScore", ascending=False).head(top_n)

    # Secondary filter: Rising stocks only
    result = candidates[candidates["IsRising"] == True].copy()

    if result.empty:
        logger.debug("trigger_afternoon_closing_strength: No qualifying stocks")
        return pd.DataFrame()

    logger.debug(f"Closing strength top detected: {len(result)} stocks")
    return enhance_dataframe(result.sort_values("CompositeScore", ascending=False).head(10))


def trigger_afternoon_volume_surge_flat(trade_date: str, snapshot: pd.DataFrame,
                                        prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None,
                                        top_n: int = 20) -> pd.DataFrame:
    """
    [Afternoon Trigger 3] Volume Surge Sideways (Consolidation Stocks)
    - Absolute criteria: Min trading value $100M + Market average volume
    - Composite score: Volume increase rate (60%) + Trading value (40%)
    - Secondary filter: Sideways stocks only (change within +-5%)
    - Market cap filter: >= $5B USD
    """
    logger.debug("trigger_afternoon_volume_surge_flat started")

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Market cap filter
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["MarketCap"]], left_index=True, right_index=True, how="inner")
        snap = snap[snap["MarketCap"] >= MIN_MARKET_CAP]
        if snap.empty:
            return pd.DataFrame()

    # Absolute filters
    snap = apply_absolute_filters(snap, min_value=MIN_TRADING_VALUE)

    # Volume increase rate
    snap["VolumeIncreaseRate"] = (snap["Volume"] / prev["Volume"].replace(0, np.nan) - 1) * 100

    # Change rate calculations
    snap["IntradayChange"] = (snap["Close"] / snap["Open"] - 1) * 100
    snap["DailyChange"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100

    # Sideways determination (change within +-5%)
    snap["IsSideways"] = (snap["DailyChange"].abs() <= 5)

    # Additional filter: Volume increase >= 50%
    snap = snap[snap["VolumeIncreaseRate"] >= 50]

    if snap.empty:
        logger.debug("trigger_afternoon_volume_surge_flat: No qualifying stocks")
        return pd.DataFrame()

    # Composite score
    scored = normalize_and_score(snap, "VolumeIncreaseRate", "Amount", 0.6, 0.4)

    # Primary filter: Top N by composite score
    candidates = scored.head(top_n)

    # Secondary filter: Sideways stocks only
    result = candidates[candidates["IsSideways"] == True].copy()

    if result.empty:
        logger.debug("trigger_afternoon_volume_surge_flat: No sideways stocks")
        return pd.DataFrame()

    logger.debug(f"Volume surge sideways detected: {len(result)} stocks")
    return enhance_dataframe(result.sort_values("CompositeScore", ascending=False).head(10))


# === Final Selection ===

def select_final_tickers(triggers: dict, trade_date: str = None, use_hybrid: bool = True,
                         lookback_days: int = 10) -> dict:
    """
    Aggregate selected stocks from all triggers and make final selection.

    Hybrid method (use_hybrid=True):
    1. Collect top 10 candidates from each trigger
    2. Calculate agent criteria scores for all candidates
    3. Final score = Composite score (30%) + Agent fit score (70%)
    4. Select #1 from each trigger by final score

    Args:
        triggers: Dict of trigger results (trigger_name -> DataFrame)
        trade_date: Reference trading date (required for hybrid mode)
        use_hybrid: Whether to use hybrid selection (default: True)
        lookback_days: Number of past days for agent scoring

    Returns:
        Dict of final selected stocks
    """
    final_result = {}
    trigger_candidates = {}
    all_tickers = set()

    # Collect candidates from each trigger
    for name, df in triggers.items():
        if not df.empty:
            candidates = df.copy()
            trigger_candidates[name] = candidates
            all_tickers.update(candidates.index.tolist())

    if not trigger_candidates:
        logger.warning("No candidates from any trigger")
        return final_result

    # Hybrid mode: Calculate agent fit scores
    if use_hybrid and trade_date:
        logger.info(f"Hybrid selection mode - calculating agent scores with {lookback_days}-day data")

        for name, candidates_df in trigger_candidates.items():
            scored_df = score_candidates_by_agent_criteria(candidates_df, trade_date,
                                                         lookback_days, trigger_type=name)

            # v1.16.6: Final score = Composite (30%) + Agent (70%)
            if "CompositeScore" in scored_df.columns and "AgentFitScore" in scored_df.columns:
                # Normalize composite score
                cp_max = scored_df["CompositeScore"].max()
                cp_min = scored_df["CompositeScore"].min()
                cp_range = cp_max - cp_min if cp_max > cp_min else 1
                scored_df["CompositeScore_norm"] = (scored_df["CompositeScore"] - cp_min) / cp_range

                # Final score
                scored_df["FinalScore"] = (
                    scored_df["CompositeScore_norm"] * 0.3 +
                    scored_df["AgentFitScore"] * 0.7
                )

                scored_df = scored_df.sort_values("FinalScore", ascending=False)

                # Logging
                logger.info(f"[{name}] Hybrid scoring complete:")
                for ticker in scored_df.index[:3]:
                    company = scored_df.loc[ticker, "CompanyName"] if "CompanyName" in scored_df.columns else ""
                    logger.info(f"  - {ticker} ({company}): "
                               f"Composite={scored_df.loc[ticker, 'CompositeScore']:.3f}, "
                               f"Agent={scored_df.loc[ticker, 'AgentFitScore']:.3f}, "
                               f"Final={scored_df.loc[ticker, 'FinalScore']:.3f}, "
                               f"R/R={scored_df.loc[ticker, 'RiskRewardRatio']:.2f}, "
                               f"SL%={scored_df.loc[ticker, 'StopLossPct']*100:.1f}%")

            trigger_candidates[name] = scored_df

    # Final selection
    selected_tickers = set()
    score_column = "FinalScore" if use_hybrid and trade_date else "CompositeScore"

    # Select top 1 from each trigger
    for name, df in trigger_candidates.items():
        if not df.empty and len(selected_tickers) < 3:
            if score_column in df.columns:
                sorted_df = df.sort_values(score_column, ascending=False)
            else:
                sorted_df = df

            for ticker in sorted_df.index:
                if ticker not in selected_tickers:
                    final_result[name] = sorted_df.loc[[ticker]]
                    selected_tickers.add(ticker)
                    logger.info(f"[{name}] Final selection: {ticker}")
                    break

    # If less than 3, add more by overall score
    if len(selected_tickers) < 3:
        all_candidates = []
        for name, df in trigger_candidates.items():
            for ticker in df.index:
                if ticker not in selected_tickers:
                    score = df.loc[ticker, score_column] if score_column in df.columns else 0
                    all_candidates.append((name, ticker, score, df.loc[[ticker]]))

        all_candidates.sort(key=lambda x: x[2], reverse=True)

        for trigger_name, ticker, _, ticker_df in all_candidates:
            if ticker not in selected_tickers and len(selected_tickers) < 3:
                if trigger_name in final_result:
                    final_result[trigger_name] = pd.concat([final_result[trigger_name], ticker_df])
                else:
                    final_result[trigger_name] = ticker_df
                selected_tickers.add(ticker)
                logger.info(f"[{trigger_name}] Additional selection: {ticker}")

    return final_result


# === Batch Execution ===

def run_batch(trigger_time: str, log_level: str = "INFO", output_file: str = None):
    """
    Execute trigger batch.

    Args:
        trigger_time: "morning" or "afternoon"
        log_level: Logging level
        output_file: Path to save results as JSON (optional)
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    ch.setLevel(numeric_level)
    logger.info(f"Log level: {log_level.upper()}")

    today_str = datetime.datetime.today().strftime("%Y%m%d")
    trade_date = get_nearest_business_day(today_str, prev=True)
    logger.info(f"Batch reference date: {trade_date}")

    # Get S&P 500 + NASDAQ-100 tickers (combined, deduplicated)
    tickers = get_major_tickers()

    try:
        snapshot = get_snapshot(trade_date, tickers)
    except ValueError as e:
        logger.error(f"Snapshot retrieval failed: {e}")
        # Try previous day
        trade_date = get_nearest_business_day(
            (datetime.datetime.strptime(trade_date, '%Y%m%d') - datetime.timedelta(days=1)).strftime('%Y%m%d'),
            prev=True
        )
        logger.info(f"Retry with date: {trade_date}")
        snapshot = get_snapshot(trade_date, tickers)

    prev_snapshot, prev_date = get_previous_snapshot(trade_date, tickers)
    logger.debug(f"Previous trading day: {prev_date}")

    cap_df = get_market_cap_df(tickers)
    logger.debug(f"Market cap data: {len(cap_df)} stocks")

    if trigger_time == "morning":
        logger.info("=== Morning Batch Execution ===")
        res1 = trigger_morning_volume_surge(trade_date, snapshot, prev_snapshot, cap_df)
        res2 = trigger_morning_gap_up_momentum(trade_date, snapshot, prev_snapshot, cap_df)
        res3 = trigger_morning_value_to_cap_ratio(trade_date, snapshot, prev_snapshot, cap_df)
        triggers = {
            "Volume Surge Top": res1,
            "Gap Up Momentum Top": res2,
            "Value-to-Cap Ratio Top": res3
        }
    elif trigger_time == "afternoon":
        logger.info("=== Afternoon Batch Execution ===")
        res1 = trigger_afternoon_daily_rise_top(trade_date, snapshot, prev_snapshot, cap_df)
        res2 = trigger_afternoon_closing_strength(trade_date, snapshot, prev_snapshot, cap_df)
        res3 = trigger_afternoon_volume_surge_flat(trade_date, snapshot, prev_snapshot, cap_df)
        triggers = {
            "Intraday Rise Top": res1,
            "Closing Strength Top": res2,
            "Volume Surge Sideways": res3
        }
    else:
        logger.error("Invalid trigger_time. Use 'morning' or 'afternoon'.")
        return

    # Log trigger results
    for name, df in triggers.items():
        if df.empty:
            logger.info(f"{name}: No qualifying stocks")
        else:
            logger.info(f"{name} detected ({len(df)} stocks):")
            for ticker in df.index:
                company = df.loc[ticker, "CompanyName"] if "CompanyName" in df.columns else ""
                logger.info(f"  - {ticker} ({company})")

    # Final selection
    final_results = select_final_tickers(triggers, trade_date=trade_date)

    # Save to JSON if requested
    if output_file:
        output_data = {}

        for trigger_type, stocks_df in final_results.items():
            if not stocks_df.empty:
                if trigger_type not in output_data:
                    output_data[trigger_type] = []

                for ticker in stocks_df.index:
                    stock_info = {
                        "ticker": ticker,
                        "name": stocks_df.loc[ticker, "CompanyName"] if "CompanyName" in stocks_df.columns else "",
                        "current_price": float(stocks_df.loc[ticker, "Close"]) if "Close" in stocks_df.columns else 0,
                        "change_rate": float(stocks_df.loc[ticker, "DailyChange"]) if "DailyChange" in stocks_df.columns else 0,
                        "volume": int(stocks_df.loc[ticker, "Volume"]) if "Volume" in stocks_df.columns else 0,
                        "trade_value": float(stocks_df.loc[ticker, "Amount"]) if "Amount" in stocks_df.columns else 0,
                    }

                    # Trigger-specific data
                    if "VolumeIncreaseRate" in stocks_df.columns and trigger_type == "Volume Surge Top":
                        stock_info["volume_increase"] = float(stocks_df.loc[ticker, "VolumeIncreaseRate"])
                    elif "GapUpRate" in stocks_df.columns:
                        stock_info["gap_rate"] = float(stocks_df.loc[ticker, "GapUpRate"])
                    elif "ValueCapRatio" in stocks_df.columns:
                        stock_info["value_cap_ratio"] = float(stocks_df.loc[ticker, "ValueCapRatio"])
                        stock_info["market_cap"] = float(stocks_df.loc[ticker, "MarketCap"])
                    elif "ClosingStrength" in stocks_df.columns:
                        stock_info["closing_strength"] = float(stocks_df.loc[ticker, "ClosingStrength"])

                    # Agent score info (hybrid mode)
                    if "AgentFitScore" in stocks_df.columns:
                        stock_info["agent_fit_score"] = float(stocks_df.loc[ticker, "AgentFitScore"])
                        stock_info["risk_reward_ratio"] = float(stocks_df.loc[ticker, "RiskRewardRatio"])
                        stock_info["stop_loss_pct"] = float(stocks_df.loc[ticker, "StopLossPct"]) * 100
                        stock_info["stop_loss_price"] = float(stocks_df.loc[ticker, "StopLossPrice"])
                        stock_info["target_price"] = float(stocks_df.loc[ticker, "TargetPrice"])
                    if "FinalScore" in stocks_df.columns:
                        stock_info["final_score"] = float(stocks_df.loc[ticker, "FinalScore"])

                    output_data[trigger_type].append(stock_info)

        # Metadata
        output_data["metadata"] = {
            "run_time": datetime.datetime.now().isoformat(),
            "trigger_mode": trigger_time,
            "trade_date": trade_date,
            "selection_mode": "hybrid",
            "lookback_days": 10,
            "market": "US",
            "min_market_cap_usd": MIN_MARKET_CAP,
            "min_trading_value_usd": MIN_TRADING_VALUE
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Results saved to {output_file}")

    return final_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="US Trigger Batch Execution")
    parser.add_argument("mode", help="Execution mode (morning or afternoon)")
    parser.add_argument("log_level", nargs="?", default="INFO", help="Logging level")
    parser.add_argument("--output", help="Output JSON file path")

    args = parser.parse_args()

    run_batch(args.mode, args.log_level, args.output)
