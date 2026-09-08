"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { createDom, loadApp, makeFetchMock, wait, waitFor } = require("./dom");

// Track windows so we can clean up intervals after each test and avoid hanging
// when multiple test files are run together via `node --test ui/tests/*.test.js`.
const _openWindows = new Set();
const _origCreateDom = createDom;
function _trackedCreateDom(...args) {
  const dom = _origCreateDom(...args);
  _openWindows.add(dom);
  return dom;
}
// Monkey-patch the helper used by tests via closure - expose tracked version
// Tests that use `createDom` directly will now be tracked; we also patch `setup`.


function setup({ routes }) {
  const dom = _trackedCreateDom();
  const window = dom.window;
  // Install a controllable fetch mock.
  window.fetch = makeFetchMock(routes);
  // Start in a known page state.
  window.location.hash = "#overview";
  const app = loadApp(dom);
  return { dom, window, app };
}

// Ensure JSDOM windows are closed after each test to clear intervals
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

test("app.js loads without throwing", () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  window.fetch = makeFetchMock({
    "/summary": { kpis: {}, hosts: [] },
    "/metrics/summary": { cpu: [], memory: [], activity: [], alerts: [] },
    "/health": { status: "ok", service: "collector", version: "1.0.0", time: 1 },
  });
  assert.doesNotThrow(() => loadApp(dom));
});

test("overview renders KPI cards when API responds", async () => {
  const { window } = setup({
    routes: {
      "/summary": {
        kpis: {
          live_hosts: 3,
          total_hosts: 4,
          total_running_processes: 17,
          active_alerts: 2,
          cpu_percent: 25.5,
          memory_percent: 48.2,
          samples_received: 1234,
          stale_hosts: 0,
          offline_hosts: 1,
        },
        hosts: [
          { host: "web-01", status: "live", process_count: 12, cpu_percent: 30, memory_percent: 60, active_alert_count: 1, activity: [], last_seen: 100, sample_count: 50 },
        ],
      },
      "/metrics/summary": {
        cpu: [{ timestamp: 1, value: 10 }, { timestamp: 2, value: 20 }],
        memory: [{ timestamp: 1, value: 40 }, { timestamp: 2, value: 50 }],
        activity: [{ timestamp: 1, value: 5 }],
        alerts: [],
      },
    },
  });
  await waitFor(() => {
    const cards = window.document.querySelectorAll(".kpi-card");
    return cards.length >= 5;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /17/); // running processes value
  assert.match(text, /Active alerts/);
  assert.match(text, /Live hosts/);
  assert.match(text, /Observed CPU/);
  // KPI cards carry a tone class for the colored left border.
  assert.ok(window.document.querySelector(".kpi-card.tone-green"), "green-tone KPI must have tone class");
  assert.ok(window.document.querySelector(".kpi-card.tone-red"), "red-tone KPI must have tone class");
  assert.ok(window.document.querySelector(".kpi-card.tone-purple"), "purple-tone KPI must have tone class");
});

test("process list renders rows from /processes", async () => {
  const { window } = setup({
    routes: {
      "/processes": {
        items: [
          { host: "h1", pid: 100, process_name: "alpha", username: "u", create_time: 1.0,
            cpu_percent: 12, memory_percent: 1, memory_rss: 1000, read_bytes: 0, write_bytes: 0,
            state: "running", has_active_alert: false, received_at: 100 },
        ],
        total: 1,
        page: 1,
        page_size: 50,
      },
      "/hosts": { items: [], total: 0 },
    },
  });
  window.location.hash = "#processes";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => {
    return window.document.querySelector("#processTable tbody tr") !== null;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /alpha/);
  assert.match(text, /100/);
});

test("process list empty state", async () => {
  const { window } = setup({
    routes: {
      "/processes": { items: [], total: 0, page: 1, page_size: 50 },
      "/hosts": { items: [], total: 0 },
    },
  });
  window.location.hash = "#processes";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => {
    return window.document.querySelector(".empty-state") !== null;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /No processes match/);
});

test("process list shows error state on API failure", async () => {
  const { window } = setup({
    routes: {
      // The mock returns 404 by default when no pattern matches.
    },
  });
  window.location.hash = "#processes";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => {
    return window.document.querySelector(".error-state") !== null;
  });
});

