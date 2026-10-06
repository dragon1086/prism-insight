import asyncio
import pytest

from prism_core.oneil_current_capture import capture_current_record
from prism_core.oneil_dispatcher import drive, dispatch_reserved
from prism_core.oneil_dispatcher import _validator, _clock
from test_oneil_execution import start, reserve, receipt, ack


class Broker:
    instances = []
    def __init__(self, account_name, store, exchange="NASD"):
        self.store = store
        self.calls = []
        self.quantity = 0
        self.instances.append(self)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def holdings(self, symbol):
        return dict(status="OK", account_id="account", symbol=symbol, quantity=self.quantity,
                    source_ref="broker", observed_at="2026-09-25T13:41:00Z")
    async def submit(self, intent, reservation, *, quote_validator):
        self.calls.append(intent.side)
        quote_validator(float(intent.limit_price))
        self.store.claim_reservation(reservation, intent, expected_side=intent.side)
        self.store.record_result(intent, status="SUBMITTED", accepted=True,
                                 response=dict(order_no="123", quantity=intent.quantity, price=intent.limit_price))
        self.quantity += intent.quantity if intent.side == "BUY" else -intent.quantity
        return dict(success=True, order_no="123")
    async def reconcile(self, intent, order_id, order_date):
        return receipt(dict(intent=intent), order=order_id)
    async def cancel(self, intent, order_id, order_date):
        self.calls.append("CANCEL")
        return dict(accepted=True)


def test_shadow_same_driver_never_constructs_broker(tmp_path):
    core, cid, args, _ = start(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("real broker in SHADOW")
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                               broker_factory=forbidden, now=args["now"]))
    assert result["status"] == "SHADOW_SIMULATED"
    assert result["campaign"]["confirmed_quantity"] == 48


def test_live_driver_uses_shared_store_reservation_ack_and_fill(tmp_path):
    core, cid, args, _ = start(tmp_path, "LIVE")
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                               broker_factory=Broker, now=args["now"], authorize_add=lambda: True))
    assert result["campaign"]["confirmed_quantity"] == 48
    assert Broker.instances[-1].store is core.store
    assert Broker.instances[-1].calls == ["BUY"]


def test_before_submit_links_strategy_before_external_submission(tmp_path):
    core, cid, args, _ = start(tmp_path, "LIVE")
    async def link(reserved):
        assert reserved["intent"].side == "BUY"
        core.link_strategy_position(cid, "strategy:123")
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                               broker_factory=Broker, now=args["now"], before_submit=link, authorize_add=lambda: True))
    assert result["campaign"]["strategy_position_id"] == "strategy:123"
    assert result["campaign"]["confirmed_quantity"] == 48


def test_strategy_link_failure_releases_without_submit(tmp_path):
    core, cid, args, _ = start(tmp_path, "LIVE")
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                               broker_factory=Broker, now=args["now"], before_submit=lambda _: False, authorize_add=lambda: True))
    assert result["status"] == "STRATEGY_LINK_REFUSED"
    assert Broker.instances[-1].calls == []
    assert result["campaign"]["orders"][0]["status"] == "CANCELLED"


def test_stale_pre_reserved_input_never_submits(tmp_path):
    core, cid, args, account = start(tmp_path, "LIVE")
    reserved = reserve(core, cid, args, account)
    result = asyncio.run(dispatch_reserved(core, reserved, account_name="account", broker_factory=Broker,
                                           now="2026-09-25T13:45:00Z", authorize_add=lambda: True))
    assert result["status"] == "SUBMISSION_UNCONFIRMED"
    assert Broker.instances[-1].calls == []
    assert result["campaign"]["orders"][0]["status"] == "CANCELLED"


def test_changed_quote_blocks_and_releases_unsubmitted(tmp_path):
    core, cid, args, account = start(tmp_path, "LIVE")
    reserved = reserve(core, cid, args, account)
    class Changed(Broker):
        async def submit(self, intent, reservation, *, quote_validator):
            quote_validator(105)
            raise AssertionError("must reject")
    result = asyncio.run(dispatch_reserved(core, reserved, account_name="account", broker_factory=Changed, now=args["now"], authorize_add=lambda: True))
    assert result["status"] == "SUBMISSION_UNCONFIRMED"
    assert result["campaign"]["confirmed_quantity"] == 0


