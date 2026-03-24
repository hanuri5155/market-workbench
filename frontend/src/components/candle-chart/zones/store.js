import { DEFAULT_SYMBOL, TF_LIST } from "../constants";

// 타임프레임별 Structure Zone 상태를 한곳에서 관리하기 위함
export const zoneStore = {
  zonesByTf: {},
  boxStateCache: {},
  zonesLoaded: {},
  rangeCoverageByTf: {},
  inFlightByTf: {},
  lastFetchedAtByTf: {},
  allLoaded: false,
  allLoading: null,
  hoveredBoxId: null,
  chart: null,
  ws: null,
  wsReconnectTimer: null,
};

for (const tf of TF_LIST) {
  zoneStore.zonesByTf[tf] = [];
  zoneStore.zonesLoaded[tf] = false;
  zoneStore.rangeCoverageByTf[tf] = [];
  zoneStore.inFlightByTf[tf] = {};
  zoneStore.lastFetchedAtByTf[tf] = 0;
}

// 차트와 알림 패널이 같은 액션을 쓰도록 브리지를 노출하기 위함
if (typeof window !== "undefined" && !window.__setZoneHoveredId) {
  window.__setZoneHoveredId = null;
}

if (typeof window !== "undefined" && !window.__toggleZoneActiveById) {
  window.__toggleZoneActiveById = null;
}

export function getZonesForTf(tfStr) {
  return zoneStore.zonesByTf[String(tfStr)] || [];
}

export function getHoveredZoneId() {
  return zoneStore.hoveredBoxId;
}

export function setHoveredZoneId(boxId) {
  zoneStore.hoveredBoxId = boxId;
}

export function setZoneChart(chart) {
  zoneStore.chart = chart || null;
}

export function makeZoneKey(symbol, tfStr, startTs, side) {
  return `${symbol}-${tfStr}-${startTs}-${side}`;
}

function normalizeRange(fromMs, toMs) {
  const from = Number(fromMs);
  const to = Number(toMs);
  if (!Number.isFinite(from) || !Number.isFinite(to)) {
    return { from: null, to: null };
  }
  return {
    from: Math.min(from, to),
    to: Math.max(from, to),
  };
}

function mergeCoverageSegments(segments, nextSegment) {
  const normalizedNext = normalizeRange(nextSegment?.from, nextSegment?.to);
  if (!Number.isFinite(normalizedNext.from) || !Number.isFinite(normalizedNext.to)) {
    return segments.slice();
  }

  const sorted = [...segments, normalizedNext]
    .filter((segment) => Number.isFinite(segment?.from) && Number.isFinite(segment?.to))
    .sort((a, b) => a.from - b.from);

  const merged = [];
  for (const segment of sorted) {
    const last = merged[merged.length - 1];
    if (!last || segment.from > last.to + 1) {
      merged.push({ ...segment });
      continue;
    }
    last.to = Math.max(last.to, segment.to);
  }
  return merged;
}

export function getZoneRangeKey(fromMs, toMs) {
  const normalized = normalizeRange(fromMs, toMs);
  if (!Number.isFinite(normalized.from) || !Number.isFinite(normalized.to)) {
    return "all";
  }
  return `${normalized.from}:${normalized.to}`;
}

export function isZoneRangeCovered(tfStr, fromMs, toMs) {
  const tfKey = String(tfStr);
  const normalized = normalizeRange(fromMs, toMs);
  if (!Number.isFinite(normalized.from) || !Number.isFinite(normalized.to)) {
    return zoneStore.zonesLoaded[tfKey] === true;
  }

  const segments = zoneStore.rangeCoverageByTf[tfKey] || [];
  return segments.some(
    (segment) => segment.from <= normalized.from && segment.to >= normalized.to
  );
}

export function markZoneRangeLoaded(tfStr, fromMs, toMs) {
  const tfKey = String(tfStr);
  const normalized = normalizeRange(fromMs, toMs);
  zoneStore.zonesLoaded[tfKey] = true;
  zoneStore.lastFetchedAtByTf[tfKey] = Date.now();

  if (!Number.isFinite(normalized.from) || !Number.isFinite(normalized.to)) {
    zoneStore.rangeCoverageByTf[tfKey] = [
      { from: Number.NEGATIVE_INFINITY, to: Number.POSITIVE_INFINITY },
    ];
    return;
  }

  zoneStore.rangeCoverageByTf[tfKey] = mergeCoverageSegments(
    zoneStore.rangeCoverageByTf[tfKey] || [],
    normalized
  );
}

