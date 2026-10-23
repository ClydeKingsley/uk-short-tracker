"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const webRoot = join(__dirname, "..", "web");
const source = readFileSync(join(webRoot, "app.js"), "utf8");
const catalog = readFileSync(join(webRoot, "i18n.js"), "utf8");

function status({ running = false, error = null, database = null, automatic = {}, time = "2026-09-15T10:00:00Z" } = {}) {
  return {
    status: running ? "syncing" : "ready",
    last_sync_at: time,
    sync: { running, last_error: error, database_last_run: database },
    auto_sync: { running, last_error: error, ...automatic },
  };
}

// Execute the real application functions without starting the UI, contacting a
// server, or waiting in real time. Only I/O boundaries are replaced in the VM.
function harness(responses) {
  let now = 0;
  const calls = { requests: [], toasts: [], rankings: 0, security: 0 };
  const elements = new Map();
  const element = () => ({
    disabled: false,
    hidden: false,
    textContent: "",
    classList: { add() {}, remove() {}, toggle() {} },
  });
  const context = vm.createContext({
    document: {
      readyState: "loading",
      addEventListener() {},
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
    },
    Date: class extends Date { static now() { return now; } },
    window: {
      localStorage: { getItem() { return null; } },
      __fetch: async (path) => {
        calls.requests.push(path);
        assert.ok(responses.length, "unexpected extra status poll");
        const next = responses.shift();
        if (next instanceof Error) throw next;
        return next;
      },
      __wait: async (milliseconds) => { now += milliseconds; },
      __toast: (...args) => calls.toasts.push(args),
      __rankings: async () => { calls.rankings += 1; },
      __security: async () => { calls.security += 1; },
    },
  });
  vm.runInContext(catalog, context);
  const instrumented = source.replace(/\}\)\(\);\s*$/, `
    apiFetch = window.__fetch;
    waitDelay = window.__wait;
    showToast = window.__toast;
    loadRankings = window.__rankings;
    loadSecurity = window.__security;
    configureFormatters();
    window.__test = { state, dom, t, syncHasFailed, waitForSyncCompletion, loadStatus, syncData };
  })();`);
  assert.notEqual(instrumented, source, "application test hook must be injected");
  vm.runInContext(instrumented, context);
  return { ...context.window.__test, calls };
}

test("a completed sync failure stops after one successful HTTP poll", async () => {
  const h = harness([status({ error: "FCA schema changed" })]);
  await assert.rejects(h.waitForSyncCompletion(), (error) => error.payload?.error?.code === "sync_failed");
  assert.equal(h.calls.requests.length, 1);
  assert.equal(h.calls.toasts.length, 0);
});

test("temporary transport errors retry and reset after a successful poll", async () => {
  const ready = status({ time: "2026-10-23T10:00:00Z" });
  const h = harness([new Error("offline"), status({ running: true }), new Error("offline"), ready]);
  assert.equal(await h.waitForSyncCompletion(), h.state.status);
  assert.equal(h.calls.requests.length, 4);
  assert.equal(h.calls.toasts.length, 1);
  assert.equal(h.calls.toasts[0][0], h.t("sync.complete"));
  assert.equal(h.state.observedSyncRunning, false);
  assert.equal(h.state.observedLastSyncAt, ready.last_sync_at);
});

test("three consecutive transport failures stop polling", async () => {
  const h = harness([new Error("one"), new Error("two"), new Error("three")]);
  await assert.rejects(h.waitForSyncCompletion(), /three/);
  assert.equal(h.calls.requests.length, 3);
  assert.equal(h.calls.toasts.length, 0);
});

test("manual sync failure shows an error, keeps the old dataset and unlocks the button", async () => {
  const h = harness([{ accepted: true, status: "queued" }, status({ error: "FCA schema changed" })]);
  await h.syncData();
  assert.equal(h.calls.requests.length, 2);
  assert.equal(h.calls.rankings, 0);
  assert.equal(h.dom.syncButton.disabled, false);
  assert.equal(h.state.connectionState.labelKey, "connection.syncFailed");
  assert.equal(h.state.connectionState.detailKey, "sync.failedDetail");
  assert.equal(h.calls.toasts.at(-1)[2], "error");
  assert.equal(h.calls.toasts.at(-1)[1], h.t("sync.failedDetail"));
  assert.ok(!h.calls.toasts.some(([title]) => title === h.t("sync.complete") || title === h.t("sync.stillRunning")));
});

