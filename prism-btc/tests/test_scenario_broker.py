"""Transport/evidence failure tests; never use credentials or submit orders."""
import sqlite3

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker, read_evidence_pages


def reply(rows, cursor=""):
    return dict(retCode=0, result=dict(list=rows, nextPageCursor=cursor))


class Session:
    endpoint = "https://api-demo.bybit.com"

    def __init__(self):
        self.calls = []
        self.uid = "123"
        self.size = "0"

    def get_api_key_information(self):
        self.calls.append("identity")
        return dict(retCode=0, result=dict(userID=self.uid))

    def get_positions(self, **kwargs):
        self.calls.append("positions")
        return reply([dict(symbol="BTCUSDT", positionIdx=0, size=self.size,
            side="Buy" if self.size != "0" else "", avgPrice="100", stopLoss="90", leverage="10")])

    def get_open_orders(self, **kwargs):
        return reply([])

    def get_wallet_balance(self, **kwargs):
        return reply([dict(totalEquity="10000", totalAvailableBalance="9000",
            totalMarginBalance="10000", totalMaintenanceMargin="0", totalInitialMargin="0",
            accountIMRate="0", accountMMRate="0", coin=[dict(coin="USDT", equity="10000", usdValue="10000")])])

    def get_account_info(self):
        return dict(retCode=0, result=dict(marginMode="REGULAR_MARGIN"))

    def get_risk_limit(self, **kwargs):
        return reply([dict(symbol="BTCUSDT", riskLimitValue="1000000", maintenanceMargin=".005",
                           initialMargin=".01", maxLeverage="100")])

    def get_tickers(self, **kwargs):
        return reply([dict(symbol="BTCUSDT", markPrice="100", lastPrice="100")])


@pytest.fixture
def setup(tmp_path):
    conn = sqlite3.connect(tmp_path / "broker.db")
    session = Session()
    return ScenarioDemoBroker(conn, session=session, expected_main_uid="123", clock=lambda: 1800000000), session


def test_wrong_endpoint_rejected_before_network(tmp_path):
    s = Session()
    s.endpoint = "https://api.bybit.com"
    with pytest.raises(BrokerNotReady):
        ScenarioDemoBroker(sqlite3.connect(tmp_path / "b.db"), session=s, expected_main_uid="123")
    assert s.calls == []


def test_wrong_identity_rejected_before_wallet_and_positions(setup):
    b, s = setup
    s.uid = "999"
    with pytest.raises(BrokerNotReady):
        b.capture_account()
    assert s.calls == ["identity"]


def test_fresh_read_only_snapshot_has_no_fake_execution_capability(setup):
    b, s = setup
    observed = b.capture_account()
    assert observed["equity"] == 10000
    assert observed["exchange_flat"] is True
    assert observed["execution_ready"] is False
    assert not b.capabilities
    with pytest.raises(BrokerNotReady):
        b.execute({}, "intent")


def test_nonflat_legacy_exposure_is_not_adopted(setup):
    b, s = setup
    s.size = "1"
    observed = b.capture_account()
    assert observed["legacy_fenced"] is True
    assert observed["protection_confirmed"] is False
    assert b.reconcile()["intents"] == []


def test_legacy_pending_metadata_fences_even_when_exchange_flat(setup):
    b, _ = setup
    b.conn.execute("CREATE TABLE btc_meta(mode TEXT,key TEXT,value TEXT)")
    b.conn.execute("INSERT INTO btc_meta VALUES('demo','entry_pending','{\"unknown\":true}')")
    b.conn.commit()
    assert b.capture_account()["legacy_fenced"] is True


def test_cursor_cycle_fails_not_partial_success():
    with pytest.raises(BrokerNotReady):
        read_evidence_pages(lambda *_args, **_kw: reply([dict(execId="x")], "same"), "get_executions")


def test_conflicting_duplicate_execution_fails():
    pages = iter([reply([dict(execId="x", execFee="1")], "next"), reply([dict(execId="x", execFee="2")])])
    with pytest.raises(BrokerNotReady):
        read_evidence_pages(lambda *_args, **_kw: next(pages), "get_executions")


def test_complete_pages_deduplicate_exact_ids():
    pages = iter([reply([dict(execId="x")], "next"), reply([dict(execId="x"), dict(execId="y")])])
    assert len(read_evidence_pages(lambda *_args, **_kw: next(pages), "get_executions")) == 2


def test_transaction_failure_is_unknown_not_zero():
    with pytest.raises(BrokerNotReady):
        read_evidence_pages(lambda *_args, **_kw: dict(retCode=10001), "get_transaction_log")


def test_empty_funding_snapshot_never_claims_finality(setup):
    b, s = setup
    s.get_executions = lambda **kw: reply([])
    s.get_transaction_log = lambda **kw: reply([])
    evidence = b.capture_financial_evidence(1799999000000, 1800000000000)
    assert evidence["funding_complete"] is False
    assert evidence["settlement_ready"] is False