export function isZoneRangeStale(tfStr, staleMs = 15_000) {
  const tfKey = String(tfStr);
  const fetchedAt = Number(zoneStore.lastFetchedAtByTf[tfKey] || 0);
  if (!fetchedAt) return true;
  return Date.now() - fetchedAt > staleMs;
}

export function getZoneInFlightRequest(tfStr, requestKey) {
  const tfKey = String(tfStr);
  const requestMap = zoneStore.inFlightByTf[tfKey] || {};
  return requestMap[requestKey] || null;
}

export function setZoneInFlightRequest(tfStr, requestKey, promise) {
  const tfKey = String(tfStr);
  if (!zoneStore.inFlightByTf[tfKey]) {
    zoneStore.inFlightByTf[tfKey] = {};
  }
  zoneStore.inFlightByTf[tfKey][requestKey] = promise || null;
  return zoneStore.inFlightByTf[tfKey][requestKey];
}

export function clearZoneInFlightRequest(tfStr, requestKey, promise = null) {
  const tfKey = String(tfStr);
  const requestMap = zoneStore.inFlightByTf[tfKey];
  if (!requestMap || !(requestKey in requestMap)) return;
  if (promise == null || requestMap[requestKey] === promise) {
    delete requestMap[requestKey];
  }
}

export function replaceZonesInRange(tfStr, nextZones, { fromMs = null, toMs = null } = {}) {
  const tfKey = String(tfStr);
  const incoming = Array.isArray(nextZones) ? nextZones : [];
  const normalized = normalizeRange(fromMs, toMs);
  const existing = Array.isArray(zoneStore.zonesByTf[tfKey]) ? zoneStore.zonesByTf[tfKey] : [];

  let mergedBase = existing;
  if (Number.isFinite(normalized.from) && Number.isFinite(normalized.to)) {
    mergedBase = existing.filter((zone) => {
      const startTs = Number(zone?.startTs ?? zone?.start_ts);
      return !Number.isFinite(startTs) || startTs < normalized.from || startTs > normalized.to;
    });
  } else {
    mergedBase = [];
  }

  const mergedMap = new Map();
  for (const zone of mergedBase) {
    if (!zone?.id) continue;
    mergedMap.set(zone.id, zone);
  }

  for (const zone of incoming) {
    if (!zone?.id) continue;
    mergedMap.set(zone.id, zone);
  }

  const merged = Array.from(mergedMap.values()).sort(
    (a, b) => Number(a.startTs) - Number(b.startTs)
  );
  zoneStore.zonesByTf[tfKey] = merged;
  applyStateToZones(tfKey);
  markZoneRangeLoaded(tfKey, fromMs, toMs);
  return merged;
}

