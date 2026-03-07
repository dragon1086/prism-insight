import asyncio
import time
from datetime import datetime

from cores.analysis import analyze_stock

async def main():
    try:
        # Execute analysis based on specific date with a 60 minute timeout
        result = await asyncio.wait_for(
            analyze_stock(company_code="036570", company_name="엔씨소프트", reference_date="20260209"),
            timeout=3600
        )
        return result
    except asyncio.TimeoutError:
        print("60-minute timeout reached: Gracefully terminating process")
        return None

if __name__ == "__main__":
    start = time.time()

    result = asyncio.run(main())

    if result:
        # Save results
        with open(f"엔씨소프트_분석보고서_{datetime.now().strftime('%Y%m%d')}_gpt5_2.md", "w", encoding="utf-8") as f:
            f.write(result)

        end = time.time()
        print(f"Total execution time: {end - start:.2f} seconds")
        print(f"Final report length: {len(result):,} characters")
    else:
        print("Timeout or error occurred, no report generated")
