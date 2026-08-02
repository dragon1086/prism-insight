#!/usr/bin/env python3
"""
Verify daily grounded-facts wiring for the bot commands.

The weekly report has been grounded in KRX/yfinance numbers for a while; the
daily commands were not. Wiring them up meant teaching weekly_market_facts to
speak about a single session, and that is where the traps are:

1. weekly output is unchanged            — the Sunday report must not regress
2. daily prose drops the weekly wording  — "금요일 종가" on a Tuesday is a lie the
                                           LLM will faithfully repeat
3. movers refuses baseline == end        — close(x)/close(x) is +0.0% for every
                                           stock; silent corruption, not an error
4. facts never block the event loop      — one KRX scan shared by N callers
5. timeouts degrade to no facts          — an ungrounded answer beats a hung bot
6. every command call site merges facts  — the failure mode is silent omission
7. the receiving signature accepts them

Usage:  python3 tools/bench/verify_grounded_facts.py [--live]
        --live hits KRX/yfinance for real and checks the numbers are non-degenerate.
"""
import ast
import asyncio
import inspect
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import weekly_market_facts as wmf  # noqa: E402
from cores import market_facts_cache as mfc  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# Fakes — the KRX backend is resolved through _krx_fn, so one patch covers all.
# ---------------------------------------------------------------------------
_TICKERS = {"KOSPI": ["005930", "000660"], "KOSDAQ": ["247540"]}
_NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "247540": "에코프로비엠"}
# close by (yyyymmdd, ticker) — day 2 moves, day 5 moves more
_CLOSES = {
    "20260727": {"005930": 100.0, "000660": 200.0, "247540": 50.0},
    "20260728": {"005930": 110.0, "000660": 190.0, "247540": 55.0},
    "20260731": {"005930": 120.0, "000660": 180.0, "247540": 60.0},
}


def _install_fakes(monkey: dict) -> None:
    import pandas as pd

    def fake_index_ohlcv(start: str, end: str, ticker: str):
        rows = [d for d in sorted(_CLOSES) if start <= d <= end]
        if not rows:
            return pd.DataFrame()
        base = 1.0 if ticker == wmf.KOSPI_INDEX else 2.0
        return pd.DataFrame(
            {
                "시가": [base * 1000 + i for i, _ in enumerate(rows)],
                "고가": [base * 1000 + 50 + i for i, _ in enumerate(rows)],
                "저가": [base * 1000 - 50 + i for i, _ in enumerate(rows)],
                "종가": [base * 1000 + 10 + i * 5 for i, _ in enumerate(rows)],
            },
            index=pd.to_datetime(rows),
        )

    def fake_all_ticker_ohlcv(ymd: str):
        closes = _CLOSES.get(ymd)
        if closes is None:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "종가": [closes[t] for t in _NAMES],
                "거래대금": [10_000 - i for i, _ in enumerate(_NAMES)],
            },
            index=list(_NAMES),
        )

    def fake_ticker_list(ymd: str, market: str = "KOSPI"):
        return _TICKERS.get(market, [])

    table = {
        "get_index_ohlcv_by_date": fake_index_ohlcv,
        "get_market_ohlcv_by_ticker": fake_all_ticker_ohlcv,
        "get_market_ticker_list": fake_ticker_list,
        "get_market_ticker_name": lambda tk: _NAMES.get(tk, tk),
    }
    monkey["_krx_fn"] = wmf._krx_fn
    wmf._krx_fn = lambda name: table.get(name)

    # Investor flows come from Naver over HTTP; stub the whole fetch.
    monkey["_naver_investor_daily"] = wmf._naver_investor_daily
    wmf._naver_investor_daily = lambda sosok, bizdate: {
        date(2026, 7, 27): {"외국인": 100.0, "기관계": -50.0, "개인": -50.0},
        date(2026, 7, 28): {"외국인": 200.0, "기관계": -80.0, "개인": -120.0},
        date(2026, 7, 31): {"외국인": -30.0, "기관계": 10.0, "개인": 20.0},
    }


def _restore(monkey: dict) -> None:
    for name, original in monkey.items():
        setattr(wmf, name, original)


