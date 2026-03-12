import { DEFAULT_SYMBOL, TF_LIST } from "../constants";

// 타임프레임별 Structure Zone 상태를 한곳에서 관리하기 위함
export const zoneStore = {
  zonesByTf: {},
  boxStateCache: {},
  zonesLoaded: {},
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
