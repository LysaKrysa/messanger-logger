from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_store import EventStore
from telegram_relay import TelegramRelay
from telegram_topics import TopicRouter, TopicError, TelegramRejected, topic_title
from check_telegram_topics import main as check_topics
import contextlib
import io

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:" + "SYNTHETIC_TOPIC_TOKEN_NOT_REAL" * 2
PNG = b"\x89PNG\r\n\x1a\nsynthetic topic fixture"


def message(mid="m1", thread=42, *, attachments=None):
    return {"type": "message", "timestamp": 1001000, "data": {"id": mid, "threadId": thread, "senderId": 222, "text": "captured text", "timestampMs": 1001000, "attachments": attachments or []}}


def deletion(mid="m1", thread=42):
    return {"type": "messageUnsend", "timestamp": 1002000, "data": {"messageId": mid, "threadId": thread}}


class API:
    def __init__(self):
        self.calls = []
        self.private = True
        self.enabled = True
        self.manage = True
        self.forum = True
        self.chat_id = 7654321
        self.created = 0
        self.error = None

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "getMe":
            return {"id": 123456, "is_bot": True, "has_topics_enabled": self.enabled}
        if method == "getChat":
            return {"id": self.chat_id, "type": "private" if self.private else "supergroup", "is_forum": self.forum}
        if method == "getChatMember":
            return {"status": "administrator", "can_manage_topics": self.manage}
        if method == "createForumTopic":
            self.created += 1
            if self.error:
                raise self.error
            return {"message_thread_id": 100 + self.created, "name": params["name"]}
        if method == "editForumTopic":
            return True
        return {"message_id": 99}


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, started_at=1000, run_id="topics-run")
        self.api = API()
        self.router = TopicRouter(self.api, self.store, "123456", "7654321")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_one_topic_per_conversation_even_when_names_are_equal(self):
        a = self.router.topic("42", "Alex")
        b = self.router.topic("43", "Alex")
        self.assertNotEqual(a, b)
        self.assertEqual(self.router.topic("42", "Alex"), a)
        created = [params for method, params in self.api.calls if method == "createForumTopic"]
        self.assertEqual([item["name"] for item in created], ["Alex", "Alex (2)"])

    def test_restart_reuses_persisted_topic_without_recreating(self):
        topic = self.router.topic("42", "Friends")
        self.store.close()
        self.store = EventStore(self.root, started_at=2000, run_id="after-restart")
        router = TopicRouter(self.api, self.store, "123456", "7654321")
        self.assertEqual(router.topic("42", "Friends"), topic)
        self.assertEqual(self.api.created, 1)
        self.assertEqual(self.store.counts(), {})

    def test_destination_chat_and_bot_ids_scope_the_topic_map(self):
        original = self.router.topic("42", "Friends")
        self.api.chat_id = 888
        other = TopicRouter(self.api, self.store, "123456", "888")
        self.assertNotEqual(other.topic("42", "Friends"), original)
        self.assertIsNone(self.store.telegram_topic("999", "888", "42"))

    def test_private_chat_requires_bot_threaded_mode(self):
        self.api.enabled = False
        with self.assertRaisesRegex(TopicError, "Threaded mode"):
            self.router.topic("42", "Friends")
        self.assertEqual(self.api.created, 0)
        self.assertIsNone(self.store.telegram_topic("123456", "7654321", "42"))

    def test_failed_capability_check_refreshes_after_cooldown(self):
        self.api.enabled = False
        with patch("telegram_topics.time.monotonic", return_value=100):
            with self.assertRaises(TopicError):
                self.router.destination()
        self.api.enabled = True
        with patch("telegram_topics.time.monotonic", return_value=110):
            with self.assertRaises(TopicError):
                self.router.destination()
        with patch("telegram_topics.time.monotonic", return_value=131):
            self.assertEqual(self.router.destination(), "7654321")
        self.assertEqual(sum(method == "getMe" for method, params in self.api.calls), 2)
        self.assertEqual(self.api.created, 0)

    def test_new_router_rechecks_immediately_after_mode_is_enabled(self):
        self.api.enabled = False
        with self.assertRaises(TopicError):
            self.router.destination()
        self.api.enabled = True
        restarted = TopicRouter(self.api, self.store, "123456", "7654321")
        self.assertEqual(restarted.destination(), "7654321")
        self.assertEqual(self.api.created, 0)

    def test_forum_group_requires_manage_topics_permission(self):
        self.api.private, self.api.manage = False, False
        self.api.chat_id = -100888
        self.router = TopicRouter(self.api, self.store, "123456", "-100888")
        with self.assertRaisesRegex(TopicError, "Manage Topics"):
            self.router.topic("42", "Friends")
        self.api.manage = True
        self.router.retry_at = 0
        self.assertGreater(self.router.topic("42", "Friends"), 0)

    def test_nonforum_destination_is_rejected(self):
        self.api.private, self.api.forum = False, False
        with self.assertRaisesRegex(TopicError, "Topics enabled"):
            self.router.topic("42", "Friends")

    def test_ambiguous_creation_is_not_repeated_and_can_be_bound_locally(self):
        self.api.error = TimeoutError(TOKEN + " PRIVATE provider details")
        with self.assertRaises(TopicError) as failure:
            self.router.topic("42", "Friends")
        self.assertNotIn(TOKEN, str(failure.exception))
        self.api.error = None
        with self.assertRaisesRegex(TopicError, "unconfirmed"):
            self.router.topic("42", "Friends")
        self.assertEqual(self.api.created, 1)
        self.store.save_telegram_topic("123456", "7654321", "42", 555, "Friends")
        self.assertEqual(self.router.topic("42", "Friends"), 555)
        self.assertEqual(self.api.created, 1)

    def test_definitively_rejected_creation_can_retry_after_permission_fix(self):
        self.api.error = TelegramRejected(403)
        with self.assertRaises(TopicError):
            self.router.topic("42", "Friends")
        self.assertIsNone(self.store.telegram_topic("123456", "7654321", "42"))
        self.api.error = None
        self.assertGreater(self.router.topic("42", "Friends"), 0)

    def test_cancel_before_request_does_not_leave_an_ambiguous_mapping(self):
        self.api.error = InterruptedError()
        with self.assertRaises(InterruptedError):
            self.router.topic("42", "Friends")
        self.assertIsNone(self.store.telegram_topic("123456", "7654321", "42"))

    def test_unknown_title_is_improved_when_contact_name_arrives(self):
        topic = self.router.topic("42", "Unnamed conversation")
        self.assertEqual(self.router.topic("42", "Alex Real"), topic)
        edits = [params for method, params in self.api.calls if method == "editForumTopic"]
        self.assertEqual(edits, [{"chat_id": "7654321", "message_thread_id": topic, "name": "Alex Real"}])
        self.assertFalse(self.store.telegram_topic("123456", "7654321", "42")["is_fallback"])

    def test_titles_fit_api_limits_with_unicode_and_no_placeholder_names(self):
        for text in ("🙂" * 200, "ą" * 200, "long name " * 40, "username", ""):
            title = topic_title(text)
            self.assertTrue(title)
            self.assertLessEqual(len(title.encode("utf-8")), 128)
        self.assertEqual(topic_title("username"), "Conversation")

    def test_local_repair_command_does_not_require_bot_token_or_send_requests(self):
        self.router.topic("42", "Friends")
        command = [sys.executable, str(ROOT / "telegram_topics.py"), "bind", "--bot-id", "123456", "--chat-id", "7654321", "--conversation", "42", "--topic-id", "456", "--name", "Friends"]
        result = subprocess.run(command, env=dict(os.environ, DATA_DIR=str(self.root)), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["topic_id"], 456)


