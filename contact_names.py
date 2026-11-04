"""Resolve missing names for participants observed in newly accepted events."""
from __future__ import annotations

import queue
import threading
import time

class ContactNames:
    def __init__(self, bridge, store, stop):
        self.bridge, self.store, self.stop = bridge, store, stop
        self.items = queue.Queue(maxsize=128)
        self.lock = threading.Lock()
        self.pending = {}
        self.retry_at = {}
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name="contact-names", daemon=True)
            self.thread.start()

    def submit(self, user_id):
        user_id = str(user_id or "")
        if not self.store.contact_needed(user_id) or self.stopping.is_set() or self.stop.is_set():
            return None
        with self.lock:
            if user_id in self.pending:
                return self.pending[user_id]
            if self.retry_at.get(user_id, 0) > time.monotonic():
                return None
            ready = threading.Event()
            try:
                self.items.put_nowait(user_id)
            except queue.Full:
                return None
            self.pending[user_id] = ready
            return ready

    def wait_for(self, user_id, timeout=3):
        ready = self.submit(user_id)
        if ready is not None:
            ready.wait(timeout)

    def _work(self):
        while not self.stopping.is_set() or not self.items.empty():
            try:
                user_id = self.items.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if not self.stopping.is_set() and not self.stop.is_set() and self.store.contact_needed(user_id):
                    result = self.bridge.call("getUserInfo", {"userId": int(user_id)}, timeout=20)
                    if str(result.get("id")) == user_id:
                        self.store.accept({"type": "metadata", "data": {"contacts": [result], "threads": []}})
            except Exception:
                # A missing name is not a logging failure; never expose API text.
                pass
            finally:
                with self.lock:
                    self.retry_at[user_id] = time.monotonic() + 600
                    if len(self.retry_at) > 2048:
                        oldest = min(self.retry_at, key=self.retry_at.get)
                        del self.retry_at[oldest]
                    self.pending.pop(user_id).set()
                self.items.task_done()

    def close(self):
        self.stopping.set()
        self.start()
        self.thread.join(timeout=3)
        return not self.thread.is_alive()
