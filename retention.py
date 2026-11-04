"""Three-day retention for local captured content. Never calls Telegram."""
from __future__ import annotations

from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

RETENTION_SECONDS = 3 * 24 * 60 * 60
CLEANUP_INTERVAL = 5 * 60
HEADER = re.compile(r"^((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Za-z]{3} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4}) \|")


def _private_replace(path, writer):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".retention-", delete=False) as output:
            temporary = Path(output.name)
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def rebuild_exports(store, paths):
    """The database commits a path journal before files are replaced or removed."""
    with store.lock:
        for relative in paths:
            target = store._target(relative)
            rows = store.db.execute("SELECT line FROM events WHERE path=? ORDER BY id", (relative,)).fetchall()
            if rows:
                def write(output):
                    for (line,) in rows:
                        output.write((line + "\n").encode("utf-8"))
                _private_replace(target, write)
                size = target.stat().st_size
                with store.db:
                    store.db.execute("INSERT INTO file_offsets VALUES (?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size", (relative, size))
                    store.db.execute("UPDATE events SET exported=1 WHERE path=?", (relative,))
            else:
                target.unlink(missing_ok=True)
                with store.db:
                    store.db.execute("DELETE FROM file_offsets WHERE path=?", (relative,))
        with store.db:
            store.db.execute("DELETE FROM settings WHERE key='retention_rebuild_paths'")
        store._export_pending()


def _safe_media(store, relative):
    path = Path(relative)
    candidate = store.root / path
    if path.is_absolute() or not path.parts or path.parts[0] != "media" or not candidate.resolve().is_relative_to(store.root / "media"):
        return None
    return candidate


def _empty_directories(root):
    if not root.exists() or root.is_symlink():
        return
    for current, dirs, files in os.walk(root, topdown=False, followlinks=False):
        path = Path(current)
        if path != root:
            try:
                path.rmdir()
            except OSError:
                pass


def _legacy_file(path, cutoff, zone, *, status=False):
    """Prune complete multiline records; unknown flat files use local mtime."""
    original_mtime = path.stat().st_mtime
    if original_mtime <= cutoff:
        path.unlink()
        return 1
    changed = False
    had_record = False
    def write(output):
        nonlocal changed, had_record
        keep = True
        with path.open("rb") as source:
            for line in source:
                text = line.decode("utf-8", errors="replace")
                stamp = None
                if status:
                    try:
                        stamp = datetime.fromisoformat(text.split(" | ", 1)[0]).timestamp()
                    except ValueError:
                        pass
                else:
                    match = HEADER.match(text)
                    if match:
                        try:
                            stamp = datetime.strptime(match[1], "%a %b %d %H:%M:%S %Y").replace(tzinfo=zone).timestamp()
                        except ValueError:
                            pass
                if stamp is not None:
                    had_record = True
                    keep = stamp > cutoff
                if keep:
                    output.write(line)
                else:
                    changed = True
    _private_replace(path, write)
    os.utime(path, (original_mtime, original_mtime))
    if had_record and changed and path.stat().st_size == 0:
        path.unlink()
        return 1
    return 0


