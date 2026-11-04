"""Optional Telegram copies of observed deletions; no archive scans or retries."""
from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import queue
import re
import secrets
import stat
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from telegram_format import RichText, chunks, message_card, attachment_card, event_card
from telegram_topics import TopicRouter, TopicError, TelegramRejected

# Cloud Bot API multipart uploads: https://core.telegram.org/bots/api#sending-files
MAX_UPLOAD_BYTES = 50_000_000


class TelegramConfigurationError(ValueError):
    pass


def boolean(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise TelegramConfigurationError(name + " must be true or false.")


class TelegramRelay:
    def __init__(self, report, store, *, timezone="Europe/Warsaw", start=True, names=None):
        self.enabled = boolean("TELEGRAM_ENABLED", False)
        self.report, self.store = report, store
        self.zone = ZoneInfo(timezone)
        self.mode = os.environ.get("TELEGRAM_MODE", "deleted").lower()
        self.send_media = boolean("TELEGRAM_SEND_MEDIA", True)
        self.view_once = boolean("TELEGRAM_VIEW_ONCE", False)
        self.use_topics = boolean("TELEGRAM_TOPICS", False)
        self.topics = None
        self.names = names
        self.items = queue.Queue(maxsize=256)
        self.queued = set()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None
        self.next_send = 0.0
        self.interval = 1.05
        if self.enabled:
            self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            self.chat = os.environ.get("TELEGRAM_CHAT_ID", "")
            self.interval = 3.1 if self.chat.startswith(("-", "@")) else 1.05
            if self.mode not in {"deleted", "all"}:
                raise TelegramConfigurationError("TELEGRAM_MODE must be deleted or all.")
            if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", self.token) or not self.chat.strip() or len(self.chat) > 128 or any(char in self.chat for char in "\r\n"):
                raise TelegramConfigurationError("Telegram requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
            if self.use_topics:
                self.topics = TopicRouter(self._api, store, self.token.split(":", 1)[0], self.chat)
            if start:
                self.thread = threading.Thread(target=self._work, name="telegram-relay", daemon=True)
                self.thread.start()

    def _queue_deleted(self, tid, mid):
        key = (tid, mid)
        if not mid or self.stopping.is_set():
            return
        with self.lock:
            if key in self.queued:
                return
            try:
                self.items.put_nowait(("deleted", key))
            except queue.Full:
                self.report("Telegram queue full; one notification was skipped. Local logging continues.")
            else:
                self.queued.add(key)

    def submit(self, summary):
        if not self.enabled or self.stopping.is_set():
            return
        kind = summary["type"]
        if kind == "unsend":
            self._queue_deleted(summary["thread_id"], summary["message_id"])
        else:
            if kind == "message":
                # A delayed capture may fill an earlier deletion tombstone.
                self.media_updated(summary["thread_id"], summary["message_id"])
            if self.mode == "all" and kind != "message":
                try:
                    self.items.put_nowait(("summary", dict(summary)))
                except queue.Full:
                    self.report("Telegram queue full; one relay was skipped. Local logging continues.")

    def media_updated(self, tid, mid):
        if not self.enabled or self.stopping.is_set():
            return
        snapshot = self.store.relay_snapshot(tid, mid)
        if snapshot and self._eligible(snapshot):
            self._queue_deleted(tid, mid)

    def _eligible(self, snapshot):
        if snapshot["deleted"]:
            return snapshot["deleted_run_id"] == self.store.run_id
        once = self.view_once and any(item.get("viewOnce") for item in snapshot["attachments"])
        return snapshot["run_id"] == self.store.run_id and (once or self.mode == "all")

    def _post(self, method, body, content_type="application/json", *, length=None):
        if self.stopping.wait(max(0.0, self.next_send - time.monotonic())):
            raise InterruptedError
        self.next_send = time.monotonic() + self.interval
        headers = {"Content-Type": content_type}
        if length is not None:
            headers["Content-Length"] = str(length)
        request = Request("https://api.telegram.org/bot" + self.token + "/" + method,
                          data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise TelegramRejected(exc.code) from None
            raise ValueError("Telegram request outcome is unconfirmed.") from None
        if len(raw) > 1024 * 1024:
            raise ValueError("Telegram request was not accepted.")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            code = payload.get("error_code") if isinstance(payload, dict) else None
            if isinstance(code, int) and not isinstance(code, bool) and 400 <= code < 500:
                raise TelegramRejected(code)
            raise ValueError("Telegram request was not accepted.")
        return payload.get("result")

    def _api(self, method, parameters):
        return self._post(method, json.dumps(parameters).encode())

    def _topic(self, thread_id, title):
        return self.topics.topic(thread_id, title) if self.topics is not None else None

    def _destination_chat(self):
        return self.topics.chat_id if self.topics is not None and self.topics.chat_id is not None else self.chat

    def _text(self, text, *, topic_id=None):
        rich = text if isinstance(text, RichText) else RichText().add(text)
        for part, entities in rich.pages():
            if self.stopping.is_set():
                raise InterruptedError
            payload = {
                "chat_id": self._destination_chat(), "text": part, "entities": entities, "disable_notification": True,
                "link_preview_options": {"is_disabled": True},
            }
            if topic_id is not None:
                payload["message_thread_id"] = topic_id
            self._post("sendMessage", json.dumps(payload).encode())

    def _attempt(self, tid, mid, part, action):
        if self.stopping.is_set() or not self.store.claim_telegram(tid, mid, part):
            return
        try:
            action()
        except Exception:
            self.store.finish_telegram(tid, mid, part, "failed")
            self.report("Telegram relay failed; captured content remains local. No automatic retry of an ambiguous send.")
        else:
            self.store.finish_telegram(tid, mid, part, "sent")
            self.report("Telegram notification sent.")

    def _document(self, media, caption, *, topic_id=None):
        relative = Path(media["localPath"])
        root = self.store.root / "media"
        target = self.store.root / relative
        if relative.is_absolute() or not relative.parts or relative.parts[0] != "media" or not target.resolve().is_relative_to(root):
            raise ValueError("Invalid saved media path.")
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_UPLOAD_BYTES or info.st_size != media.get("savedBytes"):
                raise ValueError("Saved file is unavailable or exceeds the Telegram limit.")
            digest = hashlib.sha256()
            while block := handle.read(65536):
                digest.update(block)
            if digest.hexdigest() != media.get("sha256"):
                raise ValueError("Saved file integrity check failed.")
            handle.seek(0)
            boundary = "messenger-" + secrets.token_hex(16)
            rich = caption if isinstance(caption, RichText) else RichText().add(caption)
            caption_text, caption_entities = next(rich.pages(900), ("Attachment", []))
            fields = {"chat_id": self._destination_chat(), "caption": caption_text, "caption_entities": json.dumps(caption_entities), "disable_notification": "true"}
            if topic_id is not None:
                fields["message_thread_id"] = str(topic_id)
            prefix = b""
            for key, value in fields.items():
                prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + value + "\r\n").encode()
            filename = re.sub(r'[^A-Za-z0-9._-]', "_", target.name)[:150] or "attachment.bin"
            prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="document"; filename="' + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
            suffix = ("\r\n--" + boundary + "--\r\n").encode()
            def body():
                yield prefix
                while block := handle.read(65536):
                    if self.stopping.is_set():
                        raise InterruptedError
                    yield block
                yield suffix
            self._post("sendDocument", body(), "multipart/form-data; boundary=" + boundary,
                       length=len(prefix) + info.st_size + len(suffix))

    def _deleted(self, tid, mid):
        snapshot = self.store.relay_snapshot(tid, mid)
        if not snapshot or not self._eligible(snapshot):
            return
        if self.names is not None:
            self.names.wait_for(snapshot["sender_id"])
            snapshot = self.store.relay_snapshot(tid, mid)
            if not snapshot or not self._eligible(snapshot):
                return
        topic_id = self._topic(tid, snapshot["conversation"])
        # Send the text immediately, then follow up when pending downloads finish.
        notice = dict(snapshot)
        notice["attachments"] = [{key: value for key, value in item.items() if key not in {"url", "previewUrl", "localPath"}}
                                 for item in snapshot["attachments"]]
        phase = "deleted" if snapshot["deleted"] else "message"
        text_part = "text:" + phase + (":captured" if snapshot["original_text"] is not None else ":unavailable")
        self._attempt(tid, mid, text_part, lambda: self._text(message_card(notice, self.zone), topic_id=topic_id))
        if not self.send_media:
            return
        for index, media in enumerate(snapshot["attachments"]):
            status = media.get("downloadStatus")
            title = attachment_card(snapshot, media, index, self.zone)
            if status == "saved" and media.get("localPath"):
                part = "media:" + str(index) + ":" + str(media.get("sha256", ""))
                if media.get("savedBytes", 0) > MAX_UPLOAD_BYTES:
                    self._attempt(tid, mid, part, lambda title=title: self._text(title.add("\n\nSaved locally; exceeds Telegram's file-upload limit.", "italic"), topic_id=topic_id))
                else:
                    self._attempt(tid, mid, part, lambda media=media, title=title: self._document(media, title, topic_id=topic_id))
            elif status in {"failed", "interrupted"}:
                self._attempt(tid, mid, "media-status:" + str(index) + ":" + status,
                              lambda title=title, status=status: self._text(title.add("\n\nLocal download: " + status + ". Captured metadata is retained.", "italic"), topic_id=topic_id))

    def _work(self):
        while not self.stopping.is_set():
            try:
                kind, item = self.items.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "deleted":
                with self.lock:
                    self.queued.discard(item)
            try:
                if kind == "deleted":
                    self._deleted(*item)
                else:
                    plain = html.unescape(re.sub(r"<[^>]+>", "", item["body"]))
                    topic_id = self._topic(item["thread_id"], item["thread"])
                    self._text(event_card(item["thread"], plain, item["type"]), topic_id=topic_id)
            except TopicError as exc:
                self.report("Telegram topics: " + str(exc) + " Local content remains saved.")
            except Exception:
                self.report("Telegram relay could not process a notification. Local logs remain saved; private details hidden.")
            finally:
                self.items.task_done()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
        unfinished = self.items.unfinished_tasks
        if unfinished:
            self.report("Telegram notifications may be unfinished at shutdown; captured content remains local.")
        return self.thread is None or not self.thread.is_alive()
