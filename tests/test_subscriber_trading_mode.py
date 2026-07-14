"""Safety contract for selecting the subscriber's brokerage environment."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "gcp_subscriber_trading_mode_test",
    REPO_ROOT / "examples" / "messaging" / "gcp_pubsub_subscriber_example.py",
)
SUBSCRIBER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBSCRIBER)


class SubscriberTradingModeTests(unittest.TestCase):
    def _mode_for_config(self, content=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "trading" / "config" / "kis_devlp.yaml"
            if content is not None:
                config.parent.mkdir(parents=True)
                config.write_text(content, encoding="utf-8")
            with patch.object(SUBSCRIBER, "PROJECT_ROOT", root):
                return SUBSCRIBER.get_trading_mode()

    def test_missing_config_defaults_to_demo(self):
        self.assertEqual(self._mode_for_config(), "demo")

    def test_malformed_config_defaults_to_demo(self):
        self.assertEqual(self._mode_for_config("default_mode: ["), "demo")

    def test_unknown_mode_defaults_to_demo(self):
        self.assertEqual(self._mode_for_config("default_mode: live\n"), "demo")

    def test_explicit_real_mode_is_preserved(self):
        self.assertEqual(self._mode_for_config("default_mode: REAL\n"), "real")


if __name__ == "__main__":
    unittest.main()
