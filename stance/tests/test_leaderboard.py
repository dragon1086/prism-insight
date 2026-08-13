"""리더보드 산출 — 원장에서 화면까지."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from stance.server import Kind, Ledger, Quote
from stance.server.leaderboard import SCHEMA, build, preparing, write_json

UTC = timezone.utc
T0 = datetime(2026, 1, 5, 0, 30, tzinfo=UTC)


def test_preparing_state_is_honest():
    """참여 전략이 0개여도 숨기지 않고 준비 중임을 밝힌다."""
    payload = preparing(["KRX"])
    assert payload["schema"] == SCHEMA
    assert payload["status"] == "preparing"
    board = payload["boards"]["KRX"]
    assert board["entries"] == []
    # 화면이 '이 보드의 규칙' 을 보여주려면 필요한 값들
    assert board["price_authority"] and board["mark_at"]
    assert board["min_track_periods"] > 0


def test_experimental_market_carries_its_notes():
    board = preparing(["CRYPTO"])["boards"]["CRYPTO"]
    assert board["support"] == "experimental"
    assert board["notes"], "실험적 지원이면 미해결 항목을 함께 내려보내야 한다"


def test_build_from_ledger():
    led = Ledger()
    led.register("s1", "PRISM KR", "@dragon1086", market="KRX")

    for seq, (sym, w, px) in enumerate(
        [("005930", "0.3", 70000), ("000660", "0.3", 200000), ("005930", "0", 77000)],
        start=1,
    ):
        sid = led.append_stance("s1", seq, Kind.SET, sym, D(w),
                                received_at=(T0 + timedelta(days=seq)).isoformat())
        led.append_quote(sid, Quote(sym, D(px)))

    payload = build(led, [("s1", "PRISM KR", "@dragon1086", "KRX")])
    entries = payload["boards"]["KRX"]["entries"]

    assert len(entries) == 1
    e = entries[0]
    assert e["strategy"] == "s1"
    assert e["handle"] == "@dragon1086"
    # 기록이 짧으므로 예선이어야 한다
    assert not e["qualified"]
    assert e["gate_failures"]
    # 투자비중은 항상 실려야 한다 — 다른 지표를 해석하는 기준이다
    assert "avg_exposure" in e["metrics"]
    assert e["latest_decision"] == {
        "seq": 3,
        "kind": "set",
        "symbol": "005930",
        "target_weight": 0.0,
        "received_at": (T0 + timedelta(days=3)).isoformat(),
        "admit": "accepted",
    }
    led.close()


def test_latest_hold_is_visible_without_a_symbol():
    led = Ledger()
    led.register("holding", "Holding", "@me", market="NASDAQ")
    led.append_stance(
        "holding", 1, Kind.HOLD, received_at=T0.isoformat(), reason="no signal",
    )

    entry = build(
        led, [("holding", "Holding", "@me", "NASDAQ")]
    )["boards"]["NASDAQ"]["entries"][0]

    assert entry["latest_decision"]["kind"] == "hold"
    assert entry["latest_decision"]["symbol"] is None
    assert entry["latest_decision"]["received_at"] == T0.isoformat()
    led.close()


def test_profile_metadata_is_public_but_does_not_change_metrics():
    led = Ledger()
    led.register(
        "profiled", "Profiled", "@owner", market="KRX",
        owner_name="Owner", tagline="One line", description="Longer introduction",
        website_url="https://example.com", source_url="https://github.com/example/repo",
    )

    payload = build(led, [(
        "profiled", "Profiled", "@owner", "KRX", "Owner", "One line",
        "Longer introduction", "https://example.com", "https://github.com/example/repo",
    )])
    entry = payload["boards"]["KRX"]["entries"][0]

    assert entry["owner_name"] == "Owner"
    assert entry["website_url"] == "https://example.com"
    assert entry["source_url"].startswith("https://github.com/")
    assert entry["metrics"]["cumulative_return"] == 0
    led.close()


def test_boards_are_not_mixed_across_markets():
    """시장을 섞으면 벤치마크와 변동성 스케일이 달라 비교가 무의미해진다."""
    led = Ledger()
    led.register("kr", "KR 전략", "@me", market="KRX")
    led.register("us", "US 전략", "@me", market="NASDAQ")

    payload = build(led, [("kr", "KR 전략", "@me", "KRX"),
                          ("us", "US 전략", "@me", "NASDAQ")])
    assert set(payload["boards"]) == {"KRX", "NASDAQ"}
    assert len(payload["boards"]["KRX"]["entries"]) == 1
    assert len(payload["boards"]["NASDAQ"]["entries"]) == 1
    led.close()


def test_write_json_is_readable_by_dashboard(tmp_path):
    out = write_json(preparing(["KRX"]), tmp_path / "stance_leaderboard.json")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    # 대시보드 타입(StanceLeaderboard)이 기대하는 최상위 키
    for key in ("schema", "protocol", "score_profile", "generated_at", "status", "boards"):
        assert key in loaded
