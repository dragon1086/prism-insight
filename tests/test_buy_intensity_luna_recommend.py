from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from tools import buy_intensity_luna_recommend as mod
from tools.buy_intensity_luna_recommend import (
    parse_recommendation,
    persist_last,
    quiet_skip_reason,
    read_env_flags,
    run_once,
)


def test_parse_json_stance_and_korean_reason():
    raw = """```json
    {"stance": "tighten",
     "suggested_env": {"REGIME_MIN_SCORE_FLOOR": "true"},
     "reason_ko": "약세 신뢰도가 높습니다.\\n신규 매수를 줄이는 편이 안전합니다."}
    ```"""
    rec = parse_recommendation(raw)
    assert rec["stance"] == "tighten"
    assert rec["suggested_env"]["REGIME_MIN_SCORE_FLOOR"] == "true"
    assert "약세" in rec["reason_ko"]


@pytest.mark.parametrize(
    "text,stance",
    [
        ("오늘은 조임이 필요합니다", "tighten"),
        ("스탠스는 유지입니다", "hold"),
        ("완화를 권합니다", "loosen"),
    ],
)
def test_parse_korean_stance_fallback(text, stance):
    assert parse_recommendation(text)["stance"] == stance


def test_parse_rejects_unknown_stance():
    with pytest.raises(ValueError):
        parse_recommendation("no structured stance here")


def test_quiet_skip_holiday_and_missing_afternoon():
    rec = {"stance": "loosen", "suggested_env": {}}
    assert (
        quiet_skip_reason(
            is_market_day=False, has_afternoon_data=True, last={}, rec=rec
        )
        == "holiday_or_weekend"
    )
    assert (
        quiet_skip_reason(
            is_market_day=True, has_afternoon_data=False, last={}, rec=rec
        )
        == "no_afternoon_data"
    )


def test_quiet_skip_unchanged_hold_only():
    last = {"stance": "hold", "suggested_env": {}}
    hold = {"stance": "hold", "suggested_env": {}}
    tighten = {"stance": "tighten", "suggested_env": {}}
    assert (
        quiet_skip_reason(
            is_market_day=True, has_afternoon_data=True, last=last, rec=hold
        )
        == "unchanged_hold"
    )
    assert (
        quiet_skip_reason(
            is_market_day=True, has_afternoon_data=True, last=last, rec=tighten
        )
        is None
    )
    assert (
        quiet_skip_reason(
            is_market_day=True, has_afternoon_data=True, last={}, rec=hold
        )
        is None
    )


def test_run_once_does_not_mutate_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    original = "REGIME_MIN_SCORE_FLOOR=true\nREGIME_WEAK_NO_TOPDOWN=false\n"
    env_file.write_text(original, encoding="utf-8")
    root = tmp_path / "root"
    (root / "reports").mkdir(parents=True)
    (root / "logs").mkdir()
    stamp = date.today().strftime("%Y%m%d")
    (root / "reports" / f"005930_삼성_{stamp}_afternoon_gpt-5.6-luna.md").write_text(
        "ok", encoding="utf-8"
    )
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    monkeypatch.setattr(mod, "_pulse_state", lambda: None)
    monkeypatch.setattr(mod, "_demo_portfolio", lambda _p: {"available": False})
    last_path = root / "logs" / "buy_intensity_luna_last.json"

    async def fake_luna(_inputs):
        return json.dumps(
            {
                "stance": "loosen",
                "suggested_env": {"REGIME_MIN_SCORE_FLOOR": "false"},
                "reason_ko": "반등 여력이 있습니다.",
            }
        )

    result = asyncio.run(
        run_once(
            dry_run=True,
            no_telegram=True,
            root=root,
            last_path=last_path,
            luna_fn=fake_luna,
            market_day_fn=lambda: True,
        )
    )
    assert result["status"] == "ok"
    assert env_file.read_text(encoding="utf-8") == original
    assert result["recommendation"]["suggested_env"]["REGIME_MIN_SCORE_FLOOR"] == "false"
    saved = json.loads(last_path.read_text(encoding="utf-8"))
    assert saved["env_written"] is False
    assert saved["stance"] == "loosen"


def test_holiday_is_quiet_without_llm(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "reports").mkdir(parents=True)
    (root / "logs").mkdir()
    monkeypatch.setattr(mod, "_pulse_state", lambda: None)
    monkeypatch.setattr(mod, "_demo_portfolio", lambda _p: {"available": False})
    called = {"n": 0}

    async def fake_luna(_inputs):
        called["n"] += 1
        return '{"stance":"tighten","reason_ko":"x"}'

    result = asyncio.run(
        run_once(
            dry_run=True,
            no_telegram=True,
            root=root,
            last_path=root / "last.json",
            luna_fn=fake_luna,
            market_day_fn=lambda: False,
        )
    )
    assert result == {"status": "quiet", "reason": "holiday_or_weekend", "telegram": False}
    assert called["n"] == 0


def test_persist_last_never_touches_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=1\n", encoding="utf-8")
    persist_last({"stance": "hold", "env_written": False}, tmp_path / "last.json")
    assert env_file.read_text(encoding="utf-8") == "KEEP=1\n"


def test_read_env_flags_are_snapshot_only(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    monkeypatch.delenv("REGIME_WEAK_NO_TOPDOWN", raising=False)
    monkeypatch.setenv("REGIME_HIVOL_OVERRIDE", "active")
    flags = read_env_flags()
    assert flags["REGIME_MIN_SCORE_FLOOR"] == "true"
    assert flags["REGIME_WEAK_NO_TOPDOWN"] == "unset"
    assert flags["REGIME_HIVOL_OVERRIDE"] == "active"
