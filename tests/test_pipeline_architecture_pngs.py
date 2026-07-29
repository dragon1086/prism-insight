from pathlib import Path

import pytest


EXPECTED_FILENAMES = {
    "full-pipeline-overview.png",
    "market-pulse-batch-control-overview.png",
    "distribution-day-state-transitions.png",
    "screening-six-triggers-overview.png",
    "candidate-screening-reranking-overview.png",
    "trading-regime-entry-overview.png",
    "screening-analysis-deep-dive.png",
    "can-slim-company-supply-checks.png",
    "can-slim-leadership-market-checks.png",
    "entry-gates-overview.png",
    "pyramiding-portfolio-overview.png",
    "trading-exit-overview.png",
    "position-protection-loops.png",
    "feedback-reentry-overview.png",
}


def test_build_diagrams_exposes_the_approved_fourteen_png_contract():
    from tools.generate_pipeline_architecture_pngs import build_diagrams

    diagrams = build_diagrams()

    assert len(diagrams) == 14
    assert {diagram.filename for diagram in diagrams} == EXPECTED_FILENAMES
    assert all(diagram.filename.endswith(".png") for diagram in diagrams)
    assert all(diagram.title.strip() for diagram in diagrams)
    assert all(diagram.stage.strip() for diagram in diagrams)
    assert all(diagram.sources for diagram in diagrams)
    assert all(3 <= len(diagram.cards) <= 6 for diagram in diagrams)


@pytest.mark.parametrize("width,height", [(1920, 1080)])
def test_render_all_creates_full_hd_png_files(tmp_path: Path, width: int, height: int):
    from PIL import Image
    from tools.generate_pipeline_architecture_pngs import render_all

    paths = render_all(tmp_path)

    assert {path.name for path in paths} == EXPECTED_FILENAMES
    for path in paths:
        assert path.stat().st_size > 25_000
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.size == (width, height)
            assert image.mode in {"RGB", "RGBA"}


def test_committed_infographics_are_rich_full_hd_png_assets():
    from PIL import Image

    asset_dir = (
        Path(__file__).resolve().parents[1] / "docs" / "images" / "architecture"
    )
    for filename in EXPECTED_FILENAMES:
        path = asset_dir / filename
        assert path.stat().st_size > 1_000_000
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.size == (1920, 1080)
            assert image.mode in {"RGB", "RGBA"}


def _diagram_text(diagram) -> str:
    card_text = [
        piece
        for card in diagram.cards
        for piece in (card.title, *card.lines, card.note)
    ]
    return " ".join(
        [
            diagram.title,
            diagram.subtitle,
            diagram.glossary,
            *card_text,
        ]
    )


def test_diagram_copy_contains_the_required_investor_facing_explanations():
    from tools.generate_pipeline_architecture_pngs import build_diagrams

    text = "\n".join(_diagram_text(diagram) for diagram in build_diagrams())

    for required in (
        "윌리엄 오닐의 M",
        "분산일",
        "기관성 매도 압력이 의심",
        "거래량 급증",
        "갭 상승 후 강세",
        "자금 집중",
        "하루 상승률 상위",
        "장 마감 무렵 강세",
        "거래량 늘어난 횡보",
        "CAN SLIM",
        "최근 분기",
        "여러 해의 성장",
        "새로운 계기",
        "주식 수급",
        "주도주",
        "기관",
        "시장 방향",
        "수익률 5% 이상",
        "강한 상승장·과열 가속 상승",
        "매수하지 않음",
        "긴급 손절",
        "추세 이탈 매도",
        "미체결 주문 관리",
        "자율 강화학습이 아님",
    ):
        assert required in text


def test_diagram_copy_omits_known_unsupported_or_overstated_claims():
    from tools.generate_pipeline_architecture_pngs import build_diagrams

    text = "\n".join(_diagram_text(diagram) for diagram in build_diagrams())

    for prohibited in (
        "헤지 실행",
        "포지션 전환",
        "신뢰도 태그",
        "시장 폭 악화",
        "항상 가동",
        "현재 수익률이 양수",
        "강한·완만한 상승장만 허용",
    ):
        assert prohibited not in text


def test_korean_architecture_document_embeds_every_png_once_in_story_order():
    document = (
        Path(__file__).resolve().parents[1] / "docs" / "PIPELINE_ARCHITECTURE_ko.md"
    ).read_text(encoding="utf-8")

    for heading in (
        "## 1단계. 종목 스크리닝",
        "## 2단계. 종목 분석",
        "## 3단계. 매매",
        "## 4단계. 피드백",
    ):
        assert heading in document

    positions = []
    for filename in EXPECTED_FILENAMES:
        image_ref = f"images/architecture/{filename}"
        assert document.count(image_ref) == 1
        positions.append(document.index(image_ref))

    expected_order = [
        "full-pipeline-overview.png",
        "market-pulse-batch-control-overview.png",
        "distribution-day-state-transitions.png",
        "screening-six-triggers-overview.png",
        "candidate-screening-reranking-overview.png",
        "trading-regime-entry-overview.png",
        "screening-analysis-deep-dive.png",
        "can-slim-company-supply-checks.png",
        "can-slim-leadership-market-checks.png",
        "entry-gates-overview.png",
        "pyramiding-portfolio-overview.png",
        "trading-exit-overview.png",
        "position-protection-loops.png",
        "feedback-reentry-overview.png",
    ]
    assert [
        document.index(f"images/architecture/{filename}") for filename in expected_order
    ] == sorted(positions)
    assert "관심/보류" not in document
