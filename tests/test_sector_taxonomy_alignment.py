from prism_core.sector_taxonomy import SectorMap, leader_matches, matched_leader


def test_legacy_broad_sector_preserved():
    assert leader_matches({"sector": "Technology"}, "Technology", "Software - Application")
    assert not leader_matches({"sector": "Technology"}, "Healthcare")


def test_legacy_mislabeled_semiconductors_remains_narrow():
    leader = {"sector": "Semiconductors"}
    assert leader_matches(leader, "Technology", "Semiconductors")
    assert not leader_matches(leader, "Technology", "Software - Application")
    assert not leader_matches(leader, "Technology")


def test_explicit_industry_requires_candidate_industry():
    leader = {"sector": "Technology", "industry": "Semiconductors"}
    assert leader_matches(leader, "Technology", "Semiconductors")
    assert not leader_matches(leader, "Technology", "Software - Infrastructure")
    assert not leader_matches(leader, "Technology")


def test_no_fuzzy_theme_promotion():
    assert not leader_matches({"sector": "AI Technology"}, "Technology", "Semiconductors")
    assert not leader_matches({"sector": "Software"}, "Technology", "Software - Application")


def test_compatible_dict_carries_industry_without_extra_network():
    mapping = SectorMap()
    mapping.update({"NVDA": "Technology", "MSFT": "Technology"})
    mapping.industries.update({"NVDA": "Semiconductors", "MSFT": "Software - Infrastructure"})
    leading = [{"sector": "Semiconductors", "confidence": .8}]
    assert matched_leader(leading, mapping, "NVDA") is leading[0]
    assert matched_leader(leading, mapping, "MSFT") is None


def test_real_us_batch_same_candidates_retain_industry_reject_unrelated():
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = '''
import socket, sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
sys.path.insert(0, str(Path.cwd() / "prism-us"))
import us_trigger_batch as batch
from prism_core.sector_taxonomy import SectorMap
mapping = SectorMap()
mapping.update({"NVDA":"Technology", "MSFT":"Technology"})
mapping.industries.update({"NVDA":"Semiconductors", "MSFT":"Software - Infrastructure"})
frame = pd.DataFrame({"FinalScore":[1.,1.]}, index=["NVDA","MSFT"])
with patch.object(socket.socket, "connect", side_effect=AssertionError("network prohibited")):
    broad = batch._build_topdown_pool({"Gap":frame}, {"leading_sectors":[{"sector":"Technology", "confidence":.8}]}, "FinalScore", mapping)
    narrow = batch._build_topdown_pool({"Gap":frame}, {"leading_sectors":[{"sector":"Technology", "industry":"Semiconductors", "confidence":.8}]}, "FinalScore", mapping)
    legacy = batch._build_topdown_pool({"Gap":frame}, {"leading_sectors":[{"sector":"Semiconductors", "confidence":.8}]}, "FinalScore", mapping)
assert {row[0] for row in broad} == {"NVDA","MSFT"}
assert [row[0] for row in narrow] == ["NVDA"]
assert [row[0] for row in legacy] == ["NVDA"]
assert narrow[0][2] == broad[0][2] == 1.24
'''
    result = subprocess.run([sys.executable, "-c", source], cwd=root, env={**os.environ,
                            "REPORT_MARKET_CONTEXT_ENABLED": "false"}, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr
