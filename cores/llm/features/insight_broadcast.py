"""
Insight-image BROADCAST wiring — Phase 6 S6 (DEFAULT-OFF, NON-BLOCKING).

Subscriber-facing publishing helper that, AFTER each company's analysis-report
PDF has been broadcast, sends the company's annotated insight image as a
SEPARATE consecutive Telegram message with a short Korean caption.

Two INDEPENDENT gates must BOTH be true before anything is sent:
  - ``insight_image_enabled()``  — PRISM_FEATURE_INSIGHT_IMAGE=on (broadcast gate)
  - ``vision_available()``       — vision pipeline enabled + real API key

This is deliberately separate from the vision SHADOW flag: deploying this code
sends NOTHING to subscribers until PRISM_FEATURE_INSIGHT_IMAGE is set to "on".

Design constraints (mirror the renderer):
- Never raises into the analysis batch. All work is wrapped in try/except.
- Skips silently when a gate is off or the image is None.
- Failures are logged with the ``[INSIGHT_IMAGE]`` marker; the batch continues.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _parse_ticker_company(pdf_stem: str) -> tuple[str, str | None]:
    """Best-effort ticker/company extraction from a report PDF filename stem.

    Expected stem format: ``{ticker}_{company}_{date}_{mode}_gpt5.4-mini``.
    Returns (ticker, company_name|None). Never raises.
    """
    try:
        parts = pdf_stem.split("_")
        ticker = parts[0] if parts else pdf_stem
        company = parts[1] if len(parts) > 1 else None
        return ticker, company
    except Exception:  # noqa: BLE001
        return pdf_stem, None


def _build_caption(ticker: str, company_name: str | None) -> str:
    """Compose a concise Korean supplementary caption.

    Kept generic on purpose: the detailed base type / quality / pivot summary is
    already drawn ON the annotated image (and burned into its own caption band by
    the renderer), so we avoid a SECOND vision call here just to repeat it. Never
    raises.
    """
    name = company_name or ticker
    return (
        f"📊 *{name}* ({ticker}) O'Neil 인사이트\n"
        "차트의 베이스/피벗/RS 주석을 함께 참고하세요.\n"
        "※ 보조 참고용 · 투자 권유 아님"
    )


async def broadcast_insight_images(bot_agent, chat_id, pdf_paths, *, market=None):
    """Send an insight image per company AFTER their PDFs were broadcast.

    NON-BLOCKING contract: this never raises and never blocks the batch. When
    either gate is off it returns immediately having done no work.

    Args:
        bot_agent: A TelegramBotAgent (must expose ``send_photo_bytes``).
        chat_id:   Target Telegram channel id.
        pdf_paths: Iterable of report PDF paths (one per company).
        market:    Optional market hint. For KR pass None (auto KOSPI/KOSDAQ)
                   or 'KOSPI'/'KOSDAQ'; pass 'us' for US (image build no-ops
                   gracefully where a KR chart path is unavailable).
    """
    try:
        from cores.llm.capabilities import insight_image_enabled, vision_available

        if not (insight_image_enabled() and vision_available()):
            return  # default-OFF: nothing user-facing happens

        from cores.llm.features.insight_image import build_insight_image_for

        # Normalise the market hint: the KR RS index resolver only understands
        # 'KOSPI'/'KOSDAQ'; anything else (incl. 'KR'/'us') falls back to
        # per-ticker auto-detection or a graceful None image.
        norm_market = market
        if isinstance(market, str) and market.strip().upper() in ("KR", "KOR", "KOREA"):
            norm_market = None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] broadcast setup skipped: %s", exc)
        return

    import asyncio

    for pdf_path in pdf_paths or []:
        try:
            stem = getattr(pdf_path, "stem", None)
            if stem is None:
                from pathlib import Path
                stem = Path(str(pdf_path)).stem
            ticker, company = _parse_ticker_company(stem)

            image = await build_insight_image_for(
                ticker, company_name=company, market=norm_market
            )
            if not image:
                logger.info("[INSIGHT_IMAGE] no image for %s; skipping", ticker)
                continue

            caption = _build_caption(ticker, company)
            ok = await bot_agent.send_photo_bytes(
                chat_id, image, caption=caption, market=market
            )
            if ok:
                logger.info("[INSIGHT_IMAGE] sent for %s", ticker)
            else:
                logger.warning("[INSIGHT_IMAGE] send failed for %s", ticker)
            await asyncio.sleep(1)
        except Exception as exc:  # noqa: BLE001 — never break the batch
            logger.warning("[INSIGHT_IMAGE] broadcast error for %s: %s", pdf_path, exc)
            continue
