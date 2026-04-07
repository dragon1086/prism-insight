"""
Compatibility shim so prism-us modules can import the shared OpenAI error helper
without binding the top-level ``cores`` package to the project root.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
_module_path = _project_root / "cores" / "openai_error_logging.py"
_spec = importlib.util.spec_from_file_location("prism_root_openai_error_logging", _module_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load shared OpenAI error helper from {_module_path}")

_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

extract_openai_error_details = _module.extract_openai_error_details
log_openai_error = _module.log_openai_error