# ---------------------------------------------------------------------------
def test_weekly_unchanged() -> None:
    """The Sunday report is live. Its wording must survive this refactor."""
    print("\n[1] weekly output unchanged")
    out = wmf.build_kr_facts(date(2026, 7, 27), date(2026, 7, 31))
    check("weekly keeps '주간 시가'", "주간 시가" in out)
    check("weekly keeps '금요일 종가'", "금요일 종가" in out)
    check("weekly keeps '주간 누적 순매수'", "주간 누적 순매수" in out)
    check("weekly keeps '주간 상승률 상위'", "주간 상승률 상위" in out)
    check("weekly keeps '일별 종가'", "일별 종가" in out)
    check("weekly header spans the range", "2026-07-27 ~ 2026-07-31" in out)


def test_daily_labels() -> None:
    print("\n[2] daily prose drops weekly wording")
    out = wmf.build_kr_facts(
        date(2026, 7, 31), date(2026, 7, 31),
        kind="daily", baseline=date(2026, 7, 28),
    )
    check("no '금요일 종가' in daily", "금요일 종가" not in out, out[:120])
    check("no '주간' anywhere in daily", "주간" not in out,
          next((ln for ln in out.splitlines() if "주간" in ln), ""))
    check("uses '당일 순매수'", "당일 순매수" in out)
    check("uses '당일 상승률 상위'", "당일 상승률 상위" in out)
    check("header shows a single date", "2026-07-31]" in out)

    # 등락률의 기준점. 한국 시장 관례는 전일 종가 대비이고, 시가 대비(장중
    # 변동)를 쓰면 같은 날인데 기사와 다른 숫자가 나온다.
    # fake: 07-28 종가 1010 / 07-31 종가 1015 -> +0.50% (시가 대비면 +1.50%)
    check("daily references 전일 종가", "전일 종가" in out)
    check("daily pct is close-to-close, not open-to-close",
          "(+0.50%)" in out and "(+1.50%)" not in out,
          next((ln for ln in out.splitlines() if "KOSPI:" in ln), ""))
    check("daily bar shows only the session itself",
          "07-28" not in out.split("· 종가:")[1].split("\n")[0]
          if "· 종가:" in out else False)
    try:
        wmf.build_kr_facts(date(2026, 7, 31), date(2026, 7, 31), kind="hourly")
        check("unknown kind rejected", False, "no ValueError raised")
    except ValueError:
        check("unknown kind rejected", True)


def test_movers_guard() -> None:
    print("\n[3] movers refuses a degenerate baseline")
    labels = wmf._PERIOD_LABELS["daily"]
    same = wmf._kr_movers_block(date(2026, 7, 31), date(2026, 7, 31), labels)
    check("baseline == end yields no lines", same == [], f"got {same}")

    ok = wmf._kr_movers_block(date(2026, 7, 28), date(2026, 7, 31), labels)
    check("distinct sessions yield lines", bool(ok), f"got {len(ok)} lines")
    check("percentages are not all zero",
          bool(ok) and not all("+0.0%" in ln for ln in ok),
          " | ".join(ok))

    # Latency guard. movers costs 63.0s on app-server against 0.3s for the other
    # two blocks combined; leaving it on made KR grounding miss the timeout every
    # time, so the interactive path must never request it.
    fast = wmf.build_kr_facts(
        date(2026, 7, 31), date(2026, 7, 31),
        kind="daily", baseline=date(2026, 7, 28), include_movers=False,
    )
    check("include_movers=False drops the movers block", "상승률 상위" not in fast)
    check("...but keeps index and investor", "KOSPI:" in fast and "순매수" in fast)

    src = inspect.getsource(mfc._build)
    check("production KR path disables movers", "include_movers=False" in src,
          "cores.market_facts_cache._build must not pay the 63s cost")


