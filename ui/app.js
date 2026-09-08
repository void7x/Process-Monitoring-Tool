/* Process Monitor dashboard - dependency-free, API-backed UI.
 *
 * The app deliberately keeps its rendering primitives small and predictable:
 * every value comes from a collector endpoint, process detail requests carry
 * create_time, and failed panels can render independently of one another.
 */
(() => {
  "use strict";

  const API_BASE = (window.MONITORING_CONFIG?.apiBase || "/api").replace(/\/$/, "");
  const STORAGE_KEY = "process-monitor-settings";
  const root = document.getElementById("pageContent");
  const drawerRoot = document.getElementById("drawerRoot");
  const state = {
    page: "overview",
    param: "",
    connected: false,
    loading: false,
    lastUpdated: null,
    lastSuccessfulUpdate: null,
    refreshInterval: 30,
    liveUpdates: true,
    refreshTimer: null,
    clockTimer: null,
    requestToken: 0,
    summary: null,
    metrics: null,
    ruleEditing: null,
    filters: {
      processSearch: "",
      processHost: "",
      processCpu: "",
      processMemory: "",
      processAlert: false,
      processPage: 1,
      processPageSize: 50,
      processSort: "cpu_percent",
      processOrder: "desc",
      alertStatus: "active",
      alertSearch: "",
      alertSeverity: "",
      alertHost: "",
      alertWindow: "all",
      hostsSearch: "",
      historySearch: "",
      historyHost: "",
      historyPage: 0,
    },
  };

  const ICONS = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 7h.01M7 18h.01M11 7h6M11 18h6"/>',
    process: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12h2l1.5-4 2.5 8 1.5-4H18M2 12h2M20 12h2"/>',
    bell: '<path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 22h4"/>',
    sliders: '<path d="M4 6h16M4 12h16M4 18h16M8 4v4M16 10v4M10 16v4"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5M12 7v5l3 2"/>',
    settings: '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="m19.4 15 .1.1a2 2 0 0 1-2.8 2.8l-.1-.1a2 2 0 0 0-3.4 1.4v.2a2 2 0 0 1-4 0v-.2a2 2 0 0 0-3.4-1.4l-.1.1A2 2 0 0 1 3 15.1l.1-.1A2 2 0 0 0 1.7 11.6h-.2a2 2 0 0 1 0-4h.2A2 2 0 0 0 3.1 4.2L3 4.1A2 2 0 0 1 5.8 1.3l.1.1A2 2 0 0 0 9.3 0h.2a2 2 0 0 1 4 0v.2a2 2 0 0 0 3.4 1.4l.1-.1A2 2 0 0 1 19.8 4l-.1.1A2 2 0 0 0 21.1 7.5h.2a2 2 0 0 1 0 4h-.2a2 2 0 0 0-1.4 3.5Z" transform="scale(.86) translate(1.9 1.9)"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.8-4L3 10M3 4v6h6M4 13a8 8 0 0 0 14.8 4L21 14m0 6v-6h-6"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    play: '<path d="m8 5 11 7-11 7V5Z"/>',
    "cloud-off": '<path d="m2 2 20 20M7 18H6a4 4 0 0 1-.7-7.9A7 7 0 0 1 17 7.3M19 19h-5"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    alert: '<path d="M10.3 3.3 2.2 17a2 2 0 0 0 1.7 3h16.2a2 2 0 0 0 1.7-3L13.7 3.3a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 9h6v6H9zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    memory: '<path d="M5 6h14v12H5zM2 9v6M22 9v6M8 9v3M12 9v3M16 9v3M8 15v.01M12 15v.01M16 15v.01"/>',
    activity: '<path d="M3 12h4l2-7 4 14 2-7h6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    up: '<path d="m6 15 6-6 6 6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    edit: '<path d="m4 16-.8 4.8L8 20l11.3-11.3a2.1 2.1 0 0 0-3-3L4 16Z"/><path d="m14.5 7.5 3 3"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    download: '<path d="M12 3v12m0 0 5-5m-5 5-5-5M4 21h16"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    checkcircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>',
  };

  function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.info}</svg>`;
  }

  function initIcons() {
    document.querySelectorAll("[data-icon]").forEach((node) => {
      node.innerHTML = icon(node.dataset.icon);
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function apiUrl(path, params = {}) {
    const url = new URL(`${API_BASE}${path}`, window.location.origin);
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "" && value !== false) url.searchParams.set(key, value);
    });
    return url.toString();
  }

  async function apiFetch(path, options = {}, params = {}) {
    try {
      const response = await fetch(apiUrl(path, params), { cache: "no-store", ...options });
      let body = null;
      try { body = await response.json(); } catch (_) { /* non-JSON error */ }
      if (!response.ok) {
        const message = body?.error?.message || `Request failed with status ${response.status}`;
        const error = new Error(message);
        error.status = response.status;
        if (response.status >= 500 || response.status === 0) setConnection(false);
        throw error;
      }
      setConnection(true);
      return body;
    } catch (error) {
      if (!error.status || error.status >= 500) setConnection(false);
      throw error;
    }
  }

  function setConnection(connected) {
    state.connected = connected;
    const pill = document.getElementById("connectionPill");
    const text = document.getElementById("connectionText");
    const dot = document.getElementById("sidebarStatusDot");
    const sidebarText = document.getElementById("sidebarConnectionText");
    const pulse = document.getElementById("livePulse");
    const banner = document.getElementById("offlineBanner");
    if (pill) {
      pill.classList.toggle("live", connected);
      pill.classList.toggle("error", !connected);
    }
    if (text) text.textContent = connected ? "Connected" : "Offline";
    if (dot) { dot.classList.toggle("live", connected); dot.classList.toggle("error", !connected); }
    if (sidebarText) sidebarText.textContent = connected ? "Collector connected" : "Collector offline";
    if (pulse) { pulse.classList.toggle("error", !connected); pulse.classList.toggle("paused", !state.liveUpdates && connected); }
    if (banner) banner.hidden = connected;
    if (!connected) updateOfflineMessage();
  }

  function updateOfflineMessage() {
    const message = document.getElementById("offlineMessage");
    if (!message) return;
    message.textContent = state.lastSuccessfulUpdate
      ? `Last successful update ${relativeTime(state.lastSuccessfulUpdate)}. Check whether the collector service is running.`
      : "Check whether the collector service is running, then retry.";
  }

  function markUpdated() {
    state.lastUpdated = Date.now() / 1000;
    state.lastSuccessfulUpdate = state.lastUpdated;
    const text = document.getElementById("updateText");
    if (text) text.textContent = `Updated ${relativeTime(state.lastUpdated)}`;
    updateOfflineMessage();
  }

  function relativeTime(value) {
    if (!value) return "never";
    const seconds = Math.max(0, Math.round(Date.now() / 1000 - Number(value)));
    if (seconds < 5) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3_600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86_400) return `${Math.floor(seconds / 3_600)}h ago`;
    return `${Math.floor(seconds / 86_400)}d ago`;
  }

  function dateTime(value) {
    if (!value) return "—";
    const date = new Date(Number(value) * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function fullDateTime(value) {
    if (!value) return "—";
    const date = new Date(Number(value) * 1000);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
  }

  function number(value, digits = 0) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function percent(value, digits = 1) {
    return `${number(value, digits)}%`;
  }

  function bytes(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (n < 1024) return `${number(n)} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let v = n;
    let unit = "B";
    for (const next of units) {
      v /= 1024;
      unit = next;
      if (v < 1024 || next === "TB") break;
    }
    return `${number(v, v < 10 ? 1 : 0)} ${unit}`;
  }

  function operatorText(operator) {
    return ({ gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=" })[operator] || operator || "—";
  }

  function metricLabel(metric) {
    return ({ cpu_percent: "CPU", memory_percent: "Memory", memory_rss: "RSS", read_bytes: "Read I/O", write_bytes: "Write I/O" })[metric] || metric;
  }

  function metricValue(metric, value) {
    if (metric === "cpu_percent" || metric === "memory_percent") return percent(value);
    if (metric === "memory_rss" || metric === "read_bytes" || metric === "write_bytes") return bytes(value);
    return number(value, 1);
  }

  function valueState(metric, value) {
    const n = Number(value);
    if (metric === "cpu_percent") return n >= 80 ? "critical" : n >= 50 ? "elevated" : "normal";
    if (metric === "memory_percent") return n >= 60 ? "critical" : n >= 30 ? "elevated" : "normal";
    return "normal";
  }

  function statusBadge(status, label = status) {
    return `<span class="status-badge ${escapeHtml(String(status || "neutral").toLowerCase())}">${escapeHtml(label || "UNKNOWN")}</span>`;
  }

  function severityBadge(severity) {
    const value = String(severity || "info").toLowerCase();
    return statusBadge(value, value.toUpperCase());
  }

  function skeletonOverview() {
    return `<div class="page-intro"><div><div class="skeleton" style="width:180px;height:24px"></div><div class="skeleton" style="width:290px;height:12px;margin-top:9px"></div></div></div><div class="kpi-grid">${Array.from({ length: 8 }, () => '<div class="skeleton-card skeleton"></div>').join("")}</div><div class="grid-2">${Array.from({ length: 4 }, () => '<div class="card chart-card"><div class="skeleton" style="height:16px;width:150px;margin:17px"></div><div class="skeleton" style="height:160px;margin:12px 17px"></div></div>').join("")}</div>`;
  }

  function loadingRows(count = 7) {
    return `<div class="loading-center"><span class="spinner"></span>Loading live data…</div>${Array.from({ length: count }, () => '<div class="skeleton-row skeleton"></div>').join("")}`;
  }

  function emptyState(title, message, iconName = "checkcircle", green = false) {
    return `<div class="empty-state"><div><div class="empty-icon ${green ? "green" : ""}">${icon(iconName)}</div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div></div>`;
  }

  function errorState(error, retryAction = "retry-page") {
    return `<div class="error-state"><span>${icon("alert")}</span><div><strong>Unable to load this panel</strong><p>${escapeHtml(error?.message || "The collector returned an unexpected response.")} <button class="link-button" data-action="${retryAction}" type="button">Try again</button></p></div></div>`;
  }

  function chartCard(id, title, subtitle, color = "#2f6fed", legend = "Observed") {
    return `<article class="card chart-card"><div class="card-header"><div><h3 class="card-title">${escapeHtml(title)}</h3><p class="card-subtitle">${escapeHtml(subtitle)}</p></div><span class="status-badge info">Live window</span></div><div class="card-body"><div id="${id}" class="chart-wrap" data-color="${color}" data-legend="${escapeHtml(legend)}"></div><div class="chart-legend"><span><i class="legend-dot" style="background:${color}"></i>${escapeHtml(legend)}</span></div></div></article>`;
  }

  function lineChart(target, series, { color = "#2f6fed", label = "Observed", format = number } = {}) {
    if (!target) return;
    const values = (series || []).filter((point) => Number.isFinite(Number(point.value)));
    if (!values.length) {
      target.innerHTML = `<div class="chart-empty">${icon("activity")}<span>No samples in this time window</span></div>`;
      return;
    }
    const width = 680;
    const height = 158;
    const pad = { top: 10, right: 12, bottom: 25, left: 39 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    let min = Math.min(...values.map((p) => Number(p.value)));
    let max = Math.max(...values.map((p) => Number(p.value)));
    if (min === max) { min = Math.max(0, min - 1); max += 1; }
    const range = max - min;
    const x = (index) => pad.left + (values.length === 1 ? plotW / 2 : index * plotW / (values.length - 1));
    const y = (value) => pad.top + plotH - ((Number(value) - min) / range) * plotH;
    const points = values.map((p, i) => `${x(i).toFixed(2)},${y(p.value).toFixed(2)}`);
    const area = `${pad.left},${pad.top + plotH} ${points.join(" ")} ${pad.left + plotW},${pad.top + plotH}`;
    const grid = [0, 1, 2, 3].map((index) => {
      const gy = pad.top + index * plotH / 3;
      const value = max - index * range / 3;
      return `<line x1="${pad.left}" y1="${gy}" x2="${pad.left + plotW}" y2="${gy}" stroke="#e9eef5"/><text x="${pad.left - 7}" y="${gy + 3}" text-anchor="end" fill="#91a0b2" font-size="9">${escapeHtml(format(value))}</text>`;
    }).join("");
    const dots = values.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.value)}" r="${values.length < 40 ? 2.8 : 2}" fill="${color}"><title>${escapeHtml(dateTime(p.timestamp))}: ${escapeHtml(format(p.value))} ${escapeHtml(label)}</title></circle>`).join("");
    const first = dateTime(values[0].timestamp);
    const last = dateTime(values[values.length - 1].timestamp);
    target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(label)} over time">${grid}<polygon points="${area}" fill="${color}" opacity=".07"/><polyline points="${points.join(" ")}" fill="none" stroke="${color}" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>${dots}<text x="${pad.left}" y="${height - 5}" fill="#91a0b2" font-size="9">${escapeHtml(first)}</text><text x="${pad.left + plotW}" y="${height - 5}" text-anchor="end" fill="#91a0b2" font-size="9">${escapeHtml(last)}</text></svg>`;
  }

  function activityBars(series) {
    const values = (series || []).map((item) => Number(item.value) || 0);
    const max = Math.max(1, ...values);
    return `<span class="activity-bars" aria-label="Recent sample activity">${values.slice(-12).map((value) => `<i style="height:${Math.max(3, Math.round(value / max * 18))}px"></i>`).join("")}</span>`;
  }

  function renderTopHostCards(hosts) {
    if (!hosts?.length) return emptyState("No hosts reporting yet", "Start an agent on Windows or Linux to see live host health here.", "server");
    return hosts.slice(0, 6).map((host) => `<button class="host-card" type="button" data-action="host-detail" data-host="${escapeHtml(host.host)}">
      <div class="host-card-top"><div class="host-name"><span class="host-icon">${icon("server")}</span><span title="${escapeHtml(host.host)}">${escapeHtml(host.host)}</span></div>${statusBadge(host.status, host.status)}</div>
      <div class="host-meta"><div class="metric-mini"><label>Processes</label><strong>${number(host.process_count)}</strong></div><div class="metric-mini"><label>CPU</label><strong>${percent(host.cpu_percent)}</strong></div><div class="metric-mini"><label>Memory</label><strong>${percent(host.memory_percent)}</strong></div></div>
      <div class="host-footer"><span>${host.active_alert_count ? `${number(host.active_alert_count)} active alert${host.active_alert_count === 1 ? "" : "s"}` : "No active alerts"}</span>${activityBars(host.activity)}</div>
    </button>`).join("");
  }

  function kpiCard(label, value, support, iconName, tone = "primary") {
    const toneClass = tone === "primary" ? "" : ` tone-${tone}`;
    return `<article class="kpi-card${toneClass}"><div class="kpi-top"><span class="kpi-label">${escapeHtml(label)}</span><span class="kpi-icon ${tone}">${icon(iconName)}</span></div><div class="kpi-value">${escapeHtml(value)}</div><div class="kpi-support">${support}</div></article>`;
  }

  async function renderOverview(token) {
    root.innerHTML = skeletonOverview();
    const results = await Promise.allSettled([
      apiFetch("/summary", {}, { window: 3600 }),
      apiFetch("/metrics/summary", {}, { window: 3600 }),
    ]);
    if (token !== state.requestToken) return;
    const summary = results[0].status === "fulfilled" ? results[0].value : null;
    const metrics = results[1].status === "fulfilled" ? results[1].value : null;
    if (summary) state.summary = summary;
    if (metrics) state.metrics = metrics;
    if (!summary && !metrics) {
      root.innerHTML = `<div class="page-intro"><div><h2>Overview</h2><p>Your monitoring workspace will appear when the collector is available.</p></div></div>${errorState(results[0].reason || results[1].reason)}`;
      return;
    }
    const k = summary?.kpis || {};
    const hosts = summary?.hosts || [];
    root.innerHTML = `<div class="page-intro"><div><h2>Operational overview</h2><p>A calm, real-time view of process health across your monitored hosts.</p></div><div class="page-actions"><span class="status-badge live">${icon("activity")} Live collection</span><button class="button secondary small" type="button" data-action="jump-processes">View process explorer ${icon("arrow")}</button></div></div>
      <div class="kpi-grid">
        ${kpiCard("Live hosts", number(k.live_hosts), `<span class="trend good">${icon("check")} Healthy reporting</span><span>of ${number(k.total_hosts)} known</span>`, "server", "green")}
        ${kpiCard("Running processes", number(k.total_running_processes), `<span>Latest process snapshot</span>`, "process", "purple")}
        ${kpiCard("Active alerts", number(k.active_alerts), k.active_alerts ? `<span class="trend bad">${icon("alert")} Needs attention</span>` : `<span class="trend good">${icon("check")} All clear</span>`, "bell", k.active_alerts ? "red" : "green")}
        ${kpiCard("Observed CPU", percent(k.cpu_percent), `<span>Sum of latest process observations</span>`, "cpu", "primary")}
        ${kpiCard("Observed memory", percent(k.memory_percent), `<span>Latest process memory total</span>`, "memory", "purple")}
        ${kpiCard("Samples received", number(k.samples_received), `<span>Within the last ${number(summary?.window_seconds / 60)} min</span>`, "activity", "green")}
        ${kpiCard("Stale hosts", number(k.stale_hosts), k.stale_hosts ? `<span class="trend warning">${icon("clock")} Check connection</span>` : `<span class="trend good">${icon("check")} None detected</span>`, "clock", k.stale_hosts ? "amber" : "green")}
        ${kpiCard("Offline hosts", number(k.offline_hosts), k.offline_hosts ? `<span class="trend bad">${icon("cloud-off")} Not reporting</span>` : `<span class="trend good">${icon("check")} None detected</span>`, "cloud-off", k.offline_hosts ? "red" : "green")}
      </div>
      <div class="grid-2">
        ${chartCard("overviewCpuChart", "CPU utilization", "Average process CPU observation in the selected window", "#2f6fed", "Average CPU")}
        ${chartCard("overviewMemoryChart", "Memory utilization", "Average process memory percentage in the selected window", "#7658c5", "Average memory")}
        ${chartCard("overviewActivityChart", "Process activity", "Process samples received per minute", "#1b9b67", "Samples / minute")}
        ${chartCard("overviewAlertsChart", "Alert trend", "New alert events by time bucket", "#c88316", "Alert events")}
      </div>
      <div class="section-heading"><h2>Host health</h2><button class="link-button" type="button" data-action="jump-hosts">View all hosts ${icon("arrow")}</button></div>
      <div class="host-grid">${renderTopHostCards(hosts)}</div>`;
    lineChart(document.getElementById("overviewCpuChart"), metrics?.cpu, { color: "#2f6fed", label: "Average CPU", format: (v) => percent(v) });
    lineChart(document.getElementById("overviewMemoryChart"), metrics?.memory, { color: "#7658c5", label: "Average memory", format: (v) => percent(v) });
    lineChart(document.getElementById("overviewActivityChart"), metrics?.activity, { color: "#1b9b67", label: "Samples", format: (v) => number(v) });
    lineChart(document.getElementById("overviewAlertsChart"), metrics?.alerts, { color: "#c88316", label: "Alert events", format: (v) => number(v) });
    if (results.some((result) => result.status === "rejected")) {
      const failed = document.createElement("div");
      failed.className = "error-state";
      failed.style.marginTop = "15px";
      failed.innerHTML = `${icon("info")}<div><strong>Some overview data is temporarily unavailable</strong><p>Healthy panels remain visible; retry to restore the missing chart or summary.</p></div>`;
      root.append(failed);
    }
  }

  async function renderHosts(token) {
    root.innerHTML = `<div class="page-intro"><div><h2>Hosts</h2><p>Understand freshness, workload, and alerts for every monitored machine.</p></div><div class="page-actions"><button class="button secondary" type="button" data-action="retry-page">${icon("refresh")} Refresh hosts</button></div></div><div class="table-toolbar card"><div class="toolbar-left"><input id="hostSearch" class="field" type="search" placeholder="Search hostnames…" aria-label="Search hosts"></div><div class="toolbar-right"><span id="hostCount" class="status-badge neutral">Loading…</span></div></div><div id="hostsPanel" class="host-grid" style="margin-top:15px">${Array.from({ length: 4 }, () => '<div class="skeleton-card skeleton"></div>').join("")}</div>`;
    const search = state.filters.hostsSearch || "";
    const searchInput = document.getElementById("hostSearch");
    if (searchInput) searchInput.value = search;
    try {
      const data = await apiFetch("/hosts", {}, { search, window: 86_400 });
      if (token !== state.requestToken) return;
      const panel = document.getElementById("hostsPanel");
      const count = document.getElementById("hostCount");
      if (count) count.textContent = `${number(data.total)} host${data.total === 1 ? "" : "s"}`;
      if (panel) panel.innerHTML = data.items?.length ? data.items.map((host) => renderHostCard(host)).join("") : emptyState("No hosts found", search ? "Try a different hostname." : "Start an agent to begin collecting host health.", "server");
      if (searchInput) {
        searchInput.oninput = () => {
          state.filters.hostsSearch = searchInput.value;
          window.clearTimeout(state.hostSearchTimer);
          state.hostSearchTimer = window.setTimeout(() => renderPage(), 280);
        };
      }
    } catch (error) {
      const panel = document.getElementById("hostsPanel");
      if (panel) panel.innerHTML = errorState(error);
    }
  }

  function renderHostCard(host) {
    return `<button class="host-card" type="button" data-action="host-detail" data-host="${escapeHtml(host.host)}"><div class="host-card-top"><div class="host-name"><span class="host-icon">${icon("server")}</span><span title="${escapeHtml(host.host)}">${escapeHtml(host.host)}</span></div>${statusBadge(host.status, host.status)}</div><div class="host-meta"><div class="metric-mini"><label>Processes</label><strong>${number(host.process_count)}</strong></div><div class="metric-mini"><label>CPU</label><strong>${percent(host.cpu_percent)}</strong></div><div class="metric-mini"><label>Memory</label><strong>${percent(host.memory_percent)}</strong></div></div><div class="host-footer"><span>${number(host.sample_count)} samples collected</span>${activityBars(host.activity)}</div><div style="margin-top:12px;color:${host.active_alert_count ? "var(--red)" : "var(--green)"};font-size:10px;font-weight:700">${host.active_alert_count ? `${icon("alert")} ${number(host.active_alert_count)} active alert${host.active_alert_count === 1 ? "" : "s"}` : `${icon("check")} No active alerts`} · ${relativeTime(host.last_seen)}</div></button>`;
  }

  async function renderHostDetail(token, host) {
    root.innerHTML = `<div class="page-intro"><div><button class="link-button" data-action="back-hosts" type="button">${icon("up")} Hosts</button><h2 style="margin-top:9px">${escapeHtml(host)}</h2><p>Host health, activity history, and workload detail.</p></div></div><div class="loading-center"><span class="spinner"></span>Loading host detail…</div>`;
    try {
      const data = await apiFetch(`/hosts/${encodeURIComponent(host)}`, {}, { window: 86_400 });
      if (token !== state.requestToken) return;
      root.innerHTML = `<div class="page-intro"><div><button class="link-button" data-action="back-hosts" type="button">${icon("up")} Back to hosts</button><h2 style="margin-top:9px">${escapeHtml(data.host)}</h2><p>Host detail and recent process activity.</p></div><div class="page-actions">${statusBadge(data.status, data.status)}<button class="button secondary" type="button" data-action="refresh-page">${icon("refresh")} Refresh</button></div></div>
        <div class="kpi-grid" style="grid-template-columns:repeat(5,minmax(0,1fr))">${kpiCard("Health", data.status.toUpperCase(), `<span>Last seen ${relativeTime(data.last_seen)}</span>`, "server", data.status === "live" ? "green" : data.status === "stale" ? "amber" : "red")}${kpiCard("Processes", number(data.process_count), `<span>Latest snapshot</span>`, "process", "purple")}${kpiCard("CPU", percent(data.cpu_percent), `<span>Latest observed total</span>`, "cpu", "primary")}${kpiCard("Memory", percent(data.memory_percent), `<span>Latest observed total</span>`, "memory", "purple")}${kpiCard("Alerts", number(data.active_alert_count), data.active_alert_count ? `<span class="trend bad">${icon("alert")} Active</span>` : `<span class="trend good">${icon("check")} All clear</span>`, "bell", data.active_alert_count ? "red" : "green")}</div>
        <div class="grid-2">${chartCard("hostCpuChart", "CPU activity", "Average process CPU observations for this host", "#2f6fed", "Average CPU")}${chartCard("hostMemoryChart", "Memory activity", "Average process memory observations for this host", "#7658c5", "Average memory")}</div>
        <div class="section-heading"><h2>Top CPU processes</h2><button class="link-button" data-action="jump-processes-host" data-host="${escapeHtml(data.host)}" type="button">Explore processes ${icon("arrow")}</button></div><div class="card table-card"><div class="data-table-wrap">${miniProcessTable(data.top_cpu || [])}</div></div>
        <div class="section-heading"><h2>Top memory processes</h2></div><div class="card table-card"><div class="data-table-wrap">${miniProcessTable(data.top_memory || [], "memory_percent")}</div></div>
        <div class="section-heading"><h2>Recent alerts</h2><button class="link-button" data-action="jump-alerts-host" data-host="${escapeHtml(data.host)}" type="button">View alerts ${icon("arrow")}</button></div><div class="card table-card">${renderAlertList(data.recent_alerts || [], true)}</div>`;
      lineChart(document.getElementById("hostCpuChart"), data.series?.cpu, { color: "#2f6fed", label: "Average CPU", format: (v) => percent(v) });
      lineChart(document.getElementById("hostMemoryChart"), data.series?.memory, { color: "#7658c5", label: "Average memory", format: (v) => percent(v) });
    } catch (error) { root.innerHTML = errorState(error); }
  }

  function miniProcessTable(rows, sortMetric = "cpu_percent") {
    if (!rows.length) return emptyState("No recent process data", "This host has not sent a process snapshot in the selected window.", "process");
    return `<table><thead><tr><th>Process</th><th>PID</th><th>CPU</th><th>Memory</th><th>Last seen</th><th></th></tr></thead><tbody>${rows.slice(0, 8).map((row) => `<tr><td><div class="process-cell"><span class="process-icon">${icon("process")}</span><span><span class="process-name">${escapeHtml(row.process_name)}</span><span class="process-sub">${escapeHtml(row.username || "Unknown user")}</span></span></div></td><td class="number-cell">${number(row.pid)}</td><td>${metricCell("cpu_percent", row.cpu_percent)}</td><td>${metricCell("memory_percent", row.memory_percent)}</td><td>${relativeTime(row.received_at)}</td><td><button class="table-action" type="button" data-action="process-detail" data-host="${escapeHtml(row.host)}" data-pid="${row.pid}" data-create-time="${row.create_time}" aria-label="Open ${escapeHtml(row.process_name)} detail">${icon("chevron")}</button></td></tr>`).join("")}</tbody></table>`;
  }

  function metricCell(metric, value) {
    const status = valueState(metric, value);
    return `<span class="severity-value ${status}"><i class="severity-marker"></i>${escapeHtml(metricValue(metric, value))} <small style="font-size:9px;font-weight:600;text-transform:uppercase">${status}</small></span>`;
  }

  async function renderProcesses(token) {
    const f = state.filters;
    root.innerHTML = `<div class="page-intro"><div><h2>Process explorer</h2><p>Sort, filter, and inspect exact process instances without losing PID-reuse boundaries.</p></div><div class="page-actions"><button class="button secondary" type="button" data-action="retry-page">${icon("refresh")} Refresh</button></div></div><div class="card table-card"><div class="table-toolbar"><div class="toolbar-left"><input id="processSearch" class="field" type="search" placeholder="Search process, PID, host, or user…" aria-label="Search processes" value="${escapeHtml(f.processSearch)}"><select id="processHost" class="select-field" aria-label="Filter by host"><option value="">All hosts</option></select><select id="processCpu" class="select-field" aria-label="Minimum CPU"><option value="">Any CPU</option><option value="25">CPU ≥ 25%</option><option value="50">CPU ≥ 50%</option><option value="80">CPU ≥ 80%</option></select><select id="processMemory" class="select-field" aria-label="Minimum memory"><option value="">Any memory</option><option value="25">Memory ≥ 25%</option><option value="50">Memory ≥ 50%</option><option value="75">Memory ≥ 75%</option></select><button class="filter-chip ${f.processAlert ? "active" : ""}" type="button" data-action="toggle-process-alert">${icon("bell")} Active alerts only</button></div><div class="toolbar-right"><span id="processCount" class="status-badge neutral">Loading…</span></div></div><div id="processTable" class="data-table-wrap">${loadingRows()}</div><div id="processPagination"></div></div>`;
    const [processResult, hostResult] = await Promise.allSettled([
      apiFetch("/processes", {}, { search: f.processSearch, host: f.processHost, cpu_min: f.processCpu, memory_min: f.processMemory, alert_only: f.processAlert, page: f.processPage, page_size: f.processPageSize, sort: f.processSort, order: f.processOrder }),
      apiFetch("/hosts", {}, { window: 86_400 }),
    ]);
    if (token !== state.requestToken) return;
    const processPanel = document.getElementById("processTable");
    if (processResult.status === "rejected") {
      if (processPanel) processPanel.innerHTML = errorState(processResult.reason);
      return;
    }
    const data = processResult.value;
    const count = document.getElementById("processCount");
    if (count) count.textContent = `${number(data.total)} result${data.total === 1 ? "" : "s"}`;
    if (processPanel) processPanel.innerHTML = data.items?.length ? processTable(data.items) : emptyState("No processes match", "Try clearing a filter or wait for the next agent sample.", "search");
    const pagination = document.getElementById("processPagination");
    if (pagination) pagination.innerHTML = paginationMarkup(data.page, data.page_size, data.total, "process");
    const hostSelect = document.getElementById("processHost");
    if (hostSelect && hostResult.status === "fulfilled") {
      hostSelect.innerHTML = `<option value="">All hosts</option>${(hostResult.value.items || []).map((h) => `<option value="${escapeHtml(h.host)}">${escapeHtml(h.host)}</option>`).join("")}`;
      hostSelect.value = f.processHost;
    }
    ["processCpu", "processMemory"].forEach((id) => { const el = document.getElementById(id); if (el) el.value = id === "processCpu" ? f.processCpu : f.processMemory; });
    bindProcessFilters();
  }

  function processTable(rows) {
    const f = state.filters;
    const sortHeader = (label, field) => `<th><button class="sort-button" type="button" data-action="sort-process" data-sort="${field}">${label}${f.processSort === field ? icon(f.processOrder === "asc" ? "up" : "down") : ""}</button></th>`;
    return `<table><thead><tr>${sortHeader("Process", "process_name")}${sortHeader("PID", "pid")}${sortHeader("Host", "host")}<th>User</th>${sortHeader("CPU", "cpu_percent")}${sortHeader("Memory", "memory_percent")}${sortHeader("Read I/O", "read_bytes")}${sortHeader("Write I/O", "write_bytes")}<th>State</th><th>Last seen</th><th>Actions</th></tr></thead><tbody>${rows.map((row) => `<tr class="${row.has_active_alert ? "has-alert" : ""}"><td><div class="process-cell"><span class="process-icon">${icon("process")}</span><span><span class="process-name" title="${escapeHtml(row.process_name)}">${escapeHtml(row.process_name)}</span><span class="process-sub">Instance ${escapeHtml(Number(row.create_time).toFixed(3))}</span></span></div></td><td class="number-cell">${number(row.pid)}</td><td>${escapeHtml(row.host)}</td><td>${escapeHtml(row.username || "Unknown")}</td><td>${metricCell("cpu_percent", row.cpu_percent)}</td><td>${metricCell("memory_percent", row.memory_percent)}</td><td class="number-cell">${bytes(row.read_bytes)}</td><td class="number-cell">${bytes(row.write_bytes)}</td><td>${statusBadge(row.state === "running" ? "live" : "neutral", row.state || "unknown")}${row.has_active_alert ? ` ${severityBadge("critical")}` : ""}</td><td title="${escapeHtml(fullDateTime(row.received_at))}">${relativeTime(row.received_at)}</td><td><button class="table-action" type="button" data-action="process-detail" data-host="${escapeHtml(row.host)}" data-pid="${row.pid}" data-create-time="${row.create_time}" aria-label="Open process details">${icon("chevron")}</button></td></tr>`).join("")}</tbody></table>`;
  }

  function paginationMarkup(page, pageSize, total, kind) {
    const maxPage = Math.max(1, Math.ceil(total / pageSize));
    return `<div class="pagination"><span>Showing ${total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}` : "0"} of ${number(total)}</span><div class="pagination-actions"><button class="page-button" type="button" data-action="page-prev" data-kind="${kind}" ${page <= 1 ? "disabled" : ""} aria-label="Previous page">${icon("up")}</button><span class="status-badge neutral">${number(page)} / ${number(maxPage)}</span><button class="page-button" type="button" data-action="page-next" data-kind="${kind}" ${page >= maxPage ? "disabled" : ""} aria-label="Next page">${icon("down")}</button></div></div>`;
  }

  function bindProcessFilters() {
    const input = document.getElementById("processSearch");
    const host = document.getElementById("processHost");
    const cpu = document.getElementById("processCpu");
    const memory = document.getElementById("processMemory");
    if (input) input.oninput = () => {
      state.filters.processSearch = input.value;
      state.filters.processPage = 1;
      window.clearTimeout(state.processSearchTimer);
      state.processSearchTimer = window.setTimeout(() => renderPage(), 280);
    };
    if (host) host.onchange = () => { state.filters.processHost = host.value; state.filters.processPage = 1; renderPage(); };
    if (cpu) cpu.onchange = () => { state.filters.processCpu = cpu.value; state.filters.processPage = 1; renderPage(); };
    if (memory) memory.onchange = () => { state.filters.processMemory = memory.value; state.filters.processPage = 1; renderPage(); };
  }

  async function renderAlerts(token) {
    const f = state.filters;
    root.innerHTML = `<div class="page-intro"><div><h2>Alert center</h2><p>Review active conditions and the complete resolved alert trail.</p></div><div class="page-actions"><button class="button secondary" type="button" data-action="retry-page">${icon("refresh")} Refresh alerts</button></div></div><div class="card table-card"><div class="table-toolbar"><div class="toolbar-left"><div class="tabs"><button class="tab ${f.alertStatus === "active" ? "active" : ""}" data-alert-status="active" type="button">Active</button><button class="tab ${f.alertStatus === "all" ? "active" : ""}" data-alert-status="all" type="button">History</button><button class="tab ${f.alertStatus === "resolved" ? "active" : ""}" data-alert-status="resolved" type="button">Resolved</button></div><input id="alertSearch" class="field" type="search" placeholder="Search rule, process, PID, or host…" aria-label="Search alerts" value="${escapeHtml(f.alertSearch)}"><select id="alertSeverity" class="select-field" aria-label="Filter alert severity"><option value="">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option><option value="info">Info</option></select><input id="alertHost" class="field" style="width:150px" type="search" placeholder="Host" aria-label="Filter alert host" value="${escapeHtml(f.alertHost)}"><select id="alertWindow" class="select-field" aria-label="Filter alert time"><option value="all">All time</option><option value="3600">Last hour</option><option value="86400">Last 24 hours</option><option value="604800">Last 7 days</option></select></div><div class="toolbar-right"><span id="alertCount" class="status-badge neutral">Loading…</span></div></div><div id="alertList">${loadingRows(5)}</div></div>`;
    const since = f.alertWindow === "all" ? "" : Date.now() / 1000 - Number(f.alertWindow);
    const data = await apiFetch("/alerts", {}, { status: f.alertStatus, search: f.alertSearch, severity: f.alertSeverity, host: f.alertHost, since, limit: 100, offset: 0 }).catch((error) => ({ __error: error }));
    if (token !== state.requestToken) return;
    const list = document.getElementById("alertList");
    if (data.__error) { if (list) list.innerHTML = errorState(data.__error); return; }
    const count = document.getElementById("alertCount");
    if (count) count.textContent = `${number(data.total)} alert${data.total === 1 ? "" : "s"}`;
    if (list) list.innerHTML = data.items?.length ? renderAlertList(data.items, false) : emptyState(f.alertStatus === "active" ? "No active alerts" : "No alert history", f.alertStatus === "active" ? "Your monitored hosts are currently healthy." : "Alert events will appear here as rules evaluate samples.", "checkcircle", true);
    bindAlertFilters();
  }

  function renderAlertList(alerts, compact) {
    if (!alerts.length) return emptyState("No alerts", "There are no alert events for this host.", "checkcircle", true);
    if (compact) return `<div>${alerts.map((alert) => `<button class="alert-row alert-${escapeHtml(String(alert.severity || "info").toLowerCase())} ${alert.status === "resolved" ? "alert-resolved" : ""}" style="width:100%;border:0;text-align:left" data-action="process-detail" data-host="${escapeHtml(alert.host)}" data-pid="${alert.pid}" data-create-time="${alert.create_time}"><div>${severityBadge(alert.severity)}<small style="display:block;margin-top:5px;color:var(--text-muted);font-size:9px">${escapeHtml(alert.status)}</small></div><div class="alert-main"><strong>${escapeHtml(alert.rule_name)}</strong><small>${escapeHtml(alert.process_name)} · PID ${number(alert.pid)}</small></div><div class="alert-value">${escapeHtml(metricValue(alert.metric, alert.current_value))}<small>${escapeHtml(metricLabel(alert.metric))}</small></div><div class="alert-value">${escapeHtml(metricValue(alert.metric, alert.threshold))}<small>Threshold</small></div><div class="alert-value">${relativeTime(alert.triggered_at)}<small>${escapeHtml(alert.status)}</small></div><span>${icon("chevron")}</span></button>`).join("")}</div>`;
    return `<div>${alerts.map((alert) => `<button class="alert-row alert-${escapeHtml(String(alert.severity || "info").toLowerCase())} ${alert.status === "resolved" ? "alert-resolved" : ""}" style="width:100%;border:0;text-align:left" data-action="process-detail" data-host="${escapeHtml(alert.host)}" data-pid="${alert.pid}" data-create-time="${alert.create_time}"><div>${severityBadge(alert.severity)}<small style="display:block;margin-top:5px;color:var(--text-muted);font-size:9px">${escapeHtml(alert.status)}</small></div><div class="alert-main"><strong>${escapeHtml(alert.rule_name)}</strong><small>${escapeHtml(alert.process_name)} · PID ${number(alert.pid)} · ${escapeHtml(alert.host)}</small></div><div class="alert-value">${escapeHtml(metricValue(alert.metric, alert.current_value))}<small>${escapeHtml(metricLabel(alert.metric))}</small></div><div class="alert-value">${escapeHtml(metricValue(alert.metric, alert.threshold))}<small>Threshold</small></div><div class="alert-value">${dateTime(alert.triggered_at)}<small>${alert.resolved_at ? `Resolved ${relativeTime(alert.resolved_at)}` : "Triggered"}</small></div><span>${icon("chevron")}</span></button>`).join("")}</div>`;
  }

  function bindAlertFilters() {
    document.querySelectorAll("[data-alert-status]").forEach((button) => { button.onclick = () => { state.filters.alertStatus = button.dataset.alertStatus; renderPage(); }; });
    const search = document.getElementById("alertSearch");
    const severity = document.getElementById("alertSeverity");
    const host = document.getElementById("alertHost");
    const windowSelect = document.getElementById("alertWindow");
    if (severity) severity.value = state.filters.alertSeverity;
    if (windowSelect) windowSelect.value = state.filters.alertWindow;
    if (search) search.oninput = () => { state.filters.alertSearch = search.value; window.clearTimeout(state.alertSearchTimer); state.alertSearchTimer = window.setTimeout(() => renderPage(), 280); };
    if (severity) severity.onchange = () => { state.filters.alertSeverity = severity.value; renderPage(); };
    if (host) host.oninput = () => { state.filters.alertHost = host.value; window.clearTimeout(state.alertHostTimer); state.alertHostTimer = window.setTimeout(() => renderPage(), 280); };
    if (windowSelect) windowSelect.onchange = () => { state.filters.alertWindow = windowSelect.value; renderPage(); };
  }

  function defaultRule() {
    return { rule_id: "", name: "", metric: "cpu_percent", operator: "gt", threshold: 80, duration: 0, consecutive_samples: 1, action: "none", cooldown: 300, severity: "warning", enabled: true };
  }

  async function renderRules(token) {
    root.innerHTML = `<div class="page-intro"><div><h2>Alert rules</h2><p>Define the conditions that matter. Rules evaluate each exact process instance independently.</p></div><div class="page-actions"><button class="button" type="button" data-action="new-rule">${icon("plus")} Create rule</button></div></div>${state.ruleEditing ? ruleFormMarkup(state.ruleEditing) : ""}<div id="rulesPanel" class="rule-grid">${Array.from({ length: 4 }, () => '<div class="skeleton-card skeleton"></div>').join("")}</div>`;
    try {
      const data = await apiFetch("/alerts/rules");
      if (token !== state.requestToken) return;
      const panel = document.getElementById("rulesPanel");
      if (panel) panel.innerHTML = data.items?.length ? data.items.map(ruleCard).join("") : emptyState("No alert rules yet", "Create a rule to start evaluating CPU, memory, or I/O conditions.", "sliders");
      bindRuleForm();
    } catch (error) {
      const panel = document.getElementById("rulesPanel");
      if (panel) panel.innerHTML = errorState(error);
    }
  }

  function ruleCard(rule) {
    const id = escapeHtml(rule.rule_id);
    const condition = `${metricLabel(rule.metric)} ${operatorText(rule.operator)} ${metricValue(rule.metric, rule.threshold)}`;
    return `<article class="card rule-card"><div class="rule-card-top"><div class="rule-name"><strong title="${escapeHtml(rule.name)}">${escapeHtml(rule.name)}</strong><small>${id} · ${escapeHtml(rule.enabled ? "Enabled" : "Disabled")}</small></div><label class="switch" title="Toggle rule"><input type="checkbox" data-action="toggle-rule" data-rule-id="${id}" ${rule.enabled ? "checked" : ""}><span></span></label></div><p class="rule-description">Trigger when <strong>${escapeHtml(condition)}</strong> remains true for ${escapeHtml(durationText(rule.duration))} and ${number(rule.consecutive_samples)} consecutive sample${rule.consecutive_samples === 1 ? "" : "s"}.</p><div class="rule-meta"><div><label>Severity</label><strong>${severityBadge(rule.severity)}</strong></div><div><label>Action</label><strong>${escapeHtml(rule.action)}</strong></div><div><label>Cooldown</label><strong>${escapeHtml(durationText(rule.cooldown))}</strong></div></div><div class="rule-actions"><button class="table-action" type="button" data-action="edit-rule" data-rule-id="${id}" aria-label="Edit ${id}" title="Edit">${icon("edit")}</button><button class="table-action" type="button" data-action="delete-rule" data-rule-id="${id}" aria-label="Delete ${id}" title="Delete">${icon("trash")}</button></div></article>`;
  }

  function durationText(seconds) {
    const n = Number(seconds || 0);
    if (n === 0) return "immediately";
    if (n < 60) return `${number(n)} sec`;
    if (n < 3_600) return `${number(n / 60, 1)} min`;
    return `${number(n / 3_600, 1)} hr`;
  }

  function ruleFormMarkup(rule) {
    const editing = Boolean(rule.rule_id);
    return `<form id="ruleForm" class="card form-card" novalidate><div class="card-header" style="padding:0 0 16px"><div><h3 class="card-title">${editing ? "Edit alert rule" : "Create alert rule"}</h3><p class="card-subtitle">Duration is how long the condition must remain true before triggering.</p></div><button class="close-button" type="button" data-action="cancel-rule" aria-label="Close rule form">×</button></div><div class="form-grid"><div class="form-field"><label for="ruleId">Rule ID</label><input id="ruleId" name="rule_id" required pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" value="${escapeHtml(rule.rule_id)}" ${editing ? "readonly" : ""}><span class="field-help">Stable identifier used by integrations and alert history.</span></div><div class="form-field"><label for="ruleName">Name</label><input id="ruleName" name="name" required value="${escapeHtml(rule.name)}"><span class="field-help">A short description operators can recognize.</span></div><div class="form-field"><label for="ruleMetric">Metric</label><select id="ruleMetric" name="metric"><option value="cpu_percent">CPU percent</option><option value="memory_percent">Memory percent</option><option value="memory_rss">Memory RSS</option><option value="read_bytes">Read I/O</option><option value="write_bytes">Write I/O</option></select></div><div class="form-field"><label for="ruleOperator">Operator</label><select id="ruleOperator" name="operator"><option value="gt">Greater than</option><option value="gte">Greater than or equal</option><option value="lt">Less than</option><option value="lte">Less than or equal</option><option value="eq">Equal to</option></select></div><div class="form-field"><label for="ruleThreshold">Threshold</label><input id="ruleThreshold" name="threshold" type="number" min="0" step="any" required value="${escapeHtml(rule.threshold)}"><span class="field-help" id="thresholdHelp">Percent for CPU and memory percentage metrics.</span></div><div class="form-field"><label for="ruleDuration">Duration <span>(seconds)</span></label><input id="ruleDuration" name="duration" type="number" min="0" step="1" required value="${escapeHtml(rule.duration)}"><span class="field-help">How long the condition must remain true.</span></div><div class="form-field"><label for="ruleConsecutive">Consecutive samples</label><input id="ruleConsecutive" name="consecutive_samples" type="number" min="1" step="1" required value="${escapeHtml(rule.consecutive_samples)}"><span class="field-help">How many matching observations are required.</span></div><div class="form-field"><label for="ruleCooldown">Cooldown <span>(seconds)</span></label><input id="ruleCooldown" name="cooldown" type="number" min="0" step="1" required value="${escapeHtml(rule.cooldown)}"><span class="field-help">Quiet period after a condition resolves.</span></div><div class="form-field"><label for="ruleSeverity">Severity</label><select id="ruleSeverity" name="severity"><option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option></select></div><div class="form-field"><label for="ruleAction">Action</label><select id="ruleAction" name="action"><option value="none">No notification</option><option value="webhook">Webhook</option><option value="email">Email</option></select><span class="field-help">Delivery uses collector environment configuration.</span></div><div class="form-field full"><label class="checkbox-row"><input id="ruleEnabled" name="enabled" type="checkbox" ${rule.enabled ? "checked" : ""}> Enable this rule</label></div></div><div class="form-footer"><button class="button secondary" type="button" data-action="cancel-rule">Cancel</button><button class="button" type="submit">${editing ? "Save changes" : "Create rule"}</button></div></form>`;
  }

  function bindRuleForm() {
    const form = document.getElementById("ruleForm");
    if (!form) return;
    const metric = document.getElementById("ruleMetric");
    const thresholdHelp = document.getElementById("thresholdHelp");
    if (metric) {
      metric.value = state.ruleEditing.metric;
      metric.onchange = () => { thresholdHelp.textContent = metric.value.includes("percent") ? "Percent for CPU and memory percentage metrics." : "Use the same unit as the selected metric."; };
    }
    ["ruleOperator", "ruleSeverity", "ruleAction"].forEach((id) => {
      const el = document.getElementById(id);
      const key = { ruleOperator: "operator", ruleSeverity: "severity", ruleAction: "action" }[id];
      if (el) el.value = state.ruleEditing[key];
    });
    form.onsubmit = async (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      const payload = { rule_id: String(formData.get("rule_id") || "").trim(), name: String(formData.get("name") || "").trim(), metric: formData.get("metric"), operator: formData.get("operator"), threshold: Number(formData.get("threshold")), duration: Number(formData.get("duration")), consecutive_samples: Number(formData.get("consecutive_samples")), cooldown: Number(formData.get("cooldown")), severity: formData.get("severity"), action: formData.get("action"), enabled: document.getElementById("ruleEnabled").checked };
      if (!payload.rule_id || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(payload.rule_id) || !payload.name || !Number.isFinite(payload.threshold) || payload.threshold < 0 || !Number.isFinite(payload.duration) || payload.duration < 0 || !Number.isInteger(payload.consecutive_samples) || payload.consecutive_samples < 1 || !Number.isFinite(payload.cooldown) || payload.cooldown < 0) {
        showToast("Check the rule fields", "Enter a valid ID, name, threshold, and non-negative timing values.", "error");
        return;
      }
      const editing = Boolean(state.ruleEditing.rule_id);
      try {
        await apiFetch(editing ? `/alerts/rules/${encodeURIComponent(state.ruleEditing.rule_id)}` : "/alerts/rules", { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        state.ruleEditing = null;
        showToast(editing ? "Rule updated" : "Rule created", editing ? "The alert rule is now updated." : "The collector will evaluate it on new samples.", "success");
        renderPage();
      } catch (error) { showToast("Rule could not be saved", error.message, "error"); }
    };
  }

  async function renderHistory(token) {
    const f = state.filters;
    root.innerHTML = `<div class="page-intro"><div><h2>History</h2><p>Search retained samples for audits, investigations, and process-instance verification.</p></div><div class="page-actions"><span class="status-badge info">Instance-safe history</span></div></div><div class="card table-card"><div class="table-toolbar"><div class="toolbar-left"><input id="historySearch" class="field" type="search" placeholder="Search process, PID, host, or user…" aria-label="Search history" value="${escapeHtml(f.historySearch)}"><input id="historyHost" class="field" style="width:150px" type="search" placeholder="Host" aria-label="Filter history host" value="${escapeHtml(f.historyHost)}"></div><div class="toolbar-right"><select id="historyLimit" class="select-field" aria-label="History page size"><option value="100">100 rows</option><option value="250">250 rows</option><option value="500">500 rows</option></select></div></div><div id="historyTable" class="data-table-wrap">${loadingRows()}</div><div id="historyPagination"></div></div>`;
    const data = await apiFetch("/history", {}, { search: f.historySearch, host: f.historyHost, limit: 100, offset: f.historyPage * 100 }).catch((error) => ({ __error: error }));
    if (token !== state.requestToken) return;
    const panel = document.getElementById("historyTable");
    if (data.__error) { if (panel) panel.innerHTML = errorState(data.__error); return; }
    if (panel) panel.innerHTML = data.items?.length ? historyTable(data.items) : emptyState("No retained samples", "Samples will appear when an agent reports to the collector.", "history");
    const pagination = document.getElementById("historyPagination");
    if (pagination) pagination.innerHTML = paginationMarkup(f.historyPage + 1, 100, data.total, "history");
    bindHistoryFilters();
  }

  function historyTable(rows) {
    return `<table><thead><tr><th>Timestamp</th><th>Process</th><th>PID</th><th>Host</th><th>Instance start</th><th>CPU</th><th>Memory</th><th>Read I/O</th><th>Write I/O</th></tr></thead><tbody>${rows.map((row) => `<tr><td title="${escapeHtml(fullDateTime(row.timestamp))}">${dateTime(row.timestamp)}</td><td><span class="process-name">${escapeHtml(row.process_name)}</span><span class="process-sub">${escapeHtml(row.username || "Unknown")}</span></td><td class="number-cell">${number(row.pid)}</td><td>${escapeHtml(row.host)}</td><td title="${escapeHtml(fullDateTime(row.create_time))}">${dateTime(row.create_time)}</td><td>${metricCell("cpu_percent", row.cpu_percent)}</td><td>${metricCell("memory_percent", row.memory_percent)}</td><td class="number-cell">${bytes(row.read_bytes)}</td><td class="number-cell">${bytes(row.write_bytes)}</td></tr>`).join("")}</tbody></table>`;
  }

  function bindHistoryFilters() {
    const search = document.getElementById("historySearch");
    const host = document.getElementById("historyHost");
    if (search) search.oninput = () => { state.filters.historySearch = search.value; state.filters.historyPage = 0; window.clearTimeout(state.historySearchTimer); state.historySearchTimer = window.setTimeout(() => renderPage(), 280); };
    if (host) host.oninput = () => { state.filters.historyHost = host.value; state.filters.historyPage = 0; window.clearTimeout(state.historyHostTimer); state.historyHostTimer = window.setTimeout(() => renderPage(), 280); };
  }

  async function renderSettings(token) {
    let summary = state.summary;
    if (!summary) { try { summary = await apiFetch("/summary", {}, { window: 3600 }); state.summary = summary; } catch (_) { /* settings can still render */ } }
    if (token !== state.requestToken) return;
    root.innerHTML = `<div class="page-intro"><div><h2>Settings</h2><p>Control the browser experience without changing collector data or alert behavior.</p></div></div><div class="settings-grid"><section class="card settings-section"><h3>Live updates</h3><p>Polling is intentionally lightweight and can be paused while you investigate a historical view.</p><div class="setting-row"><div class="setting-label"><strong>Refresh interval</strong><small>How often the current view requests fresh data.</small></div><select id="settingsRefresh" class="select-field"><option value="10">Every 10 seconds</option><option value="30">Every 30 seconds</option><option value="60">Every minute</option><option value="300">Every 5 minutes</option></select></div><div class="setting-row"><div class="setting-label"><strong>Live updates</strong><small>Pause polling while keeping the current view visible.</small></div><button class="button ${state.liveUpdates ? "secondary" : ""}" id="settingsLive" type="button">${state.liveUpdates ? "Pause updates" : "Resume updates"}</button></div></section><section class="card settings-section"><h3>Collector connection</h3><p>The dashboard uses the configured same-origin API. Credentials and notification secrets remain server-side.</p><div class="setting-row"><div class="setting-label"><strong>API endpoint</strong><small>Browser request base</small></div><span class="status-badge info">${escapeHtml(API_BASE)}</span></div><div class="setting-row"><div class="setting-label"><strong>Current state</strong><small>Last request result</small></div>${statusBadge(state.connected ? "live" : "offline", state.connected ? "Connected" : "Offline")}</div><div class="setting-row"><div class="setting-label"><strong>Last successful update</strong><small>Browser-local timestamp</small></div><strong style="font-size:11px">${state.lastSuccessfulUpdate ? fullDateTime(state.lastSuccessfulUpdate) : "Not yet"}</strong></div></section><section class="card settings-section"><h3>Collector thresholds</h3><p>These values come from the collector configuration and determine host freshness.</p><div class="setting-row"><div class="setting-label"><strong>Stale after</strong><small>Host has not been seen recently.</small></div><strong style="font-size:11px">${summary?.freshness ? `${number(summary.freshness.stale_after_seconds)} seconds` : "Configured server-side"}</strong></div><div class="setting-row"><div class="setting-label"><strong>Data model</strong><small>Every sample retains exact process identity.</small></div><span class="status-badge live">host · PID · create time</span></div></section></div>`;
    const select = document.getElementById("settingsRefresh");
    if (select) { select.value = String(state.refreshInterval); select.onchange = () => { state.refreshInterval = Number(select.value); persistSettings(); scheduleRefresh(); document.getElementById("refreshIntervalLabel").textContent = `${state.refreshInterval}s`; showToast("Refresh interval updated", `Live data will refresh every ${state.refreshInterval} seconds.`, "success"); }; }
    const live = document.getElementById("settingsLive");
    if (live) live.onclick = () => toggleLiveUpdates();
  }

  async function openProcessDetail(host, pid, createTime) {
    drawerRoot.innerHTML = `<div class="drawer-backdrop" data-action="close-drawer"></div><aside class="drawer" role="dialog" aria-modal="true" aria-label="Process detail"><div class="drawer-header"><div class="detail-title"><span class="detail-icon">${icon("process")}</span><div><h2>Process detail</h2><p>${escapeHtml(host)} · PID ${escapeHtml(pid)}</p></div></div><button class="close-button" type="button" data-action="close-drawer" aria-label="Close process detail">×</button></div><div class="drawer-content"><div class="loading-center"><span class="spinner"></span>Loading exact process instance…</div></div></aside>`;
    try {
      const data = await apiFetch(`/processes/${encodeURIComponent(host)}/${encodeURIComponent(pid)}`, {}, { create_time: createTime, window: 86_400 });
      const content = drawerRoot.querySelector(".drawer-content");
      if (!content) return;
      const current = data.current || {};
      const identity = data.identity || {};
      content.innerHTML = `<div class="drawer-section"><div class="detail-title"><span class="detail-icon">${icon("process")}</span><div><h2>${escapeHtml(identity.process_name)}</h2><p>Exact process instance · ${escapeHtml(data.instance_id)}</p></div></div></div><div class="detail-metrics"><div class="detail-metric"><label>CPU</label><strong>${percent(current.cpu_percent)}</strong></div><div class="detail-metric"><label>Memory</label><strong>${percent(current.memory_percent)}</strong></div><div class="detail-metric"><label>Memory RSS</label><strong>${bytes(current.memory_rss)}</strong></div><div class="detail-metric"><label>Read I/O</label><strong>${bytes(current.read_bytes)}</strong></div><div class="detail-metric"><label>Write I/O</label><strong>${bytes(current.write_bytes)}</strong></div><div class="detail-metric"><label>State</label><strong>${escapeHtml(current.state || "unknown")}</strong></div></div><div class="card" style="box-shadow:none"><div class="card-header"><div><h3 class="card-title">Identity</h3><p class="card-subtitle">History is isolated by host, PID, and create time.</p></div>${statusBadge(current.state === "running" ? "live" : "neutral", current.state || "unknown")}</div><div class="identity-list"><div class="identity-row"><span>Host</span><span>${escapeHtml(identity.host)}</span></div><div class="identity-row"><span>PID</span><span>${number(identity.pid)}</span></div><div class="identity-row"><span>User</span><span>${escapeHtml(identity.username || "Unknown")}</span></div><div class="identity-row"><span>Start / create time</span><span title="${escapeHtml(fullDateTime(identity.create_time))}">${fullDateTime(identity.create_time)}</span></div><div class="identity-row"><span>Last sample</span><span>${fullDateTime(current.timestamp)}</span></div></div></div><div class="drawer-section"><div class="drawer-section-title"><h3>CPU over time</h3><span class="status-badge info">${number((data.history || []).length)} samples</span></div><div class="card" style="box-shadow:none"><div class="card-body"><div id="detailCpuChart" class="chart-wrap tall"></div></div></div></div><div class="drawer-section"><div class="drawer-section-title"><h3>Memory over time</h3></div><div class="card" style="box-shadow:none"><div class="card-body"><div id="detailMemoryChart" class="chart-wrap tall"></div></div></div></div><div class="drawer-section"><div class="drawer-section-title"><h3>I/O over time</h3><div class="chart-legend"><span><i class="legend-dot"></i>Read</span><span><i class="legend-dot purple"></i>Write</span></div></div><div class="card" style="box-shadow:none"><div class="card-body"><div id="detailReadChart" class="chart-wrap tall"></div><div id="detailWriteChart" class="chart-wrap tall" style="margin-top:10px"></div></div></div></div><div class="drawer-section"><div class="drawer-section-title"><h3>Alert information</h3></div><div class="card" style="box-shadow:none">${data.alerts?.length ? renderAlertList(data.alerts, true) : emptyState("No alerts for this instance", "This exact process instance has not triggered a rule.", "checkcircle", true)}</div></div><div class="drawer-section"><div class="drawer-section-title"><h3>Recent samples</h3></div><div class="card table-card"><div class="data-table-wrap">${historyTable((data.history || []).slice(-12).reverse())}</div></div></div>`;
      lineChart(document.getElementById("detailCpuChart"), data.series?.cpu, { color: "#2f6fed", label: "CPU", format: (v) => percent(v) });
      lineChart(document.getElementById("detailMemoryChart"), data.series?.memory, { color: "#7658c5", label: "Memory", format: (v) => percent(v) });
      lineChart(document.getElementById("detailReadChart"), data.series?.read_io, { color: "#2f6fed", label: "Read I/O", format: bytes });
      lineChart(document.getElementById("detailWriteChart"), data.series?.write_io, { color: "#7658c5", label: "Write I/O", format: bytes });
    } catch (error) {
      const content = drawerRoot.querySelector(".drawer-content");
      if (content) content.innerHTML = errorState(error, "close-drawer");
    }
  }

  function parseRoute() {
    const hash = window.location.hash.replace(/^#/, "") || "overview";
    const [page, ...rest] = hash.split("/");
    state.page = ["overview", "hosts", "host", "processes", "alerts", "rules", "history", "settings"].includes(page) ? page : "overview";
    state.param = rest.length ? decodeURIComponent(rest.join("/")) : "";
  }

  function updatePageChrome() {
    const titles = { overview: ["Monitoring", "Overview"], hosts: ["Infrastructure", "Hosts"], host: ["Infrastructure", "Host detail"], processes: ["Workloads", "Processes"], alerts: ["Operations", "Alerts"], rules: ["Operations", "Rules"], history: ["Investigation", "History"], settings: ["Workspace", "Settings"] };
    const [eyebrow, title] = titles[state.page] || titles.overview;
    document.getElementById("pageEyebrow").textContent = eyebrow;
    document.getElementById("pageTitle").textContent = title;
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === (state.page === "host" ? "hosts" : state.page)));
    document.title = `${title} · Process Monitor`;
  }

  async function renderPage() {
    parseRoute();
    updatePageChrome();
    state.requestToken += 1;
    const token = state.requestToken;
    state.loading = true;
    try {
      if (state.page === "overview") await renderOverview(token);
      else if (state.page === "hosts") await renderHosts(token);
      else if (state.page === "host") await renderHostDetail(token, state.param);
      else if (state.page === "processes") await renderProcesses(token);
      else if (state.page === "alerts") await renderAlerts(token);
      else if (state.page === "rules") await renderRules(token);
      else if (state.page === "history") await renderHistory(token);
      else if (state.page === "settings") await renderSettings(token);
      if (state.connected) markUpdated();
    } catch (error) {
      if (token === state.requestToken) root.innerHTML = errorState(error);
    } finally { state.loading = false; }
  }

  function persistSettings() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ refreshInterval: state.refreshInterval, liveUpdates: state.liveUpdates })); } catch (_) { /* storage is optional */ }
  }

  function restoreSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      if ([10, 30, 60, 300].includes(Number(saved.refreshInterval))) state.refreshInterval = Number(saved.refreshInterval);
      if (typeof saved.liveUpdates === "boolean") state.liveUpdates = saved.liveUpdates;
    } catch (_) { /* ignore malformed browser storage */ }
    const label = document.getElementById("refreshIntervalLabel");
    if (label) label.textContent = `${state.refreshInterval}s`;
  }

  function scheduleRefresh() {
    window.clearInterval(state.refreshTimer);
    state.refreshTimer = window.setInterval(() => { if (state.liveUpdates && !state.loading) renderPage(); }, state.refreshInterval * 1000);
  }

  function toggleLiveUpdates() {
    state.liveUpdates = !state.liveUpdates;
    persistSettings();
    const button = document.getElementById("liveToggle");
    const pulse = document.getElementById("livePulse");
    if (button) button.innerHTML = `${icon(state.liveUpdates ? "pause" : "play")}<span>${state.liveUpdates ? "Pause updates" : "Resume updates"}</span>`;
    if (pulse) pulse.classList.toggle("paused", !state.liveUpdates);
    showToast(state.liveUpdates ? "Live updates resumed" : "Live updates paused", state.liveUpdates ? "The current view will refresh automatically." : "Your current view will stay in place until you resume.", "success");
  }

  function showToast(title, message, type = "success") {
    const region = document.getElementById("toastRegion");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `${icon(type === "error" ? "alert" : "checkcircle")}<div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span></div>`;
    region.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }

  async function globalSearch(value) {
    const box = document.getElementById("globalSearchResults");
    if (!box) return;
    const query = value.trim();
    if (query.length < 2) { box.hidden = true; box.innerHTML = ""; return; }
    box.hidden = false;
    box.innerHTML = `<div class="search-result-empty"><span class="spinner"></span>Searching…</div>`;
    const results = await Promise.allSettled([
      apiFetch("/processes", {}, { search: query, page: 1, page_size: 4 }),
      apiFetch("/hosts", {}, { search: query, window: 86_400 }),
      apiFetch("/alerts", {}, { search: query, status: "all", limit: 4 }),
    ]);
    const processItems = results[0].status === "fulfilled" ? results[0].value.items || [] : [];
    const hostItems = results[1].status === "fulfilled" ? results[1].value.items || [] : [];
    const alertItems = results[2].status === "fulfilled" ? results[2].value.items || [] : [];
    const html = [
      ...hostItems.slice(0, 3).map((host) => `<button class="search-result" data-action="search-host" data-host="${escapeHtml(host.host)}" type="button"><span>${icon("server")}</span><span><strong>${escapeHtml(host.host)}</strong><small>Host · ${escapeHtml(host.status)}</small></span></button>`),
      ...processItems.slice(0, 4).map((process) => `<button class="search-result" data-action="search-process" data-host="${escapeHtml(process.host)}" data-pid="${process.pid}" data-create-time="${process.create_time}" type="button"><span>${icon("process")}</span><span><strong>${escapeHtml(process.process_name)}</strong><small>PID ${number(process.pid)} · ${escapeHtml(process.host)}</small></span></button>`),
      ...alertItems.slice(0, 3).map((alert) => `<button class="search-result" data-action="search-alert" data-host="${escapeHtml(alert.host)}" data-pid="${alert.pid}" data-create-time="${alert.create_time}" type="button"><span>${icon("bell")}</span><span><strong>${escapeHtml(alert.rule_name)}</strong><small>Alert · ${escapeHtml(alert.host)} · ${escapeHtml(alert.status)}</small></span></button>`),
    ].join("");
    box.innerHTML = html || `<div class="search-result-empty">No process, host, or alert matches.</div>`;
  }

  document.addEventListener("click", (event) => {
    const actionNode = event.target.closest("[data-action]");
    if (!actionNode) return;
    const action = actionNode.dataset.action;
    if (["close-drawer", "process-detail", "host-detail", "search-host", "search-process", "search-alert"].includes(action)) event.stopPropagation();
    if (action === "close-drawer") { drawerRoot.innerHTML = ""; return; }
    if (action === "process-detail" || action === "search-process" || action === "search-alert") { openProcessDetail(actionNode.dataset.host, actionNode.dataset.pid, actionNode.dataset.createTime); document.getElementById("globalSearchResults").hidden = true; return; }
    if (action === "host-detail" || action === "search-host") { window.location.hash = `#host/${encodeURIComponent(actionNode.dataset.host)}`; document.getElementById("globalSearchResults").hidden = true; return; }
    if (action === "jump-processes") { window.location.hash = "#processes"; return; }
    if (action === "jump-hosts" || action === "back-hosts") { window.location.hash = "#hosts"; return; }
    if (action === "jump-processes-host") { state.filters.processHost = actionNode.dataset.host; window.location.hash = "#processes"; return; }
    if (action === "jump-alerts-host") { state.filters.alertHost = actionNode.dataset.host; window.location.hash = "#alerts"; return; }
    if (action === "retry-page" || action === "refresh-page") { renderPage(); return; }
    if (action === "toggle-process-alert") { state.filters.processAlert = !state.filters.processAlert; state.filters.processPage = 1; renderPage(); return; }
    if (action === "sort-process") { if (state.filters.processSort === actionNode.dataset.sort) state.filters.processOrder = state.filters.processOrder === "asc" ? "desc" : "asc"; else { state.filters.processSort = actionNode.dataset.sort; state.filters.processOrder = "desc"; } renderPage(); return; }
    if (action === "page-prev" || action === "page-next") { const direction = action === "page-next" ? 1 : -1; if (actionNode.dataset.kind === "process") state.filters.processPage = Math.max(1, state.filters.processPage + direction); else state.filters.historyPage = Math.max(0, state.filters.historyPage + direction); renderPage(); return; }
    if (action === "new-rule") { state.ruleEditing = defaultRule(); renderPage(); return; }
    if (action === "cancel-rule") { state.ruleEditing = null; renderPage(); return; }
    if (action === "edit-rule") { apiFetch(`/alerts/rules/${encodeURIComponent(actionNode.dataset.ruleId)}`).then((rule) => { state.ruleEditing = rule; renderPage(); }).catch((error) => showToast("Rule could not be opened", error.message, "error")); return; }
    if (action === "delete-rule") {
      if (!window.confirm(`Delete rule “${actionNode.dataset.ruleId}”? Existing alert history will remain.`)) return;
      apiFetch(`/alerts/rules/${encodeURIComponent(actionNode.dataset.ruleId)}`, { method: "DELETE" }).then(() => { showToast("Rule deleted", "Existing alert history was kept.", "success"); renderPage(); }).catch((error) => showToast("Rule could not be deleted", error.message, "error"));
      return;
    }
  });

  document.addEventListener("change", (event) => {
    const input = event.target.closest('[data-action="toggle-rule"]');
    if (!input) return;
    const ruleId = input.dataset.ruleId;
    apiFetch("/alerts/rules").then((data) => data.items.find((rule) => rule.rule_id === ruleId)).then((rule) => {
      if (!rule) throw new Error("Rule was not found");
      return apiFetch(`/alerts/rules/${encodeURIComponent(ruleId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...rule, enabled: input.checked }) });
    }).then(() => showToast(input.checked ? "Rule enabled" : "Rule disabled", "The new state applies to future samples.", "success")).catch((error) => { input.checked = !input.checked; showToast("Rule state could not be updated", error.message, "error"); });
  });

  document.getElementById("refreshButton")?.addEventListener("click", () => { renderPage(); });
  document.getElementById("retryButton")?.addEventListener("click", () => { renderPage(); });
  document.getElementById("liveToggle")?.addEventListener("click", toggleLiveUpdates);
  document.getElementById("sidebarOpen")?.addEventListener("click", () => { document.getElementById("sidebar").classList.add("open"); document.getElementById("sidebarScrim").hidden = false; });
  document.getElementById("sidebarClose")?.addEventListener("click", () => { document.getElementById("sidebar").classList.remove("open"); document.getElementById("sidebarScrim").hidden = true; });
  document.getElementById("sidebarScrim")?.addEventListener("click", () => { document.getElementById("sidebar").classList.remove("open"); document.getElementById("sidebarScrim").hidden = true; });
  document.getElementById("globalSearch")?.addEventListener("input", (event) => { window.clearTimeout(state.globalSearchTimer); state.globalSearchTimer = window.setTimeout(() => globalSearch(event.target.value), 220); });
  document.getElementById("globalSearch")?.addEventListener("keydown", (event) => { if (event.key === "Enter") { state.filters.processSearch = event.target.value; state.filters.processPage = 1; document.getElementById("globalSearchResults").hidden = true; window.location.hash = "#processes"; } if (event.key === "Escape") { event.target.value = ""; document.getElementById("globalSearchResults").hidden = true; event.target.blur(); } });
  document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); document.getElementById("globalSearch")?.focus(); } });
  document.addEventListener("click", (event) => { const results = document.getElementById("globalSearchResults"); const search = document.querySelector(".global-search-wrap"); if (results && search && !search.contains(event.target)) results.hidden = true; });
  window.addEventListener("hashchange", () => { document.getElementById("sidebar").classList.remove("open"); document.getElementById("sidebarScrim").hidden = true; drawerRoot.innerHTML = ""; renderPage(); });

  initIcons();
  restoreSettings();
  setConnection(false);
  scheduleRefresh();
  renderPage();
  apiFetch("/health").then((health) => {
    const version = document.querySelector(".sidebar-version span:first-child");
    if (version && health?.version) version.textContent = `Version ${health.version}`;
  }).catch(() => { /* the page renderer owns the visible offline state */ });
})();
