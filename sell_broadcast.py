"""Publish loop-driven (Hardstop hard-stop / Trend-exit trend-exit) sells to the SAME
Redis + GCP Pub/Sub signal stream the batch uses.

Background: the signal publish lived only in the batch orchestration
(`update_holdings` + the buy path), so intraday loop sells were executed but NEVER
broadcast. With Hardstop/Trend-exit LIVE, subscribers (who mirror the system's portfolio on
their own KIS keys) missed every hard-stop / trend-exit exit and their positions
diverged — holding names the main system had already sold.

The simulator close is the broadcast source of truth (Rocky's rule: sim = 방송,
real follows), so callers publish on `sim_ok`, independent of whether our own KIS
leg filled. Both legs are best-effort: any failure is logged and swallowed so the
loop is never broken (mirrors the batch's non-critical publish blocks).

Lives at project root (not under the messaging/ package) so importing it does not
trigger messaging/__init__'s eager publisher imports; the actual publishers are
imported lazily at call time and any failure (e.g. transport unconfigured) is
caught.
"""
import logging

logger = logging.getLogger("loop_publish")


async def publish_loop_sell(market: str, ticker: str, company_name: str, price: float,
                            buy_price: float, sell_reason: str, trade_result=None) -> None:
    """Best-effort: broadcast a loop sell to Redis Streams + GCP Pub/Sub.

    Auto-skips when the respective transport is unconfigured (the underlying
    publishers no-op without UPSTASH_REDIS_* / GCP_PROJECT_ID+GCP_PUBSUB_TOPIC_ID).
    """
    mkt = (market or "KR").upper()
    try:
        profit_rate = ((price - buy_price) / buy_price * 100.0) if buy_price else 0.0
    except Exception:
        profit_rate = 0.0

    # Redis Streams
    try:
        from messaging.redis_signal_publisher import publish_sell_signal
        await publish_sell_signal(
            ticker=ticker, company_name=company_name, price=price, buy_price=buy_price,
            profit_rate=profit_rate, sell_reason=sell_reason, trade_result=trade_result,
            market=mkt,
        )
    except Exception as e:
        logger.warning("[loop-publish] Redis sell signal failed (non-critical): %s", e)

    # GCP Pub/Sub
    try:
        from messaging.gcp_pubsub_signal_publisher import publish_sell_signal as gcp_publish_sell_signal
        await gcp_publish_sell_signal(
            ticker=ticker, company_name=company_name, price=price, buy_price=buy_price,
            profit_rate=profit_rate, sell_reason=sell_reason, trade_result=trade_result,
            market=mkt,
        )
    except Exception as e:
        logger.warning("[loop-publish] GCP sell signal failed (non-critical): %s", e)
