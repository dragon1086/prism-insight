import asyncio
import time
import argparse
import logging
from datetime import datetime

from cores.analysis import analyze_stock

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main(company_code: str, company_name: str, reference_date: str):
    try:
        # Execute analysis based on specific date with a 60 minute timeout
        logger.info(f"Starting analysis for {company_name} ({company_code}) on {reference_date}")
        result = await asyncio.wait_for(
            analyze_stock(company_code=company_code, company_name=company_name, reference_date=reference_date),
            timeout=3600
        )
        return result
    except asyncio.TimeoutError:
        logger.error("60-minute timeout reached: Gracefully terminating process")
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stock analysis.")
    parser.add_argument("--code", type=str, default="036570", help="Company Stock Code")
    parser.add_argument("--name", type=str, default="엔씨소프트", help="Company Name")
    parser.add_argument("--date", type=str, default=None, help="Reference Date (YYYYMMDD)")
    args = parser.parse_args()

    ref_date = args.date or datetime.now().strftime('%Y%m%d')
    start = time.time()

    result = asyncio.run(main(args.code, args.name, ref_date))

    if result:
        # Save results
        filename = f"{args.name}_분석보고서_{ref_date}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(result)

        end = time.time()
        logger.info(f"Report saved to {filename}")
        logger.info(f"Total execution time: {end - start:.2f} seconds")
        logger.info(f"Final report length: {len(result):,} characters")
    else:
        logger.error("Timeout or error occurred, no report generated")
