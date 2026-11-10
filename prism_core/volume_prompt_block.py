"""Shared volume-interpretation block for the BUY/SELL prompts (KR·US × ko·en).

2026-11-11 prompt diet (audit D7/X8): one source instead of eight identical copies.
BUY keeps the deterministic lower-bound exception (momentum signal 1) and the
"preserve momentum conditions / add no entry gate" closing line; SELL has neither
(no momentum count or entry gate exists there) and keeps only the priority guard.
BUY wording is byte-identical to the previous copies. SELL drops only the BUY-only sentence
("preserve momentum conditions / add no score bonus, penalty, threshold or entry gate"),
which had no object in SELL; no rule changed.
"""

_KO = {
    "heading": "### 거래량 해석 기준",
    "basis": ("- 동일 종목의 거래량을 출처·기준 거래일·세션 범위·비교 기간·수집 시각·확정 여부와 함께 확인하십시오. "
              "전일 대비와 5일 평균 대비 비율을 20일 평균 대비로 해석하지 마십시오."),
    "window": ("- 20일 평균 비교에는 비교 대상 봉 이전의 확정된 20거래일과 분석 대상 봉을 확보하십시오. 휴장일을 고려해 "
               "조회 범위를 늘리고, 이력 부족·미완성봉은 미확정으로 남기며 수치나 충족 여부를 만들어내지 마십시오. "
               "장중·시간외 거래량을 정규장 전체 거래량과 직접 비교하지 마십시오."),
    "lower_bound": ("- 예외(하한 판정): 개별 추세 팩트의 '거래량' 줄은 해당 봉 이전 확정 20거래일 평균 대비로 결정론적으로 "
                    "계산한 값이므로 거래량 비율은 이 줄을 우선 인용하십시오. 미완성 당일봉이라도 장중 누적 거래량이 이미 확정 "
                    "20거래일 평균의 200% 이상이면 거래량은 마감까지 줄지 않으므로 모멘텀 신호 1의 '당일' 조건을 충족한 것으로 "
                    "셉니다. 200% 미만이면 충족으로 세지 않되 마감 전 값이므로 거래량 부진의 근거로도 쓰지 마십시오. 확정 "
                    "세션만 인용하면서 당일 대량 거래를 생략하지 마십시오."),
    "pullback": ("- 기존 상승 추세에서 지지선을 유지하고 하락 폭이 축소되는 저거래량 조정은 정상 눌림일 수 있습니다. "
                 "지지선이 무너지면 거래량 감소를 보유·매수 근거로 삼지 마십시오."),
    "breakout": ("- 거래량 증가를 동반하고 돌파 가격을 유지하는 정상 돌파를 고점 부근이라는 이유만으로 배제하지 마십시오. "
                 "급등 후 거래량 증가에도 돌파에 실패하고 가격이 밀리면 추격 위험을 검토하십시오."),
    "extremes": ("- 거래량이 감소한 신고가·신저가만으로 매수·매도·반등을 확정하지 마십시오. "
                 "거래량만으로 기관 매집이나 분배를 단정하지 마십시오."),
    "scope_buy": ("- 이 지침은 기존 거래량 근거의 해석을 보완합니다. 기존 모멘텀 조건·트리거 가산은 유지하고, 별도 "
                  "가점·감점·임계값·진입 차단 조건을 추가하지 마십시오. 법인 이벤트·손절·트레일링 우선순위를 변경하거나 "
                  "지연하지 마십시오."),
    "scope_sell": ("- 이 지침은 기존 거래량 근거의 해석을 보완합니다. "
                   "법인 이벤트·손절·트레일링 우선순위를 변경하거나 지연하지 마십시오."),
}

_EN = {
    "heading": "### Volume Interpretation",
    "basis": ("- Compare the same stock's volume with its source, session date, session coverage, reference window, "
              "capture time and finality. Do not treat previous-day or 5-day-average ratios as 20-day-average ratios."),
    "window": ("- For a 20-day average comparison, obtain 20 completed sessions preceding the evaluated bar plus the "
               "analysis bars. Expand the query range for market holidays; leave insufficient history or unfinished "
               "bars unknown and never invent values or condition satisfaction. Do not directly compare intraday or "
               "extended-hours volume with full regular-session volume."),
    "lower_bound": ("- Exception (lower bound): the 'volume' line in the individual trend facts is computed "
                    "deterministically against the 20 completed sessions preceding each bar; cite its ratios first. "
                    "Even for today's unfinished bar, if cumulative intraday volume already reaches 200% of the "
                    "completed 20-session average, it cannot fall before the close, so count momentum signal 1 "
                    "('today') as met. Below 200%, do not count it as met, but do not cite it as weak volume either "
                    "because the session is still open. Never quote only completed sessions while omitting today's "
                    "heavy volume."),
    "pullback": ("- In an established uptrend, a low-volume pullback holding support with narrowing declines may be a "
                 "normal correction. If support breaks, do not use declining volume as a reason to hold or buy."),
    "breakout": ("- Do not reject a valid high-volume breakout holding its breakout level merely because it is near a "
                 "high. After a sharp rise, assess chasing risk when increased volume accompanies a failed breakout "
                 "and falling prices."),
    "extremes": ("- A low-volume new high or new low alone does not establish a buy, sell or rebound. Never infer "
                 "institutional accumulation or distribution from volume alone."),
    "scope_buy": ("- This guidance interprets existing volume evidence. Preserve existing momentum conditions and "
                  "trigger credits; add no score bonus, penalty, threshold or entry gate. Do not override or delay "
                  "corporate-event, stop-loss or trailing-stop priority."),
    "scope_sell": ("- This guidance interprets existing volume evidence. "
                   "Do not override or delay corporate-event, stop-loss or trailing-stop priority."),
}

PLACEHOLDER = "{volume_interpretation_block}"


def volume_interpretation_block(language: str, role: str, indent: str = "") -> str:
    """Lines joined with newline + ``indent`` so the block keeps the caller's prompt indentation."""
    text = _KO if language == "ko" else _EN
    keys = (["heading", "basis", "window"]
            + (["lower_bound"] if role == "buy" else [])
            + ["pullback", "breakout", "extremes", "scope_buy" if role == "buy" else "scope_sell"])
    return ("\n" + indent).join(text[key] for key in keys)


def install_volume_block(instruction: str, language: str, role: str, indent: str = "") -> str:
    if instruction.count(PLACEHOLDER) != 1:   # a prompt without exactly one slot is a build error, not a runtime case
        raise ValueError("volume block placeholder must appear exactly once")
    return instruction.replace(PLACEHOLDER, volume_interpretation_block(language, role, indent))
