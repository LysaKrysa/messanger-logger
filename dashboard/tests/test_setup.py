"""Guest invitations and browser isolation; no Facebook login or Telegram send."""
import base64
import concurrent.futures
import ipaddress
import json
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

from dashboard.browser_host import create_app as controller_app
from dashboard.browser_runtime import Controller, DockerBrowser, BrowserError
from dashboard.browser_proxy import allowed_host, public_addresses
from dashboard.setup_store import SetupInvites
from dashboard.setup_web import COOKIE
from dashboard.store import Store, DashboardError
from dashboard.web import create_app

ORIGIN = "https://setup.example:8443"
COOKIES = {"c_user": "222333444", "xs": "SYNTHETIC_SESSION_NOT_REAL", "datr": "SYNTHETIC_DEVICE_NOT_REAL"}


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def call(self, ident, action, data=None):
        self.calls.append((ident, action, data))
        if action == "capture":
            return {"cookies": COOKIES}
        if action == "frame":
            return {"jpeg": "SYNTHETIC_FRAME", "site": "www.facebook.com", "ready": True, "cookies": COOKIES}
        return {"started": True}


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = Store(Path(self.temp.name), Fernet.generate_key(), clock=lambda: self.now[0])
        self.store.configure_bot("123456:" + "SYNTHETIC_TOKEN_NOT_REAL" * 2, {"id": 123456, "username": "synthetic_setup_bot", "has_topics_enabled": True})
        self.invites = SetupInvites(self.store)
        self.browser = FakeBrowser()
        self.app = create_app(self.store, origin=ORIGIN, testing=True, browser_client=self.browser)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def claim(self, token=None, client=None):
        client = client or self.client
        if token is None:
            _, token = self.invites.create()
        response = client.post("/setup/api/claim", json={"token": token, "name": "Alex", "consent": True}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200, response.data)
        return response.json

    def post(self, action, data, state, client=None):
        return (client or self.client).post("/setup/api/" + action, json=data, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-Setup-CSRF": state["csrf"]})

    def test_get_and_link_preview_do_not_claim_or_create_admin_sessions(self):
        ident, token = self.invites.create("For Alex")
        response = self.client.get("/setup#" + token, base_url=ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(self.invites.list()[0]["state"], "pending")
        self.assertNotIn(token, response.text)
        self.assertEqual(self.store.accounts(), [])

    def test_link_is_claimed_once_and_session_cookie_has_security_flags(self):
        ident, token = self.invites.create()
        state = self.claim(token)
        self.assertEqual(state["id"], ident)
        denied = self.app.test_client().post("/setup/api/claim", json={"token": token, "name": "Another", "consent": True}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(denied.status_code, 400)
        cookie = self.client.get_cookie(COOKIE, domain="setup.example")
        self.assertTrue(cookie.secure and cookie.http_only)
        self.assertEqual(cookie.same_site, "Strict")
        self.assertEqual(cookie.path, "/")
        self.assertEqual(self.client.get("/", base_url=ORIGIN).status_code, 302)

    def test_concurrent_claims_create_exactly_one_guest_session(self):
        _, token = self.invites.create()
        def claim():
            try:
                return self.invites.claim(token, "Alex")
            except DashboardError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            sessions = list(pool.map(lambda _: claim(), range(4)))
        self.assertEqual(sum(value is not None for value in sessions), 1)

    def test_claim_requires_consent_and_exact_origin(self):
        _, token = self.invites.create()
        data = {"token": token, "name": "Alex", "consent": True}
        for origin in (None, "https://another.example", "https://setup.example"):
            response = self.client.post("/setup/api/claim", json=data, base_url=ORIGIN, headers={"Origin": origin} if origin else {})
            self.assertEqual(response.status_code, 403)
        data["consent"] = False
        self.assertEqual(self.client.post("/setup/api/claim", json=data, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code, 400)
        self.assertEqual(self.invites.list()[0]["state"], "pending")

    def test_all_guest_actions_require_session_and_csrf(self):
        state = self.claim()
        for action in ("start", "frame", "input", "finish", "cancel"):
            self.assertEqual(self.app.test_client().post("/setup/api/" + action, json={}, base_url=ORIGIN).status_code, 401)
            self.assertEqual(self.client.post("/setup/api/" + action, json={}, base_url=ORIGIN, headers={"Origin": ORIGIN}).status_code, 403)
        self.assertEqual(self.browser.calls, [])

    def test_full_handoff_encrypts_cookies_and_link_then_waits_for_existing_admin_approval(self):
        state = self.claim()
        self.assertEqual(self.post("start", {"mobile": True}, state).status_code, 200)
        self.assertTrue(self.browser.calls[-1][2]["mobile"])
        self.assertEqual(self.post("finish", {"approve": False}, state).status_code, 400)
        self.assertEqual(self.store.accounts(), [])
        result = self.post("finish", {"approve": True}, state)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["state"], "completed")
        link = result.json["telegram_url"]
        self.assertTrue(link.startswith("https://t.me/synthetic_setup_bot?start="))
        self.assertEqual(self.browser.calls[-1][1], "stop")
        self.assertNotIn(COOKIES["xs"], result.text)
        self.assertNotIn(COOKIES["xs"], self.store.path.read_bytes().decode(errors="ignore"))
        with self.store.db() as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(COOKIES["xs"], dump)
        self.assertNotIn(link, dump)
        row = self.store.accounts(private=True)[0]
        self.assertEqual(row["name"], "Alex")
        self.assertEqual(self.store.unseal("cookies:" + row["id"], row["cookies"]), COOKIES)
        self.assertFalse(row["desired"])
        self.assertTrue(self.store.claim_invite(link.split("start=")[1], 888999, "@alex"))
        self.assertEqual(self.client.get("/setup/api/status", base_url=ORIGIN).json["pairing"], "pending_approval")
        self.store.action(row["id"], "approve")
        self.store.action(row["id"], "start")
        self.assertEqual(self.client.get("/setup/api/status", base_url=ORIGIN).json["pairing"], "approved")
        self.assertIsNone(self.client.get("/setup/api/status", base_url=ORIGIN).json["telegram_url"])

    def test_completion_is_idempotent_and_cannot_reopen_browser(self):
        state = self.claim()
        first = self.post("finish", {"approve": True}, state).json
        second = self.post("finish", {"approve": True}, state).json
        self.assertEqual(first["telegram_url"], second["telegram_url"])
        self.assertEqual(len(self.store.accounts()), 1)
        self.assertEqual(sum(call[1] == "capture" for call in self.browser.calls), 1)
        self.assertEqual(self.post("start", {}, state).status_code, 400)

    def test_frame_strips_cookie_and_unexpected_controller_fields(self):
        state = self.claim()
        result = self.post("frame", {}, state)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.json), {"jpeg", "site", "ready", "width", "height"})
        self.assertNotIn(COOKIES["xs"], result.text)

    def test_only_prescribed_inputs_are_forwarded_to_the_bound_session(self):
        state = self.claim()
        self.assertEqual(self.post("input", {"action": "text", "text": "SYNTHETIC_PRIVATE_INPUT", "id": "other", "command": "exec"}, state).status_code, 200)
        self.assertEqual(self.browser.calls[-1], (state["id"], "text", {"text": "SYNTHETIC_PRIVATE_INPUT"}))
        self.assertEqual(self.post("input", {"action": "capture"}, state).status_code, 400)
        with self.store.db() as db:
            self.assertNotIn("SYNTHETIC_PRIVATE_INPUT", "\n".join(db.iterdump()))

    def test_disable_and_expiry_block_every_bound_request(self):
        state = self.claim()
        self.invites.revoke(state["id"])
        self.assertEqual(self.post("frame", {}, state).status_code, 401)
        state = self.claim()
        self.now[0] += 1201
        self.assertEqual(self.post("finish", {"approve": True}, state).status_code, 401)
        self.assertEqual(self.invites.list()[0]["state"], "expired")
        self.assertEqual(self.store.accounts(), [])

    def test_cancel_revokes_access_and_closes_own_browser_only(self):
        state = self.claim()
        self.assertEqual(self.post("cancel", {}, state).status_code, 200)
        self.assertEqual(self.browser.calls[-1], (state["id"], "stop", None))
        self.assertIsNone(self.client.get_cookie(COOKIE, domain="setup.example"))
        self.assertEqual(self.invites.list()[0]["state"], "revoked")

    def test_revocation_racing_with_capture_cannot_save_an_account(self):
        state = self.claim()
        def capture(ident, action, data=None):
            self.invites.revoke(ident)
            return {"cookies": COOKIES}
        self.browser.call = capture
        self.assertEqual(self.post("finish", {"approve": True}, state).status_code, 400)
        self.assertEqual(self.store.accounts(), [])

    def test_revocation_racing_with_start_stops_the_new_browser(self):
        state = self.claim()
        def start(ident, action, data=None):
            self.browser.calls.append((ident, action, data))
            if action == "start":
                self.invites.revoke(ident)
            return {"started": True}
        self.browser.call = start
        self.assertEqual(self.post("start", {}, state).status_code, 400)
        self.assertEqual([call[1] for call in self.browser.calls], ["start", "stop"])

    def test_completed_link_disable_revokes_unused_telegram_invite_but_preserves_account(self):
        state = self.claim()
        result = self.post("finish", {"approve": True}, state).json
        self.invites.revoke(state["id"])
        self.assertFalse(self.store.claim_invite(result["telegram_url"].split("start=")[1], 123123, "Alex"))
        self.assertEqual(len(self.store.accounts()), 1)

    def test_old_setup_cannot_expose_or_revoke_a_newer_telegram_invitation(self):
        state = self.claim()
        self.post("finish", {"approve": True}, state)
        account = self.store.accounts()[0]
        replacement = self.store.invite(account["id"])
        progress = self.client.get("/setup/api/status", base_url=ORIGIN).json
        self.assertIsNone(progress["telegram_url"])
        self.invites.revoke(state["id"])
        self.assertTrue(self.store.claim_invite(replacement.split("start=")[1], 676767, "@alex"))

    def test_active_links_reserve_capacity_and_disabling_releases_it(self):
        self.store.max_accounts = 1
        ident, _ = self.invites.create()
        with self.assertRaises(DashboardError):
            self.invites.create()
        with self.assertRaises(DashboardError):
            self.store.add_account("Manual account")
        self.invites.revoke(ident)
        self.invites.create()
        self.assertEqual(sum(row["active"] for row in self.invites.list()), 1)

    def test_all_active_links_remain_visible_beyond_the_history_limit(self):
        self.store.max_accounts = 2
        oldest, _ = self.invites.create("Keep this active")
        for _ in range(105):
            ident, _ = self.invites.create()
            self.invites.revoke(ident)
        rows = self.invites.list()
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0]["id"], oldest)
        self.assertTrue(rows[0]["active"])

    def test_admin_list_requires_admin_login_and_can_disable_links(self):
        self.assertEqual(self.client.get("/setup-links", base_url=ORIGIN).status_code, 302)
        self.store.set_password("synthetic-setup-admin-password")
        sid = self.store.login("synthetic-setup-admin-password", "local")
        with self.client.session_transaction(base_url=ORIGIN) as session:
            session.update(sid=sid, csrf="synthetic-csrf")
        response = self.client.post("/setup-links", data={"csrf": "synthetic-csrf", "label": "Friend", "hours": "6"}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        self.assertIn("/setup#", response.text)
        row = self.invites.list()[0]
        self.assertNotIn("/setup#", self.client.get("/setup-links", base_url=ORIGIN).text)
        response = self.client.post("/setup-links/" + row["id"] + "/disable", data={"csrf": "synthetic-csrf"}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.browser.calls[-1][1], "stop")
        self.assertEqual(self.invites.list()[0]["state"], "revoked")


class BrowserIsolationTests(unittest.TestCase):
    def test_docker_command_has_no_network_no_secrets_no_data_or_debug_port(self):
        args = DockerBrowser.command("a" * 32, "/run/egress.sock", "/opt/seccomp.json")
        for flag in ("--read-only", "--network", "--cap-drop", "--security-opt", "--user", "--memory", "--log-driver"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertEqual(args[args.index("--log-driver") + 1], "none")
        self.assertFalse(any(value in args for value in ("--privileged", "--env", "-p", "--publish")))
        self.assertNotIn("/var/lib/messenger", " ".join(args))
        self.assertNotIn("--no-sandbox", " ".join(args))

    def test_controller_serializes_browser_capacity_expires_and_stops_only_bound_id(self):
        now = [1000.0]
        made = []
        def factory(ident, expires, **kwargs):
            browser = SimpleNamespace(ident=ident, expires=expires, process=SimpleNamespace(poll=lambda: None), call=Mock(return_value={"started": True}), close=Mock())
            made.append(browser)
            return browser
        controller = Controller(proxy_path="/p", policy_path="/s", factory=factory, clock=lambda: now[0])
        controller.start("a" * 32, 1200)
        self.assertEqual(controller.start("a" * 32, 1200), {"started": True})
        with self.assertRaises(BrowserError):
            controller.start("b" * 32, 1200)
        controller.stop("b" * 32)
        made[0].close.assert_not_called()
        now[0] = 1201
        controller.sweep()
        made[0].close.assert_called_once()
        self.assertEqual(controller.sessions, {})

    def test_private_controller_requires_key_and_cannot_execute_arbitrary_actions(self):
        controller = Mock()
        client = controller_app(controller, secret="synthetic-key").test_client()
        path = "/browser/" + "a" * 32
        self.assertEqual(client.post(path + "/start", json={}).status_code, 403)
        self.assertEqual(client.post(path + "/exec", json={}, headers={"Authorization": "Bearer synthetic-key"}).status_code, 409)
        controller.call.assert_not_called()

    def test_egress_matches_dns_labels_and_never_private_loopback_or_metadata_ips(self):
        for host in ("www.facebook.com", "m.facebook.com", "video.fbcdn.net", "static.xx.fbcdn.net"):
            self.assertTrue(allowed_host(host))
        for host in ("facebook.com.evil.example", "evilfacebook.com", "127.0.0.1", "169.254.169.254", "localhost"):
            self.assertFalse(allowed_host(host))
        for address in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "172.16.0.1", "192.168.0.1", "::1", "fd00::1", "224.0.0.1"):
            with patch("dashboard.browser_proxy.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]):
                with self.assertRaises(ValueError):
                    public_addresses("www.facebook.com")


if __name__ == "__main__":
    unittest.main()