def test_restart_created_released_never_blind_resubmit(tmp_path):
    core, cid, args, account = start(tmp_path, "LIVE")
    reserved = reserve(core, cid, args, account)
    args["source"] = "regular"
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                               broker_factory=Broker, now=args["now"]))
    assert Broker.instances[-1].calls == []
    assert core.snapshot(cid)["orders"][0]["status"] == "CANCELLED"
    assert reserved["intent"].id == core.snapshot(cid)["orders"][0]["intent"]["id"]
    assert result["status"] == "WAIT"


def test_cancel_supplier_failure_does_not_suppress_known_share_protection(tmp_path):
    core, cid, args, account = start(tmp_path, "LIVE")
    buy = reserve(core, cid, args, account)
    ack(core, buy, "123")
    core.reconcile(cid, buy["intent"].id, receipt(buy, quantity=12, status="PARTIAL", order="123"))
    core.request_exit(cid, reason="PROTECTIVE_STOP")
    args["now"] = "2026-09-25T13:41:01Z"
    class FailingCancel(Broker):
        async def holdings(self, symbol):
            result = await super().holdings(symbol)
            result["quantity"] = 12
            return result
        async def reconcile(self, intent, order_id, order_date):
            if intent.side == "BUY":
                raise RuntimeError("provider unavailable")
            return await super().reconcile(intent, order_id, order_date)
        async def cancel(self, *args):
            raise RuntimeError("cancel unknown")
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                               broker_factory=FailingCancel, now=args["now"], allow_add=False))
    assert Broker.instances[-1].calls == ["SELL"]
    assert result["campaign"]["confirmed_quantity"] == 0
    assert result["campaign"]["status"] == "EXIT_PENDING"


def test_unknown_submission_is_never_retried(tmp_path):
    core, cid, args, _ = start(tmp_path, "LIVE")
    # Both invocations share one logical clock. Re-anchoring a string timestamp
    # to separate wall-clock runtimes can move the second evaluation backwards.
    clock = lambda: args["now"]
    class Unknown(Broker):
        async def submit(self, intent, reservation, *, quote_validator):
            self.calls.append(intent.side)
            self.store.claim_reservation(reservation, intent, expected_side=intent.side)
            self.store.record_result(intent, status="UNKNOWN", accepted=False, response={})
            return dict(success=False)
    asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                       broker_factory=Unknown, now=clock, authorize_add=lambda: True))
    args["source"] = "regular"
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account",
                               broker_factory=Unknown, now=clock, authorize_add=lambda: True))
    assert result["status"] == "RECONCILE_REQUIRED"
    assert Broker.instances[-1].calls == []


def test_shadow_crash_after_virtual_ack_recovers_without_resubmission(tmp_path):
    core, cid, args, account = start(tmp_path)
    reserved = reserve(core, cid, args, account)
    ack(core, reserved, "SHADOW:" + reserved["intent"].id)
    args["source"] = "regular"
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account", now=args["now"]))
    assert result["campaign"]["confirmed_quantity"] == 48
    assert len(core.snapshot(cid)["orders"]) == 1


def test_buy_lower_quote_within_policy_keeps_reserved_limit_quantity(tmp_path):
    core, cid, args, account = start(tmp_path, "LIVE")
    reserved = reserve(core, cid, args, account)
    class Lower(Broker):
        async def submit(self, intent, reservation, *, quote_validator):
            quote_validator(103)
            assert intent.limit_price == "104" and intent.quantity == 48
            return await super().submit(intent, reservation, quote_validator=quote_validator)
    result = asyncio.run(dispatch_reserved(core, reserved, account_name="account", broker_factory=Lower, now=args["now"], authorize_add=lambda: True))
    assert result["campaign"]["confirmed_quantity"] == 48


def test_protective_sell_allows_changed_current_quote(tmp_path):
    core, cid, args, account = start(tmp_path, "LIVE")
    buy = reserve(core, cid, args, account)
    ack(core, buy, "123")
    core.reconcile(cid, buy["intent"].id, receipt(buy, order="123"))
    core.request_exit(cid, reason="PROTECTIVE_STOP")
    args["now"] = "2026-09-25T13:41:01Z"
    account["quantity"] = 48
    sell = reserve(core, cid, args, account)
    class Falling(Broker):
        async def submit(self, intent, reservation, *, quote_validator):
            quote_validator(99)
            return await super().submit(intent, reservation, quote_validator=quote_validator)
    result = asyncio.run(dispatch_reserved(core, sell, account_name="account", broker_factory=Falling, now=args["now"]))
    assert result["campaign"]["status"] == "CLOSED"


