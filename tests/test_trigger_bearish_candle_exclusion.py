"""매수 트리거는 음봉 종목을 뽑지 않는다 (KR·US).

Rocky 보고(2026-08-05): 스크리닝 결과에 음봉이 계속 섞여 나온다.

전수 확인 결과 8개 트리거 중 네 자리에 ``Close > Open`` 검사가 없었다:

| 트리거 | KR | US |
|---|---|---|
| volume_surge / gap_up / value_to_cap / closing_strength / contrarian | ✅ | ✅ |
| daily_rise_top | ❌ **없었다** | ✅ 있었다 |
| macro_sector_leader | ❌ **없었다** | ✅ 있었다 |
| volume_surge_flat | ❌ **없었다** | ❌ **없었다** |

KR 두 자리는 US 에 이미 있던 것이 KR 로만 이식되지 않은 비대칭이었다.

각 트리거가 음봉을 통과시킨 경로가 서로 다르다는 점이 중요하다 — 한 곳만 고치면
나머지가 남는다:

- ``daily_rise_top``  : 전일 종가 대비 +3% 만 본다. 갭상승 후 종일 밀린 종목도 통과.
- ``macro_sector_leader``: 시장 평균 대비 상대강도만 본다. 하락장에서는 음봉이어도
  상대강도가 양수일 수 있다.
- ``volume_surge_flat``: ``|전일대비| <= 5%`` 인 "횡보"만 본다. 거래량이 터지면서
  시가 아래로 마감하는 것은 매집이 아니라 분산인데 이 조건으로는 구분이 안 된다.

각 테스트는 음봉이 배제되는 것과 **같은 조건의 양봉은 여전히 뽑히는 것**을 함께
확인한다. 후자가 없으면 "전부 걸러내서 통과"하는 가짜 통과를 못 잡는다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

REPO_ROOT = Path(__file__).resolve().parents[1]

import trigger_batch  # noqa: E402

KR_CAP_FLOOR = 500_000_000_000
KR_AMOUNT = 50_000_000_000  # SCREENING_MIN_TRADE_VALUE(100억) 위
US_AMOUNT = 500_000_000  # MIN_TRADING_VALUE($100M) 위


def _run_us_scenario(script_body: str) -> str:
    """US 트리거 시나리오를 **별도 프로세스**에서 돌리고 stdout 을 돌려준다.

    ``prism-us/cores/`` 는 리포 루트의 ``cores/`` 를 가리는 **별도 패키지**다
    (양쪽에 ``rs_rating`` 이 있고, ``us_surge_detector`` 는 prism-us 쪽에만 있다).
    이 파일의 KR 테스트가 ``trigger_batch`` 를 import 하면서 루트 ``cores`` 가
    ``sys.modules`` 에 박히므로, 같은 프로세스에서 ``us_trigger_batch`` 를 import
    하면 ``ModuleNotFoundError: cores.us_surge_detector`` 가 난다.

    ``us_trigger_batch`` 는 자기 import 시점에 ``prism-us`` 를 ``sys.path`` 맨 앞에
    넣으므로, **깨끗한 프로세스**에서는 정상적으로 로드된다. 로직을 복사해 검사하면
    실제 코드가 아니라 사본을 검증하게 되므로 프로세스를 나눈다.
    """
    completed = subprocess.run(
        [sys.executable, "-c", script_body],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"US 시나리오 실패 (rc={completed.returncode})\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    return completed.stdout


def _frame(rows: dict[str, dict]) -> pd.DataFrame:
    return pd.DataFrame.from_dict(rows, orient="index")


# --- KR ---------------------------------------------------------------------


def test_kr_daily_rise_top_excludes_bearish_candle():
    """갭상승 후 밀린 음봉은 전일 대비 +3% 를 넘겨도 뽑히지 않는다."""
    # BULL: 시가 100 → 종가 110 (양봉, 전일대비 +10%)
    # BEAR: 시가 120 → 종가 105 (음봉, 전일대비 +5% — 갭상승 후 종일 밀렸다)
    snapshot = _frame(
        {
            "BULL": {"Open": 100.0, "Close": 110.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 120.0, "Close": 105.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    prev = _frame(
        {
            "BULL": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    cap = pd.DataFrame({"시가총액": {"BULL": KR_CAP_FLOOR * 2, "BEAR": KR_CAP_FLOOR * 2}})

    result = trigger_batch.trigger_afternoon_daily_rise_top("20260804", snapshot, prev, cap)

    assert "BEAR" not in result.index
    assert "BULL" in result.index


def test_kr_volume_surge_flat_excludes_bearish_candle(monkeypatch):
    """거래량이 터지면서 시가 아래로 마감한 횡보주는 매집이 아니다."""
    # MA20 게이트는 이 테스트의 관심사가 아니다. 0 = 데이터 없음 → 통과.
    monkeypatch.setattr(trigger_batch, "_compute_ma20", lambda *a, **k: 0.0)

    # 둘 다 거래량 2배, 전일대비 절대값 5% 이내(횡보).
    snapshot = _frame(
        {
            "BULL": {"Open": 99.0, "Close": 102.0, "Volume": 2_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 104.0, "Close": 98.0, "Volume": 2_000_000, "Amount": KR_AMOUNT},
        }
    )
    prev = _frame(
        {
            "BULL": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    cap = pd.DataFrame({"시가총액": {"BULL": KR_CAP_FLOOR * 2, "BEAR": KR_CAP_FLOOR * 2}})

    result = trigger_batch.trigger_afternoon_volume_surge_flat("20260804", snapshot, prev, cap)

    assert "BEAR" not in result.index
    assert "BULL" in result.index


def test_kr_closing_strength_excludes_gap_down_recovery_below_previous_close(caplog):
    """장중 양봉이어도 전일 종가를 회복하지 못한 갭하락 반등은 제외한다."""
    snapshot = _frame(
        {
            # 둘 다 장중 양봉이고 종가가 고가에 가깝고 거래량도 증가했다.
            "RECOVERED": {
                "Open": 99.0,
                "High": 106.0,
                "Low": 98.0,
                "Close": 105.0,
                "Volume": 2_000_000,
                "Amount": KR_AMOUNT,
            },
            # 실리콘투 유형: 큰 갭하락 뒤 반등했지만 전일 종가 아래에서 마감했다.
            "BELOW_PREV": {
                "Open": 93.0,
                "High": 97.0,
                "Low": 92.0,
                "Close": 96.0,
                "Volume": 2_000_000,
                "Amount": KR_AMOUNT,
            },
        }
    )
    prev = _frame(
        {
            "RECOVERED": {
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0,
                "Volume": 1_000_000,
                "Amount": KR_AMOUNT,
            },
            "BELOW_PREV": {
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0,
                "Volume": 1_000_000,
                "Amount": KR_AMOUNT,
            },
        }
    )
    cap = pd.DataFrame(
        {"시가총액": {"RECOVERED": KR_CAP_FLOOR * 2, "BELOW_PREV": KR_CAP_FLOOR * 2}}
    )

    with caplog.at_level("INFO", logger="trigger_batch"):
        result = trigger_batch.trigger_afternoon_closing_strength(
            "20260827", snapshot, prev, cap
        )

    assert "BELOW_PREV" not in result.index
    assert "RECOVERED" in result.index
    assert "reason=close_below_previous_close" in caplog.text
    assert "sample=BELOW_PREV" in caplog.text


def test_kr_macro_sector_leader_excludes_bearish_candle():
    """하락장에서 상대강도가 양수여도 음봉이면 그날의 주도주가 아니다."""
    macro_context = {
        "leading_sectors": [{"sector": "반도체", "confidence": 0.9}],
        "sector_map": {"BULL": "반도체", "BEAR": "반도체"},
    }
    snapshot = _frame(
        {
            "BULL": {"Open": 99.0, "Close": 103.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 108.0, "Close": 101.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    prev = _frame(
        {
            "BULL": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    cap = pd.DataFrame({"시가총액": {"BULL": KR_CAP_FLOOR * 2, "BEAR": KR_CAP_FLOOR * 2}})

    result = trigger_batch.trigger_macro_sector_leader(
        "20260804", snapshot, prev, cap, macro_context=macro_context
    )

    # BEAR 는 전일대비 +1% 로 상대강도가 양수지만 음봉이라 빠진다.
    assert "BEAR" not in result.index
    assert "BULL" in result.index


def test_kr_macro_sector_leader_returns_empty_when_every_leader_is_bearish():
    """전부 음봉이면 빈 결과다 — 억지로 하나 뽑지 않는다."""
    macro_context = {
        "leading_sectors": [{"sector": "반도체", "confidence": 0.9}],
        "sector_map": {"BEAR1": "반도체", "BEAR2": "반도체"},
    }
    snapshot = _frame(
        {
            "BEAR1": {"Open": 108.0, "Close": 101.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR2": {"Open": 110.0, "Close": 102.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    prev = _frame(
        {
            "BEAR1": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR2": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    cap = pd.DataFrame({"시가총액": {"BEAR1": KR_CAP_FLOOR * 2, "BEAR2": KR_CAP_FLOOR * 2}})

    result = trigger_batch.trigger_macro_sector_leader(
        "20260804", snapshot, prev, cap, macro_context=macro_context
    )

    assert result.empty


# --- US ---------------------------------------------------------------------


_US_SCENARIO = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / "prism-us")!r})

import pandas as pd
import us_trigger_batch as us

# MA20 게이트는 이 시나리오의 관심사가 아니다. 0 = 데이터 없음 → 통과.
us._compute_ma20 = lambda *a, **k: 0.0

A = {US_AMOUNT}
snapshot = pd.DataFrame.from_dict({{
    "BULL": {{"Open": 99.0, "Close": 102.0, "Volume": 2_000_000, "Amount": A}},
    "BEAR": {{"Open": 104.0, "Close": 98.0, "Volume": 2_000_000, "Amount": A}},
}}, orient="index")
prev = pd.DataFrame.from_dict({{
    "BULL": {{"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": A}},
    "BEAR": {{"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": A}},
}}, orient="index")

result = us.trigger_afternoon_volume_surge_flat("20260804", snapshot, prev, None)
print("SELECTED=" + ",".join(str(t) for t in result.index))
"""


