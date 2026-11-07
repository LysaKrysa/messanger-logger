"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const context = {setTimeout, clearTimeout};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../static/remote.js"), "utf8"), context);
const {InputQueue, mapPoint, scrollDelta} = context.MessengerRemote;
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

function displayedImage(width, height, scale, left = 37.5, top = -183.25) {
  return {naturalWidth: width, naturalHeight: height,
    width: Math.round(width * scale), height: Math.round(height * scale),
    getBoundingClientRect: () => ({left, top, width: width * scale, height: height * scale})};
}

test("clicks map to frame pixels across desktop/mobile sizes, zoom and page offsets", () => {
  for (const [width, height] of [[1024, 768], [432, 800]]) {
    for (const scale of [.3, .75, 1, 1.075, 1.5]) {
      const image = displayedImage(width, height, scale);
      const rect = image.getBoundingClientRect();
      for (const [x, y] of [[10, 10], [width / 2, height / 2], [width - 10, height - 10]]) {
        const point = mapPoint(image, rect.left + x * scale, rect.top + y * scale);
        assert.ok(Math.abs(point.x - x) < .000001);
        assert.ok(Math.abs(point.y - y) < .000001);
      }
    }
  }
});

test("unloaded, collapsed and outside-frame clicks are ignored instead of sent to an edge", () => {
  const image = displayedImage(1024, 768, .5, 100, 200);
  for (const [x, y] of [[99, 200], [100, 199], [613, 200], [100, 585], [NaN, 200]]) assert.equal(mapPoint(image, x, y), null);
  assert.equal(mapPoint({...image, naturalWidth: 0}, 100, 200), null);
  assert.equal(mapPoint(displayedImage(1024, 768, 0), 100, 200), null);
  assert.deepEqual(plain(mapPoint(image, 612, 584)), {x: 1023, y: 767});
});

test("touch and wheel distances scale with the visible frame and preserve direction", () => {
  for (const scale of [.3, .75, 1.5]) {
    const image = displayedImage(1024, 768, scale);
    assert.ok(Math.abs(scrollDelta(image, 80 * scale) - 80) < .000001);
    assert.ok(Math.abs(scrollDelta(image, -80 * scale) + 80) < .000001);
  }
  assert.equal(scrollDelta(displayedImage(1024, 768, 0), 100), 0);
  assert.equal(scrollDelta({...displayedImage(1024, 768, .5), naturalHeight: 0}, 100), 0);
});

test("rapid text and wheel bursts coalesce without crossing click/key boundaries", async () => {
  const sent = [];
  const queue = new InputQueue(async events => sent.push(plain(events)), error => { throw error; });
  queue.push({action: "click", x: 10, y: 20});
  for (const text of ["H", "e", "l", "l", "o", "🙂"]) queue.push({action: "text", text});
  queue.push({action: "key", key: "Tab"});
  queue.push({action: "text", text: "next field"});
  for (let n = 0; n < 60; n++) queue.push({action: "scroll", delta: 2});
  await queue.drain();
  assert.deepEqual(sent.flat(), [{action: "click", x: 10, y: 20}, {action: "text", text: "Hello🙂"}, {action: "key", key: "Tab"}, {action: "text", text: "next field"}, {action: "scroll", delta: 120}]);
  assert.equal(sent.length, 1);
});

test("slow connections have one request in flight and preserve every typed character", async () => {
  const sent = []; let release, running = 0, peak = 0;
  const queue = new InputQueue(async events => {
    running++; peak = Math.max(peak, running); sent.push(plain(events));
    if (sent.length === 1) await new Promise(resolve => { release = resolve; });
    running--;
  }, error => { throw error; });
  queue.push({action: "click", x: 1, y: 2}, true);
  await tick();
  for (const text of "abcdefghijklmnopqrstuvwxyz") queue.push({action: "text", text});
  queue.push({action: "key", key: "Enter"}, true);
  assert.equal(sent.length, 1);
  release(); await queue.drain();
  assert.equal(peak, 1);
  assert.equal(sent.length, 2);
  assert.deepEqual(sent[1], [{action: "text", text: "abcdefghijklmnopqrstuvwxyz"}, {action: "key", key: "Enter"}]);
});

test("failed input is not automatically resent and completion reports the failure", async () => {
  let requests = 0; const errors = [];
  const queue = new InputQueue(async () => { requests++; throw new Error("Synthetic timeout"); }, error => errors.push(error.message));
  queue.push({action: "key", key: "Enter"});
  await assert.rejects(queue.drain(), /Synthetic timeout/);
  await tick();
  assert.equal(requests, 1); assert.equal(errors.length, 1);
});

test("changing invitations discards input before dispatch, including a queued microtask", async () => {
  const sent = [];
  const queue = new InputQueue(async events => sent.push(plain(events)), () => {});
  queue.push({action: "text", text: "OLD_SYNTHETIC_INPUT"}, true);
  queue.clear();
  queue.push({action: "text", text: "NEW_SYNTHETIC_INPUT"});
  await queue.drain(); await tick();
  assert.deepEqual(sent, [[{action: "text", text: "NEW_SYNTHETIC_INPUT"}]]);
});

test("a late reply from a prior session cannot flush or erase the new session's queue", async () => {
  const sent = []; let release;
  const queue = new InputQueue(async events => {
    sent.push(plain(events));
    if (sent.length === 1) await new Promise(resolve => { release = resolve; });
  }, error => { throw error; });
  queue.push({action: "text", text: "first"}, true);
  await tick(); queue.clear();
  queue.push({action: "text", text: "second"});
  await queue.drain(); release(); await tick();
  assert.deepEqual(sent, [[{action: "text", text: "first"}], [{action: "text", text: "second"}]]);
});

test("batches remain bounded while draining ordered discrete inputs", async () => {
  const sent = [];
  const queue = new InputQueue(async events => sent.push(plain(events)), error => { throw error; });
  for (let n = 0; n < 60; n++) queue.push({action: "click", x: n, y: 0});
  await queue.drain();
  assert.equal(sent.length, 3); assert.ok(sent.every(events => events.length <= 24));
  assert.deepEqual(sent.flat().map(event => event.x), Array.from({length: 60}, (_, n) => n));
});

test("Unicode text is kept intact across the batch size limit", async () => {
  const sent = [];
  const queue = new InputQueue(async events => sent.push(plain(events)), error => { throw error; });
  queue.push({action: "text", text: "🙂".repeat(1500)});
  queue.push({action: "text", text: "🙂".repeat(1000)});
  await queue.drain();
  assert.equal(sent.length, 2);
  assert.equal([...sent.flat().map(event => event.text).join("")].length, 2500);
});
