"""Read captured messages or the deleted-only view without opening a session."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo
from identities import display_name, display_label


def load_messages(database: Path, *, deleted_only=False, run="latest", limit=50):
    if not database.is_file():
        return None, []
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "runs" not in tables:
            return None, []
        scope = run
        if scope == "latest":
            latest = db.execute("SELECT id FROM runs ORDER BY started_at DESC,rowid DESC LIMIT 1").fetchone()
            if latest is None:
                return None, []
            scope = latest[0]
        conditions = []
        params = []
        if deleted_only:
            conditions.append("m.deleted=1")
        if scope != "all":
            if deleted_only:
                conditions.append("(m.run_id=? OR m.deleted_run_id=?)")
                params.extend([scope, scope])
            else:
                conditions.append("m.run_id=?")
                params.append(scope)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        rows = db.execute(
            "SELECT m.*,c.name AS sender_name,c.username AS sender_username,"
            "t.name AS thread_name,tc.name AS peer_name,tc.username AS peer_username "
            "FROM messages m LEFT JOIN contacts c ON c.id=m.sender_id "
            "LEFT JOIN threads t ON t.id=m.thread_id LEFT JOIN contacts tc ON tc.id=m.thread_id"
            + where + " ORDER BY COALESCE(m.deleted_at_ms,NULLIF(m.created_ms,0),m.captured_at*1000) DESC,m.rowid DESC LIMIT ?",
            params,
        ).fetchall()
        own = db.execute("SELECT value FROM settings WHERE key='account_id'").fetchone()
        own_id = own[0] if own else ""
        records = []
        for row in rows:
            item = dict(row)
            item["sender"] = display_name(item.pop("sender_name"), item.pop("sender_username"), own=item["sender_id"] == own_id)
            title = item.pop("thread_name")
            peer_name, peer_username = item.pop("peer_name"), item.pop("peer_username")
            item["conversation"] = display_label(title) or (display_name(peer_name, peer_username) if display_label(peer_name) or peer_username else "Unnamed conversation")
            item["attachments"] = json.loads(item["attachments"] or "[]")
            item["reply"] = json.loads(item.pop("reply_json") or "{}")
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            reply_id = item["reply"].get("sender_id")
            if reply_id:
                contact = db.execute("SELECT name,username FROM contacts WHERE id=?", (reply_id,)).fetchone()
                item["reply"]["sender_name"] = display_name(contact[0] if contact else None, contact[1] if contact else None, own=reply_id == own_id)
            item["deleted"] = bool(item["deleted"])
            records.append(item)
        return scope, records


def stamp(milliseconds, zone):
    if not milliseconds:
        return "not captured"
    return datetime.fromtimestamp(milliseconds / 1000, zone).isoformat(timespec="seconds")


def render_message(item, zone, *, include_ids=False):
    lines = [
        "DELETED / UNSENT" if item["deleted"] else "MESSAGE",
        "Conversation: " + item["conversation"],
        "Sender: " + item["sender"],
        "Sent: " + stamp(item["created_ms"], zone),
    ]
    if item["deleted"]:
        lines.append("Deletion observed: " + stamp(item["deleted_at_ms"], zone))
    original = item["original_text"]
    if original is None and (item["created_ms"] or item["text"]):
        original = item["text"]
    if original is None:
        lines.append("Content was not captured before deletion; it cannot be recovered by this view.")
    elif original:
        lines.append("Text: " + original)
    elif not item["attachments"]:
        lines.append("[No captured text or supported attachment metadata]")
    if item["last_edit_ms"] and item["text"] != original:
        lines.append("Latest captured edit: " + (item["text"] or "[empty text]"))
    for media in item["attachments"]:
        parts = [str(media.get("type") or "attachment")]
        if media.get("viewOnce"):
            parts.append({"once": "view-once", "replay": "limited replay"}.get(media.get("viewMode"), "disappearing media"))
        for key in ("fileName", "mimeType", "description", "url", "previewUrl"):
            if media.get(key):
                parts.append(str(media[key]))
        lines.append("Attachment: " + " | ".join(parts))
        status = media.get("downloadStatus")
        if status == "saved" and media.get("localPath"):
            lines.append("Saved file: /data/" + media["localPath"] + " (" + str(media.get("savedBytes", 0)) + " bytes)")
        elif status:
            lines.append("Download: " + status + (" (" + media["downloadError"] + ")" if media.get("downloadError") else ""))
    if item["reply"]:
        lines.append("Reply to: " + item["reply"].get("sender_name", "Unknown participant"))
        if item["reply"].get("text"):
            lines.append("Quoted text: " + item["reply"]["text"])
    mentions = item.get("metadata", {}).get("mentions", [])
    if mentions:
        lines.append("Mentions: " + ", ".join(entry.get("name") or "Unknown participant" for entry in mentions))
    if include_ids:
        lines.append("Message ID: " + item["message_id"])
        lines.append("Conversation ID: " + item["thread_id"])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deleted-only", action="store_true")
    parser.add_argument("--run", default="latest", help="latest (default), all, or an exact run identifier")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-ids", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")
    root = Path(os.environ.get("DATA_DIR", "/data"))
    try:
        scope, records = load_messages(root / "messenger-modern.sqlite3", deleted_only=args.deleted_only, run=args.run, limit=args.limit)
        if args.json:
            print(json.dumps({"run": scope, "messages": records}, ensure_ascii=False, indent=2))
            return
        if scope is None:
            print("No content-only run found. Start the updated logger first.")
            return
        if not records:
            print("No deleted/unsent messages captured in this run." if args.deleted_only else "No new messages captured in this run.")
            return
        zone = ZoneInfo(os.environ.get("LOGGER_TIMEZONE", "Europe/Warsaw"))
        print("Run: " + scope)
        for item in records:
            print()
            print(render_message(item, zone, include_ids=args.include_ids))
    except (sqlite3.Error, ValueError, OSError):
        print("Could not read the message view. Stop and check the local database; private values are hidden.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