export function mergeZoneStartupSnapshot(
  boxesByTf,
  { symbol = DEFAULT_SYMBOL.ticker, fromMs = null, toMs = null } = {}
) {
  if (!boxesByTf || typeof boxesByTf !== "object") return;

  for (const [tfKeyRaw, boxes] of Object.entries(boxesByTf)) {
    const tfKey = String(tfKeyRaw);
    if (!TF_LIST.includes(tfKey) || !Array.isArray(boxes)) continue;

    if (!zoneStore.boxStateCache[tfKey]) {
      zoneStore.boxStateCache[tfKey] = {};
    }

    const zones = [];
    for (const item of boxes) {
      const startTs = Number(item?.startTs);
      if (!Number.isFinite(startTs)) continue;

      const side = String(item?.side || "").toUpperCase() === "SHORT" ? "SHORT" : "LONG";
      const sign = side === "SHORT" ? -1 : 1;
      const baseEntry = Number(item?.baseEntry);
      const baseSl = Number(item?.baseSl);
      const entry = Number(item?.entry);
      const sl = Number(item?.sl);
      const upper = Number(item?.upper);
      const lower = Number(item?.lower);
      const entryOverride =
        item?.entryOverride != null ? Number(item.entryOverride) : null;

      const safeBaseEntry = Number.isFinite(baseEntry) ? baseEntry : entry;
      const safeBaseSl = Number.isFinite(baseSl) ? baseSl : sl;
      const safeBaseUpper = Math.max(safeBaseEntry, safeBaseSl);
      const safeBaseLower = Math.min(safeBaseEntry, safeBaseSl);

      const zone = {
        id: `${tfKey}-${startTs}-${sign}`,
        symbol,
        intervalMin: Number(tfKey),
        tf: tfKey,
        side,
        startTs,
        endTs: item?.endTs != null ? Number(item.endTs) : null,
        entry,
        sl,
        upper,
        lower,
        isBroken: !!item?.isBroken,
        isActive: !!item?.isActive,
        baseEntry: safeBaseEntry,
        baseSl: safeBaseSl,
        baseUpper: safeBaseUpper,
        baseLower: safeBaseLower,
        entryOverride,
      };

      const stateKey = makeZoneKey(symbol, tfKey, startTs, side);
      zoneStore.boxStateCache[tfKey][stateKey] = {
        isActive: !!item?.isActive,
        entryOverride,
      };
      zones.push(zone);
    }

    replaceZonesInRange(tfKey, zones, { fromMs, toMs });
  }
}

export function applyStateToZones(tfStr) {
  const stateMap = zoneStore.boxStateCache[tfStr];
  const zones = zoneStore.zonesByTf[tfStr];
  if (!stateMap || !Array.isArray(zones)) return;

  const symbol = DEFAULT_SYMBOL.ticker;

  zones.forEach((zone) => {
    const key = makeZoneKey(symbol, tfStr, zone.startTs, zone.side);
    const state = stateMap[key];

    zone.isActive = !!state?.isActive;

    const baseEntry = zone.baseEntry != null ? zone.baseEntry : zone.entry;
    const baseSl = zone.baseSl != null ? zone.baseSl : zone.sl;
    const baseUpper = zone.baseUpper != null ? zone.baseUpper : zone.upper;
    const baseLower = zone.baseLower != null ? zone.baseLower : zone.lower;

    if (!state || state.entryOverride == null) {
      zone.entry = baseEntry;
      zone.sl = baseSl;
      zone.upper = baseUpper;
      zone.lower = baseLower;
      return;
    }

    const overrideEntry = Number(state.entryOverride);
    if (!Number.isFinite(overrideEntry)) {
      zone.entry = baseEntry;
      zone.sl = baseSl;
      zone.upper = baseUpper;
      zone.lower = baseLower;
      return;
    }

    zone.entry = overrideEntry;
    zone.sl = baseSl;

    const sideUpper = typeof zone.side === "string" ? zone.side.toUpperCase() : "";
    if (sideUpper === "LONG") {
      zone.upper = overrideEntry;
      zone.lower = baseSl;
    } else if (sideUpper === "SHORT") {
      zone.upper = baseSl;
      zone.lower = overrideEntry;
    } else {
      zone.upper = Math.max(overrideEntry, baseSl);
      zone.lower = Math.min(overrideEntry, baseSl);
    }
  });
}

// DB에서 받은 활성화 상태와 진입가 보정값을 차트 메모리에 반영하기 위함
export function applyZoneStatePayload(tfStr, rows) {
  if (!Array.isArray(rows)) return;

  const map = {};
  for (const row of rows) {
    const intervalMin = row.intervalMin ?? Number(tfStr);
    if (Number(intervalMin) !== Number(tfStr)) continue;

    const sideStr = (row.side || "").toUpperCase();
    const startTime = row.startTime;
    if (!startTime) continue;

    let startTs;
    if (typeof startTime === "string") {
      const hasTz = /Z$/.test(startTime) || /[+-]\d{2}:\d{2}$/.test(startTime);
      const fixed = hasTz ? startTime : `${startTime}Z`;
      startTs = Date.parse(fixed);
    } else {
      startTs = new Date(startTime).getTime();
    }

    const key = makeZoneKey(
      DEFAULT_SYMBOL.ticker,
      tfStr,
      startTs,
      sideStr === "SHORT" ? "SHORT" : "LONG"
    );

    map[key] = {
      isActive: !!row.isActive,
      entryOverride: row.entryOverride != null ? Number(row.entryOverride) : null,
    };
  }

  zoneStore.boxStateCache[tfStr] = map;
  applyStateToZones(tfStr);
}

