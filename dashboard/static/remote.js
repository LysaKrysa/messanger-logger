"use strict";
(() => {
  // Inputs are kept only until acknowledged. Ambiguous requests are never replayed.
  class InputQueue {
    constructor(send, onError, delay = 35) {
      this.send = send; this.onError = onError; this.delay = delay;
      this.pending = []; this.inflight = null; this.timer = null; this.error = null; this.generation = 0;
    }
    push(event, urgent = false) {
      if (event.action === "text" && [...event.text].length > 2000) {
        this.onError(new Error("Paste at most 2,000 characters at a time.")); return;
      }
      if (this.pending.length >= 128) { this.onError(new Error("Input is catching up. Please wait a moment.")); return; }
      const last = this.pending[this.pending.length - 1];
      if (last?.action === "text" && event.action === "text" && [...last.text, ...event.text].length <= 2000) last.text += event.text;
      else if (last?.action === "scroll" && event.action === "scroll") last.delta = Math.max(-1536, Math.min(1536, last.delta + event.delta));
      else this.pending.push({...event});
      if (urgent) this.flush();
      else if (this.timer === null) this.timer = setTimeout(() => this.flush(), this.delay);
    }
    flush() {
      if (this.timer !== null) { clearTimeout(this.timer); this.timer = null; }
      if (this.inflight || !this.pending.length) return this.inflight || Promise.resolve();
      const batch = [], generation = this.generation;
      let textLength = 0;
      while (this.pending.length && batch.length < 24) {
        const next = this.pending[0];
        const length = next.action === "text" ? [...next.text].length : 0;
        if (batch.length && textLength + length > 2000) break;
        batch.push(this.pending.shift()); textLength += length;
      }
      const promise = Promise.resolve().then(() => generation === this.generation ? this.send(batch) : undefined);
      this.inflight = promise.then(() => {
        if (generation === this.generation) this.error = null;
      }, error => {
        if (generation === this.generation) { this.error = error; this.pending.length = 0; this.onError(error); }
      }).finally(() => {
        if (generation === this.generation) { this.inflight = null; if (this.pending.length) this.flush(); }
      });
      return this.inflight;
    }
    async drain() {
      while (this.pending.length || this.inflight) { await this.flush(); if (this.inflight) await this.inflight; }
      if (this.error) throw this.error;
    }
    clear() {
      this.generation += 1; this.pending.length = 0; this.error = null; this.inflight = null;
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null;
    }
  }
  globalThis.MessengerRemote = {InputQueue};
})();
