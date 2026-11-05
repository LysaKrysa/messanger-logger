"""Synthetic dashboard, account isolation, bot and controller integration checks."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import time

from cryptography.fernet import Fernet

from dashboard.store import Store, DashboardError
from dashboard.web import create_app
from dashboard.broker import create_app as broker_app, PairingReceiver, BotUnavailable, TelegramAPI
from dashboard.manager import Manager, LABEL
from dashboard.cli import import_existing, read_env
from event_store import EventStore
from telegram_relay import TelegramRelay

TOKEN = "123456:" + "SYNTHETIC_DASHBOARD_TOKEN_NOT_REAL" * 2
COOKIES = json.dumps({"c_user": "111222333", "xs": "SYNTHETIC_XS_PRIVATE", "datr": "SYNTHETIC_DATR_PRIVATE"})
BOT = {"id": 123456, "username": "synthetic_dashboard_bot", "has_topics_enabled": True}
ORIGIN = "https://admin.example.test:8443"
PASSWORD = "synthetic-admin-password-123"


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.error = False

    def post(self, method, body, **kwargs):
        raw = body if isinstance(body, bytes) else b"".join(body)
        self.calls.append((method, raw, kwargs))
        if self.error:
            raise BotUnavailable
        return 200, {"ok": True, "result": {"message_id": 1}}

    def json(self, method, parameters):
        return self.post(method, json.dumps(parameters).encode(), chat=parameters.get("chat_id"))[1]["result"]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1000000
        self.store = Store(self.root / "state", Fernet.generate_key(), clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def paired(self, chat=87654321, cookie=COOKIES):
        self.store.configure_bot(TOKEN, BOT)
        ident = self.store.add_account("Synthetic account", cookie)
        token = self.store.invite(ident).split("start=", 1)[1]
        self.assertTrue(self.store.claim_invite(token, chat, "@synthetic_friend"))
        self.store.action(ident, "approve")
        self.store.action(ident, "start")
        return ident


class StorageTests(Base):
    def test_credentials_are_encrypted_and_bound_to_one_account(self):
        ident = self.store.add_account("Synthetic account", COOKIES)
        row = self.store.account(ident, private=True)
        self.assertEqual(self.store.unseal("cookies:" + ident, row["cookies"])["c_user"], "111222333")
        self.assertNotIn("SYNTHETIC_XS_PRIVATE", self.store.path.read_bytes().decode(errors="ignore"))
        self.assertNotIn("cookies", self.store.account(ident))
        with self.assertRaises(DashboardError):
            self.store.unseal("cookies:" + "a" * 32, row["cookies"])
        altered = row["cookies"][:-2] + "xx"
        with self.assertRaises(DashboardError):
            self.store.unseal("cookies:" + ident, altered)

    def test_duplicate_messenger_accounts_and_wrong_replacement_are_rejected(self):
        ident = self.store.add_account("Synthetic account", COOKIES)
        with self.assertRaises(DashboardError):
            self.store.add_account("Duplicate", COOKIES)
        with self.assertRaises(DashboardError):
            self.store.update_cookies(ident, COOKIES.replace("111222333", "444555666"))

    def test_account_limit_and_path_references(self):
        self.store.max_accounts = 1
        self.store.add_account("One")
        with self.assertRaises(DashboardError):
            self.store.add_account("Two")
        for ident in ("../root", "-f", "a" * 33, "A" * 32):
            with self.assertRaises(DashboardError):
                self.store.action(ident, "start")

    def test_pairing_requires_single_use_invitation_and_approval(self):
        self.store.configure_bot(TOKEN, BOT)
        ident = self.store.add_account("Friend", COOKIES)
        url = self.store.invite(ident)
        token = url.split("start=", 1)[1]
        self.assertLessEqual(len(token), 64)
        self.assertTrue(self.store.claim_invite(token, 87654321, "@friend"))
        self.assertFalse(self.store.claim_invite(token, 22222222, "@other"))
        with self.assertRaises(DashboardError):
            self.store.action(ident, "start")
        self.store.action(ident, "approve")
        self.assertFalse(self.store.account(ident)["desired"])
        self.store.action(ident, "start")
        self.assertTrue(self.store.account(ident)["desired"])
        with self.assertRaises(DashboardError):
            self.store.invite(ident)

    def test_expired_and_replaced_invitations_are_unavailable(self):
        self.store.configure_bot(TOKEN, BOT)
        ident = self.store.add_account("Friend")
        first = self.store.invite(ident).split("start=")[1]
        second = self.store.invite(ident).split("start=")[1]
        self.assertFalse(self.store.claim_invite(first, 87654321, "Friend"))
        self.now += 901
        self.assertFalse(self.store.claim_invite(second, 87654321, "Friend"))

    def test_one_telegram_identity_cannot_claim_another_account(self):
        self.paired()
        other = self.store.add_account("Other friend")
        invite = self.store.invite(other).split("start=")[1]
        self.assertFalse(self.store.claim_invite(invite, 87654321, "Friend"))

    def test_disconnect_revokes_gateway_credentials(self):
        ident = self.paired()
        key = self.store.worker_key(self.store.account(ident, private=True))
        self.assertIsNotNone(self.store.authenticate_worker(key))
        self.store.disconnect_user(87654321)
        self.assertIsNone(self.store.authenticate_worker(key))
        self.assertFalse(self.store.account(ident)["desired"])

    def test_password_sessions_expire_and_password_reset_revokes_them(self):
        self.store.set_password(PASSWORD)
        token = self.store.login(PASSWORD, "127.0.0.1")
        self.assertTrue(self.store.session(token))
        self.now += 1801
        self.assertFalse(self.store.session(token))
        token = self.store.login(PASSWORD, "127.0.0.1")
        self.store.set_password(PASSWORD + "-new")
        self.assertFalse(self.store.session(token))

    def test_password_guessing_is_throttled(self):
        self.store.set_password(PASSWORD)
        for _ in range(8):
            with self.assertRaises(DashboardError):
                self.store.login("wrong", "127.0.0.1")
        with self.assertRaisesRegex(DashboardError, "Too many"):
            self.store.login(PASSWORD, "127.0.0.1")

    def test_bot_requires_threaded_mode_and_cannot_switch_identity_silently(self):
        with self.assertRaises(DashboardError):
            self.store.configure_bot(TOKEN, {**BOT, "has_topics_enabled": False})
        self.store.configure_bot(TOKEN, BOT)
        with self.assertRaises(DashboardError):
            self.store.configure_bot(TOKEN.replace("123456:", "999999:"), {**BOT, "id": 999999})
        self.assertNotIn("token", self.store.bot())


class WebTests(Base):
    def setUp(self):
        super().setUp()
        self.store.set_password(PASSWORD)
        self.app = create_app(self.store, origin=ORIGIN, status_root=self.root / "status", testing=True)
        self.client = self.app.test_client()

    def csrf(self):
        with self.client.session_transaction(base_url=ORIGIN) as session:
            return session["csrf"]

    def login(self):
        self.client.get("/login", base_url=ORIGIN)
        response = self.client.post("/login", base_url=ORIGIN, data={"csrf": self.csrf(), "password": PASSWORD})
        self.assertEqual(response.status_code, 302)

    def test_dashboard_requires_sign_in_and_secure_cookie(self):
        self.assertEqual(self.client.get("/", base_url=ORIGIN).status_code, 302)
        response = self.client.get("/login", base_url=ORIGIN)
        cookie = response.headers.get("Set-Cookie")
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_csrf_and_foreign_origins_are_rejected(self):
        self.login()
        self.assertEqual(self.client.post("/accounts/new", base_url=ORIGIN, data={"name": "Friend"}).status_code, 403)
        self.assertEqual(self.client.post("/accounts/new", base_url=ORIGIN, headers={"Origin": "https://evil.example"}, data={"csrf": self.csrf(), "name": "Friend"}).status_code, 403)
        self.assertEqual(self.store.accounts(), [])

    def test_full_account_setup_forms_hide_saved_secrets_and_escape_labels(self):
        self.login()
        response = self.client.post("/accounts/new", base_url=ORIGIN, data={"csrf": self.csrf(), "name": '<script>alert("x")</script>', "cookies": COOKIES})
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response.location, base_url=ORIGIN).get_data(as_text=True)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("SYNTHETIC_XS_PRIVATE", page)
        self.assertNotIn("SYNTHETIC_DATR_PRIVATE", page)
        self.assertIn('name="cookies"', self.client.get("/accounts/new", base_url=ORIGIN).get_data(as_text=True))

    def test_bot_configuration_verifies_then_hides_token(self):
        self.login()
        with patch("dashboard.web.verify_bot", return_value=BOT) as verify:
            response = self.client.post("/bot", base_url=ORIGIN, data={"csrf": self.csrf(), "token": TOKEN})
        self.assertEqual(response.status_code, 302)
        verify.assert_called_once_with(TOKEN)
        page = self.client.get("/bot", base_url=ORIGIN).get_data(as_text=True)
        self.assertIn("@synthetic_dashboard_bot", page)
        self.assertNotIn(TOKEN, page)

    def test_logout_revokes_login_and_invitation_is_shown_only_on_creation(self):
        self.login()
        self.store.configure_bot(TOKEN, BOT)
        ident = self.store.add_account("Friend", COOKIES)
        response = self.client.post("/accounts/" + ident + "/invite", base_url=ORIGIN, data={"csrf": self.csrf()})
        self.assertIn("?start=", response.get_data(as_text=True))
        self.assertNotIn("?start=", self.client.get("/accounts/" + ident, base_url=ORIGIN).get_data(as_text=True))
        self.client.post("/logout", base_url=ORIGIN, data={"csrf": self.csrf()})
        self.assertEqual(self.client.get("/", base_url=ORIGIN).status_code, 302)


class BrokerTests(Base):
    def setUp(self):
        super().setUp()
        self.ident = self.paired()
        self.key = self.store.worker_key(self.store.account(self.ident, private=True))
        self.api = FakeAPI()
        self.app = broker_app(self.store, self.api)
        self.client = self.app.test_client()

    def post(self, method, payload, **kwargs):
        return self.client.post("/telegram/" + method, json=payload, headers={"Authorization": "Bearer " + self.key}, **kwargs)

    def test_gateway_rejects_cross_user_destinations_and_destructive_methods(self):
        self.assertEqual(self.post("sendMessage", {"chat_id": 22222222, "text": "PRIVATE"}).status_code, 403)
        self.assertEqual(self.post("deleteMessage", {"chat_id": 87654321, "message_id": 1}).status_code, 403)
        self.assertEqual(self.post("getUpdates", {}).status_code, 403)
        self.assertEqual(self.api.calls, [])

    def test_gateway_valid_destination_and_api_fields(self):
        response = self.post("sendMessage", {"chat_id": 87654321, "text": "Synthetic", "message_thread_id": 7})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.api.calls[0][0], "sendMessage")
        self.assertEqual(self.post("sendMessage", {"chat_id": 87654321, "text": "Synthetic", "business_connection_id": "OTHER"}).status_code, 403)
        self.assertEqual(self.post("getChatMember", {"chat_id": 87654321, "user_id": 999999}).status_code, 403)

    def test_unauthenticated_and_revoked_gateway_credentials_do_not_send(self):
        self.assertEqual(self.client.post("/telegram/getMe", json={}).status_code, 403)
        self.store.action(self.ident, "disconnect")
        self.assertEqual(self.post("getMe", {}).status_code, 403)
        self.assertEqual(self.api.calls, [])

    def test_gateway_preserves_unknown_network_outcome_as_5xx(self):
        self.api.error = True
        response = self.post("sendMessage", {"chat_id": 87654321, "text": "Synthetic"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("unconfirmed", response.json["description"])
        self.assertEqual(len(self.api.calls), 1)

    def test_media_upload_checks_destination_and_keeps_exact_file_bytes(self):
        data = {"chat_id": "87654321", "message_thread_id": "7", "caption": "Synthetic", "document": (io.BytesIO(b"synthetic image bytes"), "image.png")}
        response = self.client.post("/telegram/sendDocument", data=data, headers={"Authorization": "Bearer " + self.key})
        self.assertEqual(response.status_code, 200)
        method, body, options = self.api.calls[0]
        self.assertIn(b"synthetic image bytes", body)
        self.assertEqual(options["length"], len(body))
        self.assertEqual(options["chat"], 87654321)
        response = self.client.post("/telegram/sendDocument", data={"chat_id": "22222222", "document": (io.BytesIO(b"PRIVATE"), "x.bin")}, headers={"Authorization": "Bearer " + self.key})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.api.calls), 1)

    def test_pairing_receiver_checks_private_identity_and_is_idempotent(self):
        other = self.store.add_account("Other")
        token = self.store.invite(other).split("start=")[1]
        receiver = PairingReceiver(self.store, self.api)
        update = {"update_id": 1, "message": {"chat": {"id": 22222222, "type": "supergroup"}, "from": {"id": 22222222}, "text": "/start " + token}}
        receiver.handle(update)
        self.assertIsNone(self.store.account(other)["pending_chat"])
        update["update_id"] = 2
        update["message"]["chat"]["type"] = "private"
        update["message"]["from"]["id"] = 33333333
        receiver.handle(update)
        self.assertIsNone(self.store.account(other)["pending_chat"])
        update["update_id"] = 3
        update["message"]["from"]["id"] = 22222222
        receiver.handle(update)
        self.assertEqual(self.store.account(other)["pending_chat"], 22222222)
        receiver.handle(update)
        self.assertEqual(len(self.api.calls), 1)
        self.assertNotIn("/start", json.dumps(self.store.accounts()))

    def test_bot_disconnect_stops_only_its_own_account(self):
        other = self.paired(22222222, COOKIES.replace("111222333", "444555666"))
        PairingReceiver(self.store, self.api).handle({"update_id": 10, "message": {"chat": {"id": 87654321, "type": "private"}, "from": {"id": 87654321}, "text": "/disconnect"}})
        self.assertFalse(self.store.account(self.ident)["desired"])
        self.assertTrue(self.store.account(other)["desired"])


class FakeDocker:
    def __init__(self):
        self.calls, self.info, self.names = [], None, ""

    def call(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "ps":
            return self.names
        return "synthetic-id"

    def inspect(self, name):
        return self.info


class ManagerTests(Base):
    def setUp(self):
        super().setUp()
        self.ident = self.paired()
        self.docker = FakeDocker()
        self.manager = Manager(self.store, data_root=self.root / "accounts", status_root=self.root / "status", runtime_root=self.root / "run", code_root=self.root / "code", docker=self.docker, clock=lambda: self.now)

    def test_worker_launch_has_private_mounts_but_no_credentials_in_docker_arguments(self):
        row = self.store.account(self.ident, private=True)
        self.manager.reconcile(row, self.store.bot())
        arguments = " ".join(self.docker.calls[-1])
        for secret in (TOKEN, "SYNTHETIC_XS_PRIVATE", self.store.worker_key(row)):
            self.assertNotIn(secret, arguments)
        self.assertIn("--read-only", arguments)
        self.assertIn("--cap-drop ALL", arguments)
        self.assertIn("--memory 512m", arguments)
        self.assertIn(str(self.root / "accounts" / self.ident / "data"), arguments)
        saved = self.root / "run" / (self.ident + ".json")
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertIn("SYNTHETIC_XS_PRIVATE", saved.read_text())
        self.assertEqual(self.manager.status(self.ident)["state"], "starting")

    def test_permanent_worker_failure_waits_for_explicit_restart_or_new_cookies(self):
        row = self.store.account(self.ident, private=True)
        self.docker.info = {"Config": {"Labels": {LABEL: "true", "messenger.revision": str(row["revision"])}}, "State": {"Running": False, "ExitCode": 78}}
        self.manager.reconcile(row, self.store.bot())
        self.assertEqual(self.manager.status(self.ident)["state"], "attention")
        self.assertEqual(self.docker.calls, [])
        self.store.action(self.ident, "restart")
        self.manager.reconcile(self.store.account(self.ident, private=True), self.store.bot())
        self.assertTrue(any(args[0] == "run" for args in self.docker.calls))

    def test_transient_restarts_are_paced_and_unmanaged_containers_are_preserved(self):
        row = self.store.account(self.ident, private=True)
        self.manager.reconcile(row, self.store.bot())
        self.manager.reconcile(row, self.store.bot())
        self.assertEqual(len(self.docker.calls), 1)
        self.assertEqual(self.manager.status(self.ident)["state"], "retry_wait")
        self.docker.info = {"Config": {"Labels": {}}, "State": {"Running": True}}
        with self.assertRaises(DashboardError):
            self.manager.reconcile(row, self.store.bot())
        self.assertEqual(len(self.docker.calls), 1)

    def test_disconnect_removes_only_own_managed_container_and_runtime_secret(self):
        row = self.store.account(self.ident, private=True)
        self.manager.launch(row, self.store.bot())
        self.docker.info = {"Config": {"Labels": {LABEL: "true"}}, "State": {"Running": True}}
        self.store.action(self.ident, "disconnect")
        self.manager.reconcile(self.store.account(self.ident, private=True), self.store.bot())
        self.assertEqual(self.docker.calls[-2:], [("stop", "--time", "30", "messenger-account-" + self.ident), ("rm", "messenger-account-" + self.ident)])
        self.assertFalse((self.root / "run" / (self.ident + ".json")).exists())

    def test_stopped_account_cleanup_has_no_credentials_or_network(self):
        self.manager.path(self.ident)
        self.manager.cleanup()
        arguments = " ".join(self.docker.calls[-1])
        self.assertIn("--network none", arguments)
        self.assertIn("--cleanup", arguments)
        self.assertNotIn(TOKEN, arguments)
        self.assertNotIn("logger-credentials", arguments)

    def test_status_reports_fixed_counters_without_raw_private_lines(self):
        data = self.manager.path(self.ident)
        (data / "messenger-modern-status.log").write_text("PRIVATE content\nCookie configuration loaded.\nMessenger listener ready (regular and encrypted).\nThis run: 12 messages, 3 deletions, 20 chat events saved.\n")
        observed = self.manager.observe(self.ident, {})
        self.assertEqual(observed, {"ready": True, "messages": 12, "deletions": 3})
        self.assertNotIn("PRIVATE", json.dumps(observed))

    def test_removing_account_stops_own_worker_and_deletes_only_its_managed_data(self):
        row = self.store.account(self.ident, private=True)
        self.manager.launch(row, self.store.bot())
        data = self.manager.path(self.ident)
        (data / "synthetic-message.txt").write_text("PRIVATE_SYNTHETIC")
        unrelated = self.manager.data_root / "unrelated"
        unrelated.mkdir()
        self.docker.names = "messenger-account-" + self.ident
        self.docker.info = {"Config": {"Labels": {LABEL: "true"}}, "State": {"Running": True}}
        key = self.store.worker_key(row)
        self.store.action(self.ident, "remove")
        self.assertIsNone(self.store.authenticate_worker(key))
        self.manager.run_once()
        self.assertFalse(data.exists())
        self.assertTrue(unrelated.exists())
        self.assertIn(("stop", "--time", "30", "messenger-account-" + self.ident), self.docker.calls)

    def test_new_account_is_not_deleted_when_it_arrives_after_manager_snapshot(self):
        data = self.manager.path(self.ident)
        original = self.store.accounts
        calls = 0
        def accounts(**kwargs):
            nonlocal calls
            calls += 1
            return [] if calls == 1 else original(**kwargs)
        with patch.object(self.store, "accounts", side_effect=accounts):
            self.manager.run_once()
        self.assertTrue(data.exists())


class MigrationTests(Base):
    def test_import_stops_old_service_before_copy_and_preserves_source(self):
        source = self.root / "original-data"
        source.mkdir()
        (source / "synthetic-device-state").write_text("SYNTHETIC_DEVICE")
        env = self.root / "original.env"
        env.write_text("FB_SESSION_JSON=" + COOKIES + "\nTELEGRAM_BOT_TOKEN=" + TOKEN + "\nTELEGRAM_CHAT_ID=87654321\n")
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with patch("dashboard.cli.verify_bot", return_value=BOT), patch("dashboard.cli.TelegramAPI.json", return_value={"id": 87654321, "type": "private"}), patch("dashboard.cli.os.geteuid", return_value=0):
            ident = import_existing(self.store, env, source, "My account", data_root=self.root / "accounts", runner=run)
        self.assertEqual(calls, [["systemctl", "is-active", "--quiet", "messenger-logger"], ["systemctl", "stop", "messenger-logger"], ["systemctl", "disable", "messenger-logger"]])
        self.assertEqual((self.root / "accounts" / ident / "data/synthetic-device-state").read_text(), "SYNTHETIC_DEVICE")
        self.assertTrue((source / "synthetic-device-state").is_file())
        self.assertEqual(self.store.account(ident)["chat_id"], 87654321)
        self.assertTrue(self.store.account(ident)["desired"])

    def test_failed_import_restores_original_service_and_keeps_new_worker_stopped(self):
        source = self.root / "original-data"
        source.mkdir()
        env = self.root / "original.env"
        env.write_text("FB_SESSION_JSON=" + COOKIES + "\nTELEGRAM_BOT_TOKEN=" + TOKEN + "\nTELEGRAM_CHAT_ID=87654321\n")
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with patch("dashboard.cli.verify_bot", return_value=BOT), patch("dashboard.cli.TelegramAPI.json", return_value={"id": 87654321, "type": "private"}), patch("dashboard.cli.os.geteuid", return_value=0), patch("dashboard.cli.shutil.copytree", side_effect=OSError("PRIVATE")), self.assertRaises(DashboardError) as caught:
            import_existing(self.store, env, source, "My account", data_root=self.root / "accounts", runner=run)
        self.assertIn(["systemctl", "enable", "--now", "messenger-logger"], calls)
        self.assertFalse(self.store.accounts()[0]["desired"])
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_import_env_parser_does_not_execute_shell_text(self):
        path = self.root / "synthetic.env"
        path.write_text("FB_SESSION_JSON=$(touch NEVER_EXECUTE_THIS)\n")
        self.assertEqual(read_env(path)["FB_SESSION_JSON"], "$(touch NEVER_EXECUTE_THIS)")


class GatewayIntegrationTests(Base):
    def test_two_real_relays_route_separate_archives_through_one_gateway(self):
        first = self.paired()
        second = self.paired(22222222, COOKIES.replace("111222333", "444555666"))
        class OfflineAPI(FakeAPI):
            def post(self, method, body, **options):
                status, response = super().post(method, body, **options)
                params = json.loads(body) if isinstance(body, bytes) else {}
                result = {"message_id": 1}
                if method == "getMe":
                    result = BOT
                elif method == "getChat":
                    result = {"id": int(params["chat_id"]), "type": "private"}
                elif method == "createForumTopic":
                    result = {"message_thread_id": 100}
                return status, {"ok": True, "result": result}
        api = OfflineAPI()
        gateway = broker_app(self.store, api)
        class Response:
            def __init__(self, response):
                self.raw = response.data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return self.raw[:limit]
        def open_gateway(req, **kwargs):
            data = req.data if isinstance(req.data, bytes) else b"".join(req.data)
            result = gateway.test_client().post("/telegram/" + req.full_url.rsplit("/", 1)[-1], data=data,
                content_type=req.get_header("Content-type"), headers={"Authorization": req.get_header("Authorization")})
            if result.status_code >= 400:
                raise HTTPError(req.full_url, result.status_code, "Gateway rejected", None, io.BytesIO(result.data))
            return Response(result)
        relays, archives = [], []
        try:
            with patch("telegram_relay.urlopen", side_effect=open_gateway):
                for index, ident in enumerate((first, second)):
                    row = self.store.account(ident, private=True)
                    archive = EventStore(self.root / ("archive" + str(index)), started_at=1000, run_id="gateway" + str(index))
                    archives.append(archive)
                    env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_TOPICS": "true", "TELEGRAM_RELAY_URL": "http://127.0.0.1:8788", "TELEGRAM_ACCOUNT_KEY": self.store.worker_key(row), "TELEGRAM_BOT_ID": "123456", "TELEGRAM_CHAT_ID": str(row["chat_id"]), "TELEGRAM_MODE": "deleted"}
                    with patch.dict(os.environ, env, clear=True):
                        relay = TelegramRelay(lambda message: None, archive)
                    relay.interval = 0
                    relays.append(relay)
                    archive.accept({"type": "message", "timestamp": 1001000, "data": {"id": "m1", "threadId": 42, "senderId": 222, "timestampMs": 1001000, "text": "Synthetic content " + str(index)}})
                    relay.submit(archive.accept({"type": "messageUnsend", "timestamp": 1002000, "data": {"threadId": 42, "messageId": "m1"}}))
                    until = time.monotonic() + 5
                    while relay.items.unfinished_tasks and time.monotonic() < until:
                        time.sleep(.01)
                    self.assertEqual(relay.items.unfinished_tasks, 0)
                sends = [json.loads(body) for method, body, options in api.calls if method == "sendMessage"]
                self.assertEqual(len(sends), 2)
                self.assertEqual([str(item["chat_id"]) for item in sends], ["87654321", "22222222"])
                self.assertIn("Synthetic content 0", sends[0]["text"])
                self.assertNotIn("Synthetic content 1", sends[0]["text"])
                self.assertIn("Synthetic content 1", sends[1]["text"])
                self.assertNotIn("Synthetic content 0", sends[1]["text"])
        finally:
            for relay in relays:
                relay.close()
            for archive in archives:
                archive.close()


if __name__ == "__main__":
    unittest.main()
