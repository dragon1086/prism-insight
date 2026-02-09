import asyncio
import time
import threading
import os
import signal
from datetime import datetime

from cores.analysis import analyze_stock

if __name__ == "__main__":
    # Timer function to terminate process after 60 minutes
    def exit_after_timeout():
        time.sleep(3600)  # Wait 60 minutes
        print("60-minute timeout reached: Force terminating process")
        os.kill(os.getpid(), signal.SIGTERM)

    # Start timer as background thread
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    start = time.time()

    # Execute analysis based on specific date
    result = asyncio.run(analyze_stock(company_code="036570", company_name="엔씨소프트", reference_date="20260209"))

    # Save results
    with open(f"엔씨소프트_분석보고서_{datetime.now().strftime('%Y%m%d')}_gpt5_2.md", "w", encoding="utf-8") as f:
        f.write(result)

    end = time.time()
    print(f"Total execution time: {end - start:.2f} seconds")
    print(f"Final report length: {len(result):,} characters")
