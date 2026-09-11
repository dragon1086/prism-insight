#!/usr/bin/env python3
"""
Stock information update script

Run periodically to update stock information (codes, names) daily
"""
import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from cores.kis_market_snapshot import fetch_kis_master_universe
from prism_core.runtime_paths import resolve_stock_map_write_path

load_dotenv()  # Load environment variables from .env file

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("stock_data_update.log")
    ]
)
logger = logging.getLogger(__name__)

def update_stock_data(output_file: str | Path | None = None):
    """
    Update stock information

    Args:
        output_file: Explicit file path to save. When omitted, use the
            configured runtime path instead of the tracked seed file.

    Returns:
        bool: Success status
    """
    try:
        # Today's date
        today = datetime.now().strftime("%Y%m%d")
        logger.info(f"Starting stock data update: {today}")

        # Fetch all stock code-name mappings at once (efficient!)
        logger.info("Fetching all stock information...")
        code_to_name = fetch_kis_master_universe()
        if not code_to_name or any(not name or name == ticker for ticker, name in code_to_name.items()):
            raise ValueError("KIS stock master missing names; existing map preserved")
        logger.info(f"Loaded {len(code_to_name)} stocks")

        # Create reverse mapping
        name_to_code = {name: code for code, name in code_to_name.items()}

        # Save data
        data = {
            "code_to_name": code_to_name,
            "name_to_code": name_to_code,
            "updated_at": datetime.now().isoformat()
        }

        output_path = resolve_stock_map_write_path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Stock data update complete: {len(code_to_name)} stocks, file: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Stock data update failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def main():
    parser = argparse.ArgumentParser(description="Update stock information")
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "File path to save (default: PRISM_STOCK_MAP_PATH, "
            "KAKAO_STOCK_MAP_PATH, or runtime/stock_map.json)"
        ),
    )

    args = parser.parse_args()
    update_stock_data(args.output)

if __name__ == "__main__":
    main()
