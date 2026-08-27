"""Human-readable labels for the authoritative regime and swing context."""

from __future__ import annotations


REGIME_KO = {
    "parabolic": "폭주 강세",
    "strong_bull": "강한 강세",
    "moderate_bull": "온건 강세",
    "sideways": "횡보",
    "moderate_bear": "온건 약세",
    "strong_bear": "강한 약세",
}
REGIME_EN = {
    "parabolic": "Parabolic Bull",
    "strong_bull": "Strong Bull",
    "moderate_bull": "Moderate Bull",
    "sideways": "Sideways",
    "moderate_bear": "Moderate Bear",
    "strong_bear": "Strong Bear",
}
SWING_KO = {
    "trend_up": "상승 지속",
    "consolidation": "횡보·숨고르기",
    "pullback": "단기 조정",
    "unknown": "판단 보류",
}
SWING_EN = {
    "trend_up": "Trend Up",
    "consolidation": "Consolidation",
    "pullback": "Pullback",
    "unknown": "Unknown",
}


def regime_label(value: str | None, language: str = "ko") -> str:
    token = str(value or "unknown")
    labels = REGIME_KO if language == "ko" else REGIME_EN
    return f"{labels.get(token, token)}({token})"


def swing_label(value: str | None, language: str = "ko") -> str:
    token = str(value or "unknown")
    labels = SWING_KO if language == "ko" else SWING_EN
    return f"{labels.get(token, token)}({token})"


__all__ = ["regime_label", "swing_label"]
