import json

import pytest

from prism_core.sector_names import normalize_kr_sector, sectors_overlap
from tracking.helpers import check_sector_diversity


class Cursor:
    def __init__(self, sectors):
        self.rows = [(json.dumps({"sector": sector}),) for sector in sectors]

    def execute(self, *args):
        pass

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize("candidate", ["전기·전자", "전기전자", " 전기 전자 "])
def test_provider_spelling_cannot_bypass_existing_absolute_cap(candidate):
    assert not check_sector_diversity(Cursor(["전기·전자", "전기·전자"]), candidate, 2, .5)
    assert normalize_kr_sector(candidate) == "전기·전자"


def test_precise_financial_categories_remain_distinct_but_coarse_cannot_escape():
    assert not sectors_overlap("은행", "기타금융")
    assert sectors_overlap("금융", "기타금융")
    assert sectors_overlap("금융", "은행")
    assert not check_sector_diversity(Cursor(["은행", "기타금융"]), "금융", 2, .5)
    assert not check_sector_diversity(Cursor(["금융", "금융"]), "기타금융", 2, .5)


def test_other_sectors_remain_available_and_db_is_not_rewritten():
    cursor = Cursor(["전기·전자", "전기전자"])
    before = list(cursor.rows)
    assert check_sector_diversity(cursor, "제약", 2, .5)
    assert cursor.rows == before


def test_coarse_manufacturing_comparison_does_not_claim_a_precise_classification():
    assert normalize_kr_sector("제조") == "제조"
    assert sectors_overlap("제조", "전기·전자")
    assert not sectors_overlap("제조", "은행")
