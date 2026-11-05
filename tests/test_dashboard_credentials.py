"""Dashboard credential transport stays optional for standalone deployments."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from logger import load_runtime_credentials, ConfigurationError
from telegram_relay import TelegramRelay, TelegramConfigurationError


class MountedCredentialTests(unittest.TestCase):
    def test_runtime_file_supplies_only_the_two_allowed_secret_fields(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            path = Path(temporary) / "secrets.json"
            path.write_text(json.dumps({"FB_SESSION_JSON": '{"synthetic":"value"}', "TELEGRAM_ACCOUNT_KEY": "synthetic-worker-key"}))
            os.environ["LOGGER_CREDENTIALS_FILE"] = str(path)
            load_runtime_credentials()
            self.assertEqual(os.environ["TELEGRAM_ACCOUNT_KEY"], "synthetic-worker-key")
            path.write_text(json.dumps({"FB_SESSION_JSON": "PRIVATE", "TELEGRAM_ACCOUNT_KEY": "PRIVATE", "PATH": "/bad"}))
            with self.assertRaises(ConfigurationError) as error:
                load_runtime_credentials()
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_runtime_credentials_reject_links_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            target = root / "target"
            target.write_text("x" * 150001)
            linked = root / "linked"
            linked.symlink_to(target)
            for path in (target, linked, root):
                os.environ["LOGGER_CREDENTIALS_FILE"] = str(path)
                with self.assertRaises(ConfigurationError):
                    load_runtime_credentials()

    def test_gateway_uses_account_key_without_requiring_the_master_bot_token(self):
        key = "a" * 32 + "." + "b" * 64
        env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_RELAY_URL": "http://127.0.0.1:8788", "TELEGRAM_ACCOUNT_KEY": key,
               "TELEGRAM_BOT_ID": "123456", "TELEGRAM_CHAT_ID": "87654321", "TELEGRAM_TOPICS": "true"}
        with patch.dict(os.environ, env, clear=True):
            relay = TelegramRelay(lambda _: None, None, start=False)
            self.assertEqual(relay.token, "")
            self.assertEqual(relay.topics.bot_id, "123456")
            for url in ("https://example.com", "http://192.168.1.2:8788", "http://user:password@127.0.0.1:8788"):
                with patch.dict(os.environ, {"TELEGRAM_RELAY_URL": url}), self.assertRaises(TelegramConfigurationError):
                    TelegramRelay(lambda _: None, None, start=False)


if __name__ == "__main__":
    unittest.main()
