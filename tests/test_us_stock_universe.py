"""Offline classification/source-integrity contract for expanded US universe."""

import pytest
import requests

from prism_core import us_stock_universe as universe


N_HEADER = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"
O_HEADER = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol"
FOOTER = "File Creation Time: 0924202618:00|||||||"


def nasdaq(symbol="AAPL", name="Apple Inc. - Common Stock", **changes):
    row = dict(zip(N_HEADER.split("|"), [symbol, name, "Q", "N", "N", "100", "N", "N"]))
    row.update(changes)
    return "|".join(row.values())


def other(symbol="BRK.B", name="Berkshire Hathaway Class B Common Stock", **changes):
    row = dict(zip(O_HEADER.split("|"), [symbol, name, "N", symbol, "N", "100", "N", symbol]))
    row.update(changes)
    return "|".join(row.values())


def parse(n=None, o=None):
    return universe.parse_directories(
        "\n".join([N_HEADER, *(n or [nasdaq()]), FOOTER]),
        "\n".join([O_HEADER, *(o or [other()]), FOOTER]),
    )


def info(**changes):
    value = dict(quoteType="EQUITY", exchange="NMS", currency="USD", longName="Apple Inc.",
                 sector="Technology", industry="Consumer Electronics", marketCap=1000)
    value.update(changes)
    return value


def test_valid_common_adr_reit_and_suffix_letters():
    result = parse([
        nasdaq(), nasdaq("NOW", "ServiceNow Common Stock"),
        nasdaq("CAR", "Avis Common Stock"), nasdaq("U", "Unity Common Stock"),
        nasdaq("ASML", "ASML Ordinary Shares"),
        nasdaq("BIDU", "Baidu American Depositary Shares"),
        nasdaq("O", "Realty Income REIT Common Stock"),
    ])
    assert {r.symbol for r in result.records} == {"AAPL", "NOW", "CAR", "U", "ASML", "BIDU", "O", "BRK-B"}
    assert result.counts == {"source_rows": 8, "eligible_directory": 8}


def test_preferred_bank_is_an_individual_common_stock_not_preferred_security():
    result = parse([nasdaq("PFBC", "Preferred Bank - Common Stock")])
    assert [record.symbol for record in result.records] == ["PFBC", "BRK-B"]
    assert universe.eligibility_reason(
        info(longName="Preferred Bank", sector="Financial Services", industry="Banks - Regional"),
        500,
    ) is None


@pytest.mark.parametrize("name", [
    "Tesla Single Stock ETF", "Example ETN", "Example Fund Common Shares",
    "Example Closed-End Common Stock", "Bank Depositary Shares Representing Preferred Stock",
    "Company Preferred Ordinary Shares", "Company Warrants", "Company Rights",
    "Company Units", "Company Notes", "Company Bonds", "Company Debentures",
    "Company Shares of Beneficial Interest", "Unknown Corp", "Index Trust",
    "Bank - Series A Preferred American Depositary Shares",
    "Bank Depositary Shares Representing Series A Preferred",
])
def test_non_individual_and_unknown_names_excluded(name):
    result = parse([nasdaq("TEST", name)])
    assert [r.symbol for r in result.records] == ["BRK-B"]
    assert result.counts["source_rows"] == 2


@pytest.mark.parametrize("changes,reason", [
    ({"ETF": "Y"}, "etf"), ({"Test Issue": "Y"}, "test_issue"),
    ({"Financial Status": "D"}, "financial_status"),
    ({"Financial Status": ""}, "financial_status"),
    ({"Market Category": "X"}, "exchange"),
])
def test_directory_flags(changes, reason):
    assert parse([nasdaq(**changes)]).counts[f"excluded_{reason}"] == 1


@pytest.mark.parametrize("exchange", ["P", "Z", "V", "X", ""])
def test_other_exchanges_excluded(exchange):
    assert parse(o=[other(**{"Exchange": exchange})]).counts["excluded_exchange"] == 1


def test_american_exchange_accepted():
    assert parse(o=[other(**{"Exchange": "A"})]).records[-1].exchange == "AMEX"


@pytest.mark.parametrize("n,o", [
    ([nasdaq(), nasdaq()], [other()]),
    ([nasdaq("BRK-B")], [other()]),
    ([nasdaq("BRK.B")], [other()]),
])
def test_duplicate_mapping_rejected(n, o):
    with pytest.raises(ValueError, match="Duplicate"):
        parse(n, o)


@pytest.mark.parametrize("bad", [
    "", "<html>Error</html>", N_HEADER + "\n" + nasdaq(),
    N_HEADER + "\n" + FOOTER,
    N_HEADER + "\n" + nasdaq() + "|extra\n" + FOOTER,
    N_HEADER + "\n" + "AAPL|Common Stock\n" + FOOTER,
    N_HEADER.replace("Symbol", "Bad") + "\n" + nasdaq() + "\n" + FOOTER,
    N_HEADER + "\n" + FOOTER + "\n" + nasdaq() + "\n" + FOOTER,
])
def test_malformed_source_fails(bad):
    with pytest.raises(ValueError):
        universe.parse_directories(bad, "\n".join([O_HEADER, other(), FOOTER]))


