"""Lightweight CI must exercise the same SDK pair as the report runtime."""
from pathlib import Path


def test_lightweight_ci_sdk_pins_match_production_requirements():
    root = Path(__file__).resolve().parents[3]
    requirements = (root / 'requirements.txt').read_text().splitlines()
    workflow = (root / '.github/workflows/ci.yml').read_text()
    for package in ('openai', 'openai-agents', 'mcp'):
        pins = [line for line in requirements if line.startswith(package + '==')]
        assert len(pins) == 1
        assert '"' + pins[0] + '"' in workflow
