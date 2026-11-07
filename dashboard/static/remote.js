"use strict";
(() => {
  // HTMLImageElement.width/height report the CSS display size. Inputs use
  // the decoded frame's intrinsic size, independent of zoom and responsive CSS.
  function mapPoint(image, clientX, clientY) {
    const rect = image.getBoundingClientRect();
    if (!image.naturalWidth || !image.naturalHeight || rect.width <= 0 || rect.height <= 0 ||
        !Number.isFinite(clientX) || !Number.isFinite(clientY) ||
        clientX < rect.left || clientX > rect.left + rect.width ||
        clientY < rect.top || clientY > rect.top + rect.height) return null;
    return {
      x: Math.max(0, Math.min(image.naturalWidth - 1, (clientX - rect.left) * image.naturalWidth / rect.width)),
      y: Math.max(0, Math.min(image.naturalHeight - 1, (clientY - rect.top) * image.naturalHeight / rect.height)),
    };
  }
  function scrollDelta(image, displayedDelta) {
    const rect = image.getBoundingClientRect();
    return image.naturalHeight > 0 && rect.height > 0 && Number.isFinite(displayedDelta)
      ? displayedDelta * image.naturalHeight / rect.height : 0;
  }
  function keyboardPosition(viewport, width, height) {
    const x = viewport?.offsetLeft || 0, y = viewport?.offsetTop || 0;
    return {x: x + Math.min(8, Math.max(0, (viewport?.width || width) - 1)),
      y: y + Math.min(8, Math.max(0, (viewport?.height || height) - 1))};
  }
  class KeyboardFocus {
    constructor(input, win, compact) {
      this.input = input; this.win = win; this.compact = compact;
      this.anchor = null; this.timer = null;
      const cancel = () => this.cancel();
      const sync = () => this.sync();
      // A short focus guard must never fight an intentional page gesture.
      for (const event of ["pointerdown", "touchmove", "wheel", "orientationchange"]) win.addEventListener(event, cancel, {passive: true});
      win.addEventListener("scroll", sync, {passive: true});
      win.visualViewport?.addEventListener("resize", sync);
      win.visualViewport?.addEventListener("scroll", sync);
      input.addEventListener("blur", cancel);
    }
    cancel() {
      this.anchor = null;
      if (this.timer !== null) this.win.clearTimeout(this.timer);
      this.timer = null;
    }
    sync() {
      if (this.win.document.activeElement !== this.input) { this.cancel(); return; }
      if (this.compact()) {
        const point = keyboardPosition(this.win.visualViewport, this.win.innerWidth, this.win.innerHeight);
        this.input.style.setProperty("--keyboard-x", point.x + "px");
        this.input.style.setProperty("--keyboard-y", point.y + "px");
      }
      if (this.anchor && (Math.abs(this.win.scrollX - this.anchor.x) > 1 || Math.abs(this.win.scrollY - this.anchor.y) > 1)) {
        this.win.scrollTo({left: this.anchor.x, top: this.anchor.y, behavior: "instant"});
      }
    }
    focus() {
      if (this.input.disabled) return;
      this.cancel();
      if (this.compact()) {
        const point = keyboardPosition(this.win.visualViewport, this.win.innerWidth, this.win.innerHeight);
        this.input.style.setProperty("--keyboard-x", point.x + "px");
        this.input.style.setProperty("--keyboard-y", point.y + "px");
        this.anchor = {x: this.win.scrollX, y: this.win.scrollY};
        this.timer = this.win.setTimeout(() => this.cancel(), 900);
      }
      // Keep focus synchronous with the user's tap so iOS can open its keyboard.
      this.input.focus({preventScroll: true});
      this.sync();
    }
  }
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
  globalThis.MessengerRemote = {InputQueue, mapPoint, scrollDelta, KeyboardFocus, keyboardPosition};
})();
