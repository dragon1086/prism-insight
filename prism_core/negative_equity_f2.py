"""F2 for US issuers with negative shareholders' equity (2026-10-03).

The shared F2 test (debt ratio < 200% or at or below the industry average) cannot be
computed when equity is negative, so the BUY agent judged such issuers
inconsistently: DELL passed on net debt/EBITDA on 2026-09-04 and failed on
"negative equity" on 2026-09-11. Buyback-driven deficits (DELL, HPQ, CAH, MO) then
rose; loss-driven deficits (AMC, IBRX) and highly levered names kept failing.

For a negative-equity issuer this module computes the four checks from yfinance
annual/quarterly statements and appends a per-report block with the values and a
deterministic verdict. Every other issuer's prompt stays byte-identical. KR is not
wired: KR negative equity is loss-driven capital impairment, which fails either way.
``PRISM_US_NEG_EQUITY_F2=off`` disables the block.
"""
import logging
import os

logger = logging.getLogger(__name__)

ENV = "PRISM_US_NEG_EQUITY_F2"
ND_EBITDA_MAX = 2.5
COVERAGE_MIN = 4.0
PROFIT_YEARS = 3
RETURN_YEARS = 4


def enabled():
    return os.getenv(ENV, "on").strip().lower() not in {"0", "false", "no", "off"}


def _values(frame, names, count):
    """Most recent ``count`` non-null values of the first row found (newest first)."""
    if frame is None or getattr(frame, "empty", True):
        return []
    for name in names:
        if name in frame.index:
            return [float(v) for v in frame.loc[name].dropna().tolist()[:count]]
    return []


def _latest(frame, *names):
    values = _values(frame, names, 1)
    return values[0] if values else None


