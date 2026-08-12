"""HTTP 계층 — 껍데기가 서비스에 제대로 연결됐는지 확인한다.

로직 검증은 test_service.py 가 한다. 여기서는 HTTP 로 오갈 때
상태 코드·인증·경계가 어긋나지 않는지만 본다.

FastAPI 가 없으면 통째로 건너뛴다 — 코어는 프레임워크에 의존하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI 미설치 — HTTP 계층 테스트 생략")
# TestClient 는 httpx 를 요구한다. 없으면 import 단계에서 수집 에러가 나므로 먼저 거른다.
pytest.importorskip("httpx", reason="httpx 미설치 — TestClient 를 쓸 수 없다")

from fastapi.testclient import TestClient  # noqa: E402

from stance.server import Ledger, Quote  # noqa: E402
from stance.server import api as api_module  # noqa: E402
from stance.server.service import StanceService  # noqa: E402


@pytest.fixture
def client():
    svc = StanceService(ledger=Ledger(),
                        quote_provider=lambda market, symbol: Quote(symbol, D(10000)))
    api_module.set_service(svc)
    with TestClient(api_module.app) as c:
        yield c
    api_module.set_service(None)  # type: ignore[arg-type]


@pytest.fixture
def registered(client):
    r = client.post("/strategies", json={
        "strategy": "s1", "display_name": "테스트 전략", "handle": "@me", "market": "KRX",
    })
    assert r.status_code == 201
    return r.json()["api_key"]


def auth(key):
    return {"Authorization": f"Bearer {key}"}


# ── 공개 엔드포인트 ───────────────────────────────────────────────────────

def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["protocol"] == "stance/1"
    assert body["registration"] in {"open", "closed"}


def test_markets_exposes_support_level_and_gaps(client):
    body = client.get("/markets").json()["markets"]
    assert body["KRX"]["support"] == "stable"
    assert body["CRYPTO"]["support"] == "experimental"
    # 실험적 지원이면 미해결 항목을 숨기지 않는다
    assert body["CRYPTO"]["notes"]


def test_leaderboard_is_preparing_when_empty(client):
    body = client.get("/leaderboard").json()
    assert body["status"] == "preparing"
    assert body["boards"]["KRX"]["entries"] == []


# ── 등록 ──────────────────────────────────────────────────────────────────

def test_register_returns_key_and_notice(client):
    body = client.post("/strategies", json={
        "strategy": "abc", "display_name": "A", "handle": "@x",
    }).json()
    assert body["api_key"].startswith("stk_")
    assert "다시 볼 수 없습니다" in body["notice"]


def test_registration_token_can_close_public_registration(client, monkeypatch):
    monkeypatch.setenv("STANCE_REGISTRATION_TOKEN", "invite-only")
    body = {"strategy": "locked", "display_name": "A", "handle": "@x"}
    assert client.post("/strategies", json=body).status_code == 401
    assert client.post(
        "/strategies", json=body,
        headers={"X-Stance-Registration-Token": "invite-only"},
    ).status_code == 201


def test_request_schema_rejects_unknown_fields(client, registered):
    response = client.post(
        "/stances", headers=auth(registered),
        json={"seq": 1, "symbol": "005930", "target_weight": 0.1, "market": "KRX"},
    )
    assert response.status_code == 422


def test_duplicate_registration_is_409(client, registered):
    r = client.post("/strategies", json={
        "strategy": "s1", "display_name": "중복", "handle": "@me",
    })
    assert r.status_code == 409


def test_unknown_cadence_is_422(client):
    r = client.post("/strategies", json={
        "strategy": "z", "display_name": "Z", "handle": "@z", "cadence": "hourly",
    })
    assert r.status_code == 422


def test_strategy_id_is_a_bounded_slug(client):
    response = client.post("/strategies", json={
        "strategy": "not a slug", "display_name": "Z", "handle": "@z",
    })
    assert response.status_code == 422


# ── 인증 ──────────────────────────────────────────────────────────────────

def test_submit_without_key_is_401(client):
    r = client.post("/stances", json={"protocol": "stance/1", "seq": 1,
                                      "symbol": "005930", "target_weight": 0.1})
    assert r.status_code == 401


def test_submit_with_wrong_key_is_401(client, registered):
    r = client.post("/stances", headers=auth("stk_nope"),
                    json={"protocol": "stance/1", "seq": 1,
                          "symbol": "005930", "target_weight": 0.1})
    assert r.status_code == 401


def test_cannot_write_to_another_strategy(client, registered):
    """참여자는 자기 전략에만 쓸 수 있다."""
    client.post("/strategies", json={
        "strategy": "other", "display_name": "다른 전략", "handle": "@you",
    })
    r = client.post("/stances", headers=auth(registered),
                    json={"protocol": "stance/1", "strategy": "other", "seq": 1,
                          "symbol": "005930", "target_weight": 0.1})
    assert r.status_code == 403


def test_wrong_protocol_version_is_400(client, registered):
    r = client.post("/stances", headers=auth(registered),
                    json={"protocol": "stance/99", "seq": 1,
                          "symbol": "005930", "target_weight": 0.1})
    assert r.status_code == 400


def test_rotate_key_returns_one_time_replacement(client, registered):
    response = client.post("/keys/rotate", headers=auth(registered))
    assert response.status_code == 200
    new_key = response.json()["api_key"]
    assert new_key.startswith("stk_")
    assert client.get("/portfolio", headers=auth(registered)).status_code == 401
    assert client.get("/portfolio", headers=auth(new_key)).status_code == 200


# ── 선언 ──────────────────────────────────────────────────────────────────

def test_submit_returns_verdict(client, registered):
    body = client.post("/stances", headers=auth(registered),
                       json={"protocol": "stance/1", "seq": 1, "symbol": "005930",
                             "target_weight": 0.3, "reason": "20일선 눌림목"}).json()
    assert body["admit"] == "accepted"
    assert body["fill_price"] == 10000
    assert body["received_at"]
    assert body["next_seq"] == 2
    assert body["replayed"] is False


def test_identical_http_retry_returns_original_verdict(client, registered):
    payload = {"seq": 1, "symbol": "005930", "target_weight": 0.3}
    first = client.post("/stances", headers=auth(registered), json=payload).json()
    second = client.post("/stances", headers=auth(registered), json=payload).json()
    assert second == {**first, "replayed": True}


def test_exit_is_target_zero(client, registered):
    client.post("/stances", headers=auth(registered),
                json={"protocol": "stance/1", "seq": 1, "symbol": "005930", "target_weight": 0.5})
    body = client.post("/stances", headers=auth(registered),
                       json={"protocol": "stance/1", "seq": 2,
                             "symbol": "005930", "target_weight": 0}).json()
    assert body["admit"] == "accepted"
    assert client.get("/portfolio", headers=auth(registered)).json()["positions"] == []


def test_hold_needs_no_symbol(client, registered):
    body = client.post("/stances", headers=auth(registered),
                       json={"protocol": "stance/1", "seq": 1, "kind": "hold"}).json()
    assert body["admit"] == "accepted"


def test_out_of_range_weight_is_400(client, registered):
    r = client.post("/stances", headers=auth(registered),
                    json={"protocol": "stance/1", "seq": 1,
                          "symbol": "005930", "target_weight": 1.5})
    assert r.status_code == 400


def test_non_positive_seq_is_422(client, registered):
    r = client.post("/stances", headers=auth(registered),
                    json={"seq": 0, "kind": "hold"})
    assert r.status_code == 422


def test_stale_seq_is_409(client, registered):
    client.post("/stances", headers=auth(registered),
                json={"protocol": "stance/1", "seq": 1, "symbol": "AAA", "target_weight": 0.2})
    r = client.post("/stances", headers=auth(registered),
                    json={"protocol": "stance/1", "seq": 1, "symbol": "AAA", "target_weight": 0.9})
    assert r.status_code == 409


# ── 조회 ──────────────────────────────────────────────────────────────────

def test_portfolio_requires_auth(client):
    assert client.get("/portfolio").status_code == 401


def test_portfolio_reports_last_seq_for_recovery(client, registered):
    for seq in (1, 2):
        client.post("/stances", headers=auth(registered),
                    json={"protocol": "stance/1", "seq": seq, "kind": "hold"})
    body = client.get("/portfolio", headers=auth(registered)).json()
    assert body["last_seq"] == 2
    assert body["total_assets"] == pytest.approx(1.0)


def test_leaderboard_lists_registered_strategy(client, registered):
    client.post("/stances", headers=auth(registered),
                json={"protocol": "stance/1", "seq": 1, "symbol": "005930", "target_weight": 0.3})
    body = client.get("/leaderboard").json()
    entries = body["boards"]["KRX"]["entries"]
    assert len(entries) == 1
    assert entries[0]["handle"] == "@me"
    # 기록이 짧으므로 예선이어야 하고, 투자비중은 항상 실려야 한다
    assert not entries[0]["qualified"]
    assert "avg_exposure" in entries[0]["metrics"]


def test_openapi_exposes_typed_core_responses(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/strategies"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("RegisterOut")
    assert schema["paths"]["/stances"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("AdmissionOut")
