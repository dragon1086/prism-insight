"""Render the public macro block once for synthesis and final report assembly."""

from prism_core.market_report_context import public_macro_prose


def render_macro_section(macro_context, language="ko", market="KR"):
    """Preserve each market's existing public prose and fallback formatting."""
    if not macro_context:
        return ""
    report_prose = (public_macro_prose(macro_context, language) if market == "US"
                    else macro_context.get("report_prose", ""))
    if report_prose:
        header = ("### 거시경제 환경\n\n" if language == "ko" else "### Macroeconomic Environment\n\n") if market == "US" else ""
        return header + report_prose + "\n\n"
    macro_section = ""
    regime = macro_context.get("market_regime", "sideways")
    regime_rationale = macro_context.get("regime_rationale", "")
    leading = macro_context.get("leading_sectors", [])
    lagging = macro_context.get("lagging_sectors", [])
    risks = macro_context.get("risk_events", [])

    if language == "ko":
        regime_labels = {
            "parabolic": "폭주 강세장",
            "strong_bull": "강한 강세장", "moderate_bull": "보통 강세장",
            "sideways": "횡보장", "moderate_bear": "보통 약세장", "strong_bear": "강한 약세장"
        }
        macro_section += "### 거시경제 환경\n\n"
        macro_section += f"**시장 체제**: {regime_labels.get(regime, regime)}\n\n"
        if regime_rationale:
            macro_section += f"**판단 근거**: {regime_rationale}\n\n"
        if leading:
            sectors_str = ", ".join([s.get("sector", "") for s in leading[:3]])
            macro_section += f"**주도 섹터**: {sectors_str}\n\n"
        if lagging:
            sectors_str = ", ".join([s.get("sector", "") for s in lagging[:3]])
            macro_section += f"**소외 섹터**: {sectors_str}\n\n"
        if risks:
            for r in risks[:3]:
                macro_section += f"- ⚠️ {r.get('event', '')} (영향: {r.get('severity', 'medium')})\n"
            macro_section += "\n"
    else:
        macro_section += "### Macroeconomic Environment\n\n"
        macro_section += f"**Market Regime**: {regime.replace('_', ' ').title()}\n\n"
        if regime_rationale:
            macro_section += f"**Rationale**: {regime_rationale}\n\n"
        if leading:
            sectors_str = ", ".join([s.get("sector", "") for s in leading[:3]])
            macro_section += f"**Leading Sectors**: {sectors_str}\n\n"
        if lagging:
            sectors_str = ", ".join([s.get("sector", "") for s in lagging[:3]])
            macro_section += f"**Lagging Sectors**: {sectors_str}\n\n"
        if risks:
            for r in risks[:3]:
                macro_section += f"- ⚠️ {r.get('event', '')} (Severity: {r.get('severity', 'medium')})\n"
            macro_section += "\n"

    return macro_section
