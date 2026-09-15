"""tests/ pytest configuration.

The demo ``trading/config/kis_devlp.yaml`` bootstrap that used to live here
moved to the repo-root ``conftest.py`` so it also covers ``prism-us/tests/``
and any other test directory: several test modules import the KIS trading
stack at module level, which reads that file during import (see
``trading/kis_auth.py``).
"""
