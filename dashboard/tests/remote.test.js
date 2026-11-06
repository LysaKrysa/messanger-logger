"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const context = {setTimeout, clearTimeout};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../static/remote.js"), "utf8"), context);
const {InputQueue} = context.MessengerRemote;
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

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
