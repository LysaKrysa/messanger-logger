from __future__ import annotations

import base64
import contextlib
from datetime import datetime
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_store import EventStore
from media_saver import MediaSaver
from retention import RETENTION_SECONDS, cleanup_data, cleanup_status
from telegram_relay import TelegramRelay

ROOT = Path(__file__).resolve().parents[1]
OLD = "EXPIRED_MESSAGE_SECRET_94a38"
FRESH = "FRESH_MESSAGE_STAYS_879f1"


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.now = 1_000_000.0
        self.started = self.now - 1
        self.store = self.open_store()

    def open_store(self):
        return EventStore(self.root, own_id="12345", started_at=self.started, run_id="retention-run", retention_seconds=RETENTION_SECONDS, clock=lambda: self.now)

    def tearDown(self):
        if self.store:
            self.store.close()
        self.temp.cleanup()

    def message(self, mid="old", text=OLD, thread=42, **extras):
        data = {"id": mid, "threadId": thread, "senderId": 222, "text": text, "timestampMs": int(self.now * 1000)}
        data.update(extras)
        return {"type": "e2eeMessage", "timestamp": int(self.now * 1000), "data": data}

    def logs(self):
        return "\n".join(path.read_text() for path in self.store.logs.rglob("*") if path.is_file())

    def test_old_and_fresh_records_in_the_same_daily_file_are_pruned_exactly(self):
        self.store.accept(self.message())
        self.now += 100
        self.store.accept(self.message("fresh", FRESH))
        self.now += RETENTION_SECONDS - 50
        result = self.store.cleanup_expired()
        self.assertEqual(result["messages"], 1)
        self.assertNotIn(OLD, self.logs())
        self.assertIn(FRESH, self.logs())
        self.assertEqual(self.store.db.execute("SELECT message_id FROM messages").fetchall(), [("fresh",)])
        self.store.close()
        self.store = self.open_store()
        self.assertEqual(self.logs().count(FRESH), 1)

    def test_related_edits_reactions_and_structured_reply_copies_are_removed(self):
        self.store.accept(self.message())
        self.now += RETENTION_SECONDS - 10
        self.store.accept({"type": "messageEdit", "timestamp": int(self.now * 1000), "data": {"threadId": 42, "messageId": "old", "newText": OLD + " edited"}})
        self.store.accept({"type": "reaction", "timestamp": int(self.now * 1000), "data": {"threadId": 42, "messageId": "old", "actorId": 333, "reaction": "👍"}})
        self.store.accept(self.message("fresh", FRESH, replyTo={"messageId": "old", "senderId": 222, "text": OLD}))
        self.now += 20
        self.store.cleanup_expired()
        self.assertNotIn(OLD, self.logs())
        payloads = " ".join(row[0] for row in self.store.db.execute("SELECT payload FROM events"))
        self.assertNotIn(OLD, payloads)
        self.assertEqual(self.store.db.execute("SELECT reply_json FROM messages").fetchone()[0], "{}")
        self.assertEqual(self.store.counts(), {"message": 1})

    def test_expired_media_backups_and_orphans_are_deleted_but_config_is_preserved(self):
        self.store.accept(self.message(attachments=[{"type": "image"}]))
        media = self.root / "media/old/photo.png"
        media.parent.mkdir(parents=True)
        media.write_bytes(OLD.encode())
        self.store.update_media("42", "old", 0, {"downloadStatus": "saved", "localPath": "media/old/photo.png"})
        orphan = self.root / "media/orphan.bin"
        orphan.write_bytes(OLD.encode())
        os.utime(orphan, (self.now, self.now))
        backup = self.root / "messenger-modern.before-content.sqlite3"
        backup.write_bytes(OLD.encode())
        config = self.root / "messenger.env"
        config.write_text("KEEP_COOKIE_CONFIG")
        device = self.root / "messenger-modern-state/12345/device.json"
        device.parent.mkdir(parents=True)
        device.write_text("KEEP_DEVICE_STATE")
        self.now += RETENTION_SECONDS + 1
        self.store.cleanup_expired()
        self.assertFalse(media.exists())
        self.assertFalse(orphan.exists())
        self.assertFalse(backup.exists())
        self.assertEqual(config.read_text(), "KEEP_COOKIE_CONFIG")
        self.assertEqual(device.read_text(), "KEEP_DEVICE_STATE")

    def test_topic_routing_ids_survive_but_old_names_and_send_attempts_expire(self):
        self.store.accept({"type": "metadata", "data": {"contacts": [{"id": 222, "name": OLD}], "threads": [{"id": 42, "name": OLD}]}})
        self.store.accept(self.message())
        self.store.claim_telegram("42", "old", "text:captured")
        self.store.save_telegram_topic("111", "222", "42", 101, OLD)
        self.now += RETENTION_SECONDS + 1
        with patch("telegram_relay.urlopen", side_effect=AssertionError("Retention must not contact Telegram")) as api:
            self.store.cleanup_expired()
        api.assert_not_called()
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM telegram_attempts").fetchone()[0], 0)
        mapping = self.store.telegram_topic("111", "222", "42")
        self.assertEqual(mapping["topic_id"], 101)
        self.assertEqual(mapping["name"], "")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM threads").fetchone()[0], 0)

    def test_cleanup_resumes_after_interruption_between_database_and_log_rewrite(self):
        self.store.accept(self.message())
        self.now += 100
        self.store.accept(self.message("fresh", FRESH))
        self.now += RETENTION_SECONDS - 50
        with patch("retention.rebuild_exports", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self.store.cleanup_expired()
        self.assertIsNotNone(self.store.db.execute("SELECT 1 FROM settings WHERE key='retention_rebuild_paths'").fetchone())
        self.store.close()
        self.store = self.open_store()
        self.assertNotIn(OLD, self.logs())
        self.assertEqual(self.logs().count(FRESH), 1)

    def test_pending_media_deletion_is_resumed_even_if_file_mtime_is_fresh(self):
        media = self.root / "media/recent-write.png"
        media.parent.mkdir()
        media.write_text(OLD)
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO settings VALUES ('retention_media_paths',?)", (json.dumps(["media/recent-write.png"]),))
        self.store.close()
        self.store = self.open_store()
        self.assertFalse(media.exists())

    def test_database_and_wal_do_not_keep_deleted_plaintext_after_compaction(self):
        self.store.accept(self.message())
        self.now += RETENTION_SECONDS + 1
        self.store.cleanup_expired()
        for name in ("messenger-modern.sqlite3", "messenger-modern.sqlite3-wal"):
            path = self.root / name
            if path.exists():
                self.assertNotIn(OLD.encode(), path.read_bytes())

    def test_expired_message_replay_is_rejected_during_a_long_running_session(self):
        event = self.message()
        self.store.accept(event)
        self.now += RETENTION_SECONDS + 1
        self.store.cleanup_expired()
        self.assertIsNone(self.store.accept(event))
        self.assertEqual(self.store.counts(), {})

    def test_expired_snapshots_are_unavailable_even_before_next_scheduled_cleanup(self):
        self.store.accept(self.message())
        self.now += RETENTION_SECONDS + 1
        self.assertIsNone(self.store.relay_snapshot("42", "old"))
        self.assertFalse(self.store.media_available("42", "old"))
        self.assertFalse(self.store.update_media("42", "old", 0, {"downloadStatus": "saved"}))

    def test_late_download_cannot_recreate_expired_content(self):
        outer = self
        class Bridge:
            def call(self, *args, **kwargs):
                outer.now += RETENTION_SECONDS + 1
                return {"data": base64.b64encode(b"\x89PNG\r\n\x1a\nphoto").decode()}
        event = self.message()
        event["type"] = "message"
        event["data"]["attachments"] = [{"type": "image", "url": "https://fbcdn.net/photo"}]
        summary = self.store.accept(event)
        saver = MediaSaver(Bridge(), self.store, lambda message: None, threading.Event())
        try:
            saver.submit(event, summary)
            saver.start()
            deadline = time.monotonic() + 3
            while saver.items.unfinished_tasks and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(saver.saved, 0)
            self.assertIsNone(saver.failure)
            self.assertFalse((self.root / "media").exists())
        finally:
            saver.close()

    def test_message_expiring_during_name_lookup_is_not_relayed(self):
        self.store.accept(self.message())
        self.store.accept({"type": "messageUnsend", "timestamp": int(self.now * 1000), "data": {"threadId": 42, "messageId": "old"}})
        outer = self
        class Names:
            def wait_for(self, sender):
                outer.now += RETENTION_SECONDS + 1
                outer.store.cleanup_expired()
        relay = TelegramRelay(lambda report: None, self.store, start=False, names=Names())
        with patch.object(relay, "_topic", side_effect=AssertionError("Expired content must not create a topic")), patch("telegram_relay.urlopen", side_effect=AssertionError("Expired content must not be sent")):
            relay._deleted("42", "old")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM telegram_attempts").fetchone()[0], 0)

    def test_legacy_multiline_records_and_status_lines_are_pruned(self):
        start = self.now
        legacy = self.root / "message_logs/old-chat/log"
        legacy.parent.mkdir(parents=True)
        def header(stamp):
            return datetime.fromtimestamp(stamp, self.store.zone).strftime("%a %b %d %H:%M:%S %Y") + " | "
        legacy.write_text(header(start) + OLD + "\nold continuation\n" + header(start+100) + FRESH + "\nfresh continuation\n")
        status = self.root / "messenger-modern-status.log"
        status.write_text(datetime.fromtimestamp(start, self.store.zone).isoformat() + " | old status\n" + datetime.fromtimestamp(start+100, self.store.zone).isoformat() + " | fresh status\n")
        original_mtime = legacy.stat().st_mtime
        self.now += RETENTION_SECONDS + 50
        self.store.cleanup_expired()
        cleanup_status(self.root, self.now-RETENTION_SECONDS, self.store.zone)
        self.assertNotIn(OLD, legacy.read_text())
        self.assertNotIn("old continuation", legacy.read_text())
        self.assertIn(FRESH, legacy.read_text())
        self.assertIn("fresh continuation", legacy.read_text())
        self.assertEqual(legacy.stat().st_mtime, original_mtime)
        self.assertNotIn("old status", status.read_text())
        self.assertIn("fresh status", status.read_text())

    def test_cleanup_never_follows_a_media_path_outside_managed_storage(self):
        outside = self.root / "unrelated.txt"
        outside.write_text("KEEP")
        self.store.accept(self.message())
        with self.store.db:
            self.store.db.execute("UPDATE messages SET attachments=?", (json.dumps([{"type": "image", "localPath": "unrelated.txt"}]),))
        self.now += RETENTION_SECONDS + 1
        self.store.cleanup_expired()
        self.assertEqual(outside.read_text(), "KEEP")

    def test_timer_cleanup_runs_without_cookies_and_skips_an_active_listener(self):
        self.store.accept(self.message())
        self.now += RETENTION_SECONDS + 1
        with (self.root / ".messenger-modern.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                cleanup_data(self.root)
            self.assertIn("Listener active", output.getvalue())
        self.store.close()
        self.store = None
        with patch("event_store.time.time", return_value=self.now), patch("telegram_relay.urlopen", side_effect=AssertionError("No Telegram access")), contextlib.redirect_stdout(io.StringIO()):
            cleanup_data(self.root)
        self.store = self.open_store()
        self.assertEqual(self.store.counts(), {})

    def test_unknown_age_legacy_rows_are_not_retained_forever(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO messages(thread_id,message_id,text,transport) VALUES ('42','unknown',?,'regular')", (OLD,))
        self.store.cleanup_expired()
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
