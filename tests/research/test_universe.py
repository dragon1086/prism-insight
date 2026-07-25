from datetime import datetime, timezone
from uuid import UUID

from prism_core.data.contracts import SecurityId
from prism_core.research.backtest import UniverseEvidenceKind, UniverseSnapshot


def test_universe_can_become_known_after_its_effective_as_of_boundary() -> None:
    universe = UniverseSnapshot(
        snapshot_id=UUID(int=10),
        as_of=datetime(2026, 1, 1, 16, tzinfo=timezone.utc),
        available_at=datetime(2026, 1, 2, 16, tzinfo=timezone.utc),
        members=(SecurityId(value=UUID(int=1)),),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )

    assert universe.available_at > universe.as_of
