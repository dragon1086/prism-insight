"""F1/F2 for US financial institutions whose income statement has no operating income line (2026-11-10).

US GAAP banks, brokers, asset managers and consumer lenders report no "Operating Income" row, so the shared F1
test (operating profit in the latest two quarters) could never be verified for them and the BUY agent rejected
profitable banks as "unverifiable" (INTR, BBVA, BX, RY, SOFI, BMO in 2026-10). Their liability-heavy balance
sheets also make the shared F2 debt-ratio test meaningless, the same finding that moved KR financials to a
regulatory capital ratio (roadmap stage 4, PRISM_KR_SECTOR_F2_MODE=live). yfinance carries no regulatory ratios,
so the US rule uses the standard public proxies:

- F1: pretax income positive in the latest two reported quarters (the GAAP line every financial reports).
- F2 (banks, credit/mortgage lenders): shareholders' equity / total assets >= 5%, the US prompt-corrective-action
  "well capitalized" leverage threshold, with book equity as the public proxy for Tier 1 capital.
- F2 (asset managers, brokers, insurers, exchanges): total debt / equity < 200%, the shared F2 number on
  interest-bearing debt as the report's leverage appendix already computes it.

Like ``negative_equity_f2`` the block is computed from yfinance statements at BUY time (the caller passes the module) and appended to the per-report
message only for these issuers; every other prompt stays byte-identical. ``PRISM_US_FINANCIAL_F_GATE=off``
disables it (the shared rules then apply and such issuers keep failing F1 as before).
"""
import logging
import os

logger = logging.getLogger(__name__)

ENV = "PRISM_US_FINANCIAL_F_GATE"
EQUITY_TO_ASSETS_MIN = 0.05
DEBT_TO_EQUITY_MAX = 2.0
OPERATING_INCOME_ROWS = ("Operating Income", "Total Operating Income As Reported")
BANK_WORDS = ("bank", "credit services", "mortgage")


def enabled():
    return os.getenv(ENV, "on").strip().lower() not in {"0", "false", "no", "off"}


def _row(frame, names):
    if frame is None or getattr(frame, "empty", True):
        return None
    for name in names:
        if name in frame.index:
            return frame.loc[name]
    return None


def _latest(frame, *names):
    row = _row(frame, names)
    values = [] if row is None else row.dropna().tolist()
    return float(values[0]) if values else None


def _recent_quarters(frame, name, count=2):
    """(period, value) for the latest ``count`` quarters that report ``name`` (newest first)."""
    row = _row(frame, (name,))
    if row is None:
        return []
    out = []
    for column, value in row.items():
        if value is None or value != value:  # NaN: quarter not reported by the source
            continue
        out.append((str(getattr(column, "date", lambda: column)())[:10], float(value)))
    out.sort(reverse=True)
    return out[:count]


def compute(ticker, yf_module):
    """Facts for a financial issuer without an operating income line, else None.

    ``yf_module`` is the yfinance module passed by the caller (the US tracking agent is on the direct-import
    allowlist, tests/test_yfinance_import_allowlist.py; this module must not import it).
    """
    tk = yf_module.Ticker(ticker)
    income = tk.quarterly_income_stmt
    if income is None or getattr(income, "empty", True) or _row(income, OPERATING_INCOME_ROWS) is not None:
        return None
    info = tk.get_info() or {}
    sector, industry = str(info.get("sector") or ""), str(info.get("industry") or "")
    if sector != "Financial Services":
        return None
    balance = tk.quarterly_balance_sheet
    bank_like = any(word in industry.lower() for word in BANK_WORDS)
    pretax = _recent_quarters(income, "Pretax Income")
    assets = _latest(balance, "Total Assets")
    equity = _latest(balance, "Stockholders Equity", "Common Stock Equity")
    debt = _latest(balance, "Total Debt")
    equity_to_assets = equity / assets if equity is not None and assets else None
    debt_to_equity = debt / equity if debt is not None and equity else None
    f1 = len(pretax) == 2 and all(value > 0 for _, value in pretax)
    if bank_like:
        f2 = None if equity_to_assets is None else equity_to_assets >= EQUITY_TO_ASSETS_MIN
    else:
        f2 = None if debt_to_equity is None else debt_to_equity < DEBT_TO_EQUITY_MAX
    return {
        "ticker": ticker, "industry": industry, "subtype": "bank" if bank_like else "financial_other",
        "pretax": pretax, "assets": assets, "equity": equity, "debt": debt,
        "equity_to_assets": equity_to_assets, "debt_to_equity": debt_to_equity,
        "f1": f1, "f2": f2,
        "f2_rule": "equity_to_assets" if bank_like else "debt_to_equity",
    }


def stamp(facts):
    """Stable identifiers stored with the BUY scenario (KR twin: sector_trading_criteria.sector_profile_stamp)."""
    return {"kind": "financial", "subtype": facts["subtype"], "basis": "yfinance_no_operating_income",
            "f1_rule": "pretax_income", "f2_rule": facts["f2_rule"], "f1": facts["f1"], "f2": facts["f2"]}


def _b(value):
    return "n/a" if value is None else f"{value / 1e9:,.2f}B USD"


def _pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _x(value):
    return "n/a" if value is None else f"{value:.2f}x"