def test_cache_single_flight() -> None:
    print("\n[4] one build shared by concurrent callers")
    mfc.reset_cache()
    calls = {"n": 0}

    def slow_build(market: str) -> dict:
        calls["n"] += 1
        time.sleep(0.25)
        return {"grounded_facts": "X", "period_label": "2026-07-31 (최근 거래일)"}

    original, mfc._build = mfc._build, slow_build
    try:
        async def run():
            return await asyncio.gather(*(mfc.daily_facts("KR") for _ in range(10)))

        started = time.monotonic()
        results = asyncio.run(run())
        elapsed = time.monotonic() - started
    finally:
        mfc._build = original
        mfc.reset_cache()

    check("10 concurrent callers -> 1 build", calls["n"] == 1, f"builds={calls['n']}")
    check("all callers get the payload",
          all(r.get("grounded_facts") == "X" for r in results))
    check("did not serialize (10x0.25s)", elapsed < 1.5, f"{elapsed:.2f}s")


def test_cache_degrades() -> None:
    print("\n[5] timeout and failure degrade to no facts")
    mfc.reset_cache()

    def hang(market: str) -> dict:
        time.sleep(1.0)
        return {"grounded_facts": "late"}

    orig_build, orig_timeout = mfc._build, mfc._BUILD_TIMEOUT
    mfc._build, mfc._BUILD_TIMEOUT = hang, 0.2
    try:
        out = asyncio.run(mfc.daily_facts("KR"))
    finally:
        mfc._build, mfc._BUILD_TIMEOUT = orig_build, orig_timeout
        mfc.reset_cache()
    check("timeout returns {}", out == {}, f"got {out!r}")

    def boom(market: str) -> dict:
        raise RuntimeError("KRX down")

    mfc._build = boom
    try:
        out = asyncio.run(mfc.daily_facts("KR"))
    finally:
        mfc._build = orig_build
        mfc.reset_cache()
    check("exception returns {}", out == {}, f"got {out!r}")

    try:
        asyncio.run(mfc.daily_facts("JP"))
        check("unknown market rejected", False, "no ValueError raised")
    except ValueError:
        check("unknown market rejected", True)

    check("empty result merges into a preset cleanly",
          ({"tbs": "qdr:w"} | {}) == {"tbs": "qdr:w"})


def test_timeout_does_not_duplicate() -> None:
    """
    Regression: wait_for cancels the await, never the thread.

    The first version cached {} on timeout and released the lock, so once that
    short empty-TTL expired the next caller launched a *second* KRX scan while
    the first was still running. Under a slow upstream that multiplies threads
    exactly when the system can least afford it. shield() + a shared in-flight
    task is what prevents it, and this pins that behaviour.
    """
    print("\n[6] a timed-out build is never restarted")
    mfc.reset_cache()
    active = {"now": 0, "peak": 0, "builds": 0}

    def slow(market: str) -> dict:
        active["builds"] += 1
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.6)
        active["now"] -= 1
        return {"grounded_facts": "X", "period_label": "2026-07-31 (최근 거래일)"}

    orig_build, orig_timeout, orig_ttl = mfc._build, mfc._BUILD_TIMEOUT, mfc._TTL_EMPTY
    mfc._build, mfc._BUILD_TIMEOUT, mfc._TTL_EMPTY = slow, 0.15, 0.0
    try:
        async def run():
            first = await mfc.daily_facts("KR")      # gives up at 0.15s
            second = await mfc.daily_facts("KR")     # must attach, not restart
            await asyncio.sleep(0.8)                 # let the build land
            third = await mfc.daily_facts("KR")      # served from cache
            return first, second, third

        first, second, third = asyncio.run(run())
    finally:
        mfc._build = orig_build
        mfc._BUILD_TIMEOUT = orig_timeout
        mfc._TTL_EMPTY = orig_ttl
        mfc.reset_cache()

    check("timed-out caller gets {}", first == {}, f"got {first!r}")
    check("second caller does not start a new build", active["peak"] == 1,
          f"peak_concurrent={active['peak']}")
    check("only one build ever ran", active["builds"] == 1,
          f"builds={active['builds']}")
    check("the survivor populates the cache",
          third.get("grounded_facts") == "X", f"got {third!r}")
    check("second caller also timed out (build still running)", second == {},
          f"got {second!r}")


def test_signature() -> None:
    print("\n[7] receiver accepts the grounding kwargs")
    from report_generator import generate_firecrawl_search_response
    params = inspect.signature(generate_firecrawl_search_response).parameters
    for key in ("grounded_facts", "period_label"):
        check(f"accepts {key}", key in params)


