from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_rpc import prepare_event
from contact_names import ContactNames
from event_store import EventStore
from identities import display_name
from view_messages import load_messages

ROOT = Path(__file__).resolve().parents[1]


class ContactBridge:
    def __init__(self, result=None, fail=False):
        self.calls = []
        self.result = result or {"id": 222, "name": "Alex Real", "username": "alex.real"}
        self.fail = fail

    def call(self, method, params, **options):
        self.calls.append((method, params))
        if self.fail:
            raise ValueError("PRIVATE error text")
        return self.result


class NamesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, own_id="12345", started_at=1000, run_id="names-test")
        self.names = None

    def tearDown(self):
        if self.names:
            self.assertTrue(self.names.close())
        self.store.close()
        self.temp.cleanup()

    def test_placeholder_names_are_not_displayed_as_real_identities(self):
        for label in ("username", "USERNAME", "Username", "user name", "unknown user", "<username>", "123456"):
            self.store.accept({"type": "metadata", "data": {"contacts": [{"id": 222, "name": label, "username": label}]}})
            self.assertEqual(self.store._name("222"), "Unknown participant")
            self.assertEqual(display_name(label, label), "Unknown participant")

    def test_weak_push_name_does_not_overwrite_a_resolved_name(self):
        self.store.accept({"type": "metadata", "data": {"contacts": [{"id": 222, "name": "Alex Real"}]}})
        raw = {"type": "raw", "data": {"from": "whatsmeow", "type": "FBMessage", "data": {"Info": {"Sender": "222@msgr", "PushName": "Alex"}}}}
        self.store.accept(prepare_event(raw))
        self.assertEqual(self.store._name("222"), "Alex Real")

    def test_typed_bootstrap_metadata_extracts_names_without_private_payloads(self):
        incoming = {"type": "metadata", "data": {"contacts": [{"id": 222, "name": "username", "firstName": "Alex", "username": "alex.real", "secret": "PRIVATE"}], "threads": [{"id": 42, "name": "Friends"}], "messages": ["PRIVATE"]}}
        safe = prepare_event(incoming)
        self.assertNotIn("PRIVATE", str(safe))
        self.store.accept(safe)
        self.assertEqual(self.store._name("222"), "Alex (@alex.real)")
        self.assertEqual(self.store._thread_name("42"), "Friends")

    def test_read_only_lookup_supplies_names_and_is_not_repeated(self):
        bridge = ContactBridge()
        self.names = ContactNames(bridge, self.store, threading.Event())
        self.names.start()
        self.names.wait_for("222", timeout=2)
        self.names.wait_for("222", timeout=2)
        self.assertEqual(bridge.calls, [("getUserInfo", {"userId": 222})])
        self.assertEqual(self.store._name("222"), "Alex Real (@alex.real)")
        self.assertEqual(self.store.counts(), {})

    def test_wrong_identity_or_lookup_failure_does_not_invent_names(self):
        for bridge in (ContactBridge(result={"id": 999, "name": "Wrong person"}), ContactBridge(fail=True)):
            self.names = ContactNames(bridge, self.store, threading.Event())
            self.names.start()
            self.names.wait_for("222", timeout=2)
            self.names.wait_for("222", timeout=2)
            self.assertEqual(len(bridge.calls), 1)
            self.assertEqual(self.store._name("222"), "Unknown participant")
            self.assertTrue(self.names.close())

    def test_invalid_or_own_ids_never_trigger_contact_requests(self):
        bridge = ContactBridge()
        self.names = ContactNames(bridge, self.store, threading.Event())
        self.names.start()
        for uid in ("", "0", "12345", "-1", "not-an-id", "9999999999999999999"):
            self.assertIsNone(self.names.submit(uid))
        self.assertEqual(bridge.calls, [])

    def test_cached_placeholder_is_cleared_when_database_reopens(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO contacts(id,name,username) VALUES ('222','username','username')")
        self.store.close()
        self.store = EventStore(self.root, own_id="12345", started_at=2000, run_id="after-upgrade")
        self.assertEqual(self.store._name("222"), "Unknown participant")
        self.assertTrue(self.store.contact_needed("222"))
        self.store.accept({"type": "metadata", "data": {"contacts": [{"id": 222, "name": "Alex", "priority": 1}]}})
        self.assertEqual(self.store._name("222"), "Alex")

    def test_new_view_once_metadata_is_retained_but_keys_are_not(self):
        self.store.accept({"type": "e2eeMessage", "timestamp": 1001000, "data": {"id": "once", "senderId": 222, "senderName": "Alex", "threadId": 42, "timestampMs": 1001000, "attachments": [{"type": "image", "viewOnce": True, "viewMode": "once", "mediaKey": "PRIVATE"}]}})
        rows = load_messages(self.root / "messenger-modern.sqlite3")[1]
        self.assertEqual(rows[0]["sender"], "Alex")
        self.assertTrue(rows[0]["attachments"][0]["viewOnce"])
        self.assertEqual(rows[0]["attachments"][0]["viewMode"], "once")
        self.assertNotIn("PRIVATE", str(rows))


if __name__ == "__main__":
    unittest.main()