test("alerts page renders alert list", async () => {
  const { window } = setup({
    routes: {
      "/alerts": {
        items: [
          { id: 1, rule_name: "High CPU", severity: "critical", status: "active",
            host: "h1", pid: 5, process_name: "p1", create_time: 1.0,
            metric: "cpu_percent", current_value: 95, threshold: 80,
            triggered_at: 100 },
          { id: 2, rule_name: "Memory watch", severity: "warning", status: "resolved",
            host: "h2", pid: 9, process_name: "p2", create_time: 2.0,
            metric: "memory_percent", current_value: 70, threshold: 50,
            triggered_at: 200, resolved_at: 220 },
        ],
        total: 2,
        limit: 100,
        offset: 0,
      },
    },
  });
  window.location.hash = "#alerts";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => {
    return window.document.querySelectorAll(".alert-row").length >= 2;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /High CPU/);
  assert.match(text, /p1/);
  assert.match(text, /Memory watch/);
  // Severity classes are applied for visual distinction.
  assert.ok(window.document.querySelector(".alert-row.alert-critical"), "critical class must be applied");
  assert.ok(window.document.querySelector(".alert-row.alert-warning"), "warning class must be applied");
  assert.ok(window.document.querySelector(".alert-row.alert-resolved"), "resolved class must be applied");
});

test("alerts page empty state when no alerts", async () => {
  const { window } = setup({
    routes: { "/alerts": { items: [], total: 0, limit: 100, offset: 0 } },
  });
  window.location.hash = "#alerts";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => {
    return window.document.querySelector(".empty-state") !== null;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /No active alerts/);
});

test("rules page lists alert rules", async () => {
  const { window } = setup({
    routes: {
      "/alerts/rules": {
        items: [
          { rule_id: "r1", name: "Memory watch", metric: "memory_percent", operator: "gt",
            threshold: 50, duration: 30, consecutive_samples: 2, cooldown: 60,
            severity: "warning", action: "none", enabled: true },
        ],
        total: 1,
      },
    },
  });
  window.location.hash = "#rules";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => {
    return window.document.querySelector(".rule-card") !== null;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /Memory watch/);
  assert.match(text, /r1/);
});

test("rule form submit POSTs to /alerts/rules", async () => {
  const posts = [];
  const dom = _trackedCreateDom();
  const window = dom.window;
  const calls = [];
  window.fetch = (url, init) => {
    const urlString = typeof url === "string" ? url : url.url;
    calls.push({ url: urlString, method: init && init.method });
    if (urlString && urlString.includes("/alerts/rules") && init && init.method === "POST") {
      const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
      posts.push({ url: urlString, body });
      return Promise.resolve({
        ok: true,
        status: 201,
        json: () =>
          Promise.resolve({
            rule_id: "new-rule",
            name: "New",
            metric: "cpu_percent",
            operator: "gt",
            threshold: 50,
            duration: 0,
            consecutive_samples: 1,
            cooldown: 60,
            severity: "warning",
            action: "none",
            enabled: true,
            created_at: 0,
            updated_at: 0,
          }),
      });
    }
    if (urlString && urlString.includes("/alerts/rules")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ items: [], total: 0 }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  window.location.hash = "#rules";
  loadApp(dom);
  const buttonFound = await waitFor(() => window.document.querySelector('[data-action="new-rule"]') !== null, { timeout: 3000 });
  assert.ok(buttonFound, "Create rule button must be present");
  const button = window.document.querySelector('[data-action="new-rule"]');
  button.click();
  await wait(100);
  const formFound = await waitFor(() => window.document.getElementById("ruleForm") !== null, { timeout: 3000 });
  assert.ok(formFound, "ruleForm must be present after click");
  const form = window.document.getElementById("ruleForm");
  form.querySelector("#ruleId").value = "new-rule";
  form.querySelector("#ruleName").value = "New";
  form.querySelector("#ruleThreshold").value = "50";
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    const submit = new window.Event("submit", { cancelable: true, bubbles: true });
    form.dispatchEvent(submit);
  }
  const posted = await waitFor(() => posts.length === 1, { timeout: 3000 });
  if (!posted) {
    assert.fail(`Expected 1 POST, got ${posts.length}. Calls: ${JSON.stringify(calls)}`);
  }
  assert.equal(posts[0].body.rule_id, "new-rule");
  assert.equal(posts[0].body.metric, "cpu_percent");
});

