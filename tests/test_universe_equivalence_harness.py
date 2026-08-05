"""Offline tests for the 452-universe equivalence harness.

The harness itself decides whether Plan A step 8 may proceed, so its own
comparison logic has to be trustworthy: a harness that silently reports
"identical" would wave through exactly the change it exists to block.

Every test drives synthetic frames — no network, no KRX, no OPEN API quota.
The synthetic cases encode the three mechanisms measured live over 20 sessions
(2026-07-07 ~ 2026-08-04); see ``tasks/bench/universe_equivalence_20d.json``.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from tools.verify_universe_equivalence import (  # noqa: E402
    MARKET_CAP_FLOOR,
    _consumption_set_contrarian,
    _consumption_set_macro,
    compare_frames,
    slim_bundle,
)

# ``apply_absolute_filters`` needs Amount >= 100억 to keep a row.
BIG_AMOUNT = 50_000_000_000
OVER_FLOOR = MARKET_CAP_FLOOR * 2
UNDER_FLOOR = MARKET_CAP_FLOOR // 2


def _ohlcv(rows: dict[str, dict]) -> pd.DataFrame:
    return pd.DataFrame.from_dict(rows, orient="index")


def _caps(caps: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"시가총액": caps})


# --- slim_bundle -----------------------------------------------------------


def test_slim_bundle_drops_below_floor_from_all_three_frames():
    """전환 후 상태를 재현하려면 snapshot 만이 아니라 prev/cap 도 같이 줄여야 한다."""
    snapshot = _ohlcv({"000001": {"Close": 1.0}, "000002": {"Close": 2.0}})
    prev = _ohlcv({"000001": {"Close": 1.0}, "000002": {"Close": 2.0}})
    cap = _caps({"000001": OVER_FLOOR, "000002": UNDER_FLOOR})

    slim_snap, slim_prev, slim_cap = slim_bundle(snapshot, prev, cap)

    assert list(slim_snap.index) == ["000001"]
    assert list(slim_prev.index) == ["000001"]
    assert list(slim_cap.index) == ["000001"]


def test_slim_bundle_keeps_row_exactly_at_floor():
    """5,000억 컷은 ``>=`` 다. 경계 종목을 떨어뜨리면 유니버스가 통째로 어긋난다."""
    snapshot = _ohlcv({"000001": {"Close": 1.0}})
    cap = _caps({"000001": MARKET_CAP_FLOOR})

    slim_snap, _, _ = slim_bundle(snapshot, snapshot, cap)

    assert list(slim_snap.index) == ["000001"]


def test_slim_bundle_does_not_mutate_inputs():
    """같은 번들을 두 유니버스로 두 번 쓰므로 원본이 오염되면 대조가 무의미해진다."""
    snapshot = _ohlcv({"000001": {"Close": 1.0}, "000002": {"Close": 2.0}})
    cap = _caps({"000001": OVER_FLOOR, "000002": UNDER_FLOOR})

    slim_bundle(snapshot, snapshot, cap)

    assert list(snapshot.index) == ["000001", "000002"]
    assert list(cap.index) == ["000001", "000002"]


# --- compare_frames --------------------------------------------------------


def test_compare_frames_identical_when_same_tickers_order_and_score():
    frame = pd.DataFrame({"composite_score": [0.9, 0.5]}, index=["000001", "000002"])

    diff = compare_frames("t", frame, frame.copy())

    assert diff.identical
    assert diff.only_in_full == []
    assert diff.only_in_slim == []


def test_compare_frames_flags_ticker_dropped_by_slim_universe():
    full = pd.DataFrame({"composite_score": [0.9, 0.5]}, index=["000001", "000002"])
    slim = pd.DataFrame({"composite_score": [0.9]}, index=["000001"])

    diff = compare_frames("t", full, slim)

    assert not diff.identical
    assert diff.only_in_full == ["000002"]
    assert diff.only_in_slim == []


def test_compare_frames_flags_reordering_even_with_same_ticker_set():
    """``select_final_tickers`` 는 상위부터 집으므로 순서가 바뀌면 최종 선택이 바뀐다."""
    full = pd.DataFrame({"composite_score": [0.9, 0.5]}, index=["000001", "000002"])
    slim = pd.DataFrame({"composite_score": [0.9, 0.5]}, index=["000002", "000001"])

    diff = compare_frames("t", full, slim)

    assert not diff.identical
    assert diff.order_changed
    assert diff.only_in_full == []


def test_compare_frames_flags_score_drift_on_identical_ticker_order():
    """정규화가 집합에 의존하므로 같은 종목이라도 점수가 흔들릴 수 있다."""
    full = pd.DataFrame({"composite_score": [0.90]}, index=["000001"])
    slim = pd.DataFrame({"composite_score": [0.42]}, index=["000001"])

    diff = compare_frames("t", full, slim)

    assert not diff.identical
    assert diff.score_max_delta == pytest.approx(0.48)


def test_compare_frames_treats_error_as_not_identical():
    """예외를 삼키고 '일치'로 보고하면 하네스가 존재 이유를 잃는다."""
    diff = compare_frames("t", pd.DataFrame(), pd.DataFrame())
    diff.error = "boom"

    assert not diff.identical


# --- B군: 시총 필터가 없는 트리거의 소비 집합 -------------------------------


def _amount_ranked_frame(specs: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """(ticker, amount, open, close) → 트리거가 읽는 최소 컬럼 프레임."""
    return pd.DataFrame(
        [
            {"Open": o, "Close": c, "Amount": a, "Volume": 1_000_000}
            for _, a, o, c in specs
        ],
        index=[t for t, _, _, _ in specs],
    )


def test_macro_consumption_set_keeps_small_cap_with_heavy_turnover():
    """시총컷이 없으므로 소형·고거래대금 종목이 top100 에 들어온다.

    유니버스를 452 로 줄이면 이 종목이 사라진다 — 20거래일 실측에서 하루 평균
    11.4 개, 소비 집합의 최대 17.0% 가 5,000억 미만이었다.
    """
    snapshot = _amount_ranked_frame(
        [
            ("BIGCAP", BIG_AMOUNT, 100.0, 110.0),
            ("SMALLCAP", BIG_AMOUNT * 3, 100.0, 110.0),
        ]
    )

    consumed = _consumption_set_macro(snapshot, snapshot)

    assert set(consumed.index) == {"BIGCAP", "SMALLCAP"}
    # 거래대금이 큰 쪽이 앞이다 — nlargest(100, "Amount")
    assert list(consumed.index)[0] == "SMALLCAP"


def test_contrarian_consumption_set_requires_close_above_open():
    """``Close > Open`` 회복 신호가 없으면 후보에 못 든다 (:1290)."""
    snapshot = _amount_ranked_frame(
        [
            ("RISING", BIG_AMOUNT, 100.0, 110.0),
            ("FALLING", BIG_AMOUNT * 2, 110.0, 100.0),
        ]
    )

    consumed = _consumption_set_contrarian(snapshot, snapshot)

    assert list(consumed.index) == ["RISING"]


def test_consumption_sets_diverge_between_full_and_slim_universe():
    """B군 불일치의 재현 — 20/20 일 관측된 현상을 합성 데이터로 고정한다."""
    snapshot = _amount_ranked_frame(
        [
            ("BIGCAP", BIG_AMOUNT, 100.0, 110.0),
            ("SMALLCAP", BIG_AMOUNT * 3, 100.0, 110.0),
        ]
    )
    cap = _caps({"BIGCAP": OVER_FLOOR, "SMALLCAP": UNDER_FLOOR})
    slim_snap, slim_prev, _ = slim_bundle(snapshot, snapshot, cap)

    full_set = _consumption_set_macro(snapshot, snapshot)
    slim_set = _consumption_set_macro(slim_snap, slim_prev)
    diff = compare_frames("macro", full_set, slim_set)

    assert not diff.identical
    assert diff.only_in_full == ["SMALLCAP"]


# --- A군: 필터 순서가 만드는 불일치 ----------------------------------------


def test_absolute_filter_threshold_moves_when_universe_shrinks():
    """``morning_value_to_cap_ratio`` 불일치(9/20일)의 메커니즘.

    ``apply_absolute_filters`` 의 거래량 임계값은 **입력 집합의 평균**에서 나온다
    (trigger_batch.py:319). 이 트리거만 시총컷(:892)을 절대필터(:862) *뒤에* 걸어서,
    유니버스를 줄이면 평균이 올라가고 임계값이 따라 올라간다. 8/4 실측으로는
    58,054 → 134,804 (2.3배)였다.

    다른 5개는 시총컷을 먼저 걸므로 절대필터가 보는 집합이 양쪽에서 동일하다.
    """
    from trigger_batch import SCREENING_MIN_TRADE_VALUE, apply_absolute_filters

    # 5,000억 이상 3개(그중 BIGWEAK 은 거래량이 평범) + 5,000억 미만 소형주 20개.
    rows = {
        "BIG1": {"Amount": BIG_AMOUNT, "Volume": 1_000_000},
        "BIG2": {"Amount": BIG_AMOUNT, "Volume": 900_000},
        "BIGWEAK": {"Amount": BIG_AMOUNT, "Volume": 100_000},
    }
    for i in range(20):
        rows[f"SMALL{i:02d}"] = {"Amount": BIG_AMOUNT, "Volume": 100_000}
    full = pd.DataFrame.from_dict(rows, orient="index")
    slim = full.loc[["BIG1", "BIG2", "BIGWEAK"]]

    kept_full = apply_absolute_filters(full.copy(), min_value=SCREENING_MIN_TRADE_VALUE)
    kept_slim = apply_absolute_filters(slim.copy(), min_value=SCREENING_MIN_TRADE_VALUE)

    # 임계값은 입력 집합의 평균에서 나온다 — 유니버스를 줄이면 올라간다.
    assert full["Volume"].mean() * 0.2 < slim["Volume"].mean() * 0.2

    # 같은 대형주가 어느 유니버스로 들어왔느냐에 따라 살고 죽는다.
    # 이것이 morning_value_to_cap_ratio 가 9/20 일 갈린 이유다.
    assert "BIGWEAK" in kept_full.index
    assert "BIGWEAK" not in kept_slim.index