class Response:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return json.dumps(self.data).encode()


class TopicDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_TOPICS": "true", "TELEGRAM_MODE": "deleted", "TELEGRAM_CHAT_ID": "7654321", "TELEGRAM_BOT_TOKEN": TOKEN}, clear=True)
        self.env.start()
        self.api = API()
        self.output = io.StringIO()
        self.failure = None
        def request(req, **kwargs):
            method = req.full_url.rsplit("/", 1)[-1]
            self.assertIn(method, {"getMe", "getChat", "getChatMember"})
            if self.failure:
                raise self.failure
            result = self.api(method, json.loads(req.data))
            if method == "getMe":
                result["username"] = "CorrectConfiguredBot"
            return Response({"ok": True, "result": result})
        self.network = patch("telegram_relay.urlopen", side_effect=request)
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.env.stop()

    def check(self):
        with contextlib.redirect_stdout(self.output):
            result = check_topics()
        self.assertNotIn(TOKEN, self.output.getvalue())
        return result

    def test_fresh_check_identifies_bot_and_capability_without_creating_or_sending(self):
        self.assertEqual(self.check(), 0)
        self.assertIn("Configured bot: @CorrectConfiguredBot", self.output.getvalue())
        self.assertIn("Telegram bot Threaded mode: enabled", self.output.getvalue())
        self.assertIn("Destination type: private", self.output.getvalue())
        self.assertEqual([method for method, params in self.api.calls], ["getMe", "getChat"])

    def test_botfather_enabled_but_logger_routing_disabled_is_explained(self):
        os.environ["TELEGRAM_TOPICS"] = "false"
        self.assertEqual(self.check(), 2)
        self.assertIn("Logger conversation topics: disabled", self.output.getvalue())
        self.assertIn("Telegram bot Threaded mode: enabled", self.output.getvalue())
        self.assertIn("TELEGRAM_TOPICS=true", self.output.getvalue())

    def test_disabled_bot_capability_reports_configured_bot_and_creates_nothing(self):
        self.api.enabled = False
        self.assertEqual(self.check(), 2)
        self.assertIn("Telegram bot Threaded mode: disabled", self.output.getvalue())
        self.assertIn("same bot in BotFather", self.output.getvalue())
        self.assertEqual(self.api.created, 0)

    def test_disabled_forwarding_makes_no_requests(self):
        os.environ["TELEGRAM_ENABLED"] = "false"
        self.assertEqual(self.check(), 2)
        self.assertIn("Logger forwarding: disabled", self.output.getvalue())
        self.assertEqual(self.api.calls, [])

    def test_provider_failure_is_redacted(self):
        from urllib.error import HTTPError
        self.failure = HTTPError("https://api.telegram.org/bot" + TOKEN, 401, TOKEN + "PRIVATE_DESCRIPTION", {}, None)
        self.assertEqual(self.check(), 2)
        self.assertIn("getMe HTTP 401", self.output.getvalue())
        self.assertNotIn("PRIVATE_DESCRIPTION", self.output.getvalue())


class TopicRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, started_at=1000, run_id="relay-topics")
        self.store.accept({"type": "metadata", "data": {"threads": [{"id": 42, "name": "Friends"}, {"id": 43, "name": "Work"}]}})
        self.env = patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_TOPICS": "true", "TELEGRAM_MODE": "all", "TELEGRAM_VIEW_ONCE": "true", "TELEGRAM_SEND_MEDIA": "true", "TELEGRAM_CHAT_ID": "7654321", "TELEGRAM_BOT_TOKEN": TOKEN})
        self.env.start()
        self.api = API()
        self.calls, self.reports = [], []
        self.mock = patch("telegram_relay.urlopen", side_effect=self.fake_open)
        self.mock.start()
        self.relay = TelegramRelay(self.reports.append, self.store)
        self.relay.interval = 0

    def tearDown(self):
        self.assertTrue(self.relay.close())
        self.mock.stop()
        self.env.stop()
        self.store.close()
        self.temp.cleanup()

    def fake_open(self, request, **options):
        body = request.data if isinstance(request.data, bytes) else b"".join(request.data)
        self.calls.append((request, body))
        method = request.full_url.rsplit("/", 1)[-1]
        if method == "sendDocument":
            result = {"message_id": 9}
        else:
            result = self.api(method, json.loads(body))
        return Response({"ok": True, "result": result})

    def accept(self, event):
        summary = self.store.accept(event)
        if summary:
            self.relay.submit(summary)

    def settle(self):
        deadline = time.monotonic() + 3
        while self.relay.items.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.relay.items.unfinished_tasks, 0)

    def sends(self):
        return [json.loads(body) for request, body in self.calls if request.full_url.endswith("/sendMessage")]

    def test_different_conversations_and_all_text_pages_go_to_their_own_topics(self):
        first = message()
        first["data"]["text"] = "🙂" * 4000
        self.accept(first)
        self.accept(message("m2", thread=43))
        self.settle()
        topics = [item["message_thread_id"] for item in self.sends()]
        self.assertGreater(len(topics), 2)
        self.assertTrue(all(item == topics[0] for item in topics[:-1]))
        self.assertNotEqual(topics[0], topics[-1])

    def test_deletion_and_late_media_followup_reuse_original_topic(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.settle()
        self.accept(deletion())
        self.settle()
        path = self.root / "media/test.png"
        path.parent.mkdir()
        path.write_bytes(PNG)
        self.store.update_media("42", "m1", 0, {"downloadStatus": "saved", "localPath": "media/test.png", "savedBytes": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()})
        self.relay.media_updated("42", "m1")
        self.settle()
        texts = self.sends()
        self.assertEqual([item["message_thread_id"] for item in texts], [101, 101])
        request, body = next((request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument"))
        multipart = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in multipart.iter_parts()}
        self.assertEqual(fields["message_thread_id"], b"101")
        self.assertEqual(fields["document"], PNG)
        self.assertEqual(self.api.created, 1)

    def test_edit_and_reaction_notices_stay_in_the_conversation_topic(self):
        self.accept(message())
        for kind, fields in (("messageEdit", {"newText": "edited"}), ("reaction", {"reaction": "👍"})):
            self.accept({"type": kind, "timestamp": 1002000, "data": {"messageId": "m1", "threadId": 42, **fields}})
        self.settle()
        self.assertEqual([item["message_thread_id"] for item in self.sends()], [101, 101, 101])

    def test_setup_failure_never_falls_back_to_general_chat(self):
        self.api.enabled = False
        self.accept(message())
        self.settle()
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.store.counts(), {"message": 1})
        self.assertIn("Threaded mode", " ".join(self.reports))
        self.assertNotIn(TOKEN, " ".join(self.reports))

    def test_no_topics_are_created_merely_by_starting_logger(self):
        self.assertEqual(self.calls, [])

    def test_username_destination_is_resolved_and_sends_use_verified_numeric_id(self):
        self.relay.topics.requested_chat = "@example_destination"
        self.relay.chat = "@example_destination"
        self.accept(message())
        self.settle()
        self.assertEqual(self.sends()[0]["chat_id"], "7654321")
        self.assertEqual(self.sends()[0]["message_thread_id"], 101)

    def test_http_rejections_are_classified_without_exposing_provider_text(self):
        from urllib.error import HTTPError
        self.mock.stop()
        self.mock = patch("telegram_relay.urlopen", side_effect=HTTPError("https://api.telegram.org/bot" + TOKEN, 403, "PRIVATE provider text", None, None))
        self.mock.start()
        with self.assertRaises(TelegramRejected) as error:
            self.relay._api("createForumTopic", {"chat_id": "7654321", "name": "Test"})
        self.assertEqual(error.exception.code, 403)
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertNotIn("PRIVATE", str(error.exception))


if __name__ == "__main__":
    unittest.main()