def test_funding_sign_and_execution_cashflow_join(setup):
    b, s = setup
    s.get_executions = lambda **kw: reply([dict(execId="trade1", orderId="order1", symbol="BTCUSDT",
        execType="Trade", execQty="1", execPrice="100", execFee=".05", execTime="1799999500000")])
    s.get_transaction_log = lambda **kw: reply([
        dict(id="t1", type="TRADE", symbol="BTCUSDT", currency="USDT", tradeId="trade1", orderId="order1",
             cashFlow="5", fee=".05", funding="", transactionTime="1799999500000"),
        dict(id="f1", type="SETTLEMENT", symbol="BTCUSDT", currency="USDT", funding="-2", fee="0",
             cashFlow="0", transactionTime="1799999600000")])
    result = b.capture_financial_evidence(1799999000000, 1800000000000)
    assert result["trades"][0]["gross_pnl"] == 5
    assert result["funding"][0]["funding_net"] == -2
    assert result["funding_complete"] is False


def funding_capture_fixture(session):
    executions, transactions = [], []
    for eid, oid, side, when, price, pnl in (
            ("entry-fill", "entry-order", "Buy", "10", "100", "0"),
            ("exit-fill", "exit-order", "Sell", "30", "110", "10")):
        executions.append(dict(execId=eid, orderId=oid, symbol="BTCUSDT", side=side,
            execType="Trade", execQty="1", execPrice=price, execFee=".05", execTime=when))
        transactions.append(dict(id="txn-"+eid, tradeId=eid, orderId=oid, type="TRADE",
            symbol="BTCUSDT", currency="USDT", side=side, qty="1", fee=".05",
            cashFlow=pnl, funding="0", transactionTime=when))
    executions.append(dict(execId="funding-exec", orderId="funding-order", symbol="BTCUSDT",
        side="Buy", execType="Funding", execQty="1", execPrice="105", execFee=".2", execTime="20"))
    transactions.append(dict(id="funding-txn", tradeId="funding-exec", orderId="funding-order",
        symbol="BTCUSDT", currency="USDT", type="SETTLEMENT", side="Buy", qty="1",
        funding="-.2", fee="0", cashFlow="0", transactionTime="20"))
    session.get_executions = lambda **kw: reply(executions)
    session.get_transaction_log = lambda **kw: reply(transactions)
    return executions, transactions


def funding_accounting(evidence, **schedule_changes):
    from live.scenario_accounting import reconcile_scenario
    orders = [dict(order_id=oid, role=role, side=side, cumulative_qty="1", terminal=True)
              for oid, role, side in (("entry-order", "entry", "Buy"), ("exit-order", "exit", "Sell"))]
    snapshot = dict(position=dict(symbol="BTCUSDT", positionIdx=0, size="0", side=""), open_orders=[])
    schedule = dict(start_ms=0, end_ms=100, complete=True, events=[dict(timestamp=20, rate=".001")])
    schedule.update(schedule_changes)
    return reconcile_scenario("s", evidence, orders, snapshot, funding_schedule=schedule)


def test_funding_execution_exact_pair_not_unmatched_or_double_counted(setup):
    broker, session = setup
    funding_capture_fixture(session)
    evidence = broker.capture_financial_evidence(0, 100)
    assert evidence["unmatched_ids"] == []
    assert len(evidence["trades"]) == 2
    assert len(evidence["funding"]) == 1
    assert evidence["funding_complete"] is False  # Capture alone never proves finality.
    assert evidence["settlement_ready"] is False
    result = funding_accounting(evidence)
    assert result["accounting_complete"] is True
    assert result["net_pnl"] == 9.7  # 10 - two .05 fees - one .2 funding.
    assert result["fees"] == .1
    assert result["funding_net"] == -.2
    assert result["settlement"]["execution_ids"] == ["entry-fill", "exit-fill"]
    assert result["settlement"]["flat_confirmed"] is True


@pytest.mark.parametrize("field,value", [
    ("tradeId", "wrong"), ("orderId", "wrong"), ("transactionTime", "21"),
    ("qty", "2"), ("funding", ".2"), ("funding", "-.3"),
    ("currency", "BTC"), ("fee", ".1"), ("cashFlow", "1"),
    ("symbol", "ETHUSDT"), ("qty", None), ("funding", "NaN"),
])
def test_funding_execution_pair_mismatch_never_settles(setup, field, value):
    broker, session = setup
    _, transactions = funding_capture_fixture(session)
    transactions[-1][field] = value
    try:
        evidence = broker.capture_financial_evidence(0, 100)
    except BrokerNotReady:
        return
    assert evidence["unmatched_ids"]
    assert funding_accounting(evidence)["settlement"] is None


