"""
Vision analysis helper — Phase 6 S1 (OFF by default, zero harm when off).

Public API::

    result = await analyze_image(path_or_bytes, "describe the chart")
    # returns None when vision is disabled/unavailable; never raises to caller.

Design constraints:
- Heavy deps (openai, base64) are imported INSIDE the function body, only when
  vision_available() is True. Importing this module is always cheap.
- Uses the Responses API (client.responses.create) consistent with
  openai_responses_llm.py.
- Forces API-key auth even if the global env uses the OAuth proxy, by building
  a dedicated AsyncOpenAI client from OPENAI_API_KEY directly.
- On any error: logs one structured line [VISION_ERROR] and returns None.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Union

from cores.llm.capabilities import vision_available, vision_model

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Type alias for caller convenience
ImageInput = Union[str, bytes, "os.PathLike[str]"]


async def analyze_image(
    image_path_or_bytes: ImageInput,
    prompt: str,
    *,
    schema: "type[BaseModel] | None" = None,
    model: str | None = None,
) -> Any | None:
    """Analyse an image with GPT-4o (or configured vision model).

    Args:
        image_path_or_bytes: File path (str/Path) or raw bytes of the image.
        prompt:              Text prompt describing the analysis task.
        schema:              Optional Pydantic model for structured output.
                             When provided, the response is parsed and returned
                             as an instance of that model.
        model:               Override the vision model (default: PRISM_VISION_MODEL
                             or gpt-4o).

    Returns:
        - ``None``          when vision is unavailable (off / no key) or on error.
        - ``str``           when schema is None and the call succeeds.
        - Pydantic instance when schema is provided and the call succeeds.

    Never raises. All errors are swallowed after a single [VISION_ERROR] log line.
    """
    # ------------------------------------------------------------------ #
    # Fast exit — no encoding, no imports, no client when off             #
    # ------------------------------------------------------------------ #
    if not vision_available():
        return None

    # ------------------------------------------------------------------ #
    # Lazy heavy imports (only reached when vision is on + key present)   #
    # ------------------------------------------------------------------ #
    try:
        import base64
        import pathlib

        from openai import APIError, AsyncOpenAI
    except ImportError as exc:
        logger.error("[VISION_ERROR] type=ImportError detail=%s", exc)
        return None

    # ------------------------------------------------------------------ #
    # Build dedicated API-key client (bypass any OAuth proxy)             #
    # ------------------------------------------------------------------ #
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    # vision_available() already guarantees key is present & non-placeholder

    resolved_model = model or vision_model()

    try:
        # ------------------------------------------------------------------ #
        # Encode image to base64 data URL                                     #
        # ------------------------------------------------------------------ #
        if isinstance(image_path_or_bytes, (str, pathlib.Path)):
            raw = pathlib.Path(image_path_or_bytes).read_bytes()
        else:
            raw = bytes(image_path_or_bytes)

        b64 = base64.b64encode(raw).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"

        # ------------------------------------------------------------------ #
        # Build input_items in Responses API format (mirrors                  #
        # openai_responses_llm.py's input_items list structure)               #
        # ------------------------------------------------------------------ #
        image_content: list[dict] = [
            {
                "type": "input_image",
                "image_url": data_url,
            },
            {
                "type": "input_text",
                "text": prompt,
            },
        ]
        input_items: list[dict] = [
            {
                "role": "user",
                "content": image_content,
            }
        ]

        call_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "input": input_items,
            "store": False,
        }

        # ------------------------------------------------------------------ #
        # Structured output via text.format json_schema (Responses API path) #
        # ------------------------------------------------------------------ #
        if schema is not None:
            call_kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                }
            }

        # ------------------------------------------------------------------ #
        # Execute — dedicated client, no shared state                         #
        # ------------------------------------------------------------------ #
        async with AsyncOpenAI(api_key=api_key) as client:
            response = await client.responses.create(**call_kwargs)  # type: ignore[attr-defined]

        # ------------------------------------------------------------------ #
        # Extract text from output items (same pattern as                     #
        # openai_responses_llm.py)                                            #
        # ------------------------------------------------------------------ #
        text_parts: list[str] = []
        for item in response.output:
            if item.type == "message":
                for part in item.content:
                    if hasattr(part, "text"):
                        text_parts.append(part.text)

        raw_text = "".join(text_parts)

        if schema is not None:
            import json
            return schema.model_validate(json.loads(raw_text))

        return raw_text

    except APIError as exc:
        request_id = getattr(exc, "request_id", None)
        status = getattr(exc, "status_code", None)
        logger.error(
            "[VISION_ERROR] type=%s request_id=%s status=%s model=%s detail=%s",
            type(exc).__name__,
            request_id,
            status,
            resolved_model,
            exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[VISION_ERROR] type=%s model=%s detail=%s",
            type(exc).__name__,
            resolved_model,
            exc,
        )
        return None
