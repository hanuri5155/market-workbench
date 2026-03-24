const QUERY_PARAM_KEY = "chartPerf";
const STORAGE_KEY = "chartPerf";
const FLOAT_PRECISION = 3;

function roundMs(value) {
  if (!Number.isFinite(value)) return null;
  return Number(value.toFixed(FLOAT_PRECISION));
}

function sanitizeMeta(value, depth = 0) {
  if (depth > 4) return "[depth-limit]";
  if (value == null) return value;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.slice(0, 50).map((item) => sanitizeMeta(item, depth + 1));
  }
  if (typeof value === "object") {
    const out = {};
    for (const [key, item] of Object.entries(value).slice(0, 50)) {
      out[key] = sanitizeMeta(item, depth + 1);
    }
    return out;
  }
  return String(value);
}

function createState() {
  return {
    sessionStartedAtIso: new Date().toISOString(),
    marks: [],
    measures: [],
    counters: {},
    seq: 0,
  };
}

function detectEnabled() {
  if (typeof window === "undefined") return false;
  try {
    const params = new URLSearchParams(window.location.search || "");
    if (params.get(QUERY_PARAM_KEY) === "1") return true;
  } catch {
    // ignore
  }

  try {
    return window.localStorage?.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

const chartPerfEnabled = detectEnabled();
let state = createState();

function getNow() {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function getLastMark(name) {
  for (let index = state.marks.length - 1; index >= 0; index -= 1) {
    const item = state.marks[index];
    if (item.name === name) return item;
  }
  return null;
}

function getLatestMarkBefore(name, beforeTs) {
  for (let index = state.marks.length - 1; index >= 0; index -= 1) {
    const item = state.marks[index];
    if (item.name !== name) continue;
    if (beforeTs != null && item.ts > beforeTs) continue;
    return item;
  }
  return null;
}

function getLatestDuration(startName, endName) {
  const end = getLastMark(endName);
  if (!end) return null;
  const start = getLatestMarkBefore(startName, end.ts);
  if (!start) return null;
  return roundMs(end.ts - start.ts);
}

function getSnapshotReasonCounts() {
  const counts = {};
  for (const markItem of state.marks) {
    if (markItem.name !== "position_overlay_snapshot_start") continue;
    const reason = String(markItem.meta?.reason || "unknown");
    counts[reason] = (counts[reason] || 0) + 1;
  }
  return counts;
}

function getMeasureDurations(label) {
  return state.measures
    .filter((item) => item.label === label && Number.isFinite(item.durationMs))
    .map((item) => Number(item.durationMs));
}

function countTrackedRequestsBefore(markName) {
  const endMark = getLastMark(markName);
  if (!endMark) return null;
  const rangeStart =
    getLatestMarkBefore("chart_tab_click", endMark.ts) ||
    getLatestMarkBefore("chart_route_enter", endMark.ts) ||
    getLatestMarkBefore("chart_component_mount", endMark.ts);

  let count = 0;
  for (const markItem of state.marks) {
    if (rangeStart && markItem.ts < rangeStart.ts) continue;
    if (markItem.ts > endMark.ts) break;
    if (markItem.meta?.request === true && /_start$/.test(markItem.name)) {
      count += 1;
    }
  }
  return count;
}

function computePollingQpsEstimate() {
  const tickCount = Number(state.counters.polling_tick_count || 0);
  if (tickCount <= 0) return 0;

  const start = getLastMark("polling_subscribe_start");
  const end = getLastMark("polling_tick_end") || getLastMark("polling_tick_start");
  if (!start || !end || end.ts <= start.ts) return 0;

  const seconds = (end.ts - start.ts) / 1000;
  if (!Number.isFinite(seconds) || seconds <= 0) return 0;
  return roundMs(tickCount / seconds);
}

function buildMetrics() {
  const snapshotCount = Number(state.counters.position_overlay_snapshot_count || 0);
  const overlayDurations = getMeasureDurations("zone_overlay_apply_ms");
  const pollingDurations = getMeasureDurations("polling_tick_ms");
  const lastPollingLatency =
    pollingDurations.length > 0 ? pollingDurations[pollingDurations.length - 1] : null;

  const firstUsableMark = getLastMark("first_usable_chart_paint");
  const latestClickStart =
    (firstUsableMark && getLatestMarkBefore("chart_tab_click", firstUsableMark.ts)) ||
    (firstUsableMark && getLatestMarkBefore("chart_route_enter", firstUsableMark.ts)) ||
    null;

  const firstOverlayEnd = getLastMark("first_overlay_end");
  const overlayStart =
    (firstOverlayEnd && getLatestMarkBefore("chart_tab_click", firstOverlayEnd.ts)) ||
    (firstOverlayEnd && getLatestMarkBefore("chart_route_enter", firstOverlayEnd.ts)) ||
    null;

  return {
    click_to_first_usable_chart_paint_ms:
      latestClickStart && firstUsableMark
        ? roundMs(firstUsableMark.ts - latestClickStart.ts)
        : null,
    click_to_first_overlay_done_ms:
      overlayStart && firstOverlayEnd ? roundMs(firstOverlayEnd.ts - overlayStart.ts) : null,
    getBars_init_total_ms: getLatestDuration("getBars_init_start", "getBars_init_callback"),
    current_tf_candles_fetch_ms: getLatestDuration(
      "current_tf_candles_fetch_start",
      "current_tf_candles_fetch_end"
    ),
    zone_boxes_preload_ms: getLatestDuration("zone_boxes_preload_start", "zone_boxes_preload_end"),
    mtf_init_total_ms: getLatestDuration("mtf_init_start", "mtf_init_end"),
    number_of_chart_requests_before_first_usable_chart_paint:
      countTrackedRequestsBefore("first_usable_chart_paint"),
    position_overlay_snapshot_duplicate_count: Math.max(0, snapshotCount - 1),
    mount_count: Number(state.counters.mount_count || 0),
    unmount_count: Number(state.counters.unmount_count || 0),
    dispose_count: Number(state.counters.dispose_count || 0),
    polling_qps_estimate: computePollingQpsEstimate(),
    polling_last_latency_ms: lastPollingLatency,
    zone_overlay_apply_last_ms:
      overlayDurations.length > 0 ? overlayDurations[overlayDurations.length - 1] : null,
    zone_overlay_apply_avg_ms:
      overlayDurations.length > 0
        ? roundMs(
            overlayDurations.reduce((sum, value) => sum + value, 0) / overlayDurations.length
          )
        : null,
  };
}

function buildExportData() {
  return {
    enabled: true,
    sessionStartedAtIso: state.sessionStartedAtIso,
    marks: state.marks.map((item) => ({ ...item })),
    measures: state.measures.map((item) => ({ ...item })),
    counters: { ...state.counters },
    metrics: buildMetrics(),
    extras: {
      devMode: Boolean(import.meta.env?.DEV),
      positionOverlaySnapshotByReason: getSnapshotReasonCounts(),
    },
  };
}

export function isChartPerfEnabled() {
  return chartPerfEnabled;
}

export function mark(name, meta = undefined) {
  if (!chartPerfEnabled) return null;
  const item = {
    seq: ++state.seq,
    name,
    ts: roundMs(getNow()),
    wallTimeIso: new Date().toISOString(),
    meta: sanitizeMeta(meta),
  };
  state.marks.push(item);
  return item;
}

export function measure(startMarkName, endMarkName, label, meta = undefined) {
  if (!chartPerfEnabled) return null;
  const endMark = getLastMark(endMarkName);
  if (!endMark) return null;
  const startMark = getLatestMarkBefore(startMarkName, endMark.ts);
  if (!startMark) return null;

  const item = {
    seq: ++state.seq,
    label,
    startMark: startMarkName,
    endMark: endMarkName,
    durationMs: roundMs(endMark.ts - startMark.ts),
    meta: sanitizeMeta(meta),
  };
  state.measures.push(item);
  return item;
}

export function counter(name, delta = 1) {
  if (!chartPerfEnabled) return 0;
  const normalizedDelta = Number.isFinite(delta) ? delta : 1;
  state.counters[name] = (state.counters[name] || 0) + normalizedDelta;
  return state.counters[name];
}

export function reset() {
  if (!chartPerfEnabled) return null;
  state = createState();
  return buildExportData();
}

export function exportChartPerf() {
  if (!chartPerfEnabled) {
    return {
      enabled: false,
      reason: "chartPerf is disabled. Enable with ?chartPerf=1 or localStorage.setItem('chartPerf', '1')",
    };
  }
  return buildExportData();
}

export function printSummary() {
  if (!chartPerfEnabled) {
    console.info(
      "[chartPerf] disabled. Enable with ?chartPerf=1 or localStorage.setItem('chartPerf', '1')."
    );
    return;
  }

  const exported = buildExportData();
  const metricsRows = Object.entries(exported.metrics).map(([metric, value]) => ({
    metric,
    value,
  }));
  const counterRows = Object.entries(exported.counters).map(([counterName, value]) => ({
    counter: counterName,
    value,
  }));
  const recentMarks = exported.marks.slice(-20).map((item) => ({
    seq: item.seq,
    name: item.name,
    ts: item.ts,
    meta: JSON.stringify(item.meta || {}),
  }));

  console.groupCollapsed("[chartPerf] Summary");
  console.table(metricsRows);
  if (counterRows.length > 0) {
    console.table(counterRows);
  }
  if (exported.measures.length > 0) {
    console.table(exported.measures);
  }
  if (recentMarks.length > 0) {
    console.table(recentMarks);
  }
  console.groupEnd();
}

if (chartPerfEnabled && typeof window !== "undefined") {
  window.__chartPerf = {
    mark,
    measure,
    counter,
    export: exportChartPerf,
    reset,
    printSummary,
  };
}