def test_us_volume_surge_flat_excludes_bearish_candle():
    """US 횡보 트리거에도 같은 구멍이 있었다 — KR 과 함께 막는다."""
    stdout = _run_us_scenario(_US_SCENARIO)

    selected_line = next(
        line for line in stdout.splitlines() if line.startswith("SELECTED=")
    )
    selected = [t for t in selected_line.removeprefix("SELECTED=").split(",") if t]

    assert "BEAR" not in selected
    assert "BULL" in selected


# --- 이미 막혀 있던 자리 (회귀 방지) -----------------------------------------


def test_kr_volume_surge_still_excludes_bearish_candle():
    """이번 변경이 기존에 동작하던 음봉 배제를 건드리지 않았는지 확인한다."""
    snapshot = _frame(
        {
            "BULL": {"Open": 99.0, "Close": 105.0, "Volume": 2_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 108.0, "Close": 104.0, "Volume": 2_000_000, "Amount": KR_AMOUNT},
        }
    )
    prev = _frame(
        {
            "BULL": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
            "BEAR": {"Open": 100.0, "Close": 100.0, "Volume": 1_000_000, "Amount": KR_AMOUNT},
        }
    )
    cap = pd.DataFrame({"시가총액": {"BULL": KR_CAP_FLOOR * 2, "BEAR": KR_CAP_FLOOR * 2}})

    result = trigger_batch.trigger_morning_volume_surge("20260804", snapshot, prev, cap)

    assert "BEAR" not in result.index