def expire(store):
    now, cutoff = store.clock(), store.clock() - store.retention_seconds
    result = {"messages": 0, "events": 0, "files": 0}
    with store.lock:
        db = store.db
        touched, media_paths, expired_keys = set(), set(), set()
        pending_media = db.execute("SELECT value FROM settings WHERE key='retention_media_paths'").fetchone()
        if pending_media:
            media_paths.update(json.loads(pending_media[0]))
        before_events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        initialized = db.execute("SELECT 1 FROM settings WHERE key='retention_initialized'").fetchone() is not None
        with db:
            # Backfill the small lookup key in pre-retention journals.
            for (event_id,) in db.execute("SELECT id FROM events WHERE message_id='' AND type IN ('message','edit','unsend','reaction')").fetchall():
                payload = json.loads(db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()[0])
                mid = payload.get("message_id") or ""
                if isinstance(mid, str) and mid:
                    db.execute("UPDATE events SET message_id=? WHERE id=?", (mid, event_id))
            db.execute("CREATE TEMP TABLE IF NOT EXISTS expired_messages(thread_id TEXT,message_id TEXT,PRIMARY KEY(thread_id,message_id))")
            db.execute("DELETE FROM expired_messages")
            db.execute("INSERT INTO expired_messages SELECT m.thread_id,m.message_id FROM messages m WHERE COALESCE(NULLIF(m.captured_at,0),NULLIF(m.created_ms,0)/1000.0,(SELECT MIN(e.received_at) FROM events e WHERE e.thread_id=m.thread_id AND e.message_id=m.message_id),NULLIF(m.deleted_at_ms,0)/1000.0,0)<=?", (cutoff,))
            expired_keys = set(db.execute("SELECT thread_id,message_id FROM expired_messages"))
            for (raw,) in db.execute("SELECT m.attachments FROM messages m JOIN expired_messages x ON x.thread_id=m.thread_id AND x.message_id=m.message_id"):
                for media in json.loads(raw or "[]"):
                    if isinstance(media.get("localPath"), str):
                        media_paths.add(media["localPath"])
            touched.update(row[0] for row in db.execute("SELECT DISTINCT path FROM events WHERE received_at<=? OR EXISTS(SELECT 1 FROM expired_messages x WHERE x.thread_id=events.thread_id AND x.message_id=events.message_id)", (cutoff,)))
            db.execute("DELETE FROM events WHERE received_at<=? OR EXISTS(SELECT 1 FROM expired_messages x WHERE x.thread_id=events.thread_id AND x.message_id=events.message_id)", (cutoff,))
            db.execute("DELETE FROM messages WHERE EXISTS(SELECT 1 FROM expired_messages x WHERE x.thread_id=messages.thread_id AND x.message_id=messages.message_id)")
            result["messages"] = len(expired_keys)
            result["events"] = before_events - db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            # A fresh reply must not keep a structured copy of an expired message.
            for tid, mid, raw in db.execute("SELECT thread_id,message_id,reply_json FROM messages WHERE reply_json<>'{}'").fetchall():
                reply = json.loads(raw or "{}")
                if (tid, reply.get("message_id")) not in expired_keys:
                    continue
                db.execute("UPDATE messages SET reply_json='{}' WHERE thread_id=? AND message_id=?", (tid, mid))
                for event_id, payload, received, path in db.execute("SELECT id,payload,received_at,path FROM events WHERE thread_id=? AND message_id=? AND type='message'", (tid, mid)).fetchall():
                    value = json.loads(payload)
                    value.pop("reply", None)
                    line = datetime.fromtimestamp(received, store.zone).strftime("%a %b %d %H:%M:%S %Y") + " | " + store._body(value)
                    db.execute("UPDATE events SET payload=?,line=? WHERE id=?", (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), line, event_id))
                    touched.add(path)
            referenced = {row[0] for row in db.execute("SELECT DISTINCT sender_id FROM messages")}
            for reply, meta in db.execute("SELECT reply_json,metadata_json FROM messages"):
                referenced.add(json.loads(reply or "{}").get("sender_id", ""))
                referenced.update(item.get("user_id", "") for item in json.loads(meta or "{}").get("mentions", []))
            for uid, seen in db.execute("SELECT id,last_seen FROM contacts").fetchall():
                if seen <= cutoff and uid not in referenced:
                    db.execute("DELETE FROM contacts WHERE id=?", (uid,))
            active_threads = {row[0] for row in db.execute("SELECT thread_id FROM messages UNION SELECT thread_id FROM events UNION SELECT id FROM threads WHERE last_seen>?", (cutoff,))}
            for (tid,) in db.execute("SELECT id FROM threads WHERE last_seen<=?", (cutoff,)).fetchall():
                if tid not in active_threads:
                    db.execute("DELETE FROM threads WHERE id=?", (tid,))
            # IDs are routing configuration, not captured message content. Keep
            # those so remote Telegram topics can be reused; remove stale names.
            for (tid,) in db.execute("SELECT DISTINCT thread_id FROM telegram_topics").fetchall():
                if tid not in active_threads:
                    db.execute("UPDATE telegram_topics SET name='' WHERE thread_id=?", (tid,))
            db.execute("DELETE FROM telegram_attempts WHERE NOT EXISTS(SELECT 1 FROM messages m WHERE m.thread_id=telegram_attempts.thread_id AND m.message_id=telegram_attempts.message_id)")
            db.execute("DELETE FROM runs WHERE started_at<=? AND id<>? AND id NOT IN(SELECT run_id FROM messages UNION SELECT deleted_run_id FROM messages UNION SELECT run_id FROM events)", (cutoff, store.run_id))
            db.execute("DROP TABLE expired_messages")
            if touched:
                db.execute("INSERT INTO settings VALUES ('retention_rebuild_paths',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(sorted(touched)),))
            if media_paths:
                db.execute("INSERT INTO settings VALUES ('retention_media_paths',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(sorted(media_paths)),))
            if not initialized or touched or expired_keys:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('retention_compact_pending','1')")
            db.execute("INSERT OR REPLACE INTO settings VALUES ('retention_initialized','1')")
        if touched:
            rebuild_exports(store, sorted(touched))
        for relative in media_paths:
            path = _safe_media(store, relative)
            if path is not None and path.is_file():
                path.unlink()
                result["files"] += 1
        with db:
            db.execute("DELETE FROM settings WHERE key='retention_media_paths'")
        # Any stale orphan, including interrupted download temporaries, expires.
        media_root = store.root / "media"
        if media_root.exists() and not media_root.is_symlink():
            for current, dirs, files in os.walk(media_root, followlinks=False):
                for name in files:
                    path = Path(current) / name
                    if path.is_symlink():
                        continue
                    if path.stat().st_mtime <= cutoff:
                        path.unlink()
                        result["files"] += 1
        tracked = {row[0] for row in db.execute("SELECT path FROM file_offsets")}
        for base in (store.root / "message_logs", store.logs):
            if not base.exists() or base.is_symlink():
                continue
            for current, dirs, files in os.walk(base, followlinks=False):
                for name in files:
                    path = Path(current) / name
                    if path.is_symlink() or (base == store.logs and path.relative_to(store.logs).as_posix() in tracked):
                        continue
                    if path.name.startswith(".retention-"):
                        path.unlink()
                        result["files"] += 1
                        continue
                    result["files"] += _legacy_file(path, cutoff, store.zone)
        # Content snapshots can mix ages; the live database retains fresh data.
        for suffix in ("", "-wal", "-shm"):
            backup = store.root / ("messenger-modern.before-content.sqlite3" + suffix)
            if backup.is_file() and not backup.is_symlink():
                backup.unlink()
                result["files"] += 1
        for base in (media_root, store.logs, store.root / "message_logs"):
            _empty_directories(base)
        if db.execute("SELECT 1 FROM settings WHERE key='retention_compact_pending'").fetchone():
            try:
                db.execute("VACUUM")
                busy, _, _ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if not busy:
                    with db:
                        db.execute("DELETE FROM settings WHERE key='retention_compact_pending'")
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
        else:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return result


def cleanup_status(root, cutoff, zone):
    path = root / "messenger-modern-status.log"
    if path.is_file() and not path.is_symlink():
        _legacy_file(path, cutoff, zone, status=True)


def cleanup_data(root, *, timezone="Europe/Warsaw"):
    """Offline cleanup for the systemd timer; no cookies or Telegram access."""
    from event_store import EventStore
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".messenger-modern.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Listener active; its scheduled local cleanup handles retention.")
            return
        store = EventStore(root, timezone=timezone, retention_seconds=RETENTION_SECONDS)
        try:
            cleanup_status(root, store.clock() - RETENTION_SECONDS, store.zone)
            print("Three-day local retention cleanup completed. No Telegram requests were made.")
        finally:
            store.close()
