from cores.data_prefetch import _dict_to_markdown


def test_intraday_estimate_metadata_is_rendered_without_becoming_a_table_row():
    rendered = _dict_to_markdown(
        {
            "2026-08-07": {
                "외국인합계": 100,
                "기관합계": 200,
                "개인·기타합계": -300,
            },
            "__meta__": {
                "data_status": "intraday_estimate",
                "note": (
                    "오늘 값은 KIS 장중 추정치(14:30 KST 기준)이며 "
                    "개인·기타합계는 역산 추정치입니다."
                ),
            },
        },
        "Investor Trading Volume",
    )

    assert "**데이터 상태:**" in rendered
    assert "KIS 장중 추정치" in rendered
    assert "개인·기타합계" in rendered
    assert "역산 추정치" in rendered
    assert "__meta__" not in rendered
