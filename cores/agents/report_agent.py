"""SDK-neutral report agent definition."""

from dataclasses import dataclass
from typing import Iterable


def report_time_contract(reference_date: str, language: str = "ko") -> str:
    """Date-only reference is not evidence that a current daily bar is final."""
    if language == "ko":
        return (f"\n\n## 자료 시점과 확정 여부\n작성 기준일 {reference_date}는 개별 자료의 관측 시각이나 마감 확인이 아닙니다. "
                "가격·수급·재무의 출처와 각 기준일/시각을 따로 보존하세요. "
                "당일 OHLCV의 Close라는 열 이름만으로 확정 종가·최종 거래량·마감 완료를 단정하지 마세요. "
                "명시적 마감/확정 근거가 없으면 BAR_FINALITY_UNKNOWN으로 취급하고, 본문에는 조회 당시 가격/거래량 또는 확정 여부 미확인으로 표현하세요. "
                "요약·투자전략에서도 미확정 자료를 확정값으로 바꾸지 마세요.")
    return (f"\n\n## Evidence time and finality\nReference date {reference_date} is not an observation timestamp or proof of market close. "
            "Preserve separate source dates/times for prices, flows and financials. "
            "A current-day OHLCV column named Close does not establish a final close or final daily volume. "
            "Without explicit finality evidence, treat it as BAR_FINALITY_UNKNOWN and describe an observed price/volume, not a completed session. "
            "Summaries and strategies must preserve that uncertainty.")


@dataclass(frozen=True)
class ReportAgent:
    """Describe one report agent without constructing an SDK-specific object."""

    name: str
    instruction: str
    server_names: tuple[str, ...] = ()

    def __init__(
        self,
        name: str,
        instruction: str,
        server_names: Iterable[str] | None = None,
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "server_names", tuple(server_names or ()))
