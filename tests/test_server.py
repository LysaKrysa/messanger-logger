from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge_rpc import Bridge, BridgeFailure


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.signals = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def tearDown(self):
        for sig, handler in self.signals.items():
            signal.signal(sig, handler)

    def test_permanent_and_transient_bridge_failures_have_distinct_exit_codes(self):
        for reason, terminal, expected in (("checkpoint_required", True, 78), ("network_disconnected", False, 1)):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(dir=ROOT) as folder:
                env = {"DATA_DIR": folder, "FB_SESSION_JSON": json.dumps({"c_user": "12345", "xs": "synthetic", "datr": "synthetic"})}
                with patch.dict(os.environ, env, clear=True), patch.object(sys, "argv", [str(ROOT / "logger.py")]), patch("bridge_rpc.Bridge", side_effect=BridgeFailure(reason, terminal=terminal)), contextlib.redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(SystemExit) as stopped:
                        runpy.run_path(str(ROOT / "logger.py"), run_name="__main__")
                self.assertEqual(stopped.exception.code, expected)
                self.assertIn(reason, output.getvalue())

    def test_invalid_cookie_configuration_stops_without_an_authentication_attempt(self):
        with patch.dict(os.environ, {"FB_SESSION_JSON": "invalid"}, clear=True), patch.object(sys, "argv", [str(ROOT / "logger.py")]), patch("bridge_rpc.Bridge") as bridge, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT / "logger.py"), run_name="__main__")
        self.assertEqual(stopped.exception.code, 78)
        bridge.assert_not_called()

    def test_actual_cleanup_cli_needs_no_cookie_or_native_binary(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            env = {"DATA_DIR": folder, "FB_SESSION_JSON": "invalid", "BRIDGE_BIN": "/nonexistent/bridge"}
            result = subprocess.run([sys.executable, str(ROOT / "logger.py"), "--cleanup"], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("No Telegram requests", result.stdout)
            self.assertTrue((Path(folder) / "messenger-modern.sqlite3").exists())

    def test_terminal_connection_status_suppresses_restarts_but_network_status_does_not(self):
        for code, terminal in (("auth_failed", True), ("checkpoint_required", True), ("network_disconnected", False)):
            with self.subTest(code=code):
                bridge = object.__new__(Bridge)
                bridge.call = lambda *args, **kwargs: {"failureCode": code, "connected": False, "retryable": False, "permanent": False, "nativeReconnecting": False}
                with self.assertRaises(BridgeFailure) as failure:
                    bridge.wait_connected(timeout=120)
                self.assertEqual(failure.exception.terminal, terminal)


if __name__ == "__main__":
    unittest.main()