@pytest.mark.parametrize("footer", [
    "File Creation Time: invalid|||||||", "File Creation Time: 09242026|||||||",
    "File Creation Time: 0230202618:00|||||||", "File Creation Time: 1324202618:00|||||||",
    "File Creation Time: 0924202624:00|||||||", "File Creation Time: 0924202618:60|||||||",
    "File Creation Time: 0924202618:00|unexpected||||||",
])
def test_invalid_footer_timestamp_fails(footer):
    with pytest.raises(ValueError, match="creation"):
        universe.parse_directories(
            "\n".join([N_HEADER, nasdaq(), footer]),
            "\n".join([O_HEADER, other(), FOOTER]),
        )


def test_directory_dates_must_match_but_times_may_differ():
    n_text = "\n".join([N_HEADER, nasdaq(), FOOTER])
    o_text = "\n".join([O_HEADER, other(), FOOTER])
    with pytest.raises(ValueError, match="dates do not match"):
        universe.parse_directories(n_text, o_text.replace("09242026", "09232026"))
    assert len(universe.parse_directories(n_text, o_text.replace("18:00", "17:00")).records) == 2


@pytest.mark.parametrize("symbol", ["", "../BAD", "aapl", "A A", "A;rm", "A" * 33])
def test_unsafe_symbols_fail(symbol):
    with pytest.raises(ValueError):
        parse([nasdaq(symbol)])


def test_unsupported_suffix_does_not_get_guessed():
    assert parse([nasdaq("ABC.WS")]).counts["excluded_unsupported_symbol"] == 1


def test_metadata_accepts_complete_equity_adr_and_reit_at_boundary():
    assert universe.eligibility_reason(info(), 1000) is None
    assert universe.eligibility_reason(info(longName="Baidu, Inc.", industry="Internet Content"), 500) is None
    assert universe.eligibility_reason(info(longName="Realty Income Corporation", sector="Real Estate", industry="REIT - Retail"), 500) is None
    assert universe.eligibility_reason(info(longName="Real Estate Investment Trust", sector="Real Estate", industry="REIT - Retail"), 500) is None


@pytest.mark.parametrize("changes,reason", [
    ({"quoteType": "ETF"}, "quote_type"), ({"quoteType": None}, "quote_type"),
    ({"exchange": "PCX"}, "exchange"), ({"exchange": None}, "exchange"),
    ({"currency": "CAD"}, "currency"), ({"currency": None}, "currency"),
    ({"sector": None}, "unknown_company_classification"),
    ({"industry": ""}, "unknown_company_classification"),
    ({"longName": None}, "unknown_company_classification"),
    ({"industry": "Shell Companies"}, "non_operating_equity"),
    ({"industry": "Closed-End Fund - Equity"}, "non_operating_equity"),
    ({"longName": "Example Fund"}, "non_operating_equity"),
    ({"fundFamily": "iShares"}, "fund_metadata"),
    ({"category": "Large Blend"}, "fund_metadata"),
    ({"marketCap": None}, "missing_market_cap"), ({"marketCap": float("nan")}, "missing_market_cap"),
    ({"marketCap": float("inf")}, "missing_market_cap"), ({"marketCap": -1}, "missing_market_cap"),
    ({"marketCap": True}, "missing_market_cap"), ({"marketCap": "1000"}, "missing_market_cap"),
    ({"marketCap": 499}, "market_cap"),
])
def test_metadata_exclusions(changes, reason):
    assert universe.eligibility_reason(info(**changes), 500) == reason


@pytest.mark.parametrize("minimum", [None, 0, -1, True, float("nan"), float("inf"), "500"])
def test_explicit_valid_cap_required(minimum):
    with pytest.raises(ValueError):
        universe.eligibility_reason(info(), minimum)


def test_missing_metadata():
    assert universe.eligibility_reason({}, 500) == "missing_metadata"
    assert universe.eligibility_reason(None, 500) == "missing_metadata"


def test_download_bounded_and_no_fallback(monkeypatch):
    calls = []

    class Response:
        text = "\n".join([N_HEADER, nasdaq(), FOOTER])

        def raise_for_status(self):
            pass

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 2:
            raise requests.Timeout("offline fixture")
        return Response()

    monkeypatch.setattr(universe.requests, "get", get)
    with pytest.raises(requests.Timeout):
        universe.fetch_universe()
    assert len(calls) == 2
    assert all(kwargs == {"timeout": (5, 20)} for _, kwargs in calls)


def test_download_success(monkeypatch):
    texts = iter(["\n".join([N_HEADER, nasdaq(), FOOTER]), "\n".join([O_HEADER, other(), FOOTER])])

    class Response:
        def __init__(self):
            self.text = next(texts)

        def raise_for_status(self):
            pass

    monkeypatch.setattr(universe.requests, "get", lambda *a, **kw: Response())
    assert len(universe.fetch_universe().records) == 2
