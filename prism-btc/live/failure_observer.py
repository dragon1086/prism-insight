"""Fail-open forward observer for the Round-10 C1 pyramid hypothesis."""
from __future__ import annotations

import bisect
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.engine import _get_tf_slice
from core.failure_guard import (
    MODEL_VERSION,
    FailureGuardFeatures,
    classify_failure_cluster,
    should_observe_pyramid_block,
)
from live import tracking

DEFAULT_OI_DB = Path(__file__).resolve().parents[1] / "state" / "btc_research_5m.db"


@dataclass(frozen=True)
class FailureClassification:
    cluster: int
    features: FailureGuardFeatures


def oi_db_path() -> Path:
    return Path(os.environ.get("PRISM_BTC_OI_DB", str(DEFAULT_OI_DB)))


def load_oi_change_6h(
    db_path: str | Path,
    as_of: pd.Timestamp,
    *,
    max_stale_hours: float = 2.0,
) -> float | None:
    """Load seven hourly PIT observations and return latest/t-6h - 1."""
    path = Path(db_path)
    if not path.exists():
        return None
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")
    else:
        as_of = as_of.tz_convert("UTC")
    cutoff_ms = int(as_of.timestamp() * 1000)
    try:
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT timestamp, open_interest FROM open_interest "
                "WHERE timestamp<=? ORDER BY timestamp DESC LIMIT 7",
                (cutoff_ms,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - observer data must fail open
        return None
    if len(rows) < 7:
        return None
    latest_ts, latest_oi = int(rows[0][0]), float(rows[0][1])
    oldest_ts, oldest_oi = int(rows[-1][0]), float(rows[-1][1])
    stale_ms = cutoff_ms - latest_ts
    horizon_ms = latest_ts - oldest_ts
    if stale_ms > max_stale_hours * 3_600_000:
        return None
    if not (5 * 3_600_000 <= horizon_ms <= 7 * 3_600_000):
        return None
    if latest_oi <= 0 or oldest_oi <= 0:
        return None
    return latest_oi / oldest_oi - 1.0


class FailureObserver:
    """Records C1 would-block add-on entries without changing their intent."""

    def __init__(
        self,
        root_conn: sqlite3.Connection,
        tf_data: dict[str, pd.DataFrame],
        funding_times: list[int],
        funding_rates: list[float],
        *,
        oi_path: str | Path | None = None,
        mode: str = "shadow",
    ):
        self.conn = root_conn
        self.tf_data = tf_data
        self.funding_times = funding_times
        self.funding_rates = funding_rates
        self.oi_path = Path(oi_path) if oi_path is not None else oi_db_path()
        self.mode = mode

    def _funding_z(self, bar_time: pd.Timestamp, side: str) -> float | None:
        cutoff = int(pd.Timestamp(bar_time).value // 1_000_000)
        idx = bisect.bisect_right(self.funding_times, cutoff) - 1
        if idx < 60 or idx >= len(self.funding_rates):
            return None
        if cutoff - int(self.funding_times[idx]) > 12 * 3_600_000:
            return None
        current = float(self.funding_rates[idx])
        prior = pd.Series(self.funding_rates[max(0, idx - 180):idx], dtype=float)
        if len(prior) < 60:
            return None
        iqr = float(prior.quantile(0.75) - prior.quantile(0.25))
        if iqr == 0 or pd.isna(iqr):
            return None
        raw = (current - float(prior.median())) / iqr
        return raw if side == "long" else -raw

    def _features(
        self,
        bar_time: pd.Timestamp,
        side: str,
    ) -> FailureGuardFeatures | None:
        four_h = _get_tf_slice(self.tf_data, bar_time, "4h")
        one_d = _get_tf_slice(self.tf_data, bar_time, "1d")
        if len(four_h) < 21 or len(one_d) < 35:
            return None
        last4 = four_h.iloc[-1]
        last1 = one_d.iloc[-1]
        required4 = ("ma10", "ma35", "atr14", "close", "high", "low", "volume")
        required1 = ("ma10", "ma35", "atr14")
        if any(pd.isna(last4.get(name)) for name in required4):
            return None
        if any(pd.isna(last1.get(name)) for name in required1):
            return None
        atr4 = float(last4["atr14"])
        atr1 = float(last1["atr14"])
        close4 = float(last4["close"])
        if atr4 <= 0 or atr1 <= 0 or close4 <= 0:
            return None
        volume_base = float(four_h["volume"].iloc[-21:-1].median())
        if volume_base <= 0:
            return None
        direction = 1.0 if side == "long" else -1.0
        funding_z = self._funding_z(bar_time, side)
        oi_change = load_oi_change_6h(self.oi_path, bar_time)
        if funding_z is None or oi_change is None:
            return None
        return FailureGuardFeatures(
            ts_4h=abs(float(last4["ma10"]) - float(last4["ma35"])) / atr4,
            ts_1d=abs(float(last1["ma10"]) - float(last1["ma35"])) / atr1,
            extension_4h=(
                direction * (close4 - float(last4["ma35"])) / atr4
            ),
            ret_24h=(
                direction * (close4 / float(four_h["close"].iloc[-7]) - 1.0)
            ),
            atr_ratio_4h=atr4 / close4,
            volume_ratio_4h=float(last4["volume"]) / volume_base,
            range_ratio_4h=(float(last4["high"]) - float(last4["low"])) / atr4,
            funding_z=funding_z,
            oi_change_6h=oi_change,
            lane_swing=0.0,
        )

    def observe(
        self,
        *,
        bar_time: pd.Timestamp,
        side: str,
        tranche_index: int,
    ) -> int | None:
        """Persist one C1 would-block intent; every failure returns ``None``."""
        if self.mode != "shadow" or int(tranche_index) <= 0:
            return None
        try:
            classification = self.classify(bar_time=bar_time, side=side)
            if classification is None:
                return None
            cluster = classification.cluster
            if not should_observe_pyramid_block(
                cluster=cluster, tranche_index=tranche_index
            ):
                return None
            observation_id = tracking.record_failure_shadow_intent(
                self.conn,
                mode=self.mode,
                signal_ts=str(bar_time),
                side=side,
                tranche_index=tranche_index,
                cluster=int(cluster),
                features=classification.features.to_dict(),
                model_version=MODEL_VERSION,
            )
            tracking.log_event(
                self.conn,
                "c1_pyramid_would_block",
                f"C1 would block {side} add-on tranche={tranche_index} "
                f"observation_id={observation_id}",
                mode=self.mode,
                ts=str(bar_time),
            )
            return observation_id
        except Exception:  # noqa: BLE001 - observer must never block trading
            return None

    def classify(
        self,
        *,
        bar_time: pd.Timestamp,
        side: str,
    ) -> FailureClassification | None:
        """Return a fail-open PIT classification for shadow or demo callers."""
        try:
            features = self._features(bar_time, side)
            if features is None:
                return None
            cluster = classify_failure_cluster(features)
            if cluster is None:
                return None
            return FailureClassification(cluster=int(cluster), features=features)
        except Exception:  # noqa: BLE001 - observer must never block trading
            return None
