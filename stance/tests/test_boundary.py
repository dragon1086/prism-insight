"""경계 검사 — `stance/` 는 PRISM 을 몰라야 한다.

이 규칙을 지켜야 `git subtree split` 으로 이 디렉터리를 커밋 이력째 떼어내
별도 저장소로 만들 수 있다. 한 줄만 새어 들어와도 그 순간 불가능해진다.

실제로 marker.py 의 CLI 가 이 규칙을 어긴 적이 있어 테스트로 고정한다.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

STANCE = pathlib.Path(__file__).resolve().parent.parent
FORBIDDEN = {
    "prism_core", "prism", "cores", "trading", "tracking",
    "events", "kakao_bot", "messaging", "check_market_day",
}

SOURCES = sorted(p for p in STANCE.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_roots(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_there_are_sources_to_check():
    assert len(SOURCES) > 5


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_prism_imports(path):
    """함수 안의 지연 임포트도 잡는다 — ast 로 전체를 훑기 때문이다."""
    leaked = _imported_roots(path) & FORBIDDEN
    assert not leaked, (
        f"{path.relative_to(STANCE.parent)} 가 PRISM 모듈을 import 한다: {sorted(leaked)}. "
        "연동은 저장소 루트의 stance_server.py / stance_mark.py 가 담당한다."
    )


def test_core_needs_no_third_party():
    """엔진·원장·채점은 표준 라이브러리만으로 돌아야 한다.

    FastAPI 는 HTTP 껍데기(api.py)에만 허용된다.
    """
    allowed_third_party = {"fastapi", "pydantic", "requests", "starlette", "uvicorn"}
    core = ["models.py", "engine.py", "ledger.py", "scoring.py", "markets.py", "marker.py"]

    for name in core:
        roots = _imported_roots(STANCE / "server" / name)
        assert not (roots & allowed_third_party), f"{name} 가 외부 패키지를 쓴다: {roots}"
