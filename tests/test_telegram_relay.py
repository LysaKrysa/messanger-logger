from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_store import EventStore
from media_saver import MediaSaver
from telegram_relay import TelegramRelay, TelegramConfigurationError, chunks
from telegram_format import RichText, units, message_card
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:" + "SYNTHETIC_BOT_TOKEN_NOT_REAL" * 2
PNG = b"\x89PNG\r\n\x1a\nlocal synthetic file"


def message(mid="m1", attachments=None):
    return {"type": "message", "timestamp": 1001000, "data": {
        "id": mid, "threadId": 42, "senderId": 222, "timestampMs": 1001000,
        "text": "original <literal> & text", "attachments": attachments or [],
    }}


def deletion(mid="m1", tid=42):
    return {"type": "messageUnsend", "timestamp": 1002000, "data": {"threadId": tid, "messageId": mid}}


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return b'{"ok":true,"result":{}}'


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, started_at=1000, run_id="telegram-run")
        self.store.accept({"type": "metadata", "data": {"contacts": [{"id": 222, "name": "Alex", "username": "alex.user"}], "threads": [{"id": 42, "name": "Conversation"}]}})
        self.env = patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_MODE": "deleted", "TELEGRAM_SEND_MEDIA": "true", "TELEGRAM_VIEW_ONCE": "false", "TELEGRAM_TOPICS": "false", "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "123456"})
        self.env.start()
        self.calls, self.reports = [], []
        self.network_error = None
        self.mock = patch("telegram_relay.urlopen", side_effect=self.fake_open)
        self.mock.start()
        self.relay = TelegramRelay(self.reports.append, self.store, timezone="UTC")
        self.relay.interval = 0

    def tearDown(self):
        self.assertTrue(self.relay.close())
        self.mock.stop()
        self.env.stop()
        self.store.close()
        self.temp.cleanup()

    def fake_open(self, request, **options):
        if self.network_error:
            raise self.network_error
        body = request.data if isinstance(request.data, bytes) else b"".join(request.data)
        self.calls.append((request, body))
        return Response()

    def accept(self, event):
        result = self.store.accept(event)
        if result:
            self.relay.submit(result)
        return result

    def settle(self):
        deadline = time.monotonic() + 3
        while self.relay.items.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.relay.items.unfinished_tasks, 0)

    def texts(self):
        return [json.loads(body)["text"] for request, body in self.calls if request.full_url.endswith("/sendMessage")]

    def save_media(self, mid="m1", index=0, data=PNG):
        relative = Path("media") / "telegram-run" / (mid + ".png")
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.store.update_media("42", mid, index, {"downloadStatus": "saved", "localPath": relative.as_posix(), "savedBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})

    def test_deleted_mode_sends_captured_text_names_times_and_edit(self):
        self.accept(message())
        self.accept({"type": "reaction", "timestamp": 1001001, "data": {"threadId": 42, "messageId": "m1", "reaction": "👍"}})
        self.assertEqual(self.calls, [])
        self.accept({"type": "messageEdit", "timestamp": 1001500, "data": {"threadId": 42, "messageId": "m1", "newText": "latest edit"}})
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.texts()), 1)
        for text in ("Deleted message", "Conversation", "Alex (@alex.user)", "original <literal> & text", "latest edit", "Deleted  "):
            self.assertIn(text, self.texts()[0])
        self.assertNotIn("parse_mode", json.loads(self.calls[0][1]))

    def test_duplicate_deletions_and_media_callbacks_do_not_resend(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        self.assertIsNone(self.accept(deletion()))
        for _ in range(3):
            self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([row[0] for row in self.store.db.execute("SELECT status FROM telegram_attempts")], ["sent", "sent"])

    def test_saved_media_is_uploaded_as_exact_file_bytes(self):
        self.accept(message(attachments=[{"type": "image", "fileName": "photo.png", "url": "https://fbcdn.net/PRIVATE_SIGNED_URL"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        uploads = [(request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument")]
        self.assertEqual(len(uploads), 1)
        request, body = uploads[0]
        self.assertIn(PNG, body)
        self.assertIn(b'name="document"', body)
        self.assertIn(b'filename="m1.png"', body)
        self.assertEqual(int(request.get_header("Content-length")), len(body))
        self.assertNotIn("PRIVATE_SIGNED_URL", " ".join(self.texts()))

    def test_media_finishing_after_deletion_sends_file_without_repeating_text(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Downloading…", self.texts()[0])
        self.save_media()
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.texts()), 1)
        self.assertTrue(self.calls[1][0].full_url.endswith("/sendDocument"))

    def test_media_saver_callback_reaches_deleted_relay(self):
        import base64
        class FakeBridge:
            def call(self, *args, **kwargs):
                return {"data": base64.b64encode(PNG).decode()}
        saver = MediaSaver(FakeBridge(), self.store, self.reports.append, threading.Event(), on_update=self.relay.media_updated)
        event = message(attachments=[{"type": "image", "url": "https://fbcdn.net/photo"}])
        summary = self.accept(event)
        self.accept(deletion())
        self.settle()
        try:
            saver.submit(event, summary)
            saver.start()
            deadline = time.monotonic() + 3
            while saver.items.unfinished_tasks and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(saver.saved, 1)
            self.settle()
            self.assertEqual(len(self.calls), 2)
        finally:
            saver.close()

    def test_unknown_deleted_message_has_explicit_unavailable_notice(self):
        self.accept(deletion("not-captured"))
        self.settle()
        self.assertIn("cannot be recovered", self.texts()[0])

    def test_late_capture_can_follow_an_unavailable_notice(self):
        self.relay.send_media = False
        self.accept(deletion())
        self.settle()
        self.accept(message())
        self.settle()
        self.assertEqual(len(self.texts()), 2)
        self.assertIn("cannot be recovered", self.texts()[0])
        self.assertIn("original <literal>", self.texts()[1])

    def test_enabling_relay_does_not_scan_previous_deletions(self):
        self.store.accept(message())
        self.store.accept(deletion())
        time.sleep(0.05)
        self.assertEqual(self.calls, [])

    def test_previous_run_deletions_do_not_forward_on_media_callback(self):
        self.store.accept(message(attachments=[{"type": "image"}]))
        self.store.accept(deletion())
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE messages SET deleted_run_id='earlier-run'")
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(self.calls, [])

    def test_failed_send_is_redacted_and_not_automatically_retried(self):
        self.network_error = RuntimeError(TOKEN + " private original message")
        self.accept(message())
        self.accept(deletion())
        self.settle()
        self.network_error = None
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.db.execute("SELECT status FROM telegram_attempts").fetchone()[0], "failed")
        self.assertNotIn(TOKEN, " ".join(self.reports))
        self.assertNotIn("private original", " ".join(self.reports))

    def test_all_mode_preserves_regular_content_relay(self):
        self.relay.mode = "all"
        self.accept(message())
        self.settle()
        self.assertIn("original <literal> & text", self.texts()[0])
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.texts()), 2)
        self.assertIn("Deleted message", self.texts()[1])

    def test_send_media_false_sends_text_only(self):
        self.relay.send_media = False
        self.accept(message(attachments=[{"type": "image"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0][0].full_url.endswith("/sendMessage"))

    def test_large_upload_stays_local_with_notice(self):
        self.accept(message(attachments=[{"type": "file", "fileName": "huge.bin"}]))
        self.store.update_media("42", "m1", 0, {"downloadStatus": "saved", "localPath": "media/large.bin", "savedBytes": 50_000_001, "sha256": "hash"})
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.texts()), 2)
        self.assertIn("exceeds Telegram", self.texts()[1])
        self.assertFalse(any(request.full_url.endswith("/sendDocument") for request, _ in self.calls))

    def test_modified_media_is_not_uploaded(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.save_media()
        (self.root / "media/telegram-run/m1.png").write_bytes(b"X" * len(PNG))
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("failed", [row[0] for row in self.store.db.execute("SELECT status FROM telegram_attempts")])

    def test_external_path_is_not_uploaded(self):
        outside = self.root / "private-state"
        outside.write_bytes(PNG)
        media = {"localPath": "private-state", "savedBytes": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()}
        with self.assertRaises(ValueError):
            self.relay._document(media, "caption")
        self.assertEqual(self.calls, [])

    def test_failed_media_has_followup_status_without_resending_original(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.accept(deletion())
        self.settle()
        self.store.update_media("42", "m1", 0, {"downloadStatus": "failed", "downloadError": "download_failed"})
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.texts()), 2)
        self.assertIn("Local download: failed", self.texts()[1])

    def test_long_unicode_text_is_split_without_losing_characters(self):
        text = "🙂" * 4000 + "abc"
        parts = list(chunks(text))
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts))
        self.relay._text(text)
        self.assertEqual("".join(self.texts()), text)

    def test_disabled_relay_never_queues_or_contacts_api(self):
        self.relay.enabled = False
        self.accept(message())
        self.accept(deletion())
        self.settle()
        self.assertEqual(self.calls, [])

    def test_configuration_errors_do_not_expose_token(self):
        for values in ({"TELEGRAM_BOT_TOKEN": "bad" + TOKEN}, {"TELEGRAM_MODE": "invalid"}):
            with patch.dict(os.environ, values), self.assertRaises(TelegramConfigurationError) as error:
                TelegramRelay(self.reports.append, self.store)
            self.assertNotIn(TOKEN, str(error.exception))

    def test_rate_pacing_waits_between_api_calls_and_can_be_cancelled(self):
        self.relay.interval = 1.05
        with patch("telegram_relay.time.monotonic", side_effect=[10, 10, 10.2, 11.05]), patch.object(self.relay.stopping, "wait", return_value=False) as wait:
            self.relay._text("first")
            self.relay._text("second")
        self.assertEqual(wait.call_args_list[0].args, (0.0,))
        self.assertAlmostEqual(wait.call_args_list[1].args[0], 0.85)
        with patch.object(self.relay.stopping, "wait", return_value=True), self.assertRaises(InterruptedError):
            self.relay._post("sendMessage", b"{}")
        self.assertEqual(len(self.calls), 2)

    def test_queue_overflow_does_not_interrupt_local_deletion_capture(self):
        self.relay.close()
        self.relay = TelegramRelay(self.reports.append, self.store, start=False)
        self.relay.items = queue.Queue(maxsize=1)
        self.relay.items.put(("summary", {}))
        self.accept(message())
        self.assertIsNotNone(self.accept(deletion()))
        self.assertTrue(self.store.relay_snapshot("42", "m1")["deleted"])
        self.assertIn("queue full", " ".join(self.reports))
        self.assertEqual(self.calls, [])

    def test_formatted_notification_uses_entities_and_preserves_literal_text(self):
        self.accept(message())
        self.accept(deletion())
        self.settle()
        payload = json.loads(self.calls[0][1])
        self.assertNotIn("parse_mode", payload)
        self.assertTrue(any(item["type"] == "bold" for item in payload["entities"]))
        quote = next(item for item in payload["entities"] if item["type"] == "blockquote")
        raw = payload["text"].encode("utf-16-le")
        quoted = raw[quote["offset"]*2:(quote["offset"]+quote["length"])*2].decode("utf-16-le")
        self.assertEqual(quoted, "original <literal> & text")
        self.assertNotIn("<b>", payload["text"])

    def test_long_formatted_messages_keep_valid_entity_offsets_per_page(self):
        original = "🙂<b>literal</b> & " * 700
        rich = RichText().add("🗑 Deleted message", "bold").add("\n\n").quote(original)
        self.relay._text(rich)
        payloads = [json.loads(body) for request, body in self.calls]
        self.assertEqual("".join(item["text"] for item in payloads), rich.text)
        self.assertGreater(len(payloads), 1)
        for payload in payloads:
            self.assertLessEqual(units(payload["text"]), 3500)
            encoded = payload["text"].encode("utf-16-le")
            for entity in payload["entities"]:
                self.assertGreater(entity["length"], 0)
                self.assertLessEqual(entity["offset"] + entity["length"], units(payload["text"]))
                encoded[entity["offset"]*2:(entity["offset"]+entity["length"])*2].decode("utf-16-le")

    def test_view_once_opt_in_forwards_immediately_and_keeps_deletion_distinct(self):
        self.relay.view_once = True
        self.accept(message(attachments=[{"type": "image", "viewOnce": True, "viewMode": "once"}]))
        self.settle()
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("View-once message", self.texts()[0])
        self.assertNotIn("Deleted message", self.texts()[0])
        self.save_media()
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.calls), 2)
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.texts()), 2)
        self.assertIn("Deleted message", self.texts()[1])
        self.assertEqual(sum(request.full_url.endswith("/sendDocument") for request, _ in self.calls), 1)

    def test_view_once_does_not_expand_deleted_only_routing_without_opt_in(self):
        self.accept(message(attachments=[{"type": "image", "viewOnce": True, "viewMode": "once"}]))
        self.settle()
        self.assertEqual(self.calls, [])

    def test_formatted_caption_contains_valid_entities_and_no_raw_paths(self):
        from email.parser import BytesParser
        from email.policy import default
        self.accept(message(attachments=[{"type": "image", "fileName": "photo.png"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        request, body = next((request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument"))
        message_body = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message_body.iter_parts()}
        caption = fields["caption"].decode()
        entities = json.loads(fields["caption_entities"])
        self.assertIn("Deleted attachment", caption)
        self.assertIn("Alex (@alex.user)", caption)
        self.assertNotIn("/data/", caption)
        self.assertTrue(entities)
        self.assertTrue(all(entity["offset"]+entity["length"] <= units(caption) for entity in entities))


if __name__ == "__main__":
    unittest.main()