def _mark(value, ko):
    if value is None:
        return "확인 불가(미달)" if ko else "not confirmed (fail)"
    return ("통과" if value else "미달") if ko else ("PASS" if value else "FAIL")


def prompt_block(facts, language="ko"):
    """Per-report F1/F2 block for a financial issuer; '' when ``facts`` is None."""
    if not facts:
        return ""
    ko = language == "ko"
    pretax = ", ".join(f"{period} {_b(value)}" for period, value in facts["pretax"]) or "n/a"
    bank = facts["subtype"] == "bank"
    if ko:
        f2_line = (f"- F2 재무 건전성(은행·여신업): 자기자본/총자산 {EQUITY_TO_ASSETS_MIN * 100:.0f}% 이상"
                   f"(미국 감독기관의 '충분한 자본' 레버리지 기준, 장부 자기자본을 Tier 1 자본의 공개 대용치로 사용): "
                   f"**{_mark(facts['f2'], True)}** — 자기자본 {_b(facts['equity'])} / 총자산 {_b(facts['assets'])} = "
                   f"{_pct(facts['equity_to_assets'])}" if bank else
                   f"- F2 재무 건전성(자산운용·증권·보험 등): 이자부 총차입금/자기자본 {DEBT_TO_EQUITY_MAX * 100:.0f}% 미만: "
                   f"**{_mark(facts['f2'], True)}** — 총차입금 {_b(facts['debt'])} / 자기자본 {_b(facts['equity'])} = "
                   f"{_x(facts['debt_to_equity'])}")
        return (
            "\n\n### 금융업 F1·F2 판정 (결정론적 · yfinance 분기 재무, 영업이익 항목 없는 금융회사)\n"
            f"이 회사(업종: {facts['industry'] or 'Financial Services'})의 분기 손익계산서에는 영업이익 항목이 없습니다. "
            "미국 회계기준의 은행·증권·자산운용·여신회사는 영업이익을 보고하지 않으므로, 이 종목에 한해 1단계 펀더멘털 게이트의 "
            "F1·F2를 아래 값으로 판정하십시오. 영업이익이 없다는 이유로 F1을 미달로 쓰지 말고, 예수부채가 큰 금융업의 총부채비율을 "
            "F2 근거로 쓰지 마십시오.\n"
            f"- F1 수익성: 최근 2개 보고 분기 세전이익 흑자: **{_mark(facts['f1'], True)}** — 세전이익 {pretax}\n"
            f"{f2_line}\n"
            "F1_profitability·F2_balance_sheet에 이 판정과 값을 그대로 쓰십시오. F3·F4, 점수, 시장 국면 하한, 추세 게이트, "
            "손익비 규칙은 바뀌지 않습니다.\n")
    f2_line = (f"- F2 Balance sheet (bank/lender): shareholders' equity / total assets at least "
               f"{EQUITY_TO_ASSETS_MIN * 100:.0f}% (the US 'well capitalized' leverage threshold, book equity as the "
               f"public proxy for Tier 1 capital): **{_mark(facts['f2'], False)}** — equity {_b(facts['equity'])} / "
               f"assets {_b(facts['assets'])} = {_pct(facts['equity_to_assets'])}" if bank else
               f"- F2 Balance sheet (asset manager/broker/insurer): total debt / equity below "
               f"{DEBT_TO_EQUITY_MAX * 100:.0f}%: **{_mark(facts['f2'], False)}** — debt {_b(facts['debt'])} / equity "
               f"{_b(facts['equity'])} = {_x(facts['debt_to_equity'])}")
    return (
        "\n\n### F1/F2 for a financial institution (deterministic · yfinance quarterly statements, no operating income line)\n"
        f"This company (industry: {facts['industry'] or 'Financial Services'}) reports no operating income line in its "
        "quarterly income statement; US GAAP banks, brokers, asset managers and lenders do not. For this issuer only, "
        "judge F1 and F2 of the Stage 1 fundamental gate on the values below. Do not fail F1 because operating income "
        "is absent, and do not use a deposit-heavy financial's total debt ratio for F2.\n"
        f"- F1 Profitability: positive pretax income in the latest two reported quarters: **{_mark(facts['f1'], False)}** "
        f"— pretax income {pretax}\n"
        f"{f2_line}\n"
        "Write these verdicts and values in F1_profitability and F2_balance_sheet. F3, F4, scoring, regime floors, "
        "the trend gate and R/R rules are unchanged.\n")


def facts_for(ticker, yf_module):
    """Facts for ``ticker`` (None when disabled, not a financial without operating income, or data fails)."""
    if not enabled() or not ticker:
        return None
    try:
        facts = compute(ticker, yf_module=yf_module)
    except Exception as error:  # noqa: BLE001 - fail-open: the shared F1/F2 rules still apply
        logger.warning("[FINANCIAL_F_GATE] %s facts unavailable: %s", ticker, error)
        return None
    if facts:
        logger.info("[SECTOR_BUY][US] ticker=%s subtype=%s f1=%s f2=%s(%s) equity_to_assets=%s debt_to_equity=%s",
                    ticker, facts["subtype"], facts["f1"], facts["f2"], facts["f2_rule"],
                    _pct(facts["equity_to_assets"]), _x(facts["debt_to_equity"]))
    return facts
