"""Stable Korean sector spelling and conservative comparison of coarse labels.

This is a label/risk-comparison contract, not a source of company classifications.
"""
import asyncio
import re

KR_SECTOR_NAMES = (
    "IT 서비스", "건설", "금속", "기계·장비", "기타금융", "기타제조",
    "농업, 임업 및 어업", "보험", "부동산", "비금속", "섬유·의류",
    "오락·문화", "운송·창고", "운송장비·부품", "유통", "은행",
    "음식료·담배", "의료·정밀기기", "일반서비스", "전기·가스",
    "전기·전자", "제약", "종이·목재", "증권", "통신", "화학",
)


def _key(value: str) -> str:
    return re.sub(r"[\s·ㆍ∙]", "", value).casefold()


_CANONICAL = {_key(name): name for name in KR_SECTOR_NAMES}
_FINANCIAL = {_key("은행"), _key("기타금융")}
_MANUFACTURING = {_key(name) for name in (
    "금속", "기계·장비", "기타제조", "비금속", "섬유·의류", "운송장비·부품",
    "음식료·담배", "의료·정밀기기", "전기·전자", "제약", "종이·목재", "화학",
)}


def normalize_kr_sector(value: str) -> str:
    return _CANONICAL.get(_key(value), value.strip())


def sector_comparison_keys(value: str) -> set[str]:
    key = _key(value)
    if key == "금융":
        # KIS has a coarse residual finance label while insurance/securities
        # are separately named. Do not guess bank vs financial holding company;
        # count overlap conservatively against either existing policy label.
        return _FINANCIAL
    if key in {"제조", "출판매체복제"}:
        return _MANUFACTURING
    return {key}


def sectors_overlap(left: str, right: str) -> bool:
    return bool(sector_comparison_keys(left) & sector_comparison_keys(right))


_MISSING_SECTOR = {"", "unknown", "알 수 없음", "업종 미확인", "n/a", "none", "null"}


async def fill_missing_sector(scenario, lookup):
    """Fill scenario["sector"] from a system lookup when the BUY model left it out.

    The model stopped emitting the field (KR from 2026-10-21, US from 10-15), so the buy message
    showed "업종 미확인" and the same-sector cap saw "Unknown". The model's value still wins."""
    if not isinstance(scenario, dict) or scenario.get("analysis_failed"):
        return scenario
    if str(scenario.get("sector") or "").strip().casefold() not in _MISSING_SECTOR:
        return scenario
    try:
        found = await asyncio.to_thread(lookup)
    except Exception:  # noqa: BLE001 - lookup outage keeps the sector unknown
        found = None
    if isinstance(found, str) and found.strip().casefold() not in _MISSING_SECTOR:
        scenario["sector"] = found.strip()
        scenario["_sector_source"] = "system"
    return scenario
