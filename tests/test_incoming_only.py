"""Admission tests use an authenticated identity, including delayed encrypted events."""
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from event_store import EventStore
from logger import LoggerRuntime
from view_messages import load_messages


def message(mid="incoming", sender="222", kind="message", **data):
    return {"type": kind, "timestamp": 1001000, "data": {
        "id": mid, "threadId": 42, "senderId": sender, "timestampMs": 1001000,
        "text": "other person's message", **data,
    }}


def deletion(mid, tid=42):
    return {"type": "messageUnsend", "timestamp": 1002000, "data": {"messageId": mid, "threadId": tid}}


class IncomingOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, own_id="111", started_at=1000, run_id="incoming", clock=lambda: 1003)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_owner_text_media_and_sender_metadata_never_reach_storage(self):
        for index, kind in enumerate(("message", "e2eeMessage")):
            event = message(str(index), "111", kind, text="OWNER_SECRET", senderName="OWNER_SECRET", attachments=[{
                "type": "voice", "fileName": "OWNER_SECRET.ogg", "url": "https://example.com/OWNER_SECRET",
            }])
            self.assertIsNone(self.store.accept(event))
            self.assertIsNone(self.store.accept(deletion(str(index))))
        self.assertEqual(self.store.counts(), {})
        self.assertNotIn("OWNER_SECRET", "\n".join(self.store.db.iterdump()))
        self.assertFalse(any(path.is_file() for path in self.store.logs.rglob("*")))

    def test_owner_encrypted_jid_and_missing_sender_are_excluded(self):
        for sender in ("111:9@msgr", None, ""):
            self.assertIsNone(self.store.accept(message(sender="", kind="e2eeMessage", senderJid=sender)))
        incoming = message(sender="", kind="e2eeMessage", senderJid="222:7@msgr")
        self.assertIsNotNone(self.store.accept(incoming))
        self.assertEqual(self.store.relay_snapshot("42", "incoming")["sender_id"], "222")

    def test_group_members_are_kept_and_owner_actions_are_excluded(self):
        self.store.accept(message(threadType=2, threadName="Friends"))
        self.store.accept(message("second", "333", "e2eeMessage", threadType=2))
        for kind, fields in (("messageEdit", {"newText": "OWNER_SECRET"}), ("reaction", {"reaction": "OWNER_SECRET"})):
            self.assertIsNone(self.store.accept({"type": kind, "timestamp": 1002000, "data": {
                "threadId": 42, "messageId": "incoming", "senderId": "111", **fields,
            }}))
        self.assertEqual(self.store.counts(), {"message": 2})
        self.assertNotIn("OWNER_SECRET", "\n".join(self.store.db.iterdump()))

    def test_unattributed_deletion_is_linked_only_after_incoming_original(self):
        self.assertIsNone(self.store.accept(deletion("incoming", tid=0)))
        self.assertEqual(self.store.counts(), {})
        summary = self.store.accept(message())
        self.assertEqual(summary["following"]["type"], "unsend")
        self.assertTrue(self.store.relay_snapshot("42", "incoming")["deleted"])
        self.assertEqual(self.store.counts(), {"message": 1, "unsend": 1})
        self.assertIsNone(self.store.accept(deletion("incoming")))

    def test_delayed_owner_original_does_not_create_deletion_records(self):
        self.store.accept(deletion("own", tid=0))
        self.store.accept(message("own", "111", "e2eeMessage", text="OWNER_SECRET"))
        self.assertEqual(self.store.counts(), {})
        self.assertEqual(self.store.pending_deletions, {})

    def test_quoted_owner_and_unknown_text_is_removed_but_incoming_quotes_survive(self):
        for index, sender in enumerate(("111", "", "333")):
            mid = "reply-" + str(index)
            self.store.accept(message(mid, replyTo={"messageId": "quote", "senderId": sender, "text": "QUOTED_SECRET"}))
            snapshot = self.store.relay_snapshot("42", mid)
            self.assertEqual(snapshot["reply"]["text"], "QUOTED_SECRET" if sender == "333" else "")
        payloads = [json.loads(row[0]) for row in self.store.db.execute("SELECT payload FROM events")]
        self.assertEqual([entry["reply"]["text"] for entry in payloads], ["", "", "QUOTED_SECRET"])

    def test_legacy_owner_records_are_hidden_and_cannot_download_or_forward(self):
        self.store.accept(message())
        self.store.db.execute("INSERT INTO messages(thread_id,message_id,sender_id,text,original_text,transport,run_id,captured_at) VALUES ('42','old-own','111','OWNER_SECRET','OWNER_SECRET','regular','incoming',1001)")
        self.store.db.commit()
        self.store.accept(message("reply", replyTo={"messageId": "old-own", "text": "OWNER_SECRET"}))
        self.assertIsNone(self.store.relay_snapshot("42", "old-own"))
        self.assertFalse(self.store.media_available("42", "old-own"))
        self.assertFalse(self.store.update_media("42", "old-own", 0, {"downloadStatus": "saved"}))
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        self.assertEqual({row["message_id"] for row in rows}, {"incoming", "reply"})
        self.assertNotIn("OWNER_SECRET", json.dumps(rows))

    def test_pending_deletions_expire_without_persistent_message_content(self):
        self.store.accept(deletion("unknown"), received=1002)
        self.store.accept(message(), received=1123)
        self.assertEqual(self.store.pending_deletions, {})
        self.assertEqual(self.store.counts(), {"message": 1})

    def test_runtime_counts_and_dispatches_only_incoming_content_and_its_deletion(self):
        runtime = LoggerRuntime(self.root, "UTC", threading.Event(), started_at=1000)
        runtime.store = self.store
        runtime.bridge = SimpleNamespace(events=queue.Queue())
        runtime.relay, runtime.media, runtime.names = Mock(), Mock(), Mock()
        runtime.report = Mock()
        for event in (message("own", "111"), deletion("incoming"), message(kind="e2eeMessage")):
            runtime.bridge.events.put(event)
        runtime.finish_consumer.set()
        runtime._consume()
        self.assertIsNone(runtime.consumer_failure)
        self.assertEqual((runtime.messages_this_run, runtime.deleted_this_run, runtime.logged_this_run), (1, 1, 2))
        self.assertEqual([call.args[0]["type"] for call in runtime.relay.submit.call_args_list], ["message", "unsend"])
        self.assertEqual([call.args[1]["type"] for call in runtime.media.submit.call_args_list], ["message", "unsend"])


if __name__ == "__main__":
    unittest.main()
