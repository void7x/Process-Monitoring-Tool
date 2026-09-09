"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { createDom, loadApp, makeFetchMock, wait, waitFor } = require("./dom");

// Track windows for cleanup (same pattern as dashboard.test.js)
const _openWindows = new Set();
const _origCreateDom = createDom;
function _trackedCreateDom(...args) {
  const dom = _origCreateDom(...args);
  _openWindows.add(dom);
  return dom;
}

function setup({ routes, hash }) {
  const dom = _trackedCreateDom();
  const window = dom.window;
  window.fetch = makeFetchMock(routes);
  window.location.hash = hash || "#alerts";
  const app = loadApp(dom);
  return { dom, window, app };
}

const { afterEach } = require("node:test");
afterEach(() => {
  for (const dom of Array.from(_openWindows)) {
    try {
      const win = dom.window;
      if (win && win.__processMonitor && win.__processMonitor.cleanup) win.__processMonitor.cleanup();
    } catch (_) {}
    _openWindows.delete(dom);
  }
});

const SAMPLE_ALERTS = {
  items: [
    {
      id: 42,
      rule_id: "r1",
      rule_name: "High CPU",
      host: "web-01",
      pid: 1234,
      process_name: "chrome.exe",
      username: "operator",
      create_time: 1700000000,
      metric: "cpu_percent",
      operator: "gt",
      threshold: 80,
      current_value: 95,
      severity: "critical",
      status: "active",
      triggered_at: 1700000050,
      last_seen: 1700000050,
    },
  ],
  total: 1,
  limit: 100,
  offset: 0,
};

const INCIDENT_SUCCESS = {
  incident: {
    id: 42,
    rule_id: "r1",
    rule_name: "High CPU",
    host: "web-01",
    pid: 1234,
    process_name: "chrome.exe",
    username: "operator",
    create_time: 1700000000,
    metric: "cpu_percent",
    operator: "gt",
    threshold: 80,
    current_value: 95,
    severity: "critical",
    status: "active",
    triggered_at: 1700000050,
    last_seen: 1700000050,
  },
  process: {
    host: "web-01",
    pid: 1234,
    process_name: "chrome.exe",
    username: "operator",
    create_time: 1700000000,
    started_time: "Sep 14, 2023",
    status: "running",
    age_seconds_at_alert: 50,
  },
  summary: {
    metric: "cpu_percent",
    current_value: 95,
    previous_value: 45,
    peak_value: 95,
    delta: 50,
    memory_delta_bytes: 10485760,
    duration_seconds: 10,
    window_start: 1699999990,
    window_end: 1700000050,
    window_seconds: 60,
    sample_count: 5,
    is_insufficient: false,
  },
  timeline: [
    { timestamp: 1700000000.1, time: "12:00:00", cpu_percent: 20, memory_percent: 10, memory_rss: 50000000, state: "running", is_start: true, label: "Process started" },
    { timestamp: 1700000010, time: "12:00:10", cpu_percent: 30, memory_percent: 11, memory_rss: 51000000, state: "running", label: "12:00:10 — CPU 30.0% · 48.6 MB" },
    { timestamp: 1700000030, time: "12:00:30", cpu_percent: 45, memory_percent: 12, memory_rss: 52000000, state: "running", label: "12:00:30 — CPU 45.0% · 49.6 MB" },
    { timestamp: 1700000050, time: "12:00:50", cpu_percent: 95, memory_percent: 15, memory_rss: 60000000, state: "running", is_alert: true, label: "Alert triggered: High CPU" },
  ],
  evidence: [
    "Process chrome.exe (PID 1234) on web-01 breached cpu_percent threshold 80.0%: 95.0% at alert.",
    "CPU increased sharply (+50.0) before the alert.",
    "Duration above threshold ( > 80.0%): 10s.",
    "Peak CPU in window was 95.0%.",
  ],
  window: {
    start: 1699999990,
    end: 1700000050,
    seconds: 60,
  },
};

const INCIDENT_INSUFFICIENT = {
  incident: {
    id: 43,
    rule_id: "r1",
    rule_name: "High CPU",
    host: "web-01",
    pid: 1234,
    process_name: "chrome.exe",
    create_time: 1700000000,
    metric: "cpu_percent",
    operator: "gt",
    threshold: 80,
    current_value: 85,
    severity: "warning",
    status: "active",
    triggered_at: 1700000010,
  },
  process: {
    host: "web-01",
    pid: 1234,
    process_name: "chrome.exe",
    create_time: 1700000000,
    status: "running",
  },
  summary: {
    metric: "cpu_percent",
    current_value: 85,
    previous_value: null,
    peak_value: 85,
    delta: null,
    memory_delta_bytes: 0,
    duration_seconds: 0,
    window_start: 1699999950,
    window_end: 1700000010,
    window_seconds: 60,
    sample_count: 1,
    is_insufficient: true,
  },
  timeline: [
    { timestamp: 1700000000, time: "12:00:00", is_start: true, label: "Process started" },
    { timestamp: 1700000010, time: "12:00:10", cpu_percent: 85, is_alert: true, label: "Alert triggered" },
  ],
  evidence: [
    "Only 1 samples in the 60s window before the alert.",
    "Process chrome.exe (PID 1234) on web-01 breached cpu_percent threshold 80.0%: 85.0% at alert.",
    "Insufficient telemetry to determine the cause.",
  ],
  window: { start: 1699999950, end: 1700000010, seconds: 60 },
};

