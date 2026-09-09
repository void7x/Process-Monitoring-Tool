"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { createDom, loadApp, makeFetchMock, wait, waitFor } = require("./dom");

const _doms = [];
test.afterEach(() => {
  while (_doms.length) {
    const entry = _doms.pop();
    try { entry.window.__processMonitor?.cleanup?.(); } catch {}
    // keep window alive to avoid async write-after-close unhandledRejection in rule-form test
  }
});

function setup({ routes }) {
  const dom = createDom();
  _doms.push(dom);
  const window = dom.window;
  window.fetch = makeFetchMock(routes);
  window.location.hash = "#overview";
  const app = loadApp(dom);
  return { dom, window, app };
}

test("app.js loads without throwing", () => {
  const dom = createDom();
  _doms.push(dom);
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
      "/alerts": { items: [], total: 0, limit: 6, offset: 0 },
    },
  });
  await waitFor(() => {
    const cards = window.document.querySelectorAll(".kpi-card");
    return cards.length >= 4;
  });
  const text = window.document.getElementById("pageContent").textContent;
  assert.match(text, /Process Monitoring/);
  assert.match(text, /17/); // running processes value
  assert.match(text, /Active Alerts/);
  assert.match(text, /Hosts/);
  assert.match(text, /Samples/);
  assert.match(text, /CPU ACTIVITY/);
  assert.match(text, /MEMORY ACTIVITY/);
  assert.match(text, /SYSTEM HEALTH/);
  // KPI cards carry a tone class for the colored left border.
  assert.ok(window.document.querySelector(".kpi-card.tone-green"), "green-tone KPI must have tone class");
  assert.ok(window.document.querySelector(".kpi-card.tone-red"), "red-tone KPI must have tone class");
  assert.ok(window.document.querySelector(".kpi-card.tone-purple"), "purple-tone KPI must have tone class");
  // charts should render SVG when data exists
  assert.ok(window.document.querySelector("#overviewCpuChart svg"), "CPU chart should render SVG");
  assert.ok(window.document.querySelector("#overviewMemoryChart svg"), "Memory chart should render SVG");
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
  // new composition: table headers PROCESS/PID/CPU/MEMORY/STATE/ACTION
  const headerText = window.document.querySelector("#processTable thead").textContent;
  assert.match(headerText, /PROCESS/);
  assert.match(headerText, /PID/);
  assert.match(headerText, /STATE/);
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
  // scannable View Incident button
  assert.ok(window.document.querySelector('[data-action="view-incident"]'), "View Incident must be present");
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
  const dom = createDom();
  _doms.push(dom);
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
  const dom = createDom();
  _doms.push(dom);
  const window = dom.window;
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
  const dom = createDom();
  _doms.push(dom);
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
    if (typeof url === "string" && url.includes("/alerts") && url.includes("limit=6")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ items: [], total: 0 }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  loadApp(dom);
  await waitFor(() => {
    const pill = window.document.getElementById("connectionPill");
    return pill && pill.classList.contains("live");
  });
});

test("overview empty chart shows No historical data", async () => {
  const { window } = setup({
    routes: {
      "/summary": { kpis: { live_hosts: 1, total_hosts: 1, total_running_processes: 5, active_alerts: 0, cpu_percent: 10, memory_percent: 20, samples_received: 10 }, hosts: [] },
      "/metrics/summary": { cpu: [], memory: [], activity: [], alerts: [] },
      "/alerts": { items: [], total: 0, limit: 6, offset: 0 },
    },
  });
  await waitFor(() => window.document.getElementById("overviewCpuChart") !== null);
  // allow lineChart to render empty state
  await wait(50);
  const cpuChart = window.document.getElementById("overviewCpuChart");
  assert.match(cpuChart.textContent, /No historical data/);
});

test("incident drawer renders strongest treatment on View Incident", async () => {
  const incidentData = {
    incident: { id: 1, rule_name: "High CPU", host: "h1", pid: 5, process_name: "p1", create_time: 1.0, metric: "cpu_percent", operator: "gt", threshold: 80, current_value: 95, severity: "critical", status: "active", triggered_at: 100 },
    process: { host: "h1", pid: 5, process_name: "p1", username: "u", create_time: 1.0, started_time: "Sep 14", state: "running", age_seconds_at_alert: 10 },
    summary: { metric: "cpu_percent", current_value: 95, previous_value: 30, peak_value: 95, delta: 65, memory_delta_bytes: 10485760, duration_seconds: 10, window_start: 90, window_end: 100, window_seconds: 60, sample_count: 2, is_insufficient: false },
    timeline: [
      { timestamp: 90, time: "12:00:00", cpu_percent: 30, memory_percent: 20, memory_rss: 900000, state: "running", is_start: true, label: "Process started" },
      { timestamp: 100, time: "12:00:10", cpu_percent: 95, memory_percent: 40, memory_rss: 1000000, state: "running", is_alert: true, label: "Alert triggered" },
    ],
    evidence: ["Process p1 (PID 5) on h1 breached cpu_percent threshold 80.0%: 95.0% at alert.", "CPU increased sharply (+65.0) before the alert."],
  };
  const { window } = setup({
    routes: {
      "/alerts": {
        items: [
          { id: 1, rule_name: "High CPU", severity: "critical", status: "active", host: "h1", pid: 5, process_name: "p1", create_time: 1.0, metric: "cpu_percent", current_value: 95, threshold: 80, triggered_at: 100 },
        ],
        total: 1, limit: 100, offset: 0,
      },
      "/incidents/1": incidentData,
    },
  });
  window.location.hash = "#alerts";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitFor(() => window.document.querySelector('[data-action="view-incident"]') !== null);
  window.document.querySelector('[data-action="view-incident"]').click();
  await waitFor(() => window.document.querySelector(".incident-drawer")?.textContent.includes("Evidence"), { timeout: 3000 });
  const drawerText = window.document.querySelector(".incident-drawer").textContent;
  assert.match(drawerText, /Process Incident Analyzer/);
  assert.match(drawerText, /Evidence/);
  assert.match(drawerText, /Timeline/);
  assert.match(drawerText, /Key metrics/);
  assert.ok(window.document.querySelector(".timeline"), "timeline must be present");
  assert.ok(window.document.querySelector(".timeline-row.is-alert"), "alert dot should be emphasized");
});
