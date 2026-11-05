"""Telegram layouts using explicit entities, so chat text cannot inject markup."""
from __future__ import annotations

from datetime import datetime


def units(text):
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def chunks(text, limit=3500):
    part, length = [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if length + width > limit:
            yield "".join(part)
            part, length = [], 0
        part.append(char)
        length += width
    if part:
        yield "".join(part)


class RichText:
    def __init__(self):
        self.text = ""
        self.entities = []
        self.length = 0

    def add(self, text, style=None):
        text = str(text)
        count = units(text)
        if style and count:
            self.entities.append({"type": style, "offset": self.length, "length": count})
        self.text += text
        self.length += count
        return self

    def quote(self, text):
        return self.add(text, "expandable_blockquote" if len(text) > 500 or text.count("\n") > 5 else "blockquote")

    def pages(self, limit=3500):
        offset = 0
        for text in chunks(self.text, limit):
            length = units(text)
            entities = []
            for entity in self.entities:
                start = max(offset, entity["offset"])
                end = min(offset + length, entity["offset"] + entity["length"])
                if start < end:
                    entities.append({"type": entity["type"], "offset": start - offset, "length": end - start})
            yield text, entities
            offset += length


def stamp(value, zone):
    return datetime.fromtimestamp(value / 1000, zone).strftime("%d %b · %H:%M:%S %Z") if value else "Not captured"


def file_size(value):
    value = value if isinstance(value, (int, float)) and value >= 0 else 0
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    return f"{value / 1024:.0f} KB" if value >= 1024 else f"{value:.0f} bytes"


def media_label(media):
    return {"image": "Photo", "video": "Video", "gif": "GIF", "sticker": "Sticker", "voice": "Voice message", "audio": "Audio", "document": "File", "file": "File"}.get(media.get("type"), "Attachment")


def message_card(item, zone):
    result = RichText()
    once = any(media.get("viewOnce") for media in item["attachments"])
    title = "🗑 Deleted message" if item["deleted"] else ("👁 View-once message" if once else "💬 Message captured")
    result.add(title, "bold").add("\n\n")
    result.add("👤 ").add(item["sender"], "bold").add("\n💬 ").add(item["conversation"], "bold").add("\n")
    result.add("Sent  " + stamp(item["created_ms"], zone), "italic")
    if item["deleted"]:
        result.add("\n").add("Deleted  " + stamp(item["deleted_at_ms"], zone), "italic")
    result.add("\n\n")
    original = item["original_text"]
    if original is None:
        result.add("Content was not captured before deletion; it cannot be recovered from the log.", "italic")
    elif original:
        result.quote(original)
    elif not item["attachments"]:
        result.add("No supported text or attachment was captured.", "italic")
    if item.get("last_edit_ms") and item["text"] != original:
        result.add("\n\n").add("✏️ Edited version", "bold").add("\n").quote(item["text"] or "[Empty text]")
    reply = item.get("reply") or {}
    if reply:
        result.add("\n\n").add("↩ Reply to " + reply.get("sender_name", "Unknown participant"), "bold")
        if reply.get("text"):
            result.add("\n").quote(reply["text"])
    mentions = item.get("metadata", {}).get("mentions", [])
    if mentions:
        result.add("\n\nMentions  " + ", ".join(person.get("name") or "Unknown participant" for person in mentions))
    attachments = item["attachments"]
    if attachments:
        result.add("\n\n").add("📎 " + str(len(attachments)) + (" attachment" if len(attachments) == 1 else " attachments"), "bold")
        for media in attachments:
            status = {"saved": "Saved", "pending": "Downloading…", "failed": "Unavailable", "interrupted": "Download interrupted"}.get(media.get("downloadStatus"), "Metadata only")
            parts = [media_label(media)]
            if media.get("viewOnce"):
                parts.append({"once": "view once", "replay": "limited replay"}.get(media.get("viewMode"), "disappearing"))
            if media.get("fileName"):
                parts.append(str(media["fileName"]))
            if media.get("savedBytes"):
                parts.append(file_size(media["savedBytes"]))
            parts.append(status)
            result.add("\n• " + " · ".join(parts))
    return result


def attachment_card(item, media, index, zone, *, status=None):
    result = RichText()
    heading = "📎 Deleted attachment" if item["deleted"] else ("👁 View-once attachment" if media.get("viewOnce") else "📎 Attachment")
    result.add(heading, "bold").add("\n")
    # Bound caption components individually so the filename and status survive
    # even when a display name or conversation title is unusually long.
    result.add(item["sender"][:150], "bold").add("\n").add(item["conversation"][:150]).add("\n")
    label = str(media.get("fileName") or media_label(media))
    result.add(str(index + 1) + "/" + str(len(item["attachments"])) + " · " + label[:150])
    if media.get("savedBytes"):
        result.add(" · " + file_size(media["savedBytes"]))
    result.add("\n").add(stamp(item["created_ms"], zone), "italic")
    if status:
        result.add("\n\n").add(status, "italic")
    return result


def download_caption(item, detail):
    title = "🗑 Deleted message" if item["deleted"] else "💬 Message captured"
    return (RichText().add(title, "bold").add("\n\n👤 ").add(item["sender"][:100], "bold")
            .add("\n💬 ").add(item["conversation"][:100], "bold").add("\n\n" + detail))


def event_card(conversation, body, kind):
    title = {"edit": "✏️ Message edited", "reaction": "💟 Reaction"}.get(kind, "💬 Chat update")
    return RichText().add(title, "bold").add("\n\n").add(conversation, "bold").add("\n\n").quote(body)
