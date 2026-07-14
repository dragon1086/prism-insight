"""Cross-platform safety tests for OAuth credential persistence."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cores.chatgpt_proxy import token_manager


class TokenStorageTests(unittest.TestCase):
    def test_save_replaces_existing_file_and_leaves_no_temp_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_dir = Path(directory) / "auth"
            auth_file = auth_dir / "chatgpt_auth.json"
            with (
                patch.object(token_manager, "AUTH_DIR", auth_dir),
                patch.object(token_manager, "AUTH_FILE", auth_file),
            ):
                token_manager.save_auth_data({"access_token": "old"})
                token_manager.save_auth_data({"access_token": "new"})

            self.assertEqual(
                json.loads(auth_file.read_text(encoding="utf-8")),
                {"access_token": "new"},
            )
            self.assertEqual(list(auth_dir.glob("*.tmp")), [])
            if os.name != "nt":
                self.assertEqual(auth_file.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
