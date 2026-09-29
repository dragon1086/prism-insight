"""Pure functions of the preregistered US UDVR replay (research tool)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("udvr_replay", ROOT / "tools/replay_us_udvr_proxy.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _bars(closes, vols):
    return [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "close": c, "volume": v}
            for i, (c, v) in enumerate(zip(closes, vols))]


def test_udvr_counts_up_and_down_close_volume_over_50_sessions():
    closes = [100] + [101, 100] * 25          # 25 up days, 25 down days
    vols = [1] + [300, 100] * 25              # up days carry 3x the volume
    assert abs(m.udvr(_bars(closes, vols)) - 3.0) < 1e-9


def test_udvr_needs_51_completed_bars_and_ignores_flat_days():
    assert m.udvr(_bars([100] * 50, [1] * 50)) is None
    closes = [100] + [100] * 10 + [101, 100] * 20
    vols = [1] + [999] * 10 + [200, 100] * 20
    assert abs(m.udvr(_bars(closes, vols)) - 2.0) < 1e-9  # flat-day volume ignored


def test_us_session_date_is_new_york():
    assert m.session_date("2026-09-30 00:56:38") == "2026-09-29"


def test_overextension_uses_ma20_gap_or_rsi():
    closes = [100] * 20 + [130]
    ext, rsi = m.extension(_bars(closes, [1] * 21))
    assert ext > 25 and rsi == 100.0


def test_verdict_requires_holdout_ci_above_zero_and_discovery_direction():
    good = {"enough": True, "diff": 2.0, "ci90": [0.5, 3.0]}
    assert m.verdict({"enough": True, "diff": 1.0}, good) == "ADOPT_CANDIDATE"
    assert m.verdict({"enough": True, "diff": -1.0}, good) == "RETIRE"
    assert m.verdict({"enough": True, "diff": 1.0}, {**good, "ci90": [-0.1, 3.0]}) == "RETIRE"
    assert m.verdict({"enough": False, "diff": 1.0}, good) == "INSUFFICIENT"