@pytest.mark.parametrize("case", ["missing_settlement", "collision", "unknown_type", "missing_schedule", "bad_schedule"])
def test_funding_pair_still_requires_all_accounting_gates(setup, case):
    broker, session = setup
    executions, transactions = funding_capture_fixture(session)
    if case == "missing_settlement":
        transactions.pop()
    elif case == "collision":
        transactions.append(dict(transactions[-1], id="other-funding-txn"))
    elif case == "unknown_type":
        executions[-1]["execType"] = "BustTrade"
    try:
        evidence = broker.capture_financial_evidence(0, 100)
    except BrokerNotReady:
        assert case == "collision"
        return
    changes = {"complete": False} if case == "missing_schedule" else {"events": []} if case == "bad_schedule" else {}
    result = funding_accounting(evidence, **changes)
    assert result["settlement"] is None


def test_settlement_without_funding_execution_retains_existing_proof_path(setup):
    broker, session = setup
    executions, _ = funding_capture_fixture(session)
    executions.pop()
    evidence = broker.capture_financial_evidence(0, 100)
    assert evidence["unmatched_ids"] == []
    assert funding_accounting(evidence)["net_pnl"] == 9.7


@pytest.mark.parametrize("field,value", [
    ("execId", "missing-pair"), ("orderId", ""), ("execTime", "101"),
    ("execQty", "0"), ("execFee", None), ("execFee", "Infinity"),
    ("execFee", "0.20000000001"), ("execQty", "1.00000000001"),
])
def test_funding_execution_missing_or_inexact_fields_remain_fenced(setup, field, value):
    broker, session = setup
    executions, _ = funding_capture_fixture(session)
    executions[-1][field] = value
    try:
        evidence = broker.capture_financial_evidence(0, 100)
    except BrokerNotReady:
        return
    assert evidence["unmatched_ids"]
    assert funding_accounting(evidence)["settlement"] is None


def test_funding_and_trade_transaction_identity_collision_fenced(setup):
    broker, session = setup
    _, transactions = funding_capture_fixture(session)
    transactions[-1]["tradeId"] = "entry-fill"
    with pytest.raises(BrokerNotReady, match="identity_collision"):
        broker.capture_financial_evidence(0, 100)


def test_receiving_funding_uses_opposite_execution_fee_once(setup):
    broker, session = setup
    executions, transactions = funding_capture_fixture(session)
    executions[-1]["execFee"] = "-.2"
    transactions[-1]["funding"] = ".2"
    evidence = broker.capture_financial_evidence(0, 100)
    result = funding_accounting(evidence, events=[dict(timestamp=20, rate="-.001")])
    assert result["net_pnl"] == 10.1
    assert result["fees"] == .1
    assert result["funding_net"] == .2
    assert result["settlement"]["flat_confirmed"] is True


@pytest.mark.parametrize("execution_side,transaction_side", [
    ("Buy", "Sell"), (None, "Buy"), ("Buy", None), ("None", "None"),
])
def test_funding_pair_missing_or_conflicting_side_fenced(setup, execution_side, transaction_side):
    broker, session = setup
    executions, transactions = funding_capture_fixture(session)
    executions[-1]["side"] = execution_side
    transactions[-1]["side"] = transaction_side
    with pytest.raises(BrokerNotReady, match="funding_execution_identity_mismatch"):
        broker.capture_financial_evidence(0, 100)


@pytest.mark.parametrize("funding,rate,net", [("-.2", "-.001", 9.7), (".2", ".001", 10.1)])
def test_short_paid_and_received_funding_once(setup, funding, rate, net):
    from live.scenario_accounting import reconcile_scenario
    broker, session = setup
    executions, transactions = funding_capture_fixture(session)
    executions[0].update(side="Sell", execPrice="110")
    executions[1].update(side="Buy", execPrice="100")
    executions[2].update(side="Sell", execFee=str(-float(funding)))
    transactions[0]["side"] = "Sell"
    transactions[1]["side"] = "Buy"
    transactions[2].update(side="Sell", funding=funding)
    evidence = broker.capture_financial_evidence(0, 100)
    orders = [dict(order_id=oid, role=role, side=side, cumulative_qty="1", terminal=True)
              for oid, role, side in (("entry-order", "entry", "Sell"), ("exit-order", "exit", "Buy"))]
    snapshot = dict(position=dict(symbol="BTCUSDT", positionIdx=0, size="0", side=""), open_orders=[])
    schedule = dict(start_ms=0, end_ms=100, complete=True, events=[dict(timestamp=20, rate=rate)])
    result = reconcile_scenario("s", evidence, orders, snapshot, funding_schedule=schedule)
    assert evidence["unmatched_ids"] == []
    assert result["net_pnl"] == net
    assert result["fees"] == .1
    assert result["settlement"]["flat_confirmed"] is True
