"""
Render QA helper — Phase 6 S2 (OFF by default, non-blocking).

Inspects a generated chart/report image for rendering defects using vision.
When vision is OFF (default) this module is a complete no-op.

Public API::

    verdict = await check_render(image_path)
    # Returns None when vision is off; RenderQAVerdict when on.

    verdict = await qa_and_log(image_path, context_label="005930")
    # Convenience wrapper: calls check_render and logs a warning on failure.
    # Never raises. Callers may ignore the return value.

Design constraints:
- analyze_image is imported at module level but is itself cheap to import
  (its heavy deps are lazy-loaded only when vision_available() is True).
- vision_available() is checked first; if False, returns None immediately.
- Never raises to caller. All errors return None silently.
- In shadow mode results are logged but never fed into trading decisions.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cores.llm.capabilities import vision_available
from cores.llm.features.vision import analyze_image

logger = logging.getLogger(__name__)

_QA_PROMPT = """\
You are a chart rendering quality-assurance inspector. Your ONLY job is to \
detect visual rendering defects in the image. Do NOT analyse trading content, \
price trends, or financial signals.

Check specifically for these rendering defects:
1. Korean (Hangul) characters rendered as empty boxes (□□□) or garbled tofu glyphs.
2. Overlapping axis labels, tick labels, or legend entries that collide or are cut off.
3. Missing or blank axis tick marks / labels (e.g. empty X or Y axis).
4. Clipped or truncated text (text cut off at the chart border).
5. Legend boxes that overlap with chart content in a way that obscures data.
6. Blank, all-white, or corrupt image (no chart content visible at all).

Return a strict JSON object with these exact fields:
- ok (bool): true if no significant rendering defect found, false otherwise.
- issues (array of strings): list of specific defects found; empty array if none.
- severity (string): "none" if ok=true, "minor" for cosmetic issues, "major" for \
serious defects (tofu text, blank image, missing axes).
- confidence (integer 0-100): your confidence in this assessment.
- notes (string): one-sentence summary or "" if nothing to add.
"""


class RenderQAVerdict(BaseModel):
    """Structured verdict from a render QA check.

    Designed to be strict-JSON-schema compatible (OpenAI json_schema strict mode):
    all fields required, no extra properties allowed.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    issues: list[str]
    severity: Literal["none", "minor", "major"]
    confidence: int
    notes: str


async def check_render(image_path: str) -> RenderQAVerdict | None:
    """Inspect *image_path* for rendering defects via vision.

    Returns:
        - ``None`` when vision is unavailable (off / no key) or on error.
        - :class:`RenderQAVerdict` instance on success.

    Never raises.
    """
    if not vision_available():
        return None

    try:
        result = await analyze_image(image_path, _QA_PROMPT, schema=RenderQAVerdict)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RENDER_QA] error during analyze_image: %s", exc)
        return None

    if result is None:
        return None

    if not isinstance(result, RenderQAVerdict):
        logger.warning(
            "[RENDER_QA] unexpected result type=%s path=%s", type(result).__name__, image_path
        )
        return None

    return result


async def qa_and_log(
    image_path: str,
    *,
    context_label: str = "",
) -> RenderQAVerdict | None:
    """Run render QA and log a warning when defects are found.

    This is a non-blocking convenience wrapper. Callers should fire-and-forget
    or optionally inspect the return value, but must NEVER let this function
    block or raise in the broadcast pipeline.

    Args:
        image_path:     Path to the PNG/JPG image to inspect.
        context_label:  Human-readable label for log context (e.g. stock code).

    Returns:
        :class:`RenderQAVerdict` or ``None`` (same as :func:`check_render`).
    """
    try:
        verdict = await check_render(image_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RENDER_QA] unexpected error label=%s: %s", context_label, exc)
        return None

    if verdict is not None and not verdict.ok:
        logger.warning(
            "[RENDER_QA] FAIL label=%s severity=%s issues=%s",
            context_label,
            verdict.severity,
            verdict.issues,
        )

    return verdict