@pytest.mark.parametrize("confirmed_cancel", [True, False])
def test_stale_sell_repriced_only_after_exact_cancel_confirmation(tmp_path, confirmed_cancel):
    core, cid, args, account = start(tmp_path, "LIVE")
    buy = reserve(core, cid, args, account)
    ack(core, buy, "buy1")
    core.reconcile(cid, buy["intent"].id, receipt(buy, order="buy1"))
    core.request_exit(cid, reason="PROTECTIVE_STOP")
    args["now"] = "2026-09-25T13:41:01Z"
    account["quantity"] = 48
    old_sell = reserve(core, cid, args, account)
    ack(core, old_sell, "sell1")
    args["now"] = "2026-09-25T13:42:02Z"
    class Reprice(Broker):
        cancelled = False
        async def holdings(self, symbol):
            result = await super().holdings(symbol)
            result["quantity"] = 48
            return result
        async def reconcile(self, intent, order_id, order_date):
            if intent.id == old_sell["intent"].id:
                status = "CANCELLED" if self.cancelled and confirmed_cancel else "PENDING"
                return receipt(dict(intent=intent), quantity=0, status=status, fees="0", order="sell1")
            return await super().reconcile(intent, order_id, order_date)
        async def cancel(self, intent, order_id, order_date):
            self.cancelled = True
            self.calls.append("CANCEL")
            return dict(accepted=True)
    result = asyncio.run(drive(core, cid, capture_current_record(**args), account_name="account", broker_factory=Reprice, now=args["now"]))
    if confirmed_cancel:
        assert Broker.instances[-1].calls == ["CANCEL", "SELL"]
        assert result["campaign"]["status"] == "CLOSED"
    else:
        assert Broker.instances[-1].calls == ["CANCEL"]
        assert result["status"] == "RECONCILE_REQUIRED"
        assert len(core.snapshot(cid)["orders"]) == 2


@pytest.mark.parametrize("authorize", [None, lambda: False])
def test_live_buy_requires_immediate_authorization(tmp_path, authorize):
    core, cid, args, account = start(tmp_path, "LIVE")
    reserved = reserve(core, cid, args, account)
    count = len(Broker.instances)
    callbacks = []
    result = asyncio.run(dispatch_reserved(core, reserved, account_name="account", broker_factory=Broker,
                                           now=args["now"], authorize_add=authorize, before_submit=lambda _: callbacks.append(True)))
    assert result["status"] == "LIVE_ADD_AUTHORIZATION_REQUIRED"
    assert len(Broker.instances) == count and not callbacks
    assert core.snapshot(cid)["orders"][0]["status"] == "CANCELLED"


def test_authorization_rechecked_at_broker_quote_validation(tmp_path):
    core, cid, args, account = start(tmp_path, "LIVE")
    reserved = reserve(core, cid, args, account)
    calls = []
    def authorize():
        calls.append(True)
        return len(calls) <= 2
    result = asyncio.run(dispatch_reserved(core, reserved, account_name="account", broker_factory=Broker,
                                           now=args["now"], authorize_add=authorize))
    assert len(calls) == 3
    assert result["campaign"]["confirmed_quantity"] == 0
    assert core.snapshot(cid)["orders"][0]["status"] == "CANCELLED"


@pytest.mark.parametrize("target,quote,allowed", [("1", 101, False), ("1", 104, True),
                                               (".8", 103, True), (".8", 101, False)])
def test_live_quote_must_still_qualify_reserved_target(tmp_path, monkeypatch, target, quote, allowed):
    core, cid, args, account = start(tmp_path, "LIVE")
    reserved = reserve(core, cid, args, account)
    # Exercise the persisted target boundary with fixed higher-target fixtures.
    snapshot = core.snapshot(cid)
    snapshot["orders"][0]["target_allocation"] = target
    monkeypatch.setattr(core, "snapshot", lambda _: snapshot)
    validate = _validator(core, reserved, _clock(args["now"]), lambda: True)
    if allowed:
        validate(quote)
    else:
        with pytest.raises(ValueError, match="TARGET_PRICE_CONFIRMATION_LOST"):
            validate(quote)
