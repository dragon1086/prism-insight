"""
Temporal Gate — 창 밖 문서를 컨텍스트에 넣지 않는다.

왜 필요한가
-----------
`tbs=qdr:w` 는 검색 엔진에 대한 **힌트지 보장이 아니다.** 실측으로 `qdr:m` 창에
`May 28, 2026` 기사가 통과했다. 지금까지는 그걸 그대로 LLM 에 넘기면서
발행일을 함께 적어주고 "기간을 벗어난 기사는 이번 주 일로 서술하지 말라"고
프롬프트로 부탁했다. 부탁은 지켜질 때도 있고 아닐 때도 있다.

이 모듈은 그 부탁을 **필터로 승격**시킨다. 창 밖이면 down-rank 가 아니라 drop 이고,
발행일을 끝내 복원하지 못해도 drop 이다. 날짜를 모르는 문서는 신선도를 주장할
근거가 없으므로 신선도가 핵심인 리포트에 들어갈 자격이 없다.

drop 이 안전한 이유
-------------------
grounded_facts(KRX/yfinance 원천 수치)가 먼저 붙었기 때문이다. 웹 항목이 0건이어도
지수·수급 숫자는 남아 있어 리포트가 성립한다. 순서가 반대였으면 이 게이트는
위험했다 — 그래서 근거 연결을 먼저 했다.

날짜 파싱
---------
firecrawl_client._absolute_date 는 상대 표현("2 days ago")만 절대화하고 나머지는
**그대로 통과시킨다.** 따라서 여기 들어오는 값은 형식이 섞여 있다:

    ""                          없음
    "2026-08-01 (2 days ago)"   _absolute_date 가 만든 형태
    "2026-07-31T09:00:00+09:00" ISO datetime
    "May 28, 2026"              영문 롱폼 ← 실제로 창을 새어 나온 그 형식
    "2026.07.31" / "2026/07/31" 한국 매체
    "2026년 7월 31일"

메타데이터에서 못 찾으면 URL 에서 복원을 시도한다. 한국 매체 URL 은 대개 날짜를
품고 있다(`/2026/07/31/`, `AKR20260731...`). 복원도 실패하면 그때 버린다.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# tbs 창을 일수로. 검색 엔진이 힌트로만 쓰는 값을 여기서는 계약으로 쓴다.
# 경계는 관대하게 잡는다 — 타임존 차이로 하루 어긋난 정상 기사를 버리면 손해다.
_WINDOW_DAYS = {
    "qdr:d": 2,
    "qdr:w": 8,
    "qdr:m": 32,
    "qdr:y": 367,
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# 앞부분에 붙은 절대 날짜. "2026-08-01 (2 days ago)" 와 ISO datetime 을 함께 잡는다.
_ISO = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
# "May 28, 2026" / "28 May 2026" / "May 2026" 은 일자가 없으므로 제외
_EN_MDY = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(20\d{2})")
_EN_DMY = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(20\d{2})")
_KO = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")

# URL 복원. 구분자가 있는 형태를 먼저 보고, 없으면 8자리 연속 숫자를 본다.
_URL_SEP = re.compile(r"/(20\d{2})[/._-](\d{1,2})[/._-](\d{1,2})(?:\D|$)")
_URL_RUN = re.compile(r"(20\d{2})(\d{2})(\d{2})")


def _mk(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_published(raw: str) -> Optional[date]:
    """섞여 들어오는 발행일 표기를 date 로. 못 읽으면 None."""
    if not raw:
        return None
    text = str(raw).strip()

    m = _ISO.search(text)
    if m:
        got = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    m = _KO.search(text)
    if m:
        got = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    m = _EN_MDY.search(text)
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon:
            got = _mk(int(m.group(3)), mon, int(m.group(2)))
            if got:
                return got

    m = _EN_DMY.search(text)
    if m:
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if mon:
            got = _mk(int(m.group(3)), mon, int(m.group(1)))
            if got:
                return got

    return None


def recover_from_url(url: str, *, as_of: Optional[date] = None) -> Optional[date]:
    """
    URL 에 박힌 날짜를 복원한다.

    기사 ID 가 우연히 날짜처럼 보일 수 있으므로 결과를 반드시 검증한다 —
    2000년 이전이거나 as_of 다음날을 넘어가면 날짜가 아니라 우연이다.
    """
    if not url:
        return None
    as_of = as_of or datetime.now().date()
    horizon = as_of + timedelta(days=1)

    for pattern in (_URL_SEP, _URL_RUN):
        for m in pattern.finditer(url):
            got = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if got and date(2000, 1, 1) <= got <= horizon:
                return got
    return None


# 본문 상단의 발행 스탬프. 시:분이 붙은 형태를 먼저 본다 — 기사 본문에 등장하는
# 다른 날짜("2026년 상반기 실적")와 달리 시각이 붙어 있으면 발행 스탬프일 확률이 높다.
_BODY_STAMPED = re.compile(
    r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})\s*일?"
    r"[\sT]+\d{1,2}\s*[:시]\s*\d{2}"
)
_BODY_LABELED = re.compile(
    r"(?:입력|등록|송고|발행|기사입력|Updated|Published)\D{0,12}"
    r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})"
)
_BODY_HEAD_CHARS = 800


def recover_from_body(body: str, *, as_of: Optional[date] = None) -> Optional[date]:
    """
    본문 상단에서 발행일을 복원한다.

    검색 결과의 web 채널은 발행일 메타데이터를 거의 달고 오지 않는다. 실측에서
    `/theme` 은 7건 중 5건이 발행일 미상이었고, 그대로 두면 게이트가 web 채널을
    통째로 죽인다. 본문은 이미 손에 있으므로 추가 비용 없이 되살릴 수 있다.

    본문 전체를 뒤지면 "2026년 상반기 실적" 같은 서술상의 날짜를 발행일로
    오인한다. 그래서 (1) 앞부분만 보고, (2) 시각이 붙었거나 '입력/등록' 같은
    라벨이 앞선 형태만 인정한다.
    """
    if not body:
        return None
    head = body[:_BODY_HEAD_CHARS]
    as_of = as_of or datetime.now().date()
    horizon = as_of + timedelta(days=1)
    floor = date(as_of.year - 3, 1, 1)

    for pattern in (_BODY_LABELED, _BODY_STAMPED):
        for m in pattern.finditer(head):
            got = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if got and floor <= got <= horizon:
                return got
    return None


def window_days(tbs: Optional[str]) -> Optional[int]:
    """tbs 를 일수로. 모르는 값이면 None (= 게이트 미적용)."""
    return _WINDOW_DAYS.get(tbs or "")


def apply_temporal_gate(
    items: list,
    *,
    tbs: Optional[str],
    as_of: Optional[date] = None,
) -> tuple[list, dict]:
    """
    창 밖 문서와 발행일 미상 문서를 걸러낸다.

    Args:
        items: normalize_search_items() 결과.
        tbs: 요청한 신선도 창. 모르는 값이면 게이트를 적용하지 않고 전량 통과시킨다
             (알 수 없는 창을 임의 해석해 버리는 것보다 안전하다).
        as_of: 기준일. 생략하면 오늘.

    Returns:
        (통과 항목, 통계). 통계는 소스 품질 관측용이며 그대로 로깅된다.
        복원 건수가 크면 메타데이터 소스가 나쁘다는 뜻이고, 창밖 폐기가 크면
        tbs 가 먹지 않는다는 뜻이다 — 둘 다 조치가 다르므로 따로 센다.
    """
    stats = {
        "total": len(items),
        "kept": 0,
        "recovered_from_url": 0,
        "recovered_from_body": 0,
        "dropped_undated": 0,
        "dropped_out_of_window": 0,
    }

    days = window_days(tbs)
    if days is None:
        stats["kept"] = len(items)
        logger.info(f"Temporal gate skipped (tbs={tbs!r}): {len(items)} items passed through")
        return list(items), stats

    as_of = as_of or datetime.now().date()
    oldest = as_of - timedelta(days=days)
    kept: list = []

    for item in items:
        published = parse_published(item.get("date") or "")
        if published is None:
            published = recover_from_url(item.get("url") or "", as_of=as_of)
            if published is not None:
                stats["recovered_from_url"] += 1
                # 하위 렌더링이 그대로 쓰도록 정규화한 값을 되돌려 넣는다.
                item["date"] = f"{published:%Y-%m-%d} (URL 복원)"

        if published is None:
            published = recover_from_body(
                item.get("body") or item.get("snippet") or "", as_of=as_of
            )
            if published is not None:
                stats["recovered_from_body"] += 1
                item["date"] = f"{published:%Y-%m-%d} (본문 복원)"

        if published is None:
            stats["dropped_undated"] += 1
            continue
        if published < oldest:
            stats["dropped_out_of_window"] += 1
            continue

        item["_published"] = published
        kept.append(item)

    stats["kept"] = len(kept)
    logger.info(
        f"Temporal gate (tbs={tbs}, window={oldest}~{as_of}): "
        f"{stats['kept']}/{stats['total']} kept, "
        f"{stats['dropped_undated']} undated, "
        f"{stats['dropped_out_of_window']} out-of-window, "
        f"{stats['recovered_from_url']} recovered from URL, "
        f"{stats['recovered_from_body']} from body"
    )
    return kept, stats