test("incident: View Incident button is present on alerts", async () => {
  const { window } = setup({
    routes: {
      "/alerts": SAMPLE_ALERTS,
      "/hosts": { items: [], total: 0 },
    },
    hash: "#alerts",
  });
  await waitFor(() => window.document.body.textContent.includes("View Incident"));
  const text = window.document.body.textContent;
  assert.match(text, /View Incident/);
  const btn = window.document.querySelector('[data-action="view-incident"]');
  assert.ok(btn, "View Incident button must be present");
  assert.equal(btn.getAttribute("data-alert-id"), "42");
});

test("incident: loading state appears before data resolves", async () => {
  let resolveIncident;
  const pending = new Promise((resolve) => { resolveIncident = resolve; });
  const { window } = setup({
    routes: {
      "/alerts": SAMPLE_ALERTS,
    },
    hash: "#alerts",
  });
  // Override fetch to return a controllable promise for the incident endpoint
  const originalFetch = window.fetch;
  window.fetch = (url, init) => {
    if (String(url).includes("/incidents/42")) {
      return pending.then((data) => ({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      }));
    }
    return originalFetch(url, init);
  };
  await waitFor(() => window.document.querySelector('[data-action="view-incident"]'));
  const btn = window.document.querySelector('[data-action="view-incident"]');
  btn.click();
  // Immediately after click, drawer should show loading
  await wait(30);
  let drawer = window.document.querySelector(".drawer");
  assert.ok(drawer, "Drawer should be present after click");
  assert.match(drawer.textContent, /Analyzing incident/);
  // Now resolve the promise and ensure drawer updates to success
  resolveIncident(INCIDENT_SUCCESS);
  await waitFor(() => window.document.querySelector(".drawer") && window.document.querySelector(".drawer").textContent.includes("Evidence"));
  drawer = window.document.querySelector(".drawer");
  assert.match(drawer.textContent, /High CPU/);
  // Resolve should also show evidence
  assert.match(drawer.textContent, /CPU increased sharply/);
});

test("incident: success renders summary, timeline and evidence", async () => {
  const { window } = setup({
    routes: {
      "/alerts": SAMPLE_ALERTS,
      "/incidents/42": INCIDENT_SUCCESS,
    },
    hash: "#alerts",
  });
  await waitFor(() => window.document.querySelector('[data-action="view-incident"]'));
  const btn = window.document.querySelector('[data-action="view-incident"]');
  btn.click();
  await waitFor(() => window.document.querySelector(".drawer") && window.document.querySelector(".drawer").textContent.includes("Evidence"));
  const drawer = window.document.querySelector(".drawer");
  assert.ok(drawer, "Incident drawer must be present");
  // Check process info
  assert.match(drawer.textContent, /chrome\.exe/);
  assert.match(drawer.textContent, /PID 1234/);
  assert.match(drawer.textContent, /web-01/);
  // Check summary
  assert.match(drawer.textContent, /Current/);
  assert.match(drawer.textContent, /Previous/);
  assert.match(drawer.textContent, /Peak/);
  // Timeline
  assert.match(drawer.textContent, /Timeline/);
  assert.match(drawer.textContent, /12:00:10/);
  // Evidence
  assert.match(drawer.textContent, /Evidence/);
  assert.match(drawer.textContent, /Peak CPU/);
  // The drawer includes a disclaimer that mentions "malware" as an example of
  // what is NOT claimed ("It will never claim malware...") — allow that
  // phrasing, but ensure the evidence itself does not fabricate a malware
  // cause such as "malware detected" or "malware caused".
  assert.doesNotMatch(drawer.textContent, /malware detected/i);
  assert.doesNotMatch(drawer.textContent, /malware caused/i);
  assert.match(drawer.textContent, /It will never claim malware/);
});

test("incident: insufficient telemetry shows dedicated message", async () => {
  const { window } = setup({
    routes: {
      "/alerts": { items: [{ ...SAMPLE_ALERTS.items[0], id: 43 }], total: 1, limit: 100, offset: 0 },
      "/incidents/43": INCIDENT_INSUFFICIENT,
    },
    hash: "#alerts",
  });
  await waitFor(() => window.document.querySelector('[data-action="view-incident"]'));
  const btn = window.document.querySelector('[data-action="view-incident"]');
  btn.click();
  await waitFor(() => window.document.querySelector(".drawer") && window.document.querySelector(".drawer").textContent.includes("Insufficient telemetry"));
  const drawer = window.document.querySelector(".drawer");
  assert.match(drawer.textContent, /Insufficient telemetry/);
  assert.match(drawer.textContent, /Only 1 samples/);
  // Still shows a timeline (process start + alert) and evidence, even in insufficient mode
  assert.match(drawer.textContent, /Process started/);
  assert.match(drawer.textContent, /Evidence/);
});

test("incident: error state with retry is shown when fetch fails", async () => {
  const { window } = setup({
    routes: {
      "/alerts": SAMPLE_ALERTS,
      "/incidents/42": { body: { error: { message: "not found" } }, status: 404, ok: false },
    },
    hash: "#alerts",
  });
  // Mock fetch to simulate failure by returning ok:false
  const originalFetch = window.fetch;
  window.fetch = (url, init) => {
    if (url.includes("/incidents/")) {
      return Promise.resolve({
        ok: false,
        status: 404,
        json: () => Promise.resolve({ error: { message: "Incident was not found." } }),
      });
    }
    return originalFetch(url, init);
  };
  await waitFor(() => window.document.querySelector('[data-action="view-incident"]'));
  const btn = window.document.querySelector('[data-action="view-incident"]');
  btn.click();
  await waitFor(() => window.document.querySelector(".drawer") && window.document.querySelector(".drawer").textContent.includes("could not be loaded"));
  const drawer = window.document.querySelector(".drawer");
  assert.match(drawer.textContent, /Incident analysis could not be loaded/);
  assert.match(drawer.textContent, /Retry/);
});
