"""tests/ pytest configuration.

Several test modules import the KIS trading stack at module level, which reads
``trading/config/kis_devlp.yaml`` during import (see ``trading/kis_auth.py``).
When the file is absent — a fresh clone or any environment without real
credentials — those imports fail and abort the whole ``pytest tests/``
collection. ``tests/test_multi_account_domestic.py`` carries an identical
bootstrap for standalone runs; this conftest guarantees a demo config exists
before ANY test module is imported, regardless of collection order.

The file is only ever created when missing and removed again at process exit
when this conftest created it, so a real ``kis_devlp.yaml`` is never touched.
"""

import atexit
import textwrap
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "trading" / "config"
_CONFIG_FILE = _CONFIG_DIR / "kis_devlp.yaml"

if not _CONFIG_FILE.exists():
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(
        textwrap.dedent(
            """
            my_agent: test-agent
            default_mode: demo
            auto_trading: true
            default_product_code: "01"
            default_unit_amount: 100000
            default_unit_amount_usd: 250
            my_app: PSREALKEY
            my_sec: real-secret
            paper_app: PSVTTESTKEY
            paper_sec: paper-secret
            my_htsid: test-user
            prod: https://example.com
            vps: https://example.com
            ops: wss://example.com
            vops: wss://example.com
            accounts:
              - name: bootstrap-demo
                mode: demo
                account: "12345678"
                product: "01"
                market: all
                primary: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    atexit.register(lambda: _CONFIG_FILE.unlink(missing_ok=True))