test("background failure is not completion and its error survives later quiet polls", async () => {
  const failed = status({ error: "FCA schema changed" });
  const h = harness([failed, failed]);
  h.state.observedSyncRunning = true;
  h.state.observedLastSyncAt = failed.last_sync_at;
  await h.loadStatus({ quiet: true });
  await h.loadStatus({ quiet: true });
  assert.equal(h.calls.rankings, 0);
  assert.equal(h.calls.toasts.length, 1);
  assert.equal(h.calls.toasts[0][2], "error");
  assert.equal(h.state.connectionState.labelKey, "connection.syncFailed");
  assert.equal(h.dom.connectionBanner.hidden, false);
});

test("persisted database failure remains visible after a restart", async () => {
  const h = harness([status({ database: { status: "failed", error: "FCA schema changed" } })]);
  await h.loadStatus();
  assert.equal(h.state.connectionState.labelKey, "connection.syncFailed");
  assert.equal(h.dom.settingsSyncState.textContent, h.t("settings.stateFailed"));
  assert.equal(h.calls.rankings, 0);
});

test("a running retry takes precedence over the earlier persisted failure", async () => {
  const h = harness([status({ running: true, database: { status: "failed" }, automatic: { last_error: "previous failure" } })]);
  await h.loadStatus({ quiet: true });
  assert.equal(h.state.connectionState.labelKey, "connection.syncing");
  assert.equal(h.calls.toasts.length, 0);
});

test("background success reloads once and does not repeat completion on later polls", async () => {
  const ready = status({ time: "2026-10-23T10:00:00Z", database: { status: "success" } });
  const h = harness([ready, ready]);
  h.state.observedSyncRunning = true;
  h.state.observedLastSyncAt = "2026-09-15T10:00:00Z";
  await h.loadStatus({ quiet: true });
  await h.loadStatus({ quiet: true });
  assert.equal(h.calls.rankings, 1);
  assert.equal(h.calls.toasts.length, 1);
  assert.equal(h.calls.toasts[0][0], h.t("sync.complete"));
  assert.equal(h.state.connectionState.kind, "online");
});

test("changed last-success time cannot announce completion while still running", async () => {
  const h = harness([status({ running: true, time: "2026-10-23T10:00:00Z" })]);
  h.state.observedLastSyncAt = "2026-09-15T10:00:00Z";
  await h.loadStatus({ quiet: true });
  assert.equal(h.calls.rankings, 0);
  assert.equal(h.calls.toasts.length, 0);
});

test("database success takes precedence over a not-yet-updated scheduler error", async () => {
  const h = harness([status({ database: { status: "success" }, automatic: { last_error: "previous failure" } })]);
  await h.loadStatus({ quiet: true });
  assert.equal(h.state.connectionState.kind, "online");
  assert.notEqual(h.dom.settingsSyncState.textContent, h.t("settings.stateFailed"));
});

test("scheduler failure without a database run also remains visible", async () => {
  const h = harness([status({ automatic: { last_error: "A previous sync was interrupted" } })]);
  await h.loadStatus({ quiet: true });
  assert.equal(h.state.connectionState.labelKey, "connection.syncFailed");
});

test("quiet polling does not duplicate manual sync notifications", async () => {
  const h = harness([status({ error: "FCA schema changed" })]);
  h.dom.syncButton.disabled = true;
  h.state.observedSyncRunning = true;
  await h.loadStatus({ quiet: true });
  assert.equal(h.calls.toasts.length, 0);
  assert.equal(h.state.connectionState.labelKey, "connection.syncFailed");
});

test("the bilingual banner distinguishes sync failure from connection failure", async () => {
  const failed = status({ error: "FCA schema changed" });
  const h = harness([failed, failed, new Error("offline"), status()]);
  await h.loadStatus();
  assert.equal(h.dom.connectionBannerTitle.textContent, "本次数据同步未完成");
  assert.equal(h.dom.connectionMessage.textContent, h.t("sync.failedDetail"));
  h.state.language = "en-GB";
  await h.loadStatus();
  assert.equal(h.dom.connectionBannerTitle.textContent, "The data sync did not complete");
  await h.loadStatus();
  assert.equal(h.dom.connectionBannerTitle.textContent, h.t("connection.errorTitle"));
  assert.equal(h.dom.connectionMessage.textContent, h.t("connection.errorDetail"));
  await h.loadStatus();
  assert.equal(h.dom.connectionBanner.hidden, true);
});
