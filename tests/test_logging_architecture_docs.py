import hashlib
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "LOGGING_ARCHITECTURE_ko.md"
IMAGE = ROOT / "docs" / "images" / "logging-intelligence-architecture.png"
MANIFEST = (
    ROOT / "docs" / "images" / "logging-intelligence-architecture.manifest.json"
)
CATALOG_IMAGE = ROOT / "docs" / "images" / "logging-data-catalog.png"
CATALOG_MANIFEST = ROOT / "docs" / "images" / "logging-data-catalog.manifest.json"


def _assert_png_matches_manifest(image_path: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = image_path.read_bytes()

    assert manifest["asset"] == image_path.name
    assert hashlib.sha256(payload).hexdigest() == manifest["sha256"]
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    width, height = struct.unpack(">II", payload[16:24])
    assert [width, height] == manifest["dimensions"] == [1920, 1080]


def test_logging_architecture_assets_match_audited_manifests() -> None:
    _assert_png_matches_manifest(IMAGE, MANIFEST)
    _assert_png_matches_manifest(CATALOG_IMAGE, CATALOG_MANIFEST)


def test_logging_architecture_document_embeds_the_infographic_once() -> None:
    document = DOCUMENT.read_text(encoding="utf-8")

    assert document.count("images/logging-intelligence-architecture.png") == 1
    assert document.count("images/logging-data-catalog.png") == 1
    assert "현재 구현" in document
    assert "목표 구조" in document
    assert "AI Evidence Loop" in document
    assert "데이터를 아홉 묶음으로 펼친 것" in document


def test_logging_architecture_copy_contains_current_contract_and_caveats() -> None:
    document = DOCUMENT.read_text(encoding="utf-8")

    for required in (
        "candidate.evaluated",
        "entry.executed",
        "exit.executed",
        "candidate.outcome",
        "trade.outcome",
        "market.regime_snapshot",
        "trigger.performance_feedback",
        "deployment.applied",
        "127.0.0.1:14318",
        "180일",
        "관측 장애와 매매 장애를 분리",
        "상관관계를 인과관계라고 표현하지 않습니다",
        "실제 거래 KPI는 `trade.outcome`만 집계",
        "아직 현재 이벤트 계약에 포함되지 않습니다",
    ):
        assert required in document

    for prohibited in (
        "자동 강화학습으로",
        "수익을 보장",
        "ClickStack이 매매를 결정",
        "인과관계를 입증",
    ):
        assert prohibited not in document


def test_logging_architecture_manifest_sources_exist() -> None:
    for manifest_path in (MANIFEST, CATALOG_MANIFEST):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for source in manifest["current_implementation_sources"]:
            assert (ROOT / source).is_file(), source
