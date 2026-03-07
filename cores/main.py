import asyncio
import signal
import time
import argparse
import logging
import os
from datetime import datetime

from cores.analysis import analyze_stock

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Language-aware filename suffixes
_FILENAME_LABELS = {
    "ko": "분석보고서",
    "en": "Analysis_Report",
}


async def main(company_code: str, company_name: str, reference_date: str,
               language: str = "ko"):
    try:
        # Execute analysis based on specific date with a 60 minute timeout
        logger.info(f"Starting analysis for {company_name} ({company_code}) on {reference_date}")
        result = await asyncio.wait_for(
            analyze_stock(company_code=company_code, company_name=company_name,
                          reference_date=reference_date, language=language),
            timeout=3600
        )
        return result
    except asyncio.TimeoutError:
        logger.error("60-minute timeout reached: Gracefully terminating process")
        return None


# --- Graceful shutdown ---
_shutdown_requested = False


def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global _shutdown_requested
    if _shutdown_requested:
        logger.warning("Force shutdown requested. Exiting immediately.")
        raise SystemExit(1)
    _shutdown_requested = True
    logger.warning("Shutdown requested. Saving partial results and exiting...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stock analysis.")
    parser.add_argument("--code", type=str, default="036570", help="Company Stock Code")
    parser.add_argument("--name", type=str, default="엔씨소프트", help="Company Name")
    parser.add_argument("--date", type=str, default=None, help="Reference Date (YYYYMMDD)")
    parser.add_argument("--language", type=str, default="ko", choices=["ko", "en"],
                        help="Report language (default: ko)")
    parser.add_argument("--output-format", type=str, default="md", choices=["md", "html", "json"],
                        help="Output format (default: md)")
    parser.add_argument("--output-dir", type=str, default=".",
                        help="Output directory (default: current directory)")
    args = parser.parse_args()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (OSError, AttributeError):
        pass  # SIGTERM not available on Windows

    ref_date = args.date or datetime.now().strftime('%Y%m%d')
    start = time.time()

    result = asyncio.run(main(args.code, args.name, ref_date, language=args.language))

    if result:
        # Language-aware filename
        label = _FILENAME_LABELS.get(args.language, "Analysis_Report")
        filename = f"{args.name}_{label}_{ref_date}.{args.output_format}"
        filepath = os.path.join(args.output_dir, filename)

        # Ensure output directory exists
        os.makedirs(args.output_dir, exist_ok=True)

        if args.output_format == "md":
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(result)
        elif args.output_format == "html":
            try:
                from cores.export_formats import export_to_html
                html_content = export_to_html(result)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(html_content)
            except ImportError:
                logger.warning("HTML export not available, falling back to markdown")
                filepath = filepath.replace(".html", ".md")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(result)
        elif args.output_format == "json":
            try:
                import json
                from cores.export_formats import export_to_json_summary
                json_data = export_to_json_summary(result, {
                    "company_code": args.code,
                    "company_name": args.name,
                    "reference_date": ref_date,
                    "language": args.language,
                })
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(json_data, f, ensure_ascii=False, indent=2)
            except ImportError:
                logger.warning("JSON export not available, falling back to markdown")
                filepath = filepath.replace(".json", ".md")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(result)

        end = time.time()
        logger.info(f"Report saved to {filepath}")
        logger.info(f"Total execution time: {end - start:.2f} seconds")
        logger.info(f"Final report length: {len(result):,} characters")
    else:
        logger.error("Timeout or error occurred, no report generated")
