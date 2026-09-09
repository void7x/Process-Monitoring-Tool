"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { createDom, loadApp, makeFetchMock, wait, waitFor } = require("./dom");

const _openWindows = new Set();
const _origCreateDom = createDom;
function _trackedCreateDom(...args) {
  const dom = _origCreateDom(...args);
  _openWindows.add(dom);
  return dom;
}
const { afterEach } = require("node:test");
afterEach(() => {
  for (const dom of Array.from(_openWindows)) {
    try {
      const win = dom.window;
      if (win && win.__processMonitor && win.__processMonitor.cleanup) win.__processMonitor.cleanup();
    } catch (_) {}
    // Do not close the window immediately - async test callbacks may still
    // reference the document.  Clearing intervals is sufficient to let the
    // Node event loop exit.  The JSDOM window will be GC'd after the test.
    // Intentionally keep the dom in the set until next test's cleanup to
    // avoid double-close errors - but we still remove it to prevent leak.
    _openWindows.delete(dom);
  }
});

test("auth token from localStorage is sent as Authorization header", async () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  let capturedHeaders = null;
  window.localStorage.setItem("process-monitor-auth-token", "test-token-123");
  window.fetch = (url, init) => {
    const urlString = typeof url === "string" ? url : url.url;
    if (urlString.includes("/summary")) {
      capturedHeaders = init && init.headers ? init.headers : {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ kpis: {}, hosts: [], freshness: {} }) });
    }
    if (urlString.includes("/metrics/summary")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ cpu: [], memory: [], activity: [], alerts: [] }) });
    }
    if (urlString.includes("/health")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok", service: "collector", version: "1.0.0", time: 1 }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  loadApp(dom);
  await waitFor(() => {
    return capturedHeaders !== null;
  }, { timeout: 2000 });
  assert.ok(capturedHeaders, "fetch should have been called");
  const auth = capturedHeaders.Authorization || capturedHeaders.authorization || capturedHeaders["X-Auth-Token"] || capturedHeaders["x-auth-token"];
  assert.ok(auth, "Authorization header must be present when token is stored");
  assert.match(String(auth), /test-token-123/);
});

test("auth headers include both Authorization Bearer and X-Auth-Token", async () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  window.localStorage.setItem("process-monitor-auth-token", "abc-xyz");
  let headers = null;
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (url.includes("/summary")) {
      headers = init && init.headers;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ kpis: {}, hosts: [] }) });
    }
    if (url.includes("/metrics/summary")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ cpu: [], memory: [], activity: [], alerts: [] }) });
    if (url.includes("/health")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok", version: "1.0.0" }) });
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  loadApp(dom);
  await waitFor(() => headers !== null, { timeout: 2000 });
  assert.ok(headers, "headers captured");
  assert.equal(headers.Authorization, "Bearer abc-xyz");
  assert.equal(headers["X-Auth-Token"], "abc-xyz");
});

test("settings page shows auth token input and allows saving", async () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  window.localStorage.removeItem("process-monitor-auth-token");
  window.fetch = makeFetchMock({
    "/summary": { kpis: {}, hosts: [], window_seconds: 3600, freshness: {} },
    "/metrics/summary": { cpu: [], memory: [], activity: [], alerts: [] },
    "/health": { status: "ok", service: "collector", version: "1.0.0", time: 1 },
  });
  loadApp(dom);
  window.location.hash = "#settings";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => window.document.getElementById("authTokenInput") !== null, { timeout: 3000 });
  const input = window.document.getElementById("authTokenInput");
  assert.ok(input, "authTokenInput must exist in settings");
  const save = window.document.getElementById("authSaveButton");
  assert.ok(save, "authSaveButton must exist");
  input.value = "new-token-999";
  save.click();
  await wait(100);
  const stored = window.localStorage.getItem("process-monitor-auth-token");
  assert.equal(stored, "new-token-999");
  // The helper exposed for tests should reflect the new token
  assert.equal(window.__processMonitor.getAuthToken(), "new-token-999");
  // cleanup timers and dom
  try { window.__processMonitor.cleanup && window.__processMonitor.cleanup(); } catch(_) {}
});

test("clear token button removes stored token", async () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  window.localStorage.setItem("process-monitor-auth-token", "to-be-cleared");
  window.fetch = makeFetchMock({
    "/summary": { kpis: {}, hosts: [], window_seconds: 3600, freshness: {} },
    "/metrics/summary": { cpu: [], memory: [], activity: [], alerts: [] },
    "/health": { status: "ok", version: "1.0.0" },
  });
  loadApp(dom);
  window.location.hash = "#settings";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => window.document.getElementById("authClearButton") !== null, { timeout: 3000 });
  const clear = window.document.getElementById("authClearButton");
  clear.click();
  await wait(100);
  assert.equal(window.localStorage.getItem("process-monitor-auth-token"), null);
  assert.equal(window.__processMonitor.getAuthToken(), "");
  try { window.__processMonitor.cleanup && window.__processMonitor.cleanup(); } catch(_) {}
});

test("401 responses surface auth error but do not break app", async () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  window.localStorage.setItem("process-monitor-auth-token", "bad-token");
  window.fetch = (url, init) => {
    const s = typeof url === "string" ? url : url.url;
    if (s.includes("/health")) {
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({ error: { message: "A valid auth token is required." } }) });
    }
    if (s.includes("/summary")) {
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({ error: { message: "unauthorized" } }) });
    }
    if (s.includes("/metrics/summary")) {
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({ error: { message: "unauthorized" } }) });
    }
    return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({ error: { message: "unauthorized" } }) });
  };
  // Should not throw
  assert.doesNotThrow(() => loadApp(dom));
  await wait(200);
  const pill = window.document.getElementById("connectionPill");
  assert.ok(pill);
  // After 401, pill should be in error (offline) state
  assert.ok(pill.classList.contains("error"), "connection pill should be error after 401");
  try { window.__processMonitor.cleanup && window.__processMonitor.cleanup(); } catch(_) {}
});
