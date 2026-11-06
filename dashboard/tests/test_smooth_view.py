"""Live view authorization, stream cleanup, and ordered bounded input batches."""
import base64
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.fernet import Fernet

from browser.events import normalize
from dashboard.store import Store
from dashboard.web import create_app

ORIGIN = "https://viewer.example:8443"
JPEG = b"\xff\xd8synthetic-test-frame\xff\xd9"


class Browser:
    def __init__(self):
        self.calls = []

    def call(self, ident, action, data=None):
        self.calls.append((ident, action, data))
        return {"jpeg": base64.b64encode(JPEG).decode(), "site": "www.facebook.com", "width": 1024, "height": 768, "ready": False, "cookies": {"xs": "SYNTHETIC_PRIVATE_COOKIE"}}


class SmoothViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = Store(Path(self.temp.name), Fernet.generate_key(), clock=lambda: self.now[0])
        self.store.configure_bot("123456:" + "SYNTHETIC_TOKEN_NOT_REAL" * 2, {"id": 123456, "username": "synthetic_view_bot", "has_topics_enabled": True})
        self.browser = Browser()
        self.app = create_app(self.store, origin=ORIGIN, testing=True, browser_client=self.browser)
        self.client = self.app.test_client()
        self.invites = self.app.extensions["setup_invites"]
        self.ident, token = self.invites.create()
        response = self.client.post("/setup/api/claim", json={"token": token, "name": "Alex", "consent": True}, headers={"Origin": ORIGIN}, base_url=ORIGIN)
        self.csrf = response.json["csrf"]

    def tearDown(self):
        self.temp.cleanup()

    def post(self, action, body):
        return self.client.post("/setup/api/" + action, json=body, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-Setup-CSRF": self.csrf})

    def stream(self):
        return self.client.get("/setup/api/stream?view=1", base_url=ORIGIN, buffered=False)

    def test_metadata_does_not_transfer_jpeg_or_private_controller_fields(self):
        response = self.post("view", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json), {"site", "width", "height", "ready"})
        self.assertEqual(self.browser.calls[-1], (self.ident, "frame", {"metadata_only": True}))
        self.assertNotIn("SYNTHETIC_PRIVATE_COOKIE", response.text)

    def test_stream_delivers_binary_jpeg_with_no_token_in_url_and_no_caching(self):
        response = self.stream()
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn("multipart/x-mixed-replace", response.content_type)
            self.assertEqual(response.headers["X-Accel-Buffering"], "no")
            self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            first = next(iter(response.response))
            self.assertIn(JPEG, first)
            self.assertIn(b"Content-Length: " + str(len(JPEG)).encode(), first)
            self.assertNotIn(b"SYNTHETIC_PRIVATE_COOKIE", first)
            self.assertEqual(self.browser.calls[-1][0], self.ident)
        finally:
            response.close()

    def test_revoking_a_link_ends_the_active_stream_and_frees_its_slot(self):
        response = self.stream()
        iterator = iter(response.response)
        self.assertIn(JPEG, next(iterator))
        self.invites.revoke(self.ident)
        self.assertEqual(list(iterator), [])
        response.close()
        self.assertEqual(self.stream().status_code, 401)

    def test_expiry_ends_an_already_open_stream(self):
        response = self.stream()
        iterator = iter(response.response)
        next(iterator)
        self.now[0] += 1201
        self.assertEqual(list(iterator), [])
        response.close()

    def test_at_most_two_streams_leave_capacity_for_inputs_and_admin_requests(self):
        first, second = self.stream(), self.stream()
        third = None
        try:
            self.assertEqual((first.status_code, second.status_code), (200, 200))
            self.assertEqual(self.stream().status_code, 429)
            self.assertEqual(self.post("input", {"events": [{"action": "key", "key": "Tab"}]}).status_code, 200)
            first.close()
            third = self.stream()
            self.assertEqual(third.status_code, 200)
        finally:
            first.close(); second.close()
            if third is not None:
                third.close()

    def test_anonymous_stream_and_missing_input_csrf_are_rejected(self):
        self.assertEqual(self.app.test_client().get("/setup/api/stream", base_url=ORIGIN).status_code, 401)
        self.assertEqual(self.client.post("/setup/api/input", json={"events": [{"action": "text", "text": "SYNTHETIC"}]}, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code, 403)

    def test_batch_keeps_input_order_and_ignores_unrelated_actor_fields(self):
        events = [{"action": "click", "x": 10, "y": 20}, {"action": "text", "text": "SYNTHETIC_INPUT"}, {"action": "key", "key": "Enter"}]
        response = self.post("input", {"events": events, "id": "f" * 32})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.browser.calls[-1], (self.ident, "input", {"events": events}))
        with self.store.db() as db:
            self.assertNotIn("SYNTHETIC_INPUT", "\n".join(db.iterdump()))

    def test_unsupported_event_rejects_the_entire_batch_before_dispatch(self):
        result = self.post("input", {"events": [{"action": "text", "text": "SYNTHETIC"}, {"action": "capture"}]})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.browser.calls, [])

    def test_normalization_bounds_sizes_numbers_keys_and_unicode_without_truncation(self):
        invalid = [[], [{"action": "click", "x": float("nan"), "y": 2}], [{"action": "key", "key": "Control+L"}], [{"action": "key", "key": {}}], [{"action": "text", "text": "x" * 2001}], [{"action": "reload"}] * 25]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                normalize(value)
        self.assertEqual(normalize([{"action": "text", "text": "🙂" * 2000}])[0]["text"], "🙂" * 2000)
        self.assertEqual(normalize([{"action": "scroll", "delta": 50000}])[0]["delta"], 1536)


if __name__ == "__main__":
    unittest.main()
