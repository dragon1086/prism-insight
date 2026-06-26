"""Loop sell broadcast helper must be non-fatal and pass the right args."""
import asyncio
import sys
import types

import loop_publish as lp


def test_publish_loop_sell_non_fatal_when_unconfigured():
    """With Redis/GCP transports unimportable/unconfigured, it must not raise."""
    # messaging.* publishers require upstash/google libs not present here -> the
    # lazy imports inside the helper raise and must be swallowed.
    res = asyncio.run(lp.publish_loop_sell(
        market="US", ticker="MU", company_name="Micron", price=100.0,
        buy_price=110.0, sell_reason="TIER1:stop", trade_result={"success": True},
    ))
    assert res is None  # completed without raising


def test_publish_loop_sell_forwards_args_and_profit_rate(monkeypatch):
    """Both legs get called with normalized market + a computed profit_rate."""
    calls = []

    async def fake_pub(**kwargs):
        calls.append(kwargs)
        return "id"

    # Inject a fake messaging package + the two publisher submodules so the helper's
    # lazy `from messaging.X import publish_sell_signal` resolves to our fakes
    # without running the real (dependency-heavy) messaging/__init__.
    fake_pkg = types.ModuleType("messaging")
    redis_mod = types.ModuleType("messaging.redis_signal_publisher")
    redis_mod.publish_sell_signal = fake_pub
    gcp_mod = types.ModuleType("messaging.gcp_pubsub_signal_publisher")
    gcp_mod.publish_sell_signal = fake_pub
    monkeypatch.setitem(sys.modules, "messaging", fake_pkg)
    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_mod)
    monkeypatch.setitem(sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_mod)

    asyncio.run(lp.publish_loop_sell(
        market="kr", ticker="005930", company_name="Samsung", price=90.0,
        buy_price=100.0, sell_reason="TIER1.5_MA50", trade_result={"success": True},
    ))
    assert len(calls) == 2  # redis + gcp
    for c in calls:
        assert c["ticker"] == "005930"
        assert c["market"] == "KR"            # normalized upper
        assert c["sell_reason"] == "TIER1.5_MA50"
        assert round(c["profit_rate"], 1) == -10.0  # (90-100)/100*100
