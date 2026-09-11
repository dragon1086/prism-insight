"""
Helper Utilities for Stock Tracking

Standalone functions for ticker/price/sector operations.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def get_requested_session_prices(tickers) -> Dict[str, float]:
    """Read only requested symbols at the latest verified KIS session date.

    Per-symbol historical candles work on weekends, unlike current-only
    full-market snapshots. Missing or mismatched observations are omitted.
    """
    import math
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from cores.market_data import get_market_ohlcv_by_date, get_nearest_business_day_in_a_week

    symbols = sorted({str(ticker) for ticker in tickers if ticker})
    if not symbols:
        return {}
    try:
        day = get_nearest_business_day_in_a_week(
            datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d"), prev=True
        )
    except Exception as exc:
        logger.warning("KIS session date unavailable: %s", type(exc).__name__)
        return {}
    prices = {}
    for ticker in symbols:
        try:
            frame = get_market_ohlcv_by_date(day, day, ticker)
            if frame is None or frame.empty:
                continue
            rows = frame.loc[frame.index.strftime("%Y%m%d") == day]
            if len(rows) != 1:
                continue
            price = float(rows.iloc[0]["Close"])
            if math.isfinite(price) and price > 0:
                prices[ticker] = price
        except Exception as exc:
            logger.warning("KIS session candle unavailable for %s: %s", ticker, type(exc).__name__)
    return prices


def extract_ticker_info(report_path: str) -> Tuple[str, str]:
    """
    Extract ticker code and company name from report file path.

    Args:
        report_path: Report file path

    Returns:
        Tuple[str, str]: Ticker code, company name
    """
    try:
        file_name = Path(report_path).stem
        pattern = r'^([A-Za-z0-9]+)_([^_]+)'
        match = re.match(pattern, file_name)

        if match:
            ticker = match.group(1)
            company_name = match.group(2)
            return ticker, company_name
        else:
            # Legacy fallback
            parts = file_name.split('_')
            if len(parts) >= 2:
                return parts[0], parts[1]

        logger.error(f"Cannot extract ticker info from filename: {file_name}")
        return "", ""
    except Exception as e:
        logger.error(f"Error extracting ticker info: {str(e)}")
        return "", ""


async def get_current_stock_price(cursor, ticker: str, account_key: str | None = None) -> float:
    """
    Get current stock price.

    Args:
        cursor: SQLite cursor
        ticker: Stock code

    Returns:
        float: Current stock price
    """
    price = await _get_price_from_kis(ticker)
    if price > 0:
        return price
    return _get_last_price_from_db(cursor, ticker, account_key=account_key)


async def _get_price_from_kis(ticker: str) -> float:
    """Read-only KIS quote, independent of order eligibility or funding.

    KIS is an independent provider whose credentials are already configured
    wherever the tracking agents run (same creds the order path uses).
    Returns 0.0 on any failure — caller falls back to the last DB price.
    """
    import asyncio
    try:
        from trading.domestic_stock_trading import AsyncTradingContext
        async with AsyncTradingContext() as trading:
            info = await asyncio.to_thread(trading.get_current_price, ticker)
        price = float((info or {}).get("current_price") or 0)
        if price > 0:
            logger.warning(f"{ticker} current price via KIS: {price:,.0f} KRW")
        return price
    except Exception as e:
        logger.error(f"{ticker} KIS price fallback failed: {e}")
        return 0.0


def _get_last_price_from_db(cursor, ticker: str, account_key: str | None = None) -> float:
    """Get last saved price from DB as fallback."""
    try:
        if account_key:
            cursor.execute(
                "SELECT current_price FROM stock_holdings WHERE ticker = ? AND account_key = ?",
                (ticker, account_key)
            )
        else:
            cursor.execute(
                "SELECT current_price FROM stock_holdings WHERE ticker = ?",
                (ticker,)
            )
        row = cursor.fetchone()
        if row and row[0]:
            last_price = float(row[0])
            logger.warning(f"{ticker} price query failed, using last price: {last_price}")
            return last_price
    except:
        pass
    return 0.0


async def get_trading_value_rank_change(ticker: str) -> Tuple[float | None, str]:
    """Unknown until two comparable dated full-universe KIS snapshots exist.

    The current snapshot cannot establish the previous session's rank. Avoid
    scanning every symbol only to discover the historical capability is absent.
    This is supplemental prompt evidence, not an entry gate.
    """
    return None, "UNKNOWN: KIS 거래대금 순위 변화 비교용 과거 전체시장 스냅샷 미확보"



def is_ticker_in_holdings(cursor, ticker: str, account_key: str | None = None) -> bool:
    """
    Check if stock is already in holdings.

    Args:
        cursor: SQLite cursor
        ticker: Stock code

    Returns:
        bool: True if holding, False otherwise
    """
    try:
        if account_key:
            cursor.execute(
                "SELECT COUNT(*) FROM stock_holdings WHERE ticker = ? AND account_key = ?",
                (ticker, account_key)
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM stock_holdings WHERE ticker = ?",
                (ticker,)
            )
        count = cursor.fetchone()[0]
        return count > 0
    except Exception as e:
        logger.error(f"Error checking holdings: {str(e)}")
        return False


def get_current_slots_count(cursor, account_key: str | None = None) -> int:
    """Get current number of holdings."""
    try:
        if account_key:
            cursor.execute("SELECT COUNT(*) FROM stock_holdings WHERE account_key = ?", (account_key,))
        else:
            cursor.execute("SELECT COUNT(*) FROM stock_holdings")
        count = cursor.fetchone()[0]
        return count
    except Exception as e:
        logger.error(f"Error querying holdings count: {str(e)}")
        return 0


# ── Pyramiding (#288) ──────────────────────────────────────────────────────
# Strong-bull add-on / pyramiding helpers. Each pyramid entry is an independent
# stock_holdings row. The gate below decides whether the "already holding" block
# may be bypassed for an additional entry.

# Market regimes in which pyramiding (adding to a winning position) is allowed.
PYRAMID_ALLOWED_REGIMES = ("strong_bull", "parabolic")
# Minimum aggregate profit (%) of the existing position to allow an add.
PYRAMID_MIN_PROFIT_PCT = 5.0
# Maximum number of rows (entries) per ticker/account (initial + up to 2 adds).
PYRAMID_MAX_ROWS = 3
# Table name for holdings (overridable for US: "us_stock_holdings").
_HOLDINGS_TABLE = "stock_holdings"


def _stored_pyramid_ownership(raw):
    """Classify stored evidence only; never adopt an unowned legacy campaign."""
    if raw is None or raw == "":
        return "LEGACY_OR_UNOWNED"
    try:
        if isinstance(raw, str):
            if len(raw) > 1024 * 1024:
                return "UNKNOWN"
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            return "UNKNOWN"
        policy = raw.get("regime_entry_policy")
        pilot = raw.get("pilot")
        policy = {} if policy is None else policy
        pilot = {} if pilot is None else pilot
        if not isinstance(policy, dict) or not isinstance(pilot, dict):
            return "UNKNOWN"
        owners = [container[key] for container, key in (
            (raw, "split_owner"), (raw, "_split_owner"), (raw, "campaign_owner"),
            (policy, "owner"), (pilot, "owner")) if key in container]
        if any(owner == "split-pilot-v1" for owner in owners) or policy.get("mode") == "rebound_pilot":
            return "SPLIT_PILOT"
        if owners or pilot or ("mode" in policy and policy["mode"] != "normal"):
            return "UNKNOWN"
        if "position_fraction" in policy:
            fraction = policy["position_fraction"]
            if isinstance(fraction, bool):
                return "UNKNOWN"
            number = Decimal(str(fraction))
            if not number.is_finite():
                return "UNKNOWN"
            if number == Decimal(".5"):
                return "SPLIT_PILOT"
            if number != 1:
                return "UNKNOWN"
        if policy and not (policy.get("mode") == "normal" and "position_fraction" in policy):
            return "UNKNOWN"
        return "LEGACY_OR_UNOWNED"
    except (ValueError, TypeError, InvalidOperation, RecursionError):
        return "UNKNOWN"


def get_existing_position_for_ticker(
    cursor,
    ticker: str,
    account_key: str | None = None,
    table_name: str = _HOLDINGS_TABLE,
) -> Dict[str, Any]:
    """Aggregate the existing holding for a ticker/account.

    Returns a dict with:
        row_count: number of existing rows for (ticker, account)
        avg_buy_price: simple average buy_price across those rows (0 if none)
        pyramid_ownership: safe stored-metadata classification, never ownership adoption
    Used by the pyramiding add-gate.

    NOTE (#288, intentional): ``avg_buy_price`` is a SIMPLE MEAN of per-row entry
    prices, NOT a share-weighted average. The independent-row model deliberately
    stores no per-row quantity in ``stock_holdings``, and each add is ~1 unit, so
    the simple mean is an accurate-enough proxy for both the +5% profit gate and
    the Telegram "누적 평단" display.
    """
    try:
        # Identifiers cannot be bound as SQL values. Only the two documented
        # holdings tables are supported; no caller string enters SQL text.
        queries = {
            "stock_holdings": (
                "SELECT buy_price, scenario FROM stock_holdings WHERE ticker = ?",
                "SELECT buy_price, scenario FROM stock_holdings WHERE ticker = ? AND account_key = ?",
            ),
            "us_stock_holdings": (
                "SELECT buy_price, scenario FROM us_stock_holdings WHERE ticker = ?",
                "SELECT buy_price, scenario FROM us_stock_holdings WHERE ticker = ? AND account_key = ?",
            ),
        }
        if table_name not in queries:
            raise ValueError("unsupported holdings table")
        query = queries[table_name][1 if account_key else 0]
        cursor.execute(query, (ticker, account_key) if account_key else (ticker,))
        rows = cursor.fetchall()
        ownership = {_stored_pyramid_ownership(row[1] if len(row) > 1 else None) for row in rows}
        summary = "SPLIT_PILOT" if "SPLIT_PILOT" in ownership else ("UNKNOWN" if "UNKNOWN" in ownership else "LEGACY_OR_UNOWNED")
        prices = [float(r[0]) for r in rows if r[0] is not None]
        row_count = len(prices)
        avg_buy_price = (sum(prices) / row_count) if row_count else 0.0
        return {"row_count": row_count, "avg_buy_price": avg_buy_price, "pyramid_ownership": summary}
    except Exception as e:
        logger.error(f"Error querying existing position for {ticker}: {str(e)}")
        return {"row_count": 0, "avg_buy_price": 0.0, "pyramid_ownership": "UNKNOWN"}


def _regime_label(market_condition: str | None) -> str:
    """Extract the leading regime label from a market_condition string.

    Real values look like ``"strong_bull: 보고서 기준 KOSPI가 20일선 상회..."`` —
    the regime token (with underscore) comes before the first ':'. We take the
    substring before the first ':', normalise it, and canonicalise separators so
    a hyphen/space variant ("strong-bull"/"strong bull") maps to "strong_bull".
    Fail-closed: anything unrecognised returns "" (no add).
    """
    if not market_condition or not isinstance(market_condition, str):
        return ""
    # Take the regime token before the first ':' (rest is the human description).
    text = market_condition.split(":", 1)[0].strip().lower()
    # Canonicalise separators: hyphen/space -> underscore (e.g. "strong-bull").
    text = text.replace("-", "_").replace(" ", "_")
    return text


def pyramid_add_possible_ignoring_regime(
    existing_avg_buy_price: float,
    current_price: float,
    existing_row_count: int,
    min_profit_pct: float = PYRAMID_MIN_PROFIT_PCT,
    max_rows: int = PYRAMID_MAX_ROWS,
    *, ownership: str | None = None,
) -> Tuple[bool, str]:
    """Regime-independent necessary conditions for a pyramiding add (#288).

    These are exactly conditions (2) row-count and (3) aggregate-profit of
    ``evaluate_pyramid_add_gate`` — the parts that need NO LLM output (computed
    from the DB position + current price only). If this returns False, the full
    gate can never allow an add regardless of regime, so a held stock can be
    skipped BEFORE its per-stock scenario LLM runs, with the same end result.
    Single source of truth: ``evaluate_pyramid_add_gate`` delegates here.
    """
    if ownership == "SPLIT_PILOT":
        return False, "LEGACY_PYRAMID_BLOCKED_SPLIT_PILOT"
    if ownership == "UNKNOWN":
        return False, "LEGACY_PYRAMID_OWNERSHIP_UNKNOWN"
    if existing_row_count >= max_rows:
        return False, f"row count {existing_row_count} >= max {max_rows}"

    if not existing_avg_buy_price or existing_avg_buy_price <= 0 or not current_price or current_price <= 0:
        return False, "insufficient price data for profit check"

    profit_pct = (current_price - existing_avg_buy_price) / existing_avg_buy_price * 100.0
    if profit_pct < min_profit_pct:
        return False, f"profit {profit_pct:.2f}% < required {min_profit_pct:.1f}%"

    return True, f"profit={profit_pct:.2f}%, rows={existing_row_count}"


def evaluate_pyramid_add_gate(
    market_condition: str | None,
    existing_avg_buy_price: float,
    current_price: float,
    existing_row_count: int,
    min_profit_pct: float = PYRAMID_MIN_PROFIT_PCT,
    max_rows: int = PYRAMID_MAX_ROWS,
    *, ownership: str | None = None,
) -> Tuple[bool, str]:
    """Pure add-gate for pyramiding (#288).

    Returns (allowed, reason). All of the following must hold to allow:
      1) regime in PYRAMID_ALLOWED_REGIMES (parsed from market_condition prefix)
      2) existing aggregate position profit >= min_profit_pct
      3) existing_row_count < max_rows

    Does NOT include the buy-agent Enter/score/sector checks — those are applied
    independently by the normal buy path.
    """
    if ownership == "SPLIT_PILOT":
        return False, "LEGACY_PYRAMID_BLOCKED_SPLIT_PILOT"
    if ownership == "UNKNOWN":
        return False, "LEGACY_PYRAMID_OWNERSHIP_UNKNOWN"
    regime = _regime_label(market_condition)
    if regime not in PYRAMID_ALLOWED_REGIMES:
        return False, f"regime '{regime or 'unknown'}' not in {PYRAMID_ALLOWED_REGIMES}"

    # Conditions (2) and (3) are regime-independent — delegate so the cheap
    # pre-gate that skips the scenario LLM stays in lock-step with this gate.
    ok, reason = pyramid_add_possible_ignoring_regime(
        existing_avg_buy_price, current_price, existing_row_count, min_profit_pct, max_rows,
        ownership=ownership,
    )
    if not ok:
        return False, reason

    return True, f"add allowed (regime={regime}, {reason})"


def compute_fractional_sell_quantity(total_quantity: int, remaining_rows: int) -> int:
    """Shares to sell for one row when ``remaining_rows`` rows remain (#288).

    - remaining_rows <= 1  -> sell all (total_quantity)  [zero regression for non-pyramided]
    - remaining_rows >  1  -> floor(total_quantity / remaining_rows)

    Recomputed live at each sell so the LAST remaining row always sweeps the
    remainder (since it sees remaining_rows == 1 and sells everything left).
    """
    try:
        total = int(total_quantity)
        n = int(remaining_rows)
    except (TypeError, ValueError):
        return int(total_quantity) if total_quantity else 0
    if total <= 0:
        return 0
    if n <= 1:
        return total
    return total // n


# Apply ratio guard only when portfolio is large enough that the ratio is meaningful.
# With <4 holdings, a single same-sector position naturally produces 25-100% — blocking
# every additional buy in that sector even though absolute count is well under the cap.
MIN_HOLDINGS_FOR_RATIO_CHECK = 4


def check_sector_diversity(cursor, sector: str, max_same_sector: int, concentration_ratio: float, account_key: str | None = None) -> bool:
    """
    Check for over-concentration in same sector.

    The absolute cap (`max_same_sector`) is always enforced. The ratio cap
    (`concentration_ratio`) is only applied once the portfolio holds at least
    `MIN_HOLDINGS_FOR_RATIO_CHECK` positions, so that small portfolios are not
    blocked by trivially high ratios (e.g. 1/2 = 50%).

    Args:
        cursor: SQLite cursor
        sector: Sector name
        max_same_sector: Maximum holdings in same sector
        concentration_ratio: Sector concentration limit ratio

    Returns:
        bool: Investment availability (True: available, False: over-concentrated)
    """
    try:
        if not sector or sector == "Unknown":
            return True

        if account_key:
            cursor.execute("SELECT scenario FROM stock_holdings WHERE account_key = ?", (account_key,))
        else:
            cursor.execute("SELECT scenario FROM stock_holdings")
        holdings_scenarios = cursor.fetchall()

        sectors = []
        for row in holdings_scenarios:
            if row[0]:
                try:
                    scenario_data = json.loads(row[0])
                    if 'sector' in scenario_data:
                        sectors.append(scenario_data['sector'])
                except:
                    pass

        from prism_core.sector_names import sectors_overlap
        same_sector_count = sum(1 for s in sectors if s and sectors_overlap(s, sector))

        if same_sector_count >= max_same_sector:
            logger.warning(
                f"Sector '{sector}' absolute cap reached: "
                f"holding {same_sector_count} stocks (max {max_same_sector})"
            )
            return False

        if len(sectors) >= MIN_HOLDINGS_FOR_RATIO_CHECK and \
           same_sector_count / len(sectors) >= concentration_ratio:
            logger.warning(
                f"Sector '{sector}' ratio cap reached: "
                f"{same_sector_count}/{len(sectors)} = "
                f"{same_sector_count/len(sectors)*100:.0f}% "
                f"(limit {concentration_ratio*100:.0f}%)"
            )
            return False

        return True

    except Exception as e:
        logger.error(f"Error checking sector diversity: {str(e)}")
        return True


def parse_price_value(value: Any) -> float:
    """
    Parse price value and convert to number.

    Args:
        value: Price value (number, string, range, etc.)

    Returns:
        float: Parsed price (0 on failure)
    """
    try:
        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            value = value.replace(',', '')

            range_patterns = [
                r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)',
                r'(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)',
            ]

            for pattern in range_patterns:
                match = re.search(pattern, value)
                if match:
                    low = float(match.group(1))
                    high = float(match.group(2))
                    return (low + high) / 2

            number_match = re.search(r'(\d+(?:\.\d+)?)', value)
            if number_match:
                return float(number_match.group(1))

        return 0
    except Exception as e:
        logger.warning(f"Failed to parse price value: {value} - {str(e)}")
        return 0


def default_scenario(error: str = "trading_scenario_unavailable") -> Dict[str, Any]:
    """Return a safe scenario that is explicitly marked as incomplete."""
    return {
        "portfolio_analysis": "Analysis failed",
        "analysis_status": "failed",
        "analysis_error": error,
        "buy_score": 0,
        "decision": "No Entry",
        "target_price": 0,
        "stop_loss": 0,
        "investment_period": "Short-term",
        "rationale": "Analysis failed",
        "sector": "Unknown",
        "considerations": "Analysis failed"
    }
