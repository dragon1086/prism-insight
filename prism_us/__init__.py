"""Import-compatible namespace for the historical ``prism-us`` source tree.

The on-disk directory predates Python package naming rules. Exposing it through
``prism_us`` makes fully qualified fallbacks deterministic without renaming the
operational scripts or relying on global ``sys.path`` order.
"""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent / "prism-us")]