// 실시간 delta(created, broken)를 현재 차트 상태에 이어붙이기 위함
export function applyZoneDelta(tfStr, delta) {
  if (!delta) return;

  const created = Array.isArray(delta.created) ? delta.created : [];
  const broken = Array.isArray(delta.broken) ? delta.broken : [];

  if (!Array.isArray(zoneStore.zonesByTf[tfStr])) {
    zoneStore.zonesByTf[tfStr] = [];
  }

  const zones = zoneStore.zonesByTf[tfStr] || [];
  const map = new Map();

  for (const zone of zones) {
    const startTsRaw = zone?.startTs ?? zone?.start_ts;
    const startTs = startTsRaw != null ? Number(startTsRaw) : NaN;
    const sideUp = String(zone?.side ?? "").toUpperCase() === "SHORT" ? "SHORT" : "LONG";
    const sign = sideUp === "SHORT" ? -1 : 1;
    const id = Number.isFinite(startTs) ? `${tfStr}-${startTs}-${sign}` : String(zone.id);

    if (Number.isFinite(startTs)) zone.startTs = startTs;
    zone.side = sideUp;
    zone.id = id;
    map.set(id, zone);
  }

  for (const zone of created) {
    if (!zone) continue;
    const zoneTf = String(zone.tf || zone.intervalMin || "");
    if (zoneTf !== String(tfStr)) continue;
    map.set(zone.id, zone);
  }

  for (const brokenZone of broken) {
    const startTsRaw = brokenZone?.startTs ?? brokenZone?.start_ts;
    const startTs = startTsRaw != null ? Number(startTsRaw) : NaN;
    if (!Number.isFinite(startTs)) continue;

    const sideUp = (brokenZone?.side || "").toUpperCase() === "SHORT" ? "SHORT" : "LONG";
    const sign = sideUp === "SHORT" ? -1 : 1;
    const id = `${tfStr}-${startTs}-${sign}`;

    const target = map.get(id);
    if (!target) continue;

    target.isBroken = true;
    target.endTs = brokenZone?.endTs != null ? Number(brokenZone.endTs) : target.endTs;
  }

  const merged = Array.from(map.values());
  merged.sort((a, b) => Number(a.startTs) - Number(b.startTs));
  zoneStore.zonesByTf[tfStr] = merged;
  applyStateToZones(tfStr);
}

// 알림 패널 또는 차트 클릭으로 활성화 여부를 토글하기 위함
export function toggleZoneActiveById(boxId) {
  if (!boxId) return null;

  let targetZone = null;
  let tfStr = null;

  for (const [tfKey, zones] of Object.entries(zoneStore.zonesByTf)) {
    if (!Array.isArray(zones)) continue;
    const found = zones.find((zone) => zone.id === boxId);
    if (found) {
      targetZone = found;
      tfStr = tfKey;
      break;
    }
  }

  if (!targetZone || !tfStr) return null;

  const symbol = DEFAULT_SYMBOL.ticker;
  const side = targetZone.side;
  const startTs = targetZone.startTs;
  const key = makeZoneKey(symbol, tfStr, startTs, side);

  if (!zoneStore.boxStateCache[tfStr]) {
    zoneStore.boxStateCache[tfStr] = {};
  }

  const prevState = zoneStore.boxStateCache[tfStr][key] || {};
  const nextActive = !prevState.isActive;

  zoneStore.boxStateCache[tfStr][key] = {
    ...prevState,
    isActive: nextActive,
  };

  targetZone.isActive = nextActive;

  return {
    symbol,
    intervalMin: Number(tfStr),
    startTime: new Date(startTs).toISOString(),
    side: (side || "LONG").toUpperCase() === "SHORT" ? "SHORT" : "LONG",
    isActive: nextActive,
    entryOverride:
      typeof prevState.entryOverride === "number" ? prevState.entryOverride : null,
  };
}
