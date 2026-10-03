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