test("process sort header toggles sort order", async () => {
  let lastSort;
  const { window } = setup({
    routes: {
      "/processes": ({ url }) => {
        const params = new URL(url, "http://localhost").searchParams;
        lastSort = { sort: params.get("sort"), order: params.get("order") };
        return {
          items: [
            { host: "h", pid: 1, process_name: "p", username: "u",
              create_time: 1.0, cpu_percent: 0, memory_percent: 0,
              memory_rss: 0, read_bytes: 0, write_bytes: 0,
              state: "running", has_active_alert: false, received_at: 0 },
          ],
          total: 1,
          page: 1,
          page_size: 50,
        };
      },
      "/hosts": { items: [], total: 0 },
    },
  });
  window.location.hash = "#processes";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await wait(100);
  await waitFor(() => {
    return window.document.querySelector('[data-action="sort-process"][data-sort="pid"]') !== null;
  }, { timeout: 3000 });
  const sortButton = window.document.querySelector('[data-action="sort-process"][data-sort="pid"]');
  assert.ok(sortButton, "Sort button for PID must be present");
  sortButton.click();
  await waitFor(() => lastSort && lastSort.sort === "pid");
  assert.equal(lastSort.order, "desc");
  sortButton.click();
  await waitFor(() => lastSort && lastSort.order === "asc");
});

test("process search input is debounced and forwarded", async () => {
  let lastSearch = "";
  const { window } = setup({
    routes: {
      "/processes": ({ url }) => {
        const params = new URL(url, "http://localhost").searchParams;
        lastSearch = params.get("search") || "";
        return { items: [], total: 0, page: 1, page_size: 50 };
      },
      "/hosts": { items: [], total: 0 },
    },
  });
  window.location.hash = "#processes";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await wait(100);
  // The toolbar is rendered before the API call returns, so the search
  // input is present immediately.  We don't need to wait for the table.
  await waitFor(() => window.document.getElementById("processSearch") !== null, { timeout: 3000 });
  const search = window.document.getElementById("processSearch");
  assert.ok(search, "processSearch input must be present");
  search.value = "alpha";
  search.dispatchEvent(new window.Event("input", { bubbles: true }));
  await waitFor(() => lastSearch === "alpha", { timeout: 1500 });
  assert.equal(lastSearch, "alpha");
});

test("history page renders retained samples", async () => {
  const { window } = setup({
    routes: {
      "/history": {
        items: [
          { host: "h1", pid: 9, process_name: "legacy", username: "u",
            create_time: 1.0, timestamp: 100.0,
            cpu_percent: 1, memory_percent: 1, memory_rss: 0, read_bytes: 0, write_bytes: 0 },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      },
    },
  });
  window.location.hash = "#history";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => {
    return window.document.querySelector("#historyTable tbody tr") !== null;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /legacy/);
});

test("API failure sets the connection pill to offline", async () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  // Always-fail fetch.
  window.fetch = () =>
    Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) });
  window.location.hash = "#overview";
  loadApp(dom);
  await wait(100);
  const pill = window.document.getElementById("connectionPill");
  assert.ok(pill);
  assert.ok(pill.classList.contains("error"));
});

test("connection pill shows Connected when API is healthy", async () => {
  const dom = _trackedCreateDom();
  const window = dom.window;
  window.fetch = (url) => {
    if (typeof url === "string" && url.includes("/health")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok", version: "1.0.0" }) });
    }
    if (typeof url === "string" && url.includes("/summary")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ kpis: {}, hosts: [] }) });
    }
    if (typeof url === "string" && url.includes("/metrics/summary")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ cpu: [], memory: [], activity: [], alerts: [] }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  loadApp(dom);
  await waitFor(() => {
    const pill = window.document.getElementById("connectionPill");
    return pill && pill.classList.contains("live");
  });
});
