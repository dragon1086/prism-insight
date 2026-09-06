"""Pure, fail-closed statistics for the preregistered historical mixture round.

Returns and drawdowns are fractions, not percentage points. No NAV rescaling,
candidate selection, execution accounting, or claims about future profit live here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite

import numpy as np

DAY_MS = 86_400_000
PATHS = {"OHLC", "OLHC"}
REFERENCES = {"single", "BH"}
YEARS = {"2023", "2024", "2025"}
CELLS = {f"c{cost}/d{delay}/{path}" for cost in (1, 2) for delay in (5, 30) for path in PATHS}


def _failure(reason, status="INVALID_RESEARCH"):
    return {"status": status, "reason_codes": [reason]}


def _finite(value):
    return isinstance(value, (int, float, np.number)) and not isinstance(value, bool) and isfinite(value)


def _days(timestamps):
    """Accept exact UTC close boundaries or their final millisecond, never opens."""
    values = np.asarray(timestamps)
    if values.ndim != 1 or not len(values):
        raise ValueError("EMPTY_TIMESTAMPS")
    if not all(_finite(x) and int(x) == x for x in values):
        raise ValueError("INVALID_TIMESTAMPS")
    values = values.astype(np.int64)
    if not np.all(np.diff(values) > 0):
        raise ValueError("UNORDERED_OR_DUPLICATE_TIMESTAMPS")
    remainders = values % DAY_MS
    if not (np.all(remainders == 0) or np.all(remainders == DAY_MS - 1)):
        raise ValueError("NOT_UTC_DAILY_CLOSES")
    days = (values - (remainders == 0)) // DAY_MS
    if len(days) > 1 and not np.all(np.diff(days) == 1):
        raise ValueError("MISSING_DAILY_CLOSES")
    return days


def summarise_nav(daily_nav, *, start_ms, end_ms, mtm_mdd,
                  completed_campaigns, initial_nav=10000.0, turnover=0.0,
                  underwater_days=None, campaign_pnls=None):
    """Summarize one continuous replay; supplied MDD must include intraday events.

    daily_nav is a sequence of timestamp_ms/nav dicts (no inception row).
    start_ms/end_ms are inclusive inception/exclusive end UTC midnight boundaries.
    Completed-campaign PnLs, when supplied, must contain net PnL per flat-to-flat
    campaign, not individual fills. Undefined PF/payoff are JSON null, not infinity.
    """
    try:
        days = _days([r["timestamp_ms"] for r in daily_nav])
        nav = np.asarray([r["nav"] for r in daily_nav], dtype=float)
        if not all(_finite(x) for x in (start_ms, end_ms, initial_nav, mtm_mdd, turnover)):
            raise ValueError("NONFINITE_INPUT")
        if initial_nav != 10000 or start_ms % DAY_MS or end_ms % DAY_MS or end_ms <= start_ms:
            raise ValueError("INVALID_INCEPTION_OR_PERIOD")
        if days[0] != start_ms // DAY_MS or days[-1] != end_ms // DAY_MS - 1:
            raise ValueError("INCOMPLETE_PERIOD")
        if not np.all(np.isfinite(nav)) or np.any(nav <= 0):
            raise ValueError("NONPOSITIVE_OR_NONFINITE_NAV")
        if not 0 <= mtm_mdd <= 1 or turnover < 0:
            raise ValueError("INVALID_DRAWDOWN_OR_TURNOVER")
        if isinstance(completed_campaigns, bool) or not isinstance(completed_campaigns, (int, np.integer)) or completed_campaigns < 0:
            raise ValueError("INVALID_CAMPAIGN_COUNT")
        if underwater_days is not None and (not _finite(underwater_days) or not 0 <= underwater_days <= (end_ms - start_ms) / DAY_MS):
            raise ValueError("INVALID_UNDERWATER_DURATION")
        previous = np.r_[initial_nav, nav[:-1]]
        returns = nav / previous - 1
        logs = np.log(nav / previous)
        dates = [datetime.fromtimestamp(int(d) * 86400, timezone.utc) for d in days]
        monthly, yearly = {}, {}
        for key_format, output in (("%Y-%m", monthly), ("%Y", yearly)):
            group_start = initial_nav
            for i, date in enumerate(dates):
                key = date.strftime(key_format)
                if i == len(dates) - 1 or dates[i + 1].strftime(key_format) != key:
                    output[key] = float(nav[i] / group_start - 1)
                    group_start = nav[i]
        peak, peak_day, max_underwater = initial_nav, start_ms / DAY_MS, 0.0
        was_underwater = False
        for day, value in zip(days, nav):
            if value < peak or was_underwater:
                max_underwater = max(max_underwater, float(day + 1 - peak_day))
            was_underwater = value < peak
            if value >= peak:
                peak, peak_day = value, float(day + 1)
        daily_mdd = float(np.max(1 - nav / np.maximum.accumulate(np.r_[initial_nav, nav])[1:]))
        if mtm_mdd + 1e-12 < daily_mdd:
            raise ValueError("MTM_MDD_BELOW_DAILY_MDD")
        growth = float(np.sum(logs))
        top5 = float(np.sum(np.sort(logs)[-5:]))
        with np.errstate(over="raise", invalid="raise"):
            cagr = float(np.expm1(growth / ((end_ms - start_ms) / DAY_MS / 365.25)))
            removed = float(np.expm1(growth - top5))
        pf = payoff = None
        if campaign_pnls is not None:
            pnls = np.asarray(campaign_pnls, dtype=float)
            if pnls.ndim != 1 or len(pnls) != completed_campaigns or not np.all(np.isfinite(pnls)):
                raise ValueError("INVALID_CAMPAIGN_PNLS")
            wins, losses = pnls[pnls > 0], pnls[pnls < 0]
            pf = float(wins.sum() / -losses.sum()) if len(losses) else None
            payoff = float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None
        result = {
            "status": "OK", "reason_codes": [], "initial_nav": initial_nav,
            "start_ms": int(start_ms), "end_ms": int(end_ms), "daily_count": len(nav),
            "final_nav": float(nav[-1]), "total_return": float(nav[-1] / initial_nav - 1),
            "cagr": cagr, "mtm_mdd": float(mtm_mdd), "daily_close_mdd": daily_mdd,
            "daily_returns": returns.tolist(),
            "daily_volatility": float(np.std(returns, ddof=1)) if len(returns) > 1 else None,
            "monthly_returns": monthly, "yearly_returns": yearly,
            "positive_month_fraction": sum(x > 0 for x in monthly.values()) / len(monthly),
            "worst_month": min(monthly.values()), "worst_year": min(yearly.values()),
            "daily_close_underwater_days": max_underwater, "underwater_days": underwater_days,
            "completed_campaigns": int(completed_campaigns), "turnover": float(turnover),
            "profit_factor": pf, "payoff_ratio": payoff,
            "top5_removed_return": removed, "top5_log_contribution": top5,
            "net_log_growth": growth, "top5_share": top5 / growth if growth > 0 else None,
            "concentration_status": "OK" if growth > 0 else "NONPOSITIVE_LOG_GROWTH",
        }
        json.dumps(result, allow_nan=False)
        return result
    except (ValueError, TypeError, KeyError, OverflowError, FloatingPointError) as exc:
        return _failure(str(exc))


def train_reference_scale(mixture_vols, reference_vols):
    """One training-only capital allocation coefficient, shared across all cells."""
    if set(mixture_vols) != PATHS or set(reference_vols) != PATHS:
        return _failure("MISSING_TRAIN_PATH", "INSUFFICIENT")
    if any(not _finite(v) or v <= 0 for v in [*mixture_vols.values(), *reference_vols.values()]):
        return _failure("NONPOSITIVE_OR_NONFINITE_TRAIN_VOL", "INSUFFICIENT")
    ratios = {p: float(mixture_vols[p] / reference_vols[p]) for p in sorted(PATHS)}
    if any(not _finite(v) for v in ratios.values()):
        return _failure("NONFINITE_TRAIN_VOL_RATIO", "INSUFFICIENT")
    return {"status": "OK", "reason_codes": [], "scale": min(1.0, *ratios.values()), "path_ratios": ratios}


def bootstrap_max_error(differences, timestamps_ms, *, draws=2000, seed=20260906,
                        block_days=30, require_full_period=True):
    """Stream within-year circular blocks; identical indices across both paths.

    Production requires exactly fourteen columns and 1096 aligned daily returns.
    require_full_period=False exists solely for small hand-calculation fixtures.
    This is a centered-mean max-error bound, NOT studentized maxT or DSR.
    """
    try:
        days = _days(timestamps_ms)
        if set(differences) != PATHS:
            raise ValueError("MISSING_BOOTSTRAP_PATH")
        columns = sorted(differences["OHLC"])
        if not columns or set(columns) != set(differences["OLHC"]):
            raise ValueError("MISMATCHED_BOOTSTRAP_COLUMNS")
        years = np.asarray([datetime.fromtimestamp(int(d) * 86400, timezone.utc).year for d in days])
        if require_full_period and (len(columns) != 14 or len(days) != 1096 or
                                    (years[0], years[-1]) != (2023, 2025)):
            raise ValueError("INCOMPLETE_BOOTSTRAP_FAMILY_OR_PERIOD")
        if not isinstance(draws, int) or draws < 1 or not isinstance(block_days, int) or block_days < 1:
            raise ValueError("INVALID_BOOTSTRAP_PARAMETERS")
        matrices = {p: np.asarray([differences[p][c] for c in columns], dtype=float).T for p in sorted(PATHS)}
        if any(a.shape != (len(days), len(columns)) or not np.all(np.isfinite(a)) for a in matrices.values()):
            raise ValueError("INVALID_BOOTSTRAP_SERIES")
        means = {p: a.mean(axis=0) for p, a in matrices.items()}
        maxima = {p: np.empty(draws) for p in matrices}
        groups = [np.flatnonzero(years == year) for year in sorted(set(years))]
        rng = np.random.default_rng(seed)
        offsets = np.arange(block_days)
        for b in range(draws):
            indices = []
            for group in groups:
                starts = rng.integers(0, len(group), size=(len(group) + block_days - 1) // block_days)
                within = ((starts[:, None] + offsets) % len(group)).ravel()[:len(group)]
                indices.append(group[within])
            idx = np.concatenate(indices)
            for path, matrix in matrices.items():
                maxima[path][b] = np.max(matrix[idx].mean(axis=0) - means[path])
        results = {}
        for path in matrices:
            q = float(np.quantile(maxima[path], 0.95, method="linear"))
            if not _finite(q) or not np.all(np.isfinite(means[path] - q)):
                raise ValueError("NONFINITE_BOOTSTRAP_RESULT")
            results[path] = {"q95_max_error": q, "mean_differences": dict(zip(columns, means[path].tolist())),
                             "lower_bounds": dict(zip(columns, (means[path] - q).tolist()))}
        return {"status": "OK", "reason_codes": [], "paths": results, "columns": columns,
                "draws": draws, "seed": seed, "block_days": block_days,
                "method": "unstudentized_centered_mean_max_error", "daily_count": len(days)}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return _failure(str(exc), "INSUFFICIENT")


def evaluate_research_gate(selected_id, base, matched_references, adjusted_lowers,
                           stress, partial, *, train_eligible=True):
    """Evaluate only the frozen train choice. Never choose an OOS replacement.

    base is path->metrics; references and lowers are path->single/BH->evidence.
    stress and partial contain all eight distinct preregistered cell IDs.
    The caller must establish registry completeness and frozen-selection integrity.
    """
    checks, invalid = {}, []

    def check(name, value):
        checks[name] = bool(value)

    def metric_valid(metrics, label, concentration=False):
        fields = ["cagr", "mtm_mdd", "total_return", "positive_month_fraction"]
        if concentration:
            fields += ["top5_removed_return"]
        if not isinstance(metrics, dict) or metrics.get("status") != "OK" or any(not _finite(metrics.get(k)) for k in fields):
            invalid.append(label + ":INVALID_METRICS")
            return False
        years = metrics.get("yearly_returns", {})
        count = metrics.get("completed_campaigns")
        if (set(years) != YEARS or any(not _finite(v) for v in years.values()) or
                not isinstance(count, (int, np.integer)) or isinstance(count, bool) or count < 0 or
                not 0 <= metrics["mtm_mdd"] <= 1 or not 0 <= metrics["positive_month_fraction"] <= 1 or
                metrics["total_return"] <= -1 or metrics["cagr"] <= -1):
            invalid.append(label + ":INVALID_METRIC_DOMAIN")
            return False
        if concentration and ("top5_share" not in metrics or (metrics["top5_share"] is not None and not _finite(metrics["top5_share"]))):
            invalid.append(label + ":INVALID_CONCENTRATION")
            return False
        return True

    if set(base) != PATHS or set(matched_references) != PATHS or set(adjusted_lowers) != PATHS:
        invalid.append("MISSING_BASE_PATH")
    if set(stress) != CELLS or set(partial) != CELLS:
        invalid.append("MISSING_OR_MISMATCHED_STRESS_CELLS")
    for path in sorted(PATHS & set(base)):
        metrics = base[path]
        if metric_valid(metrics, path, True):
            check(path + ":CAGR", metrics["cagr"] >= 0.20)
            check(path + ":MTM_MDD", metrics["mtm_mdd"] <= 0.20)
            check(path + ":POSITIVE_MONTHS", metrics["positive_month_fraction"] >= 0.55)
            check(path + ":ALL_YEARS_POSITIVE", all(v > 0 for v in metrics["yearly_returns"].values()))
            check(path + ":COMPLETED_CAMPAIGNS", metrics["completed_campaigns"] >= 60)
            check(path + ":TOP5_REMOVED", metrics["top5_removed_return"] > 0)
            check(path + ":TOP5_SHARE", metrics.get("top5_share") is not None and metrics["top5_share"] <= 0.50)
        refs, lowers = matched_references.get(path, {}), adjusted_lowers.get(path, {})
        if set(refs) != REFERENCES or set(lowers) != REFERENCES:
            invalid.append(path + ":MISSING_REFERENCE_OR_CI")
        for ref in sorted(REFERENCES & set(refs) & set(lowers)):
            ref_valid = metric_valid(refs[ref], path + ":" + ref)
            if not _finite(lowers[ref]):
                invalid.append(path + ":" + ref + ":INVALID_CI")
            elif ref_valid and metric_valid(metrics, path, True):
                check(path + ":" + ref + ":CAGR_OUTPERFORMANCE", metrics["cagr"] > refs[ref]["cagr"])
                check(path + ":" + ref + ":MDD_NONWORSE", metrics["mtm_mdd"] <= refs[ref]["mtm_mdd"])
                check(path + ":" + ref + ":ADJUSTED_LOWER", lowers[ref] > 0)
    for group_name, group in (("STRESS", stress), ("PARTIAL", partial)):
        for cell, metrics in sorted(group.items()):
            label = group_name + ":" + cell
            if metric_valid(metrics, label):
                check(label + ":NET_POSITIVE", metrics["total_return"] > 0)
                check(label + ":MDD", metrics["mtm_mdd"] <= 0.25)
                if group_name == "STRESS":
                    check(label + ":TWO_POSITIVE_YEARS", sum(v > 0 for v in metrics["yearly_returns"].values()) >= 2)
    check("FROZEN_TRAIN_SELECTION_ELIGIBLE", bool(selected_id) and train_eligible)
    status = "INVALID_RESEARCH" if invalid else ("RESEARCH_CANDIDATE" if all(checks.values()) else "NO_QUALIFYING_CANDIDATE")
    return {"status": status, "selected_id": selected_id, "checks": checks,
            "reason_codes": sorted(set(invalid + [k for k, passed in checks.items() if not passed])),
            "profitability_status": "INSUFFICIENT", "auto_activate": False,
            "forward_completed_campaigns": 0}