def test_call_sites() -> None:
    """A command that forgets the merge fails silently — pin it in the AST."""
    print("\n[8] every command call site merges daily_facts")
    src = (ROOT / "telegram_ai_bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    merged = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in ("_run_firecrawl_command", "_run_search_and_claude"):
            continue
        opts = next((k.value for k in node.keywords if k.arg == "search_opts"), None)
        if opts is None:
            continue
        merges = (
            isinstance(opts, ast.BinOp)
            and isinstance(opts.op, ast.BitOr)
            and "daily_facts" in ast.dump(opts.right)
        )
        check(f"line {node.lineno} merges daily_facts", merges,
              "" if merges else ast.dump(opts)[:90])
        merged += int(merges)
    check("all 5 commands grounded", merged == 5, f"merged={merged}")


def test_live() -> None:
    print("\n[9] LIVE: real KRX / yfinance daily facts")
    sessions = wmf.resolve_recent_sessions()
    check("KR sessions resolve", sessions is not None, str(sessions))
    if sessions:
        baseline, latest = sessions
        check("KR sessions are distinct days", baseline != latest, f"{baseline} vs {latest}")
        facts = wmf.build_kr_facts(latest, latest, kind="daily", baseline=baseline)
        check("KR daily facts non-empty", bool(facts), f"{len(facts)} chars")
        if facts:
            print("\n".join("     " + ln for ln in facts.splitlines()[:8]))
            movers = [ln for ln in facts.splitlines() if "상승률 상위" in ln]
            check("movers present and not all +0.0%",
                  bool(movers) and not all("+0.0%" in m for m in movers),
                  f"{len(movers)} lines")
            check("no weekly wording leaked", "주간" not in facts and "금요일" not in facts)

            # 실데이터 교차검증: 리포트에 실린 등락률이 정말 전일 종가 대비인가.
            # 2026-07-31 KOSPI 는 시가 대비 +16.57%, 전일 종가 대비 +17.91% 로
            # 두 기준의 차이가 큰 날이라 이 검사가 실제로 변별력을 가진다.
            fn = wmf._krx_fn("get_index_ohlcv_by_date")
            raw = fn(wmf._ymd(baseline), wmf._ymd(latest), wmf.KOSPI_INDEX)
            col = wmf._col(raw, "close")
            expected = (float(raw.iloc[-1][col]) / float(raw.iloc[-2][col]) - 1) * 100
            check("KOSPI pct equals close-to-close",
                  f"({expected:+.2f}%)" in facts,
                  f"expected {expected:+.2f}%")

    us = wmf.resolve_recent_us_sessions()
    check("US sessions resolve", us is not None, str(us))
    if us:
        # Must mirror cores.market_facts_cache._build exactly — calling without
        # baseline would exercise a branch production never takes.
        us_facts = wmf.build_us_facts(us[1], us[1], kind="daily", baseline=us[0])
        check("US daily facts non-empty", bool(us_facts), f"{len(us_facts)} chars")
        check("US drops weekly wording", "주간" not in us_facts)
        check("US references 전일 종가", "전일 종가" in us_facts,
              next((ln for ln in us_facts.splitlines() if "S&P" in ln), ""))

        import yfinance as yf
        hist = yf.Ticker("^GSPC").history(
            start=us[0].strftime("%Y-%m-%d"),
            end=(us[1] + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        if len(hist) >= 2:
            prev, last = float(hist.iloc[0]["Close"]), float(hist.iloc[-1]["Close"])
            expected = f"({(last - prev) / prev * 100:+.2f}%)"
            sp = next((ln for ln in us_facts.splitlines() if "S&P 500" in ln), "")
            check("US S&P pct equals close-to-close", expected in sp,
                  f"expected {expected} in {sp!r}")


def main() -> int:
    monkey: dict = {}
    _install_fakes(monkey)
    try:
        test_weekly_unchanged()
        test_daily_labels()
        test_movers_guard()
    finally:
        _restore(monkey)

    test_cache_single_flight()
    test_cache_degrades()
    test_timeout_does_not_duplicate()
    test_signature()
    test_call_sites()

    if "--live" in sys.argv:
        test_live()
    else:
        print("\n[9] LIVE skipped (pass --live to run)")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
