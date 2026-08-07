import pandas as pd

from cores.stock_chart import _investor_types_for_chart


def test_intraday_chart_uses_combined_personal_other_group_only():
    frame = pd.DataFrame(
        columns=["기관합계", "외국인합계", "개인", "기타합계", "개인·기타합계"]
    )
    frame.attrs["intraday_estimate"] = True

    assert _investor_types_for_chart(frame) == [
        "기관합계",
        "외국인합계",
        "개인·기타합계",
    ]


def test_daily_chart_prefers_exact_other_total():
    frame = pd.DataFrame(columns=["기관합계", "외국인합계", "개인", "기타합계", "기타법인"])

    assert _investor_types_for_chart(frame) == [
        "기관합계",
        "외국인합계",
        "개인",
        "기타합계",
    ]
