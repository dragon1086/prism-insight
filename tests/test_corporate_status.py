"""cores.corporate_status (이벤트 강제청산 TIER0) 단위테스트.

순수 모듈이라 root pytest 세션에서 실행 가능(KR/US cores shadowing 무관).
실행: .venv/bin/python -m pytest tests/test_corporate_status.py -q
"""
import json

from cores import corporate_status as cs


def _write_overrides(tmp_path, tickers: dict):
    f = tmp_path / "event_force_exit.json"
    f.write_text(json.dumps({"tickers": tickers}, ensure_ascii=False), encoding="utf-8")
    return str(f)


def test_override_force_exit(tmp_path, monkeypatch):
    path = _write_overrides(tmp_path, {"012510": {"reason": "자진상폐", "market": "KR"}})
    monkeypatch.setenv("EVENT_FORCE_EXIT_FILE", path)
    ok, reason = cs.check_event_exit("012510", market="KR")
    assert ok is True
    assert "OVERRIDE" in reason and "자진상폐" in reason


def test_override_market_mismatch_skips(tmp_path, monkeypatch):
    # market 지정이 다르면 적용 안 됨(오등록 방지)
    path = _write_overrides(tmp_path, {"012510": {"reason": "x", "market": "US"}})
    monkeypatch.setenv("EVENT_FORCE_EXIT_FILE", path)
    ok, _ = cs.check_event_exit("012510", market="KR")
    assert ok is False


def test_not_listed(tmp_path, monkeypatch):
    path = _write_overrides(tmp_path, {})
    monkeypatch.setenv("EVENT_FORCE_EXIT_FILE", path)
    ok, _ = cs.check_event_exit("000660", market="KR")
    assert ok is False


def test_kis_status_codes():
    assert cs.classify_kis_status("58")[0] is True   # 거래정지
    assert cs.classify_kis_status("51")[0] is True   # 관리종목
    assert cs.classify_kis_status("52")[0] is True   # 투자위험
    assert cs.classify_kis_status("53")[0] is False  # 투자경고(청산 X)
    assert cs.classify_kis_status("00")[0] is False  # 정상
    assert cs.classify_kis_status("")[0] is False
    assert cs.classify_kis_status(None)[0] is False


def test_kis_status_injection_triggers(tmp_path, monkeypatch):
    path = _write_overrides(tmp_path, {})
    monkeypatch.setenv("EVENT_FORCE_EXIT_FILE", path)
    ok, reason = cs.check_event_exit("000660", kis_status_code="58", market="KR")
    assert ok is True and "KIS_STATUS" in reason


def test_missing_file_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_FORCE_EXIT_FILE", str(tmp_path / "nope.json"))
    ok, _ = cs.check_event_exit("012510", market="KR")
    assert ok is False


def test_empty_ticker():
    ok, _ = cs.check_event_exit("", market="KR")
    assert ok is False
