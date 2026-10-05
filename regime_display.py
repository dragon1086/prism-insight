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


def _label(labels: dict, value: str | None, code: bool) -> str:
    token = str(value or "unknown")
    if code:
        return f"{labels.get(token, token)}({token})"
    # Channel text: no internal code. Telegram Markdown reads "_" in moderate_bull/trend_up
    # as italics, which garbles or rejects the message, so an unmapped code loses it too.
    return labels.get(token, token.replace("_", " "))


def regime_label(value: str | None, language: str = "ko", *, code: bool = True) -> str:
    return _label(REGIME_KO if language == "ko" else REGIME_EN, value, code)


def swing_label(value: str | None, language: str = "ko", *, code: bool = True) -> str:
    return _label(SWING_KO if language == "ko" else SWING_EN, value, code)


__all__ = ["regime_label", "swing_label"]
