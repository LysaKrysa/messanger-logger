from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_rpc import prepare_event
from event_store import EventStore
from view_messages import load_messages, render_message

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "MEDIA_KEY_MUST_NOT_BE_PERSISTED"


def msg(mid="new-1", *, sent=1001000, text="original text", **extras):
    data = {"id":mid,"threadId":42,"senderId":222,"text":text,"timestampMs":sent}
    data.update(extras)
    return {"type":"e2eeMessage","timestamp":1002000,"data":data}


def action(kind, *, mid="new-1", stamp=1003000, **extra):
    data = {"messageId":mid,"threadId":42,"senderId":222}
    data.update(extra)
    return {"type":kind,"timestamp":stamp,"data":data}


class ContentModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, own_id="12345", started_at=1000.0, run_id="test-run-one")

    def tearDown(self):
        if self.store:
            self.store.close()
        self.temp.cleanup()

    def test_historical_message_is_rejected_even_with_new_envelope_time(self):
        self.assertIsNone(self.store.accept(msg(sent=999999)))
        self.assertEqual(self.store.counts(), {})
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertFalse(list(self.store.logs.rglob("*_*_*")))

    def test_unknown_creation_time_is_not_assumed_to_be_new(self):
        event = msg(sent=0)
        event["timestamp"] = 1005000
        self.assertIsNone(self.store.accept(event))
        self.assertEqual(self.store.counts(), {})

    def test_cutoff_boundary_and_media_metadata(self):
        self.assertIsNotNone(self.store.accept(msg(sent=1000000, text="caption", attachments=[{
            "type":"image","fileName":"photo.jpg","mediaKey":PRIVATE,
        }])))
        self.assertEqual(self.store.counts(), {"message":1})
        payload = self.store.db.execute("SELECT payload FROM events").fetchone()[0]
        self.assertIn("caption", payload)
        self.assertIn("photo.jpg", payload)
        self.assertNotIn(PRIVATE, payload)
        self.assertTrue(list((self.store.logs / "runs" / "test-run-one").rglob("*")))

    def test_receipts_typing_presence_and_control_do_not_enter_chat_journal(self):
        for kind in ("readReceipt","e2eeReceipt","typing","presence","ready","e2eeConnected","disconnected"):
            with self.subTest(kind=kind):
                self.assertIsNone(self.store.accept(action(kind)))
        self.assertEqual(self.store.counts(), {})
        for kind in ("readReceipt","e2eeReceipt","typing","presence"):
            self.assertIsNone(prepare_event(action(kind)))

    def test_actions_with_explicit_old_timestamp_are_rejected(self):
        self.store.accept(msg())
        old = action("reaction", reaction="👍", timestampMs=999999)
        self.assertIsNone(self.store.accept(old))
        self.assertEqual(self.store.counts(), {"message":1})

    def test_new_run_has_fresh_counts_and_skips_previous_message_replay(self):
        self.store.accept(msg())
        self.store.close()
        self.store = EventStore(self.root, own_id="12345", started_at=2000.0, run_id="test-run-two")
        self.assertEqual(self.store.counts(), {})
        self.assertIsNone(self.store.accept(msg()))
        self.assertIsNotNone(self.store.accept(msg("new-2",sent=2001000)))
        self.assertEqual(self.store.counts(), {"message":1})
        run, rows = load_messages(self.root / "messenger-modern.sqlite3")
        self.assertEqual(run, "test-run-two")
        self.assertEqual([row["message_id"] for row in rows], ["new-2"])

    def test_deleted_only_view_retains_text_media_and_latest_edit(self):
        self.store.accept(msg(attachments=[{"type":"file","fileName":"notes.pdf","mimeType":"application/pdf"}]))
        self.assertEqual(load_messages(self.root / "messenger-modern.sqlite3", deleted_only=True)[1], [])
        self.store.accept(action("messageEdit", newText="edited text", timestampMs=1002000))
        self.store.accept(action("messageUnsend", isE2EE=True))
        run, rows = load_messages(self.root / "messenger-modern.sqlite3", deleted_only=True)
        self.assertEqual(run, "test-run-one")
        self.assertEqual(len(rows), 1)
        item = rows[0]
        self.assertTrue(item["deleted"])
        self.assertEqual(item["original_text"], "original text")
        self.assertEqual(item["text"], "edited text")
        self.assertEqual(item["attachments"][0]["fileName"], "notes.pdf")
        rendered = render_message(item, ZoneInfo("Europe/Warsaw"))
        for expected in ("DELETED / UNSENT", "original text", "edited text", "notes.pdf"):
            self.assertIn(expected, rendered)

    def test_viewer_after_logger_stops_does_not_change_the_database(self):
        import hashlib
        self.store.accept(msg())
        self.store.accept(action("messageUnsend"))
        self.store.close()
        self.store = None
        database = self.root / "messenger-modern.sqlite3"
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        _, rows = load_messages(database, deleted_only=True)
        self.assertEqual(rows[0]["original_text"], "original text")
        after = hashlib.sha256(database.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_new_deletion_can_mark_a_message_captured_by_previous_run(self):
        self.store.accept(msg())
        self.store.close()
        self.store = EventStore(self.root, own_id="12345", started_at=2000.0, run_id="test-run-two")
        self.store.accept(action("messageUnsend", stamp=2001000, isE2EE=True))
        self.assertEqual(self.store.counts(), {"unsend":1})
        _, rows = load_messages(self.root / "messenger-modern.sqlite3", deleted_only=True)
        self.assertEqual(rows[0]["original_text"], "original text")
        self.assertEqual(rows[0]["deleted_run_id"], "test-run-two")

    def test_unattributed_deletion_waits_without_persisting_a_tombstone(self):
        self.store.accept(action("messageUnsend", mid="not-captured"))
        _, rows = load_messages(self.root / "messenger-modern.sqlite3", deleted_only=True)
        self.assertEqual(rows, [])
        self.assertEqual(self.store.counts(), {})
        self.assertEqual(len(self.store.pending_deletions), 1)

    def test_deletion_before_delayed_message_capture_preserves_flag(self):
        self.store.accept(action("messageUnsend"))
        self.store.accept(msg())
        _, rows = load_messages(self.root / "messenger-modern.sqlite3", deleted_only=True)
        self.assertTrue(rows[0]["deleted"])
        self.assertEqual(rows[0]["original_text"], "original text")

    def test_unresolved_deletion_is_linked_when_the_message_arrives(self):
        deleted = action("messageUnsend")
        deleted["data"]["threadId"] = 0
        self.store.accept(deleted)
        self.store.accept(msg())
        _, rows = load_messages(self.root / "messenger-modern.sqlite3", deleted_only=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["thread_id"], "42")
        self.assertEqual(rows[0]["original_text"], "original text")

    def test_duplicate_deletion_does_not_inflate_counts(self):
        self.store.accept(msg())
        self.store.accept(action("messageUnsend", stamp=1002000))
        self.assertIsNone(self.store.accept(action("messageUnsend", stamp=1003000)))
        self.assertEqual(self.store.counts(), {"message":1,"unsend":1})

    def test_mention_metadata_contains_resolved_names_without_protocol_payloads(self):
        self.store.accept({"type":"metadata","data":{"contacts":[{"id":333,"name":"Sam"}],"threads":[]}})
        self.store.accept(msg(mentions=[{"userId":333,"offset":0,"length":3,"key":PRIVATE}]))
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        self.assertEqual(rows[0]["metadata"]["mentions"][0]["name"], "Sam")
        self.assertNotIn(PRIVATE, json.dumps(rows))

    def test_sender_display_name_and_username_are_merged(self):
        self.store.accept({"type":"metadata","data":{"contacts":[{"id":222,"name":"Alex"}],"threads":[]}})
        self.store.accept({"type":"metadata","data":{"contacts":[{"id":222,"name":"","username":"alex.example"}],"threads":[]}})
        self.store.accept(msg())
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        self.assertEqual(rows[0]["sender"], "Alex (@alex.example)")

    def test_unresolved_sender_does_not_display_an_arbitrary_number(self):
        self.store.accept(msg())
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        text = render_message(rows[0], ZoneInfo("Europe/Warsaw"))
        self.assertIn("Sender: Unknown participant", text)
        self.assertNotIn("User 222", text)

    def test_raw_encrypted_push_name_is_used_without_storing_raw_body(self):
        event = {"type":"raw","data":{"from":"whatsmeow","type":"FBMessage","data":{
            "Info":{"Sender":"222:7@msgr","PushName":"Alex"},
            "Message":{"text":PRIVATE}, "Transport":{"key":PRIVATE},
        }}}
        metadata = prepare_event(event)
        self.assertNotIn(PRIVATE, json.dumps(metadata))
        self.store.accept(metadata)
        self.store.accept(msg())
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        self.assertEqual(rows[0]["sender"], "Alex")

    def test_both_contact_table_variants_supply_display_names_and_usernames(self):
        event = {"type":"raw","data":{"from":"lightspeed","type":"LSTable","data":{
            "LSVerifyContactRowExists":[{"ContactId":222,"Name":"Alex","SecondaryName":"alex.example"}],
        }}}
        self.store.accept(prepare_event(event))
        self.store.accept(msg())
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        self.assertEqual(rows[0]["sender"], "Alex (@alex.example)")


class MigrationTests(unittest.TestCase):
    def test_additive_schema_upgrade_preserves_captured_content(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            with sqlite3.connect(root / "messenger-modern.sqlite3") as db:
                db.executescript('''
                    CREATE TABLE contacts(id TEXT PRIMARY KEY,name TEXT NOT NULL);
                    CREATE TABLE messages(thread_id TEXT NOT NULL,message_id TEXT NOT NULL,sender_id TEXT,text TEXT,transport TEXT NOT NULL,PRIMARY KEY(thread_id,message_id));
                    INSERT INTO contacts VALUES ('222','Alex');
                    INSERT INTO messages VALUES ('42','old-1','222','already captured','encrypted');
                ''')
            store = EventStore(root, own_id="12345", started_at=2000.0, run_id="new-run")
            try:
                backup = root / "messenger-modern.before-content.sqlite3"
                self.assertTrue(backup.is_file())
                with sqlite3.connect(backup) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT text FROM messages").fetchone()[0], "already captured")
                    self.assertNotIn("deleted", {row[1] for row in snapshot.execute("PRAGMA table_info(messages)")})
                self.assertEqual(store.counts(), {})
                self.assertEqual(store.db.execute("SELECT text FROM messages").fetchone()[0], "already captured")
                store.accept(action("messageUnsend", mid="old-1", stamp=2001000))
                _, rows = load_messages(root / "messenger-modern.sqlite3", deleted_only=True)
                self.assertEqual(rows[0]["text"], "already captured")
                self.assertIn("already captured", render_message(rows[0], ZoneInfo("Europe/Warsaw")))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