def compute(ticker, yf_module=None):
    """Checks for a negative-equity issuer, or None when equity is not negative or unknown."""
    if yf_module is None:
        import yfinance as yf_module
    tk = yf_module.Ticker(ticker)
    balance = tk.quarterly_balance_sheet
    equity = _latest(balance, "Stockholders Equity", "Common Stock Equity")
    if equity is None or equity >= 0:
        return None
    income, cashflow = tk.income_stmt, tk.cashflow
    net_income = _values(income, ("Net Income",), PROFIT_YEARS)
    retained = _latest(balance, "Retained Earnings")
    returns = sum(abs(v) for v in _values(cashflow, ("Repurchase Of Capital Stock",), RETURN_YEARS)) + \
        sum(abs(v) for v in _values(cashflow, ("Cash Dividends Paid",), RETURN_YEARS))
    debt = _latest(balance, "Total Debt")
    cash = _latest(balance, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents") or 0.0
    ebitda = _latest(income, "EBITDA", "Normalized EBITDA")
    ebit = _latest(income, "EBIT", "Operating Income")
    interest = _latest(income, "Interest Expense")
    fcf = _latest(cashflow, "Free Cash Flow")

    net_debt = None if debt is None else debt - cash
    nd_ebitda = net_debt / ebitda if net_debt is not None and ebitda and ebitda > 0 else None
    coverage = ebit / abs(interest) if ebit is not None and interest else None
    profitable = len(net_income) == PROFIT_YEARS and all(v > 0 for v in net_income)
    shareholder_returns = retained is not None and retained > 0 or returns >= abs(equity)
    checks = {
        "a": profitable and shareholder_returns,
        "b": None if nd_ebitda is None else (nd_ebitda <= ND_EBITDA_MAX or net_debt <= 0),
        "c": None if coverage is None else coverage >= COVERAGE_MIN,
        "d": None if fcf is None else fcf > 0,
    }
    return {
        "ticker": ticker, "equity": equity, "retained_earnings": retained, "shareholder_returns_4y": returns,
        "net_income": net_income, "net_debt": net_debt, "ebitda": ebitda, "nd_ebitda": nd_ebitda,
        "coverage": coverage, "fcf": fcf, "checks": checks, "passed": all(v is True for v in checks.values()),
    }


def _b(value):
    return "n/a" if value is None else f"{value / 1e9:,.2f}B USD"


def _x(value):
    return "n/a" if value is None else f"{value:.2f}x"


def _mark(value, language):
    if value is None:
        return "확인 불가(미달)" if language == "ko" else "not confirmed (fail)"
    if language == "ko":
        return "충족" if value else "미달"
    return "met" if value else "not met"


def prompt_block(facts, language="ko"):
    """Per-report F2 block for a negative-equity issuer; '' when ``facts`` is None."""
    if not facts:
        return ""
    c = facts["checks"]
    ni = ", ".join(_b(v) for v in facts["net_income"]) or "n/a"
    if language == "ko":
        verdict = "통과" if facts["passed"] else "미달"
        return (
            "\n\n### F2 자기자본 음수 기업 판정 (결정론적 · yfinance 연간/분기 재무)\n"
            f"자기자본이 {_b(facts['equity'])}로 음수라 부채비율을 산출할 수 없습니다. 이 종목의 F2는 부채비율 대신 아래 "
            "네 가지로 판정하며, 음수 자본 자체를 미달 사유로 쓰지 않습니다. 하나라도 미달이거나 확인할 수 없으면 F2 미달입니다.\n"
            f"- (a) 최근 {PROFIT_YEARS}개 회계연도 순이익 흑자이고 음수 자본이 주주환원(자사주·배당)에서 생김: {_mark(c['a'], 'ko')} "
            f"— 순이익(최근순) {ni}, 이익잉여금 {_b(facts['retained_earnings'])}, 최근 {RETURN_YEARS}년 자사주+배당 "
            f"{_b(facts['shareholder_returns_4y'])}\n"
            f"- (b) 순부채/EBITDA {ND_EBITDA_MAX}배 이하: {_mark(c['b'], 'ko')} — 순부채 {_b(facts['net_debt'])}, "
            f"EBITDA {_b(facts['ebitda'])}, {_x(facts['nd_ebitda'])}\n"
            f"- (c) 이자보상배율(EBIT/이자비용) {COVERAGE_MIN:g}배 이상: {_mark(c['c'], 'ko')} — {_x(facts['coverage'])}\n"
            f"- (d) 최근 회계연도 잉여현금흐름 흑자: {_mark(c['d'], 'ko')} — {_b(facts['fcf'])}\n"
            f"F2 판정: **{verdict}**. F2_balance_sheet에 이 판정과 값을 그대로 쓰십시오. F1·F3·F4, 점수, 시장 국면 하한, "
            "추세 게이트, 손익비 규칙은 바뀌지 않습니다.\n")
    verdict = "PASS" if facts["passed"] else "FAIL"
    return (
        "\n\n### F2 for a negative-equity issuer (deterministic · yfinance annual/quarterly statements)\n"
        f"Shareholders' equity is negative ({_b(facts['equity'])}), so the debt ratio cannot be computed. Judge this "
        "issuer's F2 on the four checks below instead of the debt ratio, and never use negative equity by itself as a "
        "fail. Any check not met or not confirmed fails F2.\n"
        f"- (a) net income positive in each of the last {PROFIT_YEARS} fiscal years and the deficit comes from "
        f"shareholder returns (buybacks/dividends): {_mark(c['a'], 'en')} — net income (newest first) {ni}, retained "
        f"earnings {_b(facts['retained_earnings'])}, buybacks+dividends over {RETURN_YEARS} years "
        f"{_b(facts['shareholder_returns_4y'])}\n"
        f"- (b) net debt/EBITDA at most {ND_EBITDA_MAX}x: {_mark(c['b'], 'en')} — net debt {_b(facts['net_debt'])}, "
        f"EBITDA {_b(facts['ebitda'])}, {_x(facts['nd_ebitda'])}\n"
        f"- (c) interest coverage (EBIT/interest expense) at least {COVERAGE_MIN:g}x: {_mark(c['c'], 'en')} — "
        f"{_x(facts['coverage'])}\n"
        f"- (d) positive free cash flow in the latest fiscal year: {_mark(c['d'], 'en')} — {_b(facts['fcf'])}\n"
        f"F2 verdict: **{verdict}**. Write this verdict and the values in F2_balance_sheet. F1, F3, F4, scoring, regime "
        "floors, the trend gate and R/R rules are unchanged.\n")


def block_for(ticker, language="ko", yf_module=None):
    """Prompt block for ``ticker`` ('' when disabled, equity is not negative, or data fails)."""
    if not enabled() or not ticker:
        return ""
    try:
        facts = compute(ticker, yf_module=yf_module)
    except Exception as error:  # noqa: BLE001 - fail-open: the shared F2 rule still applies
        logger.warning("[NEG_EQUITY_F2] %s facts unavailable: %s", ticker, error)
        return ""
    if facts:
        logger.info("[NEG_EQUITY_F2] %s equity=%s checks=%s passed=%s", ticker, _b(facts["equity"]),
                    facts["checks"], facts["passed"])
    return prompt_block(facts, language)
